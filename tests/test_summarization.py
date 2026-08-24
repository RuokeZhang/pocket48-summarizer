import pytest

from pocket48_summarizer.errors import ExternalServiceError
from pocket48_summarizer.models import (
    ChunkSummary,
    DanmakuPeak,
    FinalSummary,
    TranscriptSegment,
)
from pocket48_summarizer.summarization.prompts import final_prompt
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
            "danmaku_peak_summaries": [
                {
                    "start_ms": 0,
                    "end_ms": 30_000,
                    "summary": (
                        "主播正在向观众问好，弹幕样本显示观众积极回应。"
                    ),
                    "evidence_segment_ids": [1],
                }
            ],
            "verification_needed": [],
        }


class RepairingFinalLLM(FakeLLM):
    def __init__(self):
        super().__init__()
        self.prompts = []

    async def chat_json(self, **kwargs):
        self.prompts.append(kwargs)
        self.calls += 1
        if self.calls == 1:
            return await self._chunk()
        if self.calls == 2:
            return {"overview": "字段不完整"}
        return await self._final()

    async def _chunk(self):
        return {
            "start_ms": 0,
            "end_ms": 5000,
            "summary": "主播问候观众。",
            "topics": [],
            "timeline_candidates": [],
            "highlight_candidates": [],
            "verification_needed": [],
            "evidence_segment_ids": [1],
        }

    async def _final(self):
        return {
            "overview": "主播向观众问好。",
            "timeline": [],
            "topics": [],
            "highlights": [],
            "danmaku_peak_summaries": [],
            "verification_needed": [],
        }


def test_old_final_summary_defaults_peak_summaries():
    summary = FinalSummary.model_validate(
        {
            "overview": "旧总结",
            "timeline": [],
            "topics": [],
            "highlights": [],
        }
    )

    assert summary.danmaku_peak_summaries == []


def test_final_prompt_reuses_window_transcript_and_keeps_danmaku_untrusted():
    prompt = final_prompt(
        [
            ChunkSummary(
                start_ms=0,
                end_ms=5000,
                summary="主播向观众问好。",
                evidence_segment_ids=[1],
            )
        ],
        [
            DanmakuPeak(
                rank=1,
                start_ms=0,
                end_ms=30_000,
                message_count=12,
                score=4,
                samples=[{"text": "晚上好"}],
            )
        ],
    )

    assert '"transcript_context":' in prompt
    assert "主播向观众问好。" in prompt
    assert "弹幕只能证明观众在某个时段活跃" in prompt
    assert "观众反应而不是事实" in prompt


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
    assert "观众积极回应" in summary.danmaku_peak_summaries[0].summary
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


@pytest.mark.asyncio
async def test_final_summary_retries_with_schema_feedback(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=123458",
        "123458",
    )
    llm = RepairingFinalLLM()
    service = SummarizationService(settings, repository, llm)

    summary, _ = await service.summarize(
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

    assert summary.overview == "主播向观众问好。"
    assert llm.prompts[0]["response_model"] is ChunkSummary
    assert llm.prompts[1]["response_model"] is FinalSummary
    assert "previous_response_validation_error" in llm.prompts[2][
        "user_prompt"
    ]
