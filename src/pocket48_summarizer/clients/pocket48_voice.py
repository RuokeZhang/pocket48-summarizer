from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from ..config import Settings
from ..errors import ConfigurationError, ExternalServiceError
from ..security import (
    POCKET_API_HOSTS,
    inspect_room_voice_stream_url,
    strip_control_chars,
    validate_https_url,
    validate_room_voice_stream_url,
)

ROOM_INFO_PATH = "/im/api/v1/im/team/room/info"
MEMBER_ROOM_PATH = "/im/api/v1/im/server/jump"
VOICE_OPERATE_PATH = "/im/api/v1/team/voice/operate"


@dataclass(frozen=True, slots=True, repr=False)
class Pocket48VoiceCredentials:
    token: SecretStr
    app_info: SecretStr
    user_agent: str
    pa: SecretStr | None = None
    pa_provider: Callable[[], SecretStr] | None = field(
        default=None, compare=False, repr=False
    )

    def request_headers(self) -> dict[str, str]:
        token = self.token.get_secret_value().strip()
        generated_pa = (
            self.pa_provider()
            if self.pa is None and self.pa_provider is not None
            else None
        )
        effective_pa = self.pa or generated_pa
        pa = (
            effective_pa.get_secret_value().strip()
            if effective_pa is not None
            else ""
        )
        app_info_text = self.app_info.get_secret_value().strip()
        user_agent = strip_control_chars(self.user_agent)
        if not token or not app_info_text or not user_agent:
            raise ConfigurationError(
                "口袋48房间语音凭证缺少 token、appInfo 或 User-Agent"
            )
        if len(app_info_text) > 4096 or len(user_agent) > 300:
            raise ConfigurationError(
                "口袋48房间语音 appInfo 或 User-Agent 超过允许长度"
            )
        try:
            app_info = json.loads(app_info_text)
        except ValueError as exc:
            raise ConfigurationError(
                "POCKET48_VOICE_APP_INFO 必须是 JSON 对象"
            ) from exc
        if not isinstance(app_info, dict) or not app_info:
            raise ConfigurationError(
                "POCKET48_VOICE_APP_INFO 必须是非空 JSON 对象"
            )
        headers = {
            "Accept": "application/json",
            "Accept-Language": "zh-Hans-CN;q=1",
            "Content-Type": "application/json;charset=utf-8",
            "User-Agent": user_agent,
            "appInfo": json.dumps(
                app_info, separators=(",", ":"), ensure_ascii=False
            ),
            "token": token,
        }
        if pa:
            headers["pa"] = pa
            headers["P-Sign-Type"] = "V0"
        return headers


class RoomInfoContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server_id: int = Field(alias="serverId", gt=0)


class RoomInfoEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: int | None = None
    success: bool = False
    content: RoomInfoContent | None = None


class MemberRoomServerInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server_id: int = Field(alias="serverId", gt=0)


class MemberRoomContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    channel_id: int = Field(alias="channelId", gt=0)
    server_id: int | None = Field(default=None, alias="serverId", gt=0)
    server: MemberRoomServerInfo | None = Field(
        default=None, alias="jumpServerInfo"
    )


class MemberRoomEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: int | None = None
    success: bool = False
    content: MemberRoomContent | None = None


@dataclass(frozen=True, slots=True)
class MemberRoom:
    member_id: int
    channel_id: int
    server_id: int


class RoomVoiceParticipant(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: str = Field(
        default="",
        validation_alias=AliasChoices("userId", "account", "accid"),
    )
    nickname: str = Field(
        default="",
        validation_alias=AliasChoices(
            "nickname", "nickName", "userName", "name"
        ),
    )
    voice_status: bool | None = Field(default=None, alias="voiceStatus")

    @field_validator("user_id", "nickname", mode="before")
    @classmethod
    def normalize_text(cls, value: Any) -> str:
        if value is None:
            return ""
        return strip_control_chars(str(value))[:200]


class RoomVoiceContent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stream_url: SecretStr | None = Field(default=None, alias="streamUrl")
    voice_users: list[RoomVoiceParticipant] = Field(
        default_factory=list, alias="voiceUserList"
    )

    @field_validator("stream_url", mode="before")
    @classmethod
    def normalize_stream_url(cls, value: Any) -> str | None:
        raw = str(value or "")
        if len(raw) > 4096 or any(
            ord(character) < 32 or ord(character) == 127
            for character in raw
        ):
            raise ValueError("invalid room voice stream URL")
        normalized = raw.strip()
        return normalized or None

    @field_validator("voice_users", mode="before")
    @classmethod
    def normalize_voice_users(cls, value: Any) -> Any:
        return [] if value is None else value


class RoomVoiceEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: int | None = None
    success: bool | None = None
    content: RoomVoiceContent | None = None


@dataclass(frozen=True, slots=True)
class RoomVoiceStatus:
    channel_id: int
    server_id: int
    stream_url: SecretStr | None
    participants: tuple[RoomVoiceParticipant, ...]

    @property
    def active(self) -> bool:
        return self.stream_url is not None or any(
            participant.voice_status is not False
            for participant in self.participants
        )

    def stream_endpoint(self) -> tuple[str, str, int | None] | None:
        if self.stream_url is None:
            return None
        return inspect_room_voice_stream_url(
            self.stream_url.get_secret_value()
        )

    def require_recordable_stream_url(
        self, allowed_hosts: set[str]
    ) -> str:
        if self.stream_url is None:
            raise ExternalServiceError(
                "room_voice_not_active",
                "房间当前没有可录制的上麦音频流",
                True,
            )
        return validate_room_voice_stream_url(
            self.stream_url.get_secret_value(), allowed_hosts
        )

    def redacted_summary(self) -> dict[str, Any]:
        endpoint = self.stream_endpoint()
        return {
            "active": self.active,
            "channel_id": self.channel_id,
            "server_id": self.server_id,
            "participant_count": len(self.participants),
            "stream": (
                {
                    "scheme": endpoint[0],
                    "host": endpoint[1],
                    "port": endpoint[2],
                }
                if endpoint is not None
                else None
            ),
        }


class Pocket48VoiceClient:
    def __init__(
        self,
        settings: Settings,
        credentials: Pocket48VoiceCredentials,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.credentials = credentials
        self.client = client or httpx.AsyncClient(
            timeout=settings.request_timeout_seconds,
            follow_redirects=False,
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def resolve_server_id(self, channel_id: int) -> int:
        response = await self._post(
            ROOM_INFO_PATH, {"channelId": str(channel_id)}
        )
        try:
            envelope = RoomInfoEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise self._schema_error() from exc
        if (
            response.status_code != 200
            or not envelope.success
            or envelope.content is None
        ):
            raise self._response_error(response, envelope.status)
        return envelope.content.server_id

    async def resolve_member_room(self, member_id: int) -> MemberRoom:
        response = await self._post(
            MEMBER_ROOM_PATH,
            {"starId": member_id, "targetType": 1},
        )
        try:
            envelope = MemberRoomEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise self._schema_error() from exc
        if (
            response.status_code != 200
            or not envelope.success
            or envelope.status != 200
            or envelope.content is None
        ):
            raise self._response_error(response, envelope.status)
        server_id = envelope.content.server_id
        if server_id is None and envelope.content.server is not None:
            server_id = envelope.content.server.server_id
        if server_id is None:
            raise self._schema_error(
                "口袋48成员房间响应缺少 serverId"
            )
        return MemberRoom(
            member_id=member_id,
            channel_id=envelope.content.channel_id,
            server_id=server_id,
        )

    async def fetch_status(
        self, channel_id: int, server_id: int
    ) -> RoomVoiceStatus:
        response = await self._post(
            VOICE_OPERATE_PATH,
            {
                "channelId": channel_id,
                "serverId": server_id,
                "operateCode": 2,
            },
        )
        try:
            envelope = RoomVoiceEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise self._schema_error() from exc
        if (
            response.status_code != 200
            or envelope.status != 200
            or envelope.content is None
        ):
            raise self._response_error(response, envelope.status)
        content = envelope.content
        status = RoomVoiceStatus(
            channel_id=channel_id,
            server_id=server_id,
            stream_url=content.stream_url,
            participants=tuple(content.voice_users),
        )
        status.stream_endpoint()
        return status

    async def _post(
        self, path: str, payload: dict[str, Any]
    ) -> httpx.Response:
        endpoint = self.settings.pocket_api_base_url.rstrip("/") + path
        validate_https_url(
            endpoint,
            POCKET_API_HOSTS,
            code="invalid_pocket48_voice_api_url",
            label="口袋48房间语音接口",
        )
        try:
            response = await self.client.post(
                endpoint,
                content=json.dumps(
                    payload, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8"),
                headers=self.credentials.request_headers(),
            )
        except httpx.RequestError as exc:
            raise ExternalServiceError(
                "room_voice_request_failed",
                "连接口袋48房间语音接口失败",
                True,
            ) from exc
        if response.is_redirect:
            raise ExternalServiceError(
                "unexpected_redirect",
                "口袋48房间语音接口返回了未允许的重定向",
                False,
            )
        if len(response.content) > self.settings.max_api_response_bytes:
            raise self._schema_error("口袋48房间语音响应超过允许大小")
        return response

    @staticmethod
    def _schema_error(
        message: str = "口袋48房间语音接口返回了无法识别的数据格式",
    ) -> ExternalServiceError:
        return ExternalServiceError(
            "room_voice_api_changed", message, False
        )

    @staticmethod
    def _response_error(
        response: httpx.Response, envelope_status: int | None
    ) -> ExternalServiceError:
        if response.status_code == 401 or envelope_status == 401004:
            return ExternalServiceError(
                "room_voice_auth_required",
                "口袋48账号 token 已失效；已停止，请人工重新登录",
                False,
            )
        retryable = response.status_code == 429 or response.status_code >= 500
        status = (
            str(envelope_status)
            if envelope_status is not None
            else "unknown"
        )
        return ExternalServiceError(
            "room_voice_lookup_failed",
            f"口袋48房间语音查询失败（HTTP {response.status_code}，"
            f"业务状态 {status}）",
            retryable,
        )
