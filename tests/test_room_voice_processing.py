from __future__ import annotations

import json
from pathlib import Path

import pytest

from pocket48_summarizer.models import (
    FinalSummary,
    HighlightItem,
    TimelineItem,
    TopicItem,
)
from pocket48_summarizer.room_voice_processing import (
    RoomVoiceProcessingService,
)


class FakeFFmpeg:
    def __init__(self):
        self.inputs: list[Path] = []

    async def concat_audio_segments(self, input_paths, output_path):
        self.inputs = list(input_paths)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"combined-audio")
        return output_path


class FakeOSS:
    def __init__(self):
        self.uploaded: list[tuple[Path, str]] = []
        self.deleted: list[str] = []

    def room_voice_object_key(self, session_id):
        return f"temporary/room-voice/{session_id}/audio.mp3"

    async def upload(self, path, key):
        assert path.read_bytes() == b"combined-audio"
        self.uploaded.append((path, key))

    async def signed_get_url(self, key):
        return f"https://oss.example/{key}?signature=secret"

    async def delete(self, key):
        self.deleted.append(key)


class FakeDashScope:
    def __init__(self):
        self.submitted_url = None

    async def submit(self, url, *, vocabulary_id=None):
        self.submitted_url = url
        assert vocabulary_id == "vocabulary-1"
        return "task-1", "PENDING"

    async def wait_for_result(self, task_id, on_status=None):
        assert task_id == "task-1"
        if on_status:
            await on_status("SUCCEEDED")
        return {
            "file_url": "https://oss.example/audio.mp3?signature=secret",
            "transcripts": [
                {
                    "sentences": [
                        {
                            "begin_time": 0,
                            "end_time": 5000,
                            "text": "大家好，今天聊聊最近的安排。",
                        }
                    ]
                }
            ],
        }


class ActiveVocabulary:
    vocabulary_id = "vocabulary-1"


class FakeVocabulary:
    async def ensure_current(self):
        return ActiveVocabulary()


class FakeSummarizer:
    def __init__(self):
        self.segments = []

    async def summarize(self, **kwargs):
        self.segments = kwargs["segments"]
        if kwargs["on_progress"]:
            await kwargs["on_progress"](1, 1)
        return (
            FinalSummary(
                overview="成员分享了最近的安排。",
                timeline=[
                    TimelineItem(
                        start_ms=0,
                        end_ms=5000,
                        title="近况分享",
                        detail="介绍最近的安排。",
                        evidence_segment_ids=[1],
                    )
                ],
                topics=[
                    TopicItem(
                        name="近期安排",
                        detail="成员说明了近期计划。",
                        evidence_segment_ids=[1],
                    )
                ],
                highlights=[
                    HighlightItem(
                        start_ms=0,
                        end_ms=5000,
                        title="开场分享",
                        detail="直接进入近况话题。",
                        evidence_segment_ids=[1],
                    )
                ],
                verification_needed=[],
            ),
            "# 上麦录音\n",
        )


def write_session(settings, session_id):
    session_path = settings.room_voice_path / session_id
    session_path.mkdir(parents=True, mode=0o700)
    session_path.chmod(0o700)
    segments_path = session_path / "segments"
    segments_path.mkdir(mode=0o700)
    segments_path.chmod(0o700)
    total_bytes = 0
    for index in range(2):
        segment = segments_path / f"segment-{index:06d}.mp3"
        segment.write_bytes(f"segment-{index}".encode())
        segment.chmod(0o600)
        total_bytes += segment.stat().st_size
    state_path = session_path / "session.json"
    state_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "monitor_id": "wang-ruiqi",
                "member_name": "王睿琦",
                "member_id": "530390",
                "channel_id": "1230624",
                "server_id": "6227955",
                "status": "ended",
                "started_at": "2026-09-01T14:57:05+00:00",
                "ended_at": "2026-09-01T15:16:26+00:00",
                "segment_count": 2,
                "total_bytes": total_bytes,
            }
        ),
        encoding="utf-8",
    )
    state_path.chmod(0o600)


@pytest.mark.asyncio
async def test_discovers_and_processes_completed_room_voice_session(
    settings, repository
):
    session_id = "f01b316a-d5dc-44ac-b882-9bee0d30ac2f"
    write_session(settings, session_id)
    ffmpeg = FakeFFmpeg()
    oss = FakeOSS()
    dashscope = FakeDashScope()
    summarizer = FakeSummarizer()
    service = RoomVoiceProcessingService(
        settings=settings,
        repository=repository,
        ffmpeg=ffmpeg,
        oss=oss,
        dashscope=dashscope,
        summarizer=summarizer,
        vocabulary=FakeVocabulary(),
    )

    assert service.discover_sessions() == 1
    assert service.discover_sessions() == 0
    claimed = repository.claim_next_room_voice_processing("worker-1", 120)
    assert claimed and claimed.session_id == session_id

    await service.run(session_id)

    completed = repository.get_room_voice_processing(session_id)
    assert completed and completed.status == "completed"
    assert completed.progress_percent == 100
    assert repository.count_room_voice_transcript(session_id) == 1
    assert repository.get_room_voice_transcript(session_id)[0].text == (
        "大家好，今天聊聊最近的安排。"
    )
    assert completed.summary_json
    assert len(ffmpeg.inputs) == 2
    assert len(oss.uploaded) == 1
    assert len(oss.deleted) == 1
    assert completed.audio_path is None
    assert completed.oss_object_key is None
    assert "signature=secret" not in (completed.asr_raw_json or "")
    assert summarizer.segments[0].sequence == 1


def test_room_voice_processing_failure_can_be_retried(
    settings, repository
):
    session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    write_session(settings, session_id)
    service = RoomVoiceProcessingService(
        settings=settings,
        repository=repository,
        ffmpeg=FakeFFmpeg(),
        oss=FakeOSS(),
        dashscope=FakeDashScope(),
        summarizer=FakeSummarizer(),
    )
    service.discover_sessions()
    claimed = repository.claim_next_room_voice_processing("worker-1", 120)
    assert claimed
    repository.mark_room_voice_processing_failed(
        session_id,
        "temporary_failure",
        "临时失败",
        True,
    )

    retried = repository.retry_room_voice_processing(session_id)

    assert retried.status == "queued"
    assert retried.retry_count == 1
    assert retried.error_message is None
