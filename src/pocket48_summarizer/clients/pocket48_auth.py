from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from ..config import Settings
from ..errors import ConfigurationError, ExternalServiceError
from ..security import POCKET_API_HOSTS, validate_https_url
from .pocket48_voice import Pocket48VoiceCredentials

SMS_SEND_PATH = "/user/api/v1/sms/send2"
SMS_LOGIN_PATH = "/user/api/v1/login/app/mobile/code"
DEFAULT_USER_AGENT = (
    "PocketFans201807/7.0.41 (iPhone; iOS 16.3.1; Scale/2.00)"
)
PA_REFERENCE_URL = (
    "https://raw.githubusercontent.com/sjsj1849/pocket48-bot/"
    "034a337a810f426ce32299af0e86b47a1313ce1f/"
    "internal/pocket48/client.go"
)
PA_REFERENCE_SHA256 = (
    "a4f3e7b9735d2b9f1a6e39931dd29d4f6d80978d9eee69311fa053b55bc7fcb4"
)


@dataclass(frozen=True, slots=True)
class Pocket48DeviceIdentity:
    app_info: dict[str, Any]
    user_agent: str = DEFAULT_USER_AGENT

    @classmethod
    def create(cls) -> Pocket48DeviceIdentity:
        return cls(
            app_info={
                "vendor": "apple",
                "deviceId": str(uuid.uuid4()).upper(),
                "appVersion": "7.0.41",
                "appBuild": "24011601",
                "osVersion": "16.3.1",
                "osType": "ios",
                "deviceName": "iPhone XR",
                "os": "ios",
            }
        )

    def request_headers(
        self, pa: SecretStr | None = None
    ) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Accept-Language": "zh-Hans-CN;q=1",
            "Content-Type": "application/json;charset=utf-8",
            "User-Agent": self.user_agent,
            "appInfo": json.dumps(
                self.app_info, separators=(",", ":"), ensure_ascii=False
            ),
        }
        if pa is not None and pa.get_secret_value().strip():
            headers["pa"] = pa.get_secret_value().strip()
            headers["P-Sign-Type"] = "V0"
        return headers


@dataclass(frozen=True, slots=True, repr=False)
class Pocket48PaGenerator:
    signing_seed: SecretStr
    clock_ms: Callable[[], int] = field(
        default=lambda: int(time.time() * 1000),
        compare=False,
        repr=False,
    )
    nonce: Callable[[], int] = field(
        default=lambda: secrets.randbelow(10_000),
        compare=False,
        repr=False,
    )

    def generate(self) -> SecretStr:
        seed = self.signing_seed.get_secret_value().strip()
        if not seed:
            raise ConfigurationError("本地 pa 签名种子为空")
        timestamp_ms = self.clock_ms()
        nonce = self.nonce()
        if timestamp_ms <= 0 or nonce < 0 or nonce >= 10_000:
            raise ConfigurationError("本地 pa 生成器状态无效")
        digest = hashlib.md5(
            f"{timestamp_ms}{nonce}{seed}".encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()
        encoded = base64.b64encode(
            f"{timestamp_ms},{nonce},{digest},".encode("ascii")
        ).decode("ascii")
        return SecretStr(encoded)


@dataclass(frozen=True, slots=True)
class SmsChallenge:
    question: str
    options: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SmsSendResult:
    sent: bool
    challenge: SmsChallenge | None = None


class LoginContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    token: SecretStr


class LoginEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: int
    success: bool = False
    content: LoginContent | None = None


class Pocket48AuthClient:
    def __init__(
        self,
        settings: Settings,
        identity: Pocket48DeviceIdentity,
        *,
        pa: SecretStr | None = None,
        pa_provider: Callable[[], SecretStr] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.identity = identity
        self.pa = pa
        self.pa_provider = pa_provider
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def send_sms(
        self,
        *,
        mobile: str,
        area: str = "86",
        challenge_answer: str | None = None,
    ) -> SmsSendResult:
        payload = {"mobile": mobile, "area": area}
        if challenge_answer:
            payload["answer"] = challenge_answer
        response = await self._post(SMS_SEND_PATH, payload)
        body = self._json_object(response)
        status = self._status(body)
        if response.status_code == 200 and status == 200:
            return SmsSendResult(sent=True)
        if response.status_code == 200 and status == 2001:
            return SmsSendResult(
                sent=False, challenge=self._parse_challenge(body)
            )
        raise self._response_error(response, status, "发送验证码")

    async def login_by_code(
        self, *, mobile: str, code: str, area: str = "86"
    ) -> Pocket48VoiceCredentials:
        response = await self._post(
            SMS_LOGIN_PATH,
            {"mobile": mobile, "code": code, "area": area},
        )
        body = self._json_object(response)
        try:
            envelope = LoginEnvelope.model_validate(body)
        except ValidationError as exc:
            raise self._schema_error() from exc
        if (
            response.status_code != 200
            or envelope.status != 200
            or not envelope.success
            or envelope.content is None
            or not envelope.content.token.get_secret_value().strip()
        ):
            raise self._response_error(
                response, envelope.status, "短信登录"
            )
        return Pocket48VoiceCredentials(
            token=envelope.content.token,
            app_info=SecretStr(
                json.dumps(
                    self.identity.app_info,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            ),
            user_agent=self.identity.user_agent,
            pa=self.pa,
        )

    async def _post(
        self, path: str, payload: dict[str, str]
    ) -> httpx.Response:
        endpoint = self.settings.pocket_api_base_url.rstrip("/") + path
        validate_https_url(
            endpoint,
            POCKET_API_HOSTS,
            code="invalid_pocket48_auth_api_url",
            label="口袋48登录接口",
        )
        generated_pa = (
            self.pa_provider()
            if self.pa is None and self.pa_provider is not None
            else None
        )
        try:
            response = await self.client.post(
                endpoint,
                content=json.dumps(
                    payload, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8"),
                headers=self.identity.request_headers(
                    self.pa or generated_pa
                ),
            )
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "pocket48_auth_request_failed",
                "连接口袋48登录接口失败",
                True,
            ) from exc
        if response.is_redirect:
            raise ExternalServiceError(
                "unexpected_redirect",
                "口袋48登录接口返回了未允许的重定向",
                False,
            )
        if len(response.content) > self.settings.max_api_response_bytes:
            raise self._schema_error("口袋48登录响应超过允许大小")
        return response

    def _json_object(self, response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("Content-Type", "").lower()
        if (
            response.status_code == 403
            and "application/json" not in content_type
        ):
            raise ExternalServiceError(
                "pocket48_auth_edge_blocked",
                "Pocket48 CDN/WAF 拒绝了当前签名、客户端指纹或网络出口，"
                "短信请求未到达登录接口",
                False,
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise self._schema_error() from exc
        if not isinstance(body, dict):
            raise self._schema_error()
        return body

    @staticmethod
    def _status(body: dict[str, Any]) -> int | None:
        try:
            return int(body.get("status"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_challenge(body: dict[str, Any]) -> SmsChallenge:
        message = body.get("message")
        try:
            value = json.loads(message) if isinstance(message, str) else {}
        except ValueError as exc:
            raise Pocket48AuthClient._schema_error(
                "口袋48返回了无法识别的人机验证题"
            ) from exc
        question = str(value.get("question") or "").strip()[:500]
        raw_options = value.get("answer")
        options = (
            tuple(str(item).strip()[:200] for item in raw_options[:20])
            if isinstance(raw_options, list)
            else ()
        )
        if not question:
            raise Pocket48AuthClient._schema_error(
                "口袋48返回了空的人机验证题"
            )
        return SmsChallenge(question=question, options=options)

    @staticmethod
    def _schema_error(
        message: str = "口袋48登录接口返回了无法识别的数据格式",
    ) -> ExternalServiceError:
        return ExternalServiceError(
            "pocket48_auth_api_changed", message, False
        )

    @staticmethod
    def _response_error(
        response: httpx.Response,
        envelope_status: int | None,
        action: str,
    ) -> ExternalServiceError:
        if response.status_code in {401, 403}:
            message = f"口袋48拒绝了{action}请求"
        elif envelope_status in {401, 403}:
            message = f"口袋48拒绝了{action}请求；当前接口可能要求 pa"
        else:
            status = (
                str(envelope_status)
                if envelope_status is not None
                else "unknown"
            )
            message = (
                f"口袋48{action}失败（HTTP {response.status_code}，"
                f"业务状态 {status}）"
            )
        retryable = response.status_code == 429 or response.status_code >= 500
        return ExternalServiceError(
            "pocket48_auth_failed", message, retryable
        )


def save_room_voice_credentials(
    path: Path, credentials: Pocket48VoiceCredentials
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    payload = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "token": credentials.token.get_secret_value(),
        "app_info": json.loads(
            credentials.app_info.get_secret_value()
        ),
        "user_agent": credentials.user_agent,
    }
    temporary_path = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    temporary_path.chmod(0o600)
    temporary_path.replace(path)
    path.chmod(0o600)


def save_pa_signing_seed(path: Path, signing_seed: SecretStr) -> None:
    seed = signing_seed.get_secret_value().strip()
    if not seed:
        raise ConfigurationError("pa 签名种子为空")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary_path = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary_path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "signing_seed": seed},
                handle,
                ensure_ascii=False,
            )
            handle.write("\n")
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    temporary_path.chmod(0o600)
    temporary_path.replace(path)
    path.chmod(0o600)


def load_pa_generator(path: Path) -> Pocket48PaGenerator:
    _require_private_file(path, "pa 签名种子")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError("无法读取本地 pa 签名种子") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("本地 pa 签名种子文件格式无效")
    seed = str(payload.get("signing_seed") or "").strip()
    if not seed:
        raise ConfigurationError("本地 pa 签名种子文件格式无效")
    return Pocket48PaGenerator(SecretStr(seed))


def load_room_voice_credentials(
    path: Path,
    *,
    pa: SecretStr | None = None,
    pa_provider: Callable[[], SecretStr] | None = None,
) -> Pocket48VoiceCredentials:
    _require_private_file(path, "房间上麦凭证")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(
            "无法读取本地房间上麦凭证文件"
        ) from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("本地房间上麦凭证文件格式无效")
    try:
        token = str(payload["token"]).strip()
        app_info = payload["app_info"]
        user_agent = str(payload["user_agent"]).strip()
    except (KeyError, TypeError) as exc:
        raise ConfigurationError(
            "本地房间上麦凭证文件缺少必要字段"
        ) from exc
    if not token or not isinstance(app_info, dict) or not user_agent:
        raise ConfigurationError("本地房间上麦凭证文件格式无效")
    return Pocket48VoiceCredentials(
        token=SecretStr(token),
        app_info=SecretStr(
            json.dumps(
                app_info, separators=(",", ":"), ensure_ascii=False
            )
        ),
        user_agent=user_agent,
        pa=pa,
        pa_provider=pa_provider,
    )


def _require_private_file(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ConfigurationError(f"无法读取本地{label}文件") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"本地{label}路径不是普通文件")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ConfigurationError(
            f"本地{label}文件权限不正确，必须设置为 0600"
        )
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise ConfigurationError(f"本地{label}文件所有者不正确")
