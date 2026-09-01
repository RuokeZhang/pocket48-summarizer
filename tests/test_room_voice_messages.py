from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pocket48_summarizer.clients.pocket48_voice import PublicRoomMessage
from pocket48_summarizer.room_voice_messages import RoomVoiceMessageService


class FakePAGenerator:
    def generate(self):
        return "test-pa"


class FakeMessageClient:
    def __init__(self):
        self.closed = False
        self.fetch_call = None

    async def resolve_chatroom_id(self, member_id):
        assert member_id == 6744
        return 67333093

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

    await service.run(session_id)

    completed = repository.get_room_voice_processing(session_id)
    assert completed and completed.messages_status == "completed"
    assert completed.room_id == "67333093"
    assert client.fetch_call == {
        "room_id": 67333093,
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
        "temporary_failure",
        "临时失败",
        True,
    )

    retried = repository.retry_room_voice_messages(session_id)

    assert retried.messages_status == "queued"
    assert retried.messages_error_message is None
