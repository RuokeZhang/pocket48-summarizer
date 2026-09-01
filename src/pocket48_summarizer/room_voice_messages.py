from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .clients.pocket48_auth import (
    load_pa_generator,
    load_room_voice_credentials,
)
from .clients.pocket48_voice import Pocket48VoiceClient
from .config import Settings
from .errors import AppError, ConfigurationError
from .models import RoomVoiceProcessingRecord, RoomVoicePublicMessageRecord
from .repository import JobRepository

ROOM_VOICE_MESSAGES_VERSION = "public-text-v2"


class RoomVoiceMessageService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        *,
        client_factory: Callable[..., Pocket48VoiceClient] = (
            Pocket48VoiceClient
        ),
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client_factory = client_factory

    async def run(self, session_id: str, worker_id: str) -> None:
        job = self._require_job(session_id)
        member_id = self._positive_id(job.member_id, "成员")
        started_at_ms = self._timestamp_ms(
            job.capture_started_at, "录音开始"
        )
        ended_at_ms = self._timestamp_ms(job.capture_ended_at, "录音结束")
        generator = load_pa_generator(
            self.settings.pocket48_pa_signing_seed_path
        )
        credentials = load_room_voice_credentials(
            self.settings.pocket48_voice_credentials_path,
            pa_provider=generator.generate,
        )
        client = self.client_factory(self.settings, credentials)
        try:
            room_id = (
                self._positive_id(job.room_id, "房间")
                if job.room_id
                else await client.resolve_chatroom_id(member_id)
            )
            self.repository.set_room_voice_room_id(
                session_id, str(room_id), worker_id
            )
            messages = await client.fetch_public_room_messages(
                room_id=room_id,
                member_id=member_id,
                started_at_ms=started_at_ms,
                ended_at_ms=ended_at_ms,
            )
        finally:
            await client.close()
        self.repository.complete_room_voice_messages(
            session_id,
            worker_id,
            ROOM_VOICE_MESSAGES_VERSION,
            [
                RoomVoicePublicMessageRecord(
                    session_id=session_id,
                    message_id=message.message_id,
                    timestamp_ms=message.timestamp_ms,
                    sent_at=message.sent_at,
                    nickname=message.nickname,
                    text=message.text,
                )
                for message in messages
            ],
        )

    def _require_job(
        self, session_id: str
    ) -> RoomVoiceProcessingRecord:
        job = self.repository.get_room_voice_processing(session_id)
        if job is None:
            raise AppError(
                "room_voice_processing_not_found",
                "上麦录音处理任务不存在",
                False,
            )
        return job

    @staticmethod
    def _positive_id(value: str | None, label: str) -> int:
        try:
            parsed = int(value or "")
        except ValueError as exc:
            raise ConfigurationError(
                f"上麦{label} ID 缺失或无效"
            ) from exc
        if parsed <= 0:
            raise ConfigurationError(
                f"上麦{label} ID 缺失或无效"
            )
        return parsed

    @staticmethod
    def _timestamp_ms(value: str | None, label: str) -> int:
        if not value:
            raise ConfigurationError(f"上麦{label}时间缺失")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ConfigurationError(
                f"上麦{label}时间无效"
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return round(parsed.timestamp() * 1000)
