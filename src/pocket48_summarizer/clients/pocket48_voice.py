from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
CONVERSATION_PATH = "/im/api/v1/conversation/page"
TEAM_ROOM_MESSAGES_PATH = "/im/api/v1/team/message/list/all"
ROOM_MESSAGE_MAX_PAGES = 100
ROOM_MESSAGE_MAX_ITEMS = 5000


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


@dataclass(frozen=True, slots=True)
class PublicRoomMessage:
    message_id: str
    timestamp_ms: int
    sent_at: str
    nickname: str
    text: str


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

    async def resolve_chatroom_id(self, member_id: int) -> int:
        next_time = 0
        seen_cursors: set[int] = set()
        for _ in range(ROOM_MESSAGE_MAX_PAGES):
            response = await self._post(
                CONVERSATION_PATH,
                {"nextTime": next_time, "limit": 100},
            )
            payload = self._response_payload(response)
            content = payload.get("content")
            if not isinstance(content, dict):
                raise self._schema_error(
                    "口袋48会话列表缺少 content"
                )
            conversations = content.get("conversations")
            if not isinstance(conversations, list):
                conversations = content.get("conversation")
            if not isinstance(conversations, list):
                raise self._schema_error(
                    "口袋48会话列表缺少 conversations"
                )
            for conversation in conversations:
                if not isinstance(conversation, dict):
                    continue
                owner_id = self._positive_integer(
                    conversation.get("ownerId")
                )
                room_id = self._positive_integer(
                    conversation.get("targetId")
                    or conversation.get("roomId")
                )
                if owner_id == member_id and room_id is not None:
                    return room_id
            cursor = self._nonnegative_integer(content.get("nextTime"))
            if (
                cursor is None
                or cursor <= 0
                or cursor in seen_cursors
                or not conversations
            ):
                break
            seen_cursors.add(cursor)
            next_time = cursor
            await asyncio.sleep(0.2)
        raise ExternalServiceError(
            "room_voice_chatroom_not_found",
            "口袋48会话列表中没有找到该成员房间",
            False,
        )

    async def fetch_public_room_messages(
        self,
        *,
        channel_id: int,
        server_id: int,
        member_id: int,
        started_at_ms: int,
        ended_at_ms: int,
    ) -> tuple[PublicRoomMessage, ...]:
        if started_at_ms < 0 or ended_at_ms <= started_at_ms:
            raise ConfigurationError("上麦房间留言时间窗口无效")
        next_time = 0
        seen_cursors: set[int] = set()
        messages: dict[str, PublicRoomMessage] = {}
        for _ in range(ROOM_MESSAGE_MAX_PAGES):
            response = await self._post(
                TEAM_ROOM_MESSAGES_PATH,
                {
                    "channelId": channel_id,
                    "serverId": server_id,
                    "nextTime": next_time,
                    "limit": 700,
                },
            )
            payload = self._response_payload(response)
            content = payload.get("content")
            if not isinstance(content, dict):
                raise self._schema_error(
                    "口袋48房间留言响应缺少 content"
                )
            raw_messages = content.get("message")
            if not isinstance(raw_messages, list):
                raise self._schema_error(
                    "口袋48房间留言响应缺少 message"
                )
            reached_start = False
            for raw in raw_messages:
                parsed = self._public_room_message(
                    raw,
                    member_id=member_id,
                    started_at_ms=started_at_ms,
                    ended_at_ms=ended_at_ms,
                )
                raw_time = (
                    self._nonnegative_integer(raw.get("msgTime"))
                    if isinstance(raw, dict)
                    else None
                )
                if raw_time is not None and raw_time < started_at_ms:
                    reached_start = True
                if parsed is not None:
                    messages[parsed.message_id] = parsed
                    if len(messages) >= ROOM_MESSAGE_MAX_ITEMS:
                        reached_start = True
                        break
            cursor = self._nonnegative_integer(content.get("nextTime"))
            if (
                reached_start
                or cursor is None
                or cursor <= 0
                or cursor in seen_cursors
                or not raw_messages
            ):
                break
            seen_cursors.add(cursor)
            next_time = cursor
            await asyncio.sleep(0.2)
        return tuple(
            sorted(
                messages.values(),
                key=lambda message: (
                    message.timestamp_ms,
                    message.message_id,
                ),
            )
        )

    def _response_payload(
        self, response: httpx.Response
    ) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._schema_error() from exc
        if not isinstance(payload, dict):
            raise self._schema_error()
        status = self._nonnegative_integer(payload.get("status"))
        success = payload.get("success")
        if (
            response.status_code != 200
            or status not in {None, 200}
            or success is False
        ):
            raise self._response_error(response, status)
        return payload

    @classmethod
    def _public_room_message(
        cls,
        raw: Any,
        *,
        member_id: int,
        started_at_ms: int,
        ended_at_ms: int,
    ) -> PublicRoomMessage | None:
        if not isinstance(raw, dict) or raw.get("msgType") != "TEXT":
            return None
        sent_ms = cls._nonnegative_integer(raw.get("msgTime"))
        if (
            sent_ms is None
            or sent_ms < started_at_ms
            or sent_ms > ended_at_ms
        ):
            return None
        ext = cls._public_room_message_ext(raw.get("extInfo"))
        if not isinstance(ext, dict):
            return None
        user = ext.get("user")
        if not isinstance(user, dict):
            return None
        user_id = cls._positive_integer(user.get("userId"))
        if user_id == member_id:
            return None
        nickname = strip_control_chars(
            str(user.get("nickName") or user.get("nickname") or "")
        )[:100]
        text = cls._public_room_message_text(raw.get("bodys"))[:1000]
        if not text:
            text = cls._public_room_message_text(ext.get("text"))[:1000]
        if not nickname or not text:
            return None
        message_id = hashlib.sha256(
            f"{sent_ms}\0{nickname}\0{text}".encode("utf-8")
        ).hexdigest()
        return PublicRoomMessage(
            message_id=message_id,
            timestamp_ms=sent_ms - started_at_ms,
            sent_at=datetime.fromtimestamp(
                sent_ms / 1000, tz=UTC
            ).isoformat(),
            nickname=nickname,
            text=text,
        )

    @staticmethod
    def _public_room_message_ext(value: Any) -> dict[str, Any] | None:
        parsed = value
        for _ in range(3):
            if isinstance(parsed, dict):
                return parsed
            if not isinstance(parsed, str) or not parsed.strip():
                return None
            try:
                parsed = json.loads(parsed)
            except ValueError:
                return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def _public_room_message_text(cls, value: Any) -> str:
        parsed = value
        for _ in range(3):
            if isinstance(parsed, str):
                normalized = strip_control_chars(parsed)
                if not normalized:
                    return ""
                if normalized[0] not in {'"', "{", "["}:
                    return normalized
                try:
                    parsed = json.loads(normalized)
                except ValueError:
                    return normalized
                continue
            if isinstance(parsed, dict):
                for key in (
                    "text",
                    "content",
                    "msg",
                    "message",
                    "desc",
                    "title",
                    "notice",
                ):
                    candidate = parsed.get(key)
                    if isinstance(candidate, str):
                        return strip_control_chars(candidate)
                return ""
            return ""
        return strip_control_chars(parsed) if isinstance(parsed, str) else ""

    @staticmethod
    def _nonnegative_integer(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @classmethod
    def _positive_integer(cls, value: Any) -> int | None:
        parsed = cls._nonnegative_integer(value)
        return parsed if parsed is not None and parsed > 0 else None

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
