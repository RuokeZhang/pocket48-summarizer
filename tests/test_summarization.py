import pytest

from pocket48_summarizer.errors import ExternalServiceError
from pocket48_summarizer.models import (
    ChunkSummary,
    DanmakuPeak,
    FinalSummary,
    SummaryCandidate,
    TimelineItem,
    TranscriptSegment,
)
from pocket48_summarizer.summarization.chunking import (
    build_transcript_chunks,
)
from pocket48_summarizer.summarization.prompts import (
    MAX_TIMELINE_EVENT_DURATION_MS,
    PROMPT_VERSION,
    chunk_prompt,
    final_prompt,
)
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
                "timeline_candidates": [
                    {
                        "start_ms": 0,
                        "end_ms": 5000,
                        "title": "开场",
                        "detail": "主播向观众问好。",
                        "evidence_segment_ids": [1],
                    }
                ],
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
            "timeline_candidates": [
                {
                    "start_ms": 0,
                    "end_ms": 5000,
                    "title": "开场",
                    "detail": "主播向观众问好。",
                    "evidence_segment_ids": [1],
                }
            ],
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


class RewritingChunkWindowLLM:
    def __init__(self):
        self.calls = 0

    async def chat_json(self, **_):
        self.calls += 1
        return {
            "start_ms": 250,
            "end_ms": 4750,
            "summary": "主播问候观众。",
            "topics": [],
            "timeline_candidates": [
                {
                    "start_ms": 0,
                    "end_ms": 5000,
                    "title": "开场",
                    "detail": "主播向观众问好。",
                    "evidence_segment_ids": [1],
                }
            ],
            "highlight_candidates": [],
            "verification_needed": [],
            "evidence_segment_ids": [1],
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
                timeline_candidates=[
                    SummaryCandidate(
                        start_ms=0,
                        end_ms=5000,
                        title="开场",
                        detail="主播向观众问好。",
                        evidence_segment_ids=[1],
                    )
                ],
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


def test_transcript_chunks_respect_duration_limit_with_overlap():
    segments = [
        TranscriptSegment(
            sequence=index,
            start_ms=(index - 1) * 60_000,
            end_ms=index * 60_000,
            text=f"第 {index} 分钟",
        )
        for index in range(1, 9)
    ]

    chunks = build_transcript_chunks(
        segments,
        max_chars=100_000,
        max_duration_ms=180_000,
        overlap_segments=1,
    )

    assert chunks[0].segment_ids == (1, 2, 3)
    assert chunks[1].segment_ids[0] == 3
    assert chunks[-1].end_ms == 480_000
    assert all(
        chunk.end_ms - chunk.start_ms <= 180_000
        for chunk in chunks
    )


def test_timeline_prompts_and_validation_reject_coarse_or_unlinked_events():
    chunk = build_transcript_chunks(
        [
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=360_000,
                text="持续讨论一个主题。",
            )
        ],
        max_chars=100_000,
        max_duration_ms=360_000,
        overlap_segments=0,
    )[0]

    assert "最长 5 分钟" in chunk_prompt(chunk)
    assert "固定分段元数据" in chunk_prompt(chunk)
    assert "不得用十几分钟的宽泛区间" in final_prompt([], [])

    with pytest.raises(ExternalServiceError, match="最多 5 分钟"):
        SummarizationService._validate_timeline_granularity(
            SummaryCandidate(
                start_ms=0,
                end_ms=MAX_TIMELINE_EVENT_DURATION_MS + 1,
                title="过宽事件",
                detail="时间范围过宽。",
                evidence_segment_ids=[1],
            ),
            {1: (0, 360_000)},
            "分段时间线",
        )

    with pytest.raises(ExternalServiceError, match="不重叠"):
        SummarizationService._validate_timeline_granularity(
            TimelineItem(
                start_ms=0,
                end_ms=60_000,
                title="错误时间",
                detail="证据在另一个时间段。",
                evidence_segment_ids=[1],
            ),
            {1: (120_000, 180_000)},
            "时间线",
        )


@pytest.mark.asyncio
async def test_chunk_window_is_normalized_instead_of_failing(
    settings, repository
):
    llm = RewritingChunkWindowLLM()
    service = SummarizationService(settings, repository, llm)

    summary = await service._request_chunk(
        "测试提示词",
        {1},
        {1: (0, 5000)},
        expected_start_ms=0,
        expected_end_ms=5000,
    )

    assert summary.start_ms == 0
    assert summary.end_ms == 5000
    assert llm.calls == 1


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
    assert repository.get_summary_chunks(job.id, PROMPT_VERSION)


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


def test_timeline_keeps_all_model_items_and_fills_late_chunks():
    chunks = [
        ChunkSummary(
            start_ms=index * 1_200_000,
            end_ms=(index + 1) * 1_200_000,
            summary=f"第 {index + 1} 段",
            timeline_candidates=[
                SummaryCandidate(
                    start_ms=index * 1_200_000 + 300_000,
                    end_ms=index * 1_200_000 + 360_000,
                    title=f"分段事件 {index + 1}",
                    detail=f"第 {index + 1} 段的代表事件。",
                    evidence_segment_ids=[index + 1],
                )
            ],
            evidence_segment_ids=[index + 1],
        )
        for index in range(4)
    ]
    early_items = [
        TimelineItem(
            start_ms=index * 20_000,
            end_ms=index * 20_000 + 10_000,
            title=f"前段事件 {index + 1}",
            detail="模型保留的前段事件。",
            evidence_segment_ids=[1],
        )
        for index in range(20)
    ]

    timeline = SummarizationService._balanced_timeline(
        early_items,
        chunks,
    )

    assert len(timeline) == 23
    assert {item.title for item in early_items}.issubset(
        {item.title for item in timeline}
    )
    assert timeline[-1].start_ms == 3_900_000
    assert {
        evidence_id
        for item in timeline
        for evidence_id in item.evidence_segment_ids
    } >= {1, 2, 3, 4}
