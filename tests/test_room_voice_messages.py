from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pocket48_summarizer.clients.pocket48_voice import PublicRoomMessage
from pocket48_summarizer.errors import AppError
from pocket48_summarizer.models import RoomVoicePublicMessageRecord
from pocket48_summarizer.room_voice_messages import RoomVoiceMessageService


class FakePAGenerator:
    def generate(self):
        return "test-pa"


class FakeMessageClient:
    def __init__(self):
        self.closed = False
        self.fetch_call = None

    async def fetch_public_room_messages(self, **kwargs):
        self.fetch_call = kwargs
        return (
            PublicRoomMessage(
                message_id="message-1",
                timestamp_ms=12_000,
                sent_at="2026-09-01T15:00:12+00:00",
                nickname="公开昵称",
                text="公开留言",
            ),
        )

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_fetches_and_persists_minimized_room_messages(
    settings, repository, monkeypatch
):
    session_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="yang-bingyi",
        member_name="杨冰怡",
        member_id="6744",
        capture_started_at="2026-09-01T15:00:00+00:00",
        capture_ended_at="2026-09-01T15:05:00+00:00",
        channel_id="1230624",
        server_id="6227955",
        segment_count=1,
        total_bytes=100,
    )
    claimed = repository.claim_next_room_voice_messages("worker", 120)
    assert claimed and claimed.session_id == session_id

    monkeypatch.setattr(
        "pocket48_summarizer.room_voice_messages.load_pa_generator",
        lambda _: FakePAGenerator(),
    )
    monkeypatch.setattr(
        "pocket48_summarizer.room_voice_messages.load_room_voice_credentials",
        lambda *_args, **_kwargs: object(),
    )
    client = FakeMessageClient()
    service = RoomVoiceMessageService(
        settings,
        repository,
        client_factory=lambda *_args: client,
    )

    await service.run(session_id, "worker")

    completed = repository.get_room_voice_processing(session_id)
    assert completed and completed.messages_status == "completed"
    assert completed.messages_version == "public-text-v3"
    assert client.fetch_call == {
        "channel_id": 1230624,
        "server_id": 6227955,
        "member_id": 6744,
        "started_at_ms": int(
            datetime(
                2026, 9, 1, 15, 0, tzinfo=UTC
            ).timestamp()
            * 1000
        ),
        "ended_at_ms": int(
            datetime(
                2026, 9, 1, 15, 5, tzinfo=UTC
            ).timestamp()
            * 1000
        ),
    }
    messages = repository.get_room_voice_public_messages(session_id)
    assert len(messages) == 1
    assert messages[0].nickname == "公开昵称"
    assert messages[0].text == "公开留言"
    assert client.closed is True


def test_failed_room_message_job_can_be_retried(repository):
    session_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="wang-ruiqi",
        member_name="王睿琦",
        member_id="530390",
        capture_started_at="2026-09-01T15:00:00+00:00",
        capture_ended_at="2026-09-01T15:05:00+00:00",
        segment_count=1,
        total_bytes=100,
    )
    claimed = repository.claim_next_room_voice_messages("worker", 120)
    assert claimed
    repository.mark_room_voice_messages_failed(
        session_id,
        "worker",
        "temporary_failure",
        "临时失败",
        True,
    )

    retried = repository.retry_room_voice_messages(session_id)

    assert retried.messages_status == "queued"
    assert retried.messages_error_message is None


def test_stale_worker_cannot_replace_reclaimed_room_messages(repository):
    session_id = "11111111-2222-4333-8444-555555555555"
    repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="yang-bingyi",
        member_name="杨冰怡",
        member_id="6744",
        capture_started_at="2026-09-01T15:00:00+00:00",
        capture_ended_at="2026-09-01T15:05:00+00:00",
        segment_count=1,
        total_bytes=100,
    )
    first = repository.claim_next_room_voice_messages("worker-a", 120)
    assert first
    with repository.database.connect() as connection:
        connection.execute(
            """
            UPDATE room_voice_processing_jobs
            SET messages_lease_expires_at = '2000-01-01T00:00:00+00:00'
            WHERE session_id = ?
            """,
            (session_id,),
        )
    assert repository.recover_expired_room_voice_messages() == 1
    second = repository.claim_next_room_voice_messages("worker-b", 120)
    assert second
    message = RoomVoicePublicMessageRecord(
        session_id=session_id,
        message_id="message-b",
        timestamp_ms=1000,
        sent_at="2026-09-01T15:00:01+00:00",
        nickname="新 Worker",
        text="保留这条",
    )

    with pytest.raises(AppError, match="租约"):
        repository.complete_room_voice_messages(
            session_id, "worker-a", "public-text-v1", [message]
        )

    repository.complete_room_voice_messages(
        session_id, "worker-b", "public-text-v1", [message]
    )
    stored = repository.get_room_voice_public_messages(session_id)
    assert [item.message_id for item in stored] == ["message-b"]


def test_metadata_backfill_requeues_configuration_failure(repository):
    session_id = "66666666-7777-4888-8999-aaaaaaaaaaaa"
    repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="wang-ruiqi",
        member_name="王睿琦",
        segment_count=1,
        total_bytes=100,
    )
    claimed = repository.claim_next_room_voice_messages("worker", 120)
    assert claimed
    repository.mark_room_voice_messages_failed(
        session_id,
        "worker",
        "configuration_error",
        "上麦成员 ID 缺失或无效",
        True,
    )

    updated = repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="wang-ruiqi",
        member_name="王睿琦",
        member_id="530390",
        capture_started_at="2026-09-01T15:00:00+00:00",
        capture_ended_at="2026-09-01T15:05:00+00:00",
        segment_count=1,
        total_bytes=100,
    )

    assert updated.messages_status == "queued"
    assert updated.messages_error_code is None
    assert updated.member_id == "530390"


def test_message_parser_version_requeues_completed_session(repository):
    session_id = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
    repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="wang-ruiqi",
        member_name="王睿琦",
        member_id="530390",
        capture_started_at="2026-09-01T15:00:00+00:00",
        capture_ended_at="2026-09-01T15:05:00+00:00",
        segment_count=1,
        total_bytes=100,
    )
    claimed = repository.claim_next_room_voice_messages("worker", 120)
    assert claimed
    repository.complete_room_voice_messages(
        session_id, "worker", "legacy-v0", []
    )

    updated = repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="wang-ruiqi",
        member_name="王睿琦",
        member_id="530390",
        capture_started_at="2026-09-01T15:00:00+00:00",
        capture_ended_at="2026-09-01T15:05:00+00:00",
        messages_version="public-text-v1",
        segment_count=1,
        total_bytes=100,
    )

    assert updated.messages_status == "queued"
    assert updated.messages_version == "legacy-v0"
