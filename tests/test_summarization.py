import pytest

from pocket48_summarizer.errors import ExternalServiceError
from pocket48_summarizer.models import DanmakuPeak, TranscriptSegment
from pocket48_summarizer.summarization.service import SummarizationService


class FakeLLM:
    def __init__(self, invalid_final=False):
        self.calls = 0
        self.invalid_final = invalid_final

    async def chat_json(self, **_):
        self.calls += 1
        if self.calls == 1:
            return {
                "start_ms": 0,
                "end_ms": 5000,
                "summary": "主播问候观众并介绍主题。",
                "topics": ["近况"],
                "timeline_candidates": [],
                "highlight_candidates": [],
                "verification_needed": [],
                "evidence_segment_ids": [1],
            }
        return {
            "overview": "主播分享了近期情况。",
            "timeline": [
                {
                    "start_ms": 0,
                    "end_ms": 5000,
                    "title": "开场",
                    "detail": "主播向观众问好。",
                    "evidence_segment_ids": [999 if self.invalid_final else 1],
                }
            ],
            "topics": [
                {
                    "name": "近况",
                    "detail": "讨论近期安排。",
                    "evidence_segment_ids": [1],
                }
            ],
            "highlights": [
                {
                    "start_ms": 0,
                    "end_ms": 5000,
                    "title": "问候",
                    "detail": "开场互动。",
                    "evidence_segment_ids": [1],
                    "danmaku_evidence": "弹幕活跃",
                }
            ],
            "verification_needed": [],
        }


@pytest.mark.asyncio
async def test_summarizes_with_evidence(settings, repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=123456",
        "123456",
    )
    service = SummarizationService(settings, repository, FakeLLM())
    summary, markdown = await service.summarize(
        job_id=job.id,
        live_id=job.live_id,
        title="测试直播",
        member_name="成员",
        segments=[
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=5000,
                text="大家好，今天聊聊最近的安排。",
            )
        ],
        peaks=[
            DanmakuPeak(
                rank=1,
                start_ms=0,
                end_ms=30_000,
                message_count=12,
                score=4,
                samples=[],
            )
        ],
    )
    assert summary.timeline[0].evidence_segment_ids == [1]
    assert "# 测试直播" in markdown
    assert repository.get_summary_chunks(job.id, "v1")


@pytest.mark.asyncio
async def test_rejects_invented_evidence(settings, repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=123457",
        "123457",
    )
    service = SummarizationService(
        settings, repository, FakeLLM(invalid_final=True)
    )
    with pytest.raises(ExternalServiceError, match="不存在"):
        await service.summarize(
            job_id=job.id,
            live_id=job.live_id,
            title="测试直播",
            member_name="成员",
            segments=[
                TranscriptSegment(
                    sequence=1,
                    start_ms=0,
                    end_ms=5000,
                    text="大家好。",
                )
            ],
            peaks=[],
        )
