from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from .clients.pocket48_auth import (
    PA_REFERENCE_SHA256,
    PA_REFERENCE_URL,
    Pocket48AuthClient,
    Pocket48DeviceIdentity,
    SmsChallenge,
    SmsSendResult,
    load_pa_generator,
    save_pa_signing_seed,
    save_room_voice_credentials,
)
from .config import Settings
from .errors import AppError, ConfigurationError, ExternalServiceError

PA_REFERENCE_MAX_BYTES = 128 * 1024
PRIVATE_JSON_MAX_BYTES = 64 * 1024
PA_SEED_RE = re.compile(rb'paSecret\s*=\s*"([A-F0-9]{32})"')
LOGIN_PENDING_MAX_AGE = timedelta(minutes=10)
SMS_COOLDOWN = timedelta(seconds=60)
SAFE_CODE_RE = re.compile(r"^[a-z0-9_]{1,80}$")
SAFE_MONITOR_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SAFE_PHASES = {
    "active_without_stream",
    "auth_paused",
    "awaiting_inactive",
    "error",
    "inactive",
    "max_local_bytes",
    "polling",
    "recording",
    "recording_cooldown",
    "storage_limit",
    "waiting",
    "waiting_configuration",
    "waiting_credentials",
    "waiting_next_poll",
}
SAFE_SESSION_STATES = {
    "completed",
    "ended",
    "failed",
    "interrupted",
    "max_bytes",
    "max_duration",
    "partial",
    "recording",
    "starting",
}
ACTIVE_SESSION_STATES = {"recording", "starting"}
PROCESSABLE_SESSION_STATES = {
    "completed",
    "ended",
    "interrupted",
    "max_bytes",
    "max_duration",
    "partial",
}
SEGMENT_NAME_RE = re.compile(r"^segment-[0-9]{6}\.mp3$")
PUBLIC_SESSION_LIMIT = 20
PUBLIC_SEGMENT_LIMIT = 100
AREA_RE = re.compile(r"^[0-9]{1,4}$")
MOBILE_RE = re.compile(r"^[0-9]{6,20}$")
CODE_RE = re.compile(r"^[0-9]{4,8}$")


@dataclass(frozen=True, slots=True)
class PrivateFileStatus:
    exists: bool
    private: bool
    state: str


@dataclass(frozen=True, slots=True)
class SafeMonitorStatus:
    monitor_id: str | None
    phase: str
    updated_at: str | None
    error_code: str | None
    session_id: str | None
    channel_id: str | None
    server_id: str | None


@dataclass(frozen=True, slots=True)
class SafeCaptureSegment:
    name: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SafeCaptureSession:
    session_id: str
    monitor_id: str
    member_name: str | None
    status: str
    started_at: str | None
    ended_at: str | None
    segment_count: int
    total_bytes: int
    segments: tuple[SafeCaptureSegment, ...]


class PendingChallenge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=500)
    options: tuple[str, ...] = Field(default=(), max_length=20)

    @field_validator("options")
    @classmethod
    def validate_options(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() or len(value) > 200 for value in values):
            raise ValueError("invalid challenge option")
        return tuple(value.strip() for value in values)


class PendingRoomVoiceLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    app_info: dict[str, Any]
    user_agent: str = Field(min_length=1, max_length=512)
    created_at: datetime
    last_sms_at: datetime | None = None
    challenge: PendingChallenge | None = None

    def identity(self) -> Pocket48DeviceIdentity:
        return Pocket48DeviceIdentity(
            app_info=dict(self.app_info),
            user_agent=self.user_agent,
        )


async def ensure_reviewed_pa_seed(
    path: Path,
    *,
    client: httpx.AsyncClient | None = None,
    force_download: bool = False,
) -> None:
    if not force_download and (path.exists() or path.is_symlink()):
        load_pa_generator(path)
        return

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=30, follow_redirects=False)
    try:
        async with http.stream(
            "GET", PA_REFERENCE_URL, follow_redirects=False
        ) as response:
            if (
                response.status_code != 200
                or response.is_redirect
                or str(response.url) != PA_REFERENCE_URL
            ):
                raise ExternalServiceError(
                    "pa_reference_download_failed",
                    "无法下载固定版本的 pa 协议参考文件",
                    True,
                )
            content = bytearray()
            async for chunk in response.aiter_bytes():
                if len(content) + len(chunk) > PA_REFERENCE_MAX_BYTES:
                    raise ExternalServiceError(
                        "pa_reference_too_large",
                        "pa 协议参考文件超过允许大小",
                        False,
                    )
                content.extend(chunk)
    except httpx.RequestError as exc:
        raise ExternalServiceError(
            "pa_reference_download_failed",
            "无法下载固定版本的 pa 协议参考文件",
            True,
        ) from exc
    finally:
        if owns_client:
            await http.aclose()

    if hashlib.sha256(content).hexdigest() != PA_REFERENCE_SHA256:
        raise ExternalServiceError(
            "pa_reference_hash_mismatch",
            "pa 协议参考文件哈希不匹配，已拒绝导入",
            False,
        )
    matches = PA_SEED_RE.findall(content)
    if len(matches) != 1:
        raise ExternalServiceError(
            "pa_reference_schema_changed",
            "固定版本参考文件中未找到唯一 pa 签名种子",
            False,
        )
    save_pa_signing_seed(path, SecretStr(matches[0].decode("ascii")))


class RoomVoiceAdminService:
    def __init__(
        self,
        settings: Settings,
        *,
        auth_client_factory: Callable[..., Pocket48AuthClient] = (
            Pocket48AuthClient
        ),
        pa_provisioner: Callable[..., Any] = ensure_reviewed_pa_seed,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.auth_client_factory = auth_client_factory
        self.pa_provisioner = pa_provisioner
        self.now = now or (lambda: datetime.now(UTC))
        self._operation_lock = asyncio.Lock()

    async def send_sms(
        self,
        *,
        area: str,
        mobile: str,
        challenge_answer: str | None,
    ) -> SmsSendResult:
        area, mobile, challenge_answer = _validate_sms_form(
            area, mobile, challenge_answer
        )
        async with self._operation_lock:
            await self.pa_provisioner(
                self.settings.pocket48_pa_signing_seed_path
            )
            now = _aware_utc(self.now())
            pending = _load_pending_if_present(
                self.settings.room_voice_login_pending_path
            )
            if pending is not None and now - pending.created_at >= (
                LOGIN_PENDING_MAX_AGE
            ):
                delete_pending_login(
                    self.settings.room_voice_login_pending_path
                )
                pending = None
            if challenge_answer and (
                pending is None or pending.challenge is None
            ):
                raise AppError(
                    "room_voice_challenge_missing",
                    "当前没有可回答的人机验证题，请重新请求短信",
                    False,
                )
            if pending is None:
                identity = Pocket48DeviceIdentity.create()
                pending = PendingRoomVoiceLogin(
                    app_info=identity.app_info,
                    user_agent=identity.user_agent,
                    created_at=now,
                )
            if (
                pending.last_sms_at is not None
                and now - pending.last_sms_at < SMS_COOLDOWN
            ):
                raise AppError(
                    "room_voice_sms_cooldown",
                    "短信请求间隔至少为 60 秒，请稍后手动重试",
                    False,
                )
            pending = pending.model_copy(
                update={"last_sms_at": now, "challenge": None}
            )
            save_pending_login(
                self.settings.room_voice_login_pending_path, pending
            )

            generator = load_pa_generator(
                self.settings.pocket48_pa_signing_seed_path
            )
            client = self.auth_client_factory(
                self.settings,
                pending.identity(),
                pa_provider=generator.generate,
            )
            try:
                result = await client.send_sms(
                    area=area,
                    mobile=mobile,
                    challenge_answer=challenge_answer,
                )
            finally:
                await client.close()
            challenge = (
                _pending_challenge(
                    result.challenge,
                    sensitive=(mobile, challenge_answer),
                )
                if result.challenge is not None
                else None
            )
            save_pending_login(
                self.settings.room_voice_login_pending_path,
                pending.model_copy(update={"challenge": challenge}),
            )
            return result

    async def complete_login(
        self, *, area: str, mobile: str, code: str
    ) -> None:
        area, mobile, code = _validate_login_form(area, mobile, code)
        async with self._operation_lock:
            pending = load_pending_login(
                self.settings.room_voice_login_pending_path
            )
            now = _aware_utc(self.now())
            if (
                now < pending.created_at
                or now - pending.created_at >= LOGIN_PENDING_MAX_AGE
            ):
                delete_pending_login(
                    self.settings.room_voice_login_pending_path
                )
                raise AppError(
                    "room_voice_login_expired",
                    "短信登录会话已超过 10 分钟，请重新请求短信",
                    False,
                )
            generator = load_pa_generator(
                self.settings.pocket48_pa_signing_seed_path
            )
            client = self.auth_client_factory(
                self.settings,
                pending.identity(),
                pa_provider=generator.generate,
            )
            try:
                credentials = await client.login_by_code(
                    area=area,
                    mobile=mobile,
                    code=code,
                )
            finally:
                await client.close()
            save_room_voice_credentials(
                self.settings.pocket48_voice_credentials_path,
                credentials,
            )
            delete_pending_login(
                self.settings.room_voice_login_pending_path
            )


def save_pending_login(path: Path, pending: PendingRoomVoiceLogin) -> None:
    _write_private_json(
        path,
        pending.model_dump(mode="json", exclude_none=True),
    )


def load_pending_login(path: Path) -> PendingRoomVoiceLogin:
    _require_private_file(path, "房间上麦短信登录状态")
    try:
        payload = _read_small_json_object(path)
        pending = PendingRoomVoiceLogin.model_validate(payload)
    except (OSError, UnicodeError, ValueError, ValidationError) as exc:
        raise ConfigurationError(
            "本地房间上麦短信登录状态文件格式无效"
        ) from exc
    pending = pending.model_copy(
        update={
            "created_at": _aware_utc(pending.created_at),
            "last_sms_at": (
                _aware_utc(pending.last_sms_at)
                if pending.last_sms_at is not None
                else None
            ),
        }
    )
    return pending


def delete_pending_login(path: Path) -> None:
    path.unlink(missing_ok=True)


def inspect_private_file(path: Path) -> PrivateFileStatus:
    if not path.exists() and not path.is_symlink():
        return PrivateFileStatus(False, False, "missing")
    try:
        _require_private_file(path, "私有")
    except ConfigurationError:
        return PrivateFileStatus(True, False, "invalid")
    return PrivateFileStatus(True, True, "ready")


def read_safe_monitor_status(path: Path) -> SafeMonitorStatus | None:
    try:
        _require_private_file(path, "房间上麦监控状态")
        payload = _read_small_json_object(path)
    except (OSError, UnicodeError, ValueError, ConfigurationError):
        return None
    phase = payload.get("phase")
    return SafeMonitorStatus(
        monitor_id=_safe_monitor_id(payload.get("monitor_id")),
        phase=phase if phase in SAFE_PHASES else "unknown",
        updated_at=_safe_datetime(payload.get("updated_at")),
        error_code=_safe_code(payload.get("error_code")),
        session_id=_safe_uuid(payload.get("session_id")),
        channel_id=_safe_positive_id(payload.get("channel_id")),
        server_id=_safe_positive_id(payload.get("server_id")),
    )


def list_safe_capture_sessions(
    root: Path,
    *,
    limit: int = PUBLIC_SESSION_LIMIT,
    segment_limit: int = PUBLIC_SEGMENT_LIMIT,
) -> list[SafeCaptureSession]:
    if limit <= 0 or not root.is_dir():
        return []
    limit = min(limit, PUBLIC_SESSION_LIMIT)
    segment_limit = max(0, min(segment_limit, PUBLIC_SEGMENT_LIMIT))
    candidates: list[tuple[int, Path, str]] = []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    for child in children:
        session_id = _strict_uuid(child.name)
        if session_id is None or not _private_directory(child):
            continue
        state_path = child / "session.json"
        try:
            _require_private_file(state_path, "房间上麦录音会话状态")
            modified = state_path.stat().st_mtime_ns
        except (OSError, ConfigurationError):
            continue
        candidates.append((modified, state_path, session_id))
    candidates.sort(reverse=True)
    summaries: list[SafeCaptureSession] = []
    for _, state_path, session_id in candidates:
        summary = _safe_capture_session(
            state_path,
            session_id,
            segment_limit=segment_limit,
        )
        if summary is not None:
            summaries.append(summary)
        if len(summaries) >= limit:
            break
    return summaries


def list_processable_capture_sessions(
    root: Path,
) -> list[SafeCaptureSession]:
    if not root.is_dir():
        return []
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    sessions: list[SafeCaptureSession] = []
    for child in children:
        session_id = _strict_uuid(child.name)
        if session_id is None or not _private_directory(child):
            continue
        state_path = child / "session.json"
        try:
            _require_private_file(
                state_path, "房间上麦录音会话状态"
            )
        except ConfigurationError:
            continue
        summary = _safe_capture_session(
            state_path,
            session_id,
            segment_limit=PUBLIC_SEGMENT_LIMIT,
        )
        if (
            summary is not None
            and summary.status in PROCESSABLE_SESSION_STATES
            and summary.segments
        ):
            sessions.append(summary)
    return sorted(
        sessions,
        key=lambda session: session.started_at or "",
    )


def safe_capture_session(
    root: Path, session_id: str
) -> SafeCaptureSession | None:
    session_id = _strict_uuid(session_id)
    if session_id is None:
        return None
    session_path = root / session_id
    if not _private_directory(session_path):
        return None
    state_path = session_path / "session.json"
    try:
        _require_private_file(state_path, "房间上麦录音会话状态")
    except ConfigurationError:
        return None
    return _safe_capture_session(state_path, session_id)


def _safe_capture_session(
    path: Path,
    session_id: str,
    *,
    segment_limit: int = PUBLIC_SEGMENT_LIMIT,
) -> SafeCaptureSession | None:
    try:
        payload = _read_small_json_object(path)
    except (OSError, UnicodeError, ValueError, ConfigurationError):
        return None
    stored_session_id = payload.get("session_id")
    if (
        stored_session_id is not None
        and _strict_uuid(stored_session_id) != session_id
    ):
        return None
    status = payload.get("status")
    if status not in SAFE_SESSION_STATES:
        return None
    segments = _list_safe_segments(
        path.parent,
        status=status,
        limit=segment_limit,
    )
    return SafeCaptureSession(
        session_id=session_id,
        monitor_id=(
            _safe_monitor_id(payload.get("monitor_id")) or "primary"
        ),
        member_name=_safe_display_name(payload.get("member_name")),
        status=status,
        started_at=_safe_datetime(payload.get("started_at")),
        ended_at=_safe_datetime(payload.get("ended_at")),
        segment_count=_safe_nonnegative_int(payload.get("segment_count")),
        total_bytes=_safe_nonnegative_int(payload.get("total_bytes")),
        segments=segments,
    )


def safe_capture_segment_path(
    root: Path, session_id: str, segment_name: str
) -> Path | None:
    session_id = _strict_uuid(session_id)
    if session_id is None or not SEGMENT_NAME_RE.fullmatch(segment_name):
        return None
    session_path = root / session_id
    if not _private_directory(session_path):
        return None
    state_path = session_path / "session.json"
    try:
        _require_private_file(state_path, "房间上麦录音会话状态")
    except ConfigurationError:
        return None
    summary = _safe_capture_session(state_path, session_id, segment_limit=0)
    if summary is None:
        return None
    segments_path = session_path / "segments"
    if not _private_directory(segments_path):
        return None
    segment_path = segments_path / segment_name
    metadata = _private_regular_file_metadata(segment_path)
    if metadata is None or metadata.st_size <= 0:
        return None
    if summary.status in ACTIVE_SESSION_STATES:
        if segment_name == _latest_segment_name(segments_path):
            return None
    return segment_path


def _list_safe_segments(
    session_path: Path, *, status: str, limit: int
) -> tuple[SafeCaptureSegment, ...]:
    segments_path = session_path / "segments"
    if limit <= 0 or not _private_directory(segments_path):
        return ()
    segment_files = sorted(
        _safe_segment_files(segments_path),
        key=lambda item: item[0].name,
    )
    if status in ACTIVE_SESSION_STATES:
        latest_name = _latest_segment_name(segments_path)
        segment_files = [
            item for item in segment_files if item[0].name != latest_name
        ]
    return tuple(
        SafeCaptureSegment(name=path.name, size_bytes=metadata.st_size)
        for path, metadata in segment_files[:limit]
    )


def _safe_segment_files(
    segments_path: Path,
) -> list[tuple[Path, os.stat_result]]:
    try:
        children = list(segments_path.iterdir())
    except OSError:
        return []
    safe_files: list[tuple[Path, os.stat_result]] = []
    for path in children:
        if not SEGMENT_NAME_RE.fullmatch(path.name):
            continue
        metadata = _private_regular_file_metadata(path)
        if metadata is not None and metadata.st_size > 0:
            safe_files.append((path, metadata))
    return safe_files


def _latest_segment_name(segments_path: Path) -> str | None:
    try:
        children = list(segments_path.iterdir())
    except OSError:
        return None
    names: list[str] = []
    for path in children:
        if not SEGMENT_NAME_RE.fullmatch(path.name):
            continue
        try:
            metadata = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(metadata.st_mode):
            names.append(path.name)
    return max(names, default=None)


def _load_pending_if_present(path: Path) -> PendingRoomVoiceLogin | None:
    if not path.exists() and not path.is_symlink():
        return None
    return load_pending_login(path)


def _pending_challenge(
    challenge: SmsChallenge,
    *,
    sensitive: tuple[str | None, ...] = (),
) -> PendingChallenge:
    def clean(value: str) -> str:
        for secret in sensitive:
            if secret:
                value = value.replace(secret, "[已隐藏]")
        return value

    return PendingChallenge(
        question=clean(challenge.question),
        options=tuple(clean(option) for option in challenge.options),
    )


def _validate_sms_form(
    area: str, mobile: str, challenge_answer: str | None
) -> tuple[str, str, str | None]:
    area = area.strip()
    mobile = mobile.strip()
    answer = (challenge_answer or "").strip()
    if not AREA_RE.fullmatch(area) or not MOBILE_RE.fullmatch(mobile):
        raise AppError(
            "room_voice_phone_invalid",
            "国家/地区代码或手机号格式无效",
            False,
        )
    if len(answer) > 200:
        raise AppError(
            "room_voice_challenge_invalid",
            "人机验证答案格式无效",
            False,
        )
    return area, mobile, answer or None


def _validate_login_form(
    area: str, mobile: str, code: str
) -> tuple[str, str, str]:
    area = area.strip()
    mobile = mobile.strip()
    code = code.strip()
    if not AREA_RE.fullmatch(area) or not MOBILE_RE.fullmatch(mobile):
        raise AppError(
            "room_voice_phone_invalid",
            "国家/地区代码或手机号格式无效",
            False,
        )
    if not CODE_RE.fullmatch(code):
        raise AppError(
            "room_voice_code_invalid",
            "短信验证码格式无效",
            False,
        )
    return area, mobile, code


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConfigurationError("房间上麦登录时间格式无效")
    return value.astimezone(UTC)


def _safe_datetime(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        parsed = _aware_utc(parsed)
    except (ValueError, ConfigurationError):
        return None
    return parsed.isoformat()


def _safe_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _strict_uuid(value: object) -> str | None:
    parsed = _safe_uuid(value)
    return parsed if parsed == value else None


def _safe_code(value: object) -> str | None:
    if not isinstance(value, str) or not SAFE_CODE_RE.fullmatch(value):
        return None
    return value


def _safe_monitor_id(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or not SAFE_MONITOR_ID_RE.fullmatch(value)
    ):
        return None
    return value


def _safe_display_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or len(value) > 100
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    ):
        return None
    return value


def _safe_positive_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return str(parsed) if parsed > 0 else None


def _safe_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _private_directory(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and stat.S_IMODE(metadata.st_mode) & 0o077 == 0
        and (not hasattr(os, "getuid") or metadata.st_uid == os.getuid())
    )


def _private_regular_file_metadata(
    path: Path,
) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except OSError:
        return None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (
            hasattr(os, "getuid")
            and metadata.st_uid != os.getuid()
        )
    ):
        return None
    return metadata


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


def _read_small_json_object(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        content = handle.read(PRIVATE_JSON_MAX_BYTES + 1)
    if len(content) > PRIVATE_JSON_MAX_BYTES:
        raise ConfigurationError("本地私有状态文件超过允许大小")
    payload = json.loads(content.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ConfigurationError("本地私有状态文件格式无效")
    return payload


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    temporary_path = path.with_name(path.name + ".tmp")
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
