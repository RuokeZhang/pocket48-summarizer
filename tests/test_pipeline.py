from pathlib import Path

import pytest

from pocket48_summarizer.media.hls import HLSManifest
from pocket48_summarizer.models import (
    FinalSummary,
    HighlightItem,
    JobStatus,
    ReplayMetadata,
    TimelineItem,
    TopicItem,
)
from pocket48_summarizer.pipeline import ReplayPipeline
from pocket48_summarizer.vocabulary import ActiveVocabulary


class FakePocket:
    async def resolve_replay(self, live_id):
        return ReplayMetadata(
            live_id=live_id,
            member_id="407126",
            member_name="成员",
            title="测试直播",
            media_url="https://idol-vod.48.cn/path/replay.m3u8",
            danmaku_url="https://source.48.cn/live/replay.lrc",
        )

    async def fetch_danmaku(self, _):
        return "[00:00:01.000]观众\t你好\n"


class FakeHLS:
    async def inspect(self, url):
        return HLSManifest(url=url, duration_ms=10_000, segment_count=2)


class FakeFFmpeg:
    async def extract_audio(self, _url, output_path, _duration):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake-audio")
        return output_path


class FakeOSS:
    def __init__(self):
        self.uploads = 0
        self.deletes = 0

    def object_key(self, job_id):
        return f"prefix/{job_id}/audio.mp3"

    async def upload(self, path: Path, _key):
        assert path.read_bytes() == b"fake-audio"
        self.uploads += 1

    async def signed_get_url(self, _key):
        return "https://oss.example/audio.mp3?signature=secret"

    async def delete(self, _key):
        self.deletes += 1


class FakeDashScope:
    def __init__(self):
        self.submits = 0
        self.vocabulary_id = None

    async def submit(self, _url, *, vocabulary_id=None):
        self.submits += 1
        self.vocabulary_id = vocabulary_id
        return "task-1", "PENDING"

    async def wait_for_result(self, _task_id, on_status=None):
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
                            "text": "大家好，今天聊聊近况。",
                        }
                    ]
                }
            ]
        }


class FakeSummarizer:
    async def summarize(self, **_):
        return (
            FinalSummary(
                overview="主播分享近况。",
                timeline=[
                    TimelineItem(
                        start_ms=0,
                        end_ms=5000,
                        title="开场",
                        detail="主播问候观众。",
                        evidence_segment_ids=[1],
                    )
                ],
                topics=[
                    TopicItem(
                        name="近况",
                        detail="近期安排。",
                        evidence_segment_ids=[1],
                    )
                ],
                highlights=[
                    HighlightItem(
                        start_ms=0,
                        end_ms=5000,
                        title="问候",
                        detail="开场互动。",
                        evidence_segment_ids=[1],
                    )
                ],
                verification_needed=[],
            ),
            "# 测试直播\n",
        )


class FakeVocabulary:
    async def ensure_current(self):
        return ActiveVocabulary("vocab-p48-test", "f" * 64)


@pytest.mark.asyncio
async def test_complete_pipeline_is_idempotent(settings, repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=123456",
        "123456",
    )
    claimed = repository.claim_next_job("worker", 120)
    assert claimed
    oss = FakeOSS()
    dashscope = FakeDashScope()
    pipeline = ReplayPipeline(
        settings=settings,
        repository=repository,
        pocket48=FakePocket(),
        hls=FakeHLS(),
        ffmpeg=FakeFFmpeg(),
        oss=oss,
        dashscope=dashscope,
        summarizer=FakeSummarizer(),
        vocabulary=FakeVocabulary(),
    )
    await pipeline.run(job.id)
    completed = repository.get_job(job.id)
    assert completed and completed.status == JobStatus.COMPLETED
    assert completed.audio_path is None
    assert completed.oss_object_key is None
    assert "signature=secret" not in completed.asr_raw_json
    assert repository.count_transcript(job.id) == 1
    assert oss.uploads == 1
    assert oss.deletes == 1
    assert dashscope.submits == 1
    assert dashscope.vocabulary_id == "vocab-p48-test"
    assert completed.asr_vocabulary_id == "vocab-p48-test"
    assert completed.asr_glossary_fingerprint == "f" * 64
