from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from ..clients.llm import OpenAICompatibleClient
from ..config import Settings
from ..errors import ExternalServiceError
from ..models import (
    ChunkSummary,
    DanmakuPeak,
    FinalSummary,
    SummaryCandidate,
    TimelineItem,
    TranscriptSegment,
)
from ..repository import JobRepository
from .chunking import build_transcript_chunks
from .prompts import (
    MAX_TIMELINE_EVENT_DURATION_MS,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    chunk_prompt,
    final_prompt,
)
from .renderer import render_summary_markdown

ProgressCallback = Callable[[int, int], Awaitable[None]]


class SummarizationService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        client: OpenAICompatibleClient,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client

    async def summarize(
        self,
        *,
        job_id: str,
        live_id: str,
        title: str,
        member_name: str,
        segments: list[TranscriptSegment],
        peaks: list[DanmakuPeak],
        on_progress: ProgressCallback | None = None,
    ) -> tuple[FinalSummary, str]:
        chunks = build_transcript_chunks(
            segments,
            max_chars=self.settings.llm_max_input_chars,
            max_duration_ms=round(
                self.settings.llm_chunk_max_duration_minutes * 60 * 1000
            ),
            overlap_segments=self.settings.llm_chunk_overlap_segments,
        )
        segment_windows = {
            segment.sequence: (segment.start_ms, segment.end_ms)
            for segment in segments
        }
        saved = self.repository.get_summary_chunks(job_id, PROMPT_VERSION)
        chunk_summaries: list[ChunkSummary] = []
        for chunk in chunks:
            prompt = chunk_prompt(chunk)
            input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            valid_chunk_ids = set(chunk.segment_ids)
            chunk_segment_windows = {
                sequence: segment_windows[sequence]
                for sequence in chunk.segment_ids
            }
            existing = saved.get(chunk.index)
            if existing and existing[0] == input_hash:
                try:
                    chunk_summary = ChunkSummary.model_validate_json(existing[1])
                    chunk_summary = self._normalize_chunk_window(
                        chunk_summary,
                        expected_start_ms=chunk.start_ms,
                        expected_end_ms=chunk.end_ms,
                    )
                    chunk_summary = self._repair_chunk_windows(
                        chunk_summary,
                        chunk_segment_windows,
                        expected_start_ms=chunk.start_ms,
                        expected_end_ms=chunk.end_ms,
                    )
                    self._validate_chunk_evidence(
                        chunk_summary,
                        valid_chunk_ids,
                        chunk_segment_windows,
                        expected_start_ms=chunk.start_ms,
                        expected_end_ms=chunk.end_ms,
                    )
                except (ValidationError, ExternalServiceError):
                    pass
                else:
                    chunk_summaries.append(chunk_summary)
                    if on_progress:
                        await on_progress(len(chunk_summaries), len(chunks))
                    continue
            chunk_summary = await self._request_chunk(
                prompt,
                valid_chunk_ids,
                chunk_segment_windows,
                expected_start_ms=chunk.start_ms,
                expected_end_ms=chunk.end_ms,
            )
            self.repository.save_summary_chunk(
                job_id,
                chunk.index,
                chunk.start_ms,
                chunk.end_ms,
                PROMPT_VERSION,
                input_hash,
                chunk_summary.model_dump_json(),
            )
            chunk_summaries.append(chunk_summary)
            if on_progress:
                await on_progress(len(chunk_summaries), len(chunks))
        valid_segment_ids = {segment.sequence for segment in segments}
        summary = await self._request_final(
            chunk_summaries,
            peaks,
            valid_segment_ids,
            segment_windows,
        )
        summary = summary.model_copy(
            update={
                "timeline": self._balanced_timeline(
                    summary.timeline,
                    chunk_summaries,
                )
            }
        )
        markdown = render_summary_markdown(
            summary,
            title=title,
            member_name=member_name,
            live_id=live_id,
        )
        return summary, markdown

    async def _request_chunk(
        self,
        prompt: str,
        valid_ids: set[int],
        segment_windows: dict[int, tuple[int, int]],
        *,
        expected_start_ms: int,
        expected_end_ms: int,
    ) -> ChunkSummary:
        last_error: Exception | None = None
        request_prompt = prompt
        for attempt in range(self.settings.llm_schema_retry_attempts):
            payload = await self.client.chat_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=request_prompt,
                response_model=ChunkSummary,
            )
            try:
                summary = ChunkSummary.model_validate(payload)
                summary = self._normalize_chunk_window(
                    summary,
                    expected_start_ms=expected_start_ms,
                    expected_end_ms=expected_end_ms,
                )
                summary = self._repair_chunk_windows(
                    summary,
                    segment_windows,
                    expected_start_ms=expected_start_ms,
                    expected_end_ms=expected_end_ms,
                )
                self._validate_chunk_evidence(
                    summary,
                    valid_ids,
                    segment_windows,
                    expected_start_ms=expected_start_ms,
                    expected_end_ms=expected_end_ms,
                )
                return summary
            except (ValidationError, ExternalServiceError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.llm_schema_retry_attempts:
                    request_prompt = self._schema_retry_prompt(prompt, exc)
        if isinstance(last_error, ExternalServiceError):
            raise last_error
        raise ExternalServiceError(
            "llm_schema_invalid",
            "模型分段总结不符合要求的 JSON 结构",
            True,
        ) from last_error

    async def _request_final(
        self,
        chunks: list[ChunkSummary],
        peaks: list[DanmakuPeak],
        valid_ids: set[int],
        segment_windows: dict[int, tuple[int, int]],
    ) -> FinalSummary:
        prompt = final_prompt(chunks, peaks)
        last_error: Exception | None = None
        request_prompt = prompt
        for attempt in range(self.settings.llm_schema_retry_attempts):
            payload = await self.client.chat_json(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=request_prompt,
                response_model=FinalSummary,
            )
            try:
                summary = FinalSummary.model_validate(payload)
                summary = self._repair_final_windows(summary, segment_windows)
                for item in summary.timeline:
                    self._validate_evidence(
                        item.evidence_segment_ids, valid_ids, "时间线"
                    )
                    self._validate_timeline_granularity(
                        item,
                        segment_windows,
                        "时间线",
                    )
                for item in summary.highlights:
                    self._validate_evidence(
                        item.evidence_segment_ids, valid_ids, "高光"
                    )
                for item in summary.topics:
                    if item.evidence_segment_ids:
                        self._validate_evidence(
                            item.evidence_segment_ids, valid_ids, "话题"
                        )
                expected_peak_windows = sorted(
                    (peak.start_ms, peak.end_ms) for peak in peaks
                )
                actual_peak_windows = sorted(
                    (item.start_ms, item.end_ms)
                    for item in summary.danmaku_peak_summaries
                )
                if actual_peak_windows != expected_peak_windows:
                    raise ExternalServiceError(
                        "llm_invalid_peak_summaries",
                        "模型弹幕高峰总结与输入时间窗口不一致",
                        True,
                    )
                for item in summary.danmaku_peak_summaries:
                    if item.evidence_segment_ids:
                        self._validate_evidence(
                            item.evidence_segment_ids,
                            valid_ids,
                            "弹幕高峰总结",
                        )
                return summary
            except (ValidationError, ExternalServiceError) as exc:
                last_error = exc
                if attempt + 1 < self.settings.llm_schema_retry_attempts:
                    request_prompt = self._schema_retry_prompt(prompt, exc)
        if isinstance(last_error, ExternalServiceError):
            raise last_error
        raise ExternalServiceError(
            "llm_schema_invalid",
            "模型最终总结不符合要求的 JSON 结构",
            True,
        ) from last_error

    @staticmethod
    def _normalize_chunk_window(
        summary: ChunkSummary,
        *,
        expected_start_ms: int,
        expected_end_ms: int,
    ) -> ChunkSummary:
        if (
            summary.start_ms == expected_start_ms
            and summary.end_ms == expected_end_ms
        ):
            return summary
        return summary.model_copy(
            update={
                "start_ms": expected_start_ms,
                "end_ms": expected_end_ms,
            }
        )

    @classmethod
    def _evidence_span(
        cls,
        item: SummaryCandidate | TimelineItem,
        segment_windows: dict[int, tuple[int, int]],
    ) -> tuple[int, int] | None:
        known = [
            segment_windows[evidence_id]
            for evidence_id in item.evidence_segment_ids
            if evidence_id in segment_windows
        ]
        if not known:
            return None
        return min(window[0] for window in known), max(
            window[1] for window in known
        )

    @classmethod
    def _repair_window(
        cls,
        item: SummaryCandidate | TimelineItem,
        segment_windows: dict[int, tuple[int, int]],
        *,
        bound_start_ms: int,
        bound_end_ms: int,
        require_evidence_overlap: bool,
        max_duration_ms: int | None = None,
    ) -> SummaryCandidate | TimelineItem:
        span = cls._evidence_span(item, segment_windows)
        if span is None:
            return item
        evidence_start, evidence_end = span
        sound = (
            item.end_ms > item.start_ms
            and item.start_ms >= bound_start_ms
            and item.end_ms <= bound_end_ms
        )
        if sound and max_duration_ms is not None:
            sound = item.end_ms - item.start_ms <= max_duration_ms
        if sound and require_evidence_overlap:
            sound = evidence_end > item.start_ms and evidence_start < item.end_ms
        if sound:
            return item
        start = min(max(evidence_start, bound_start_ms), bound_end_ms)
        end = max(min(evidence_end, bound_end_ms), start)
        if max_duration_ms is not None:
            end = min(end, start + max_duration_ms)
        if end <= start:
            end = min(start + 1000, bound_end_ms)
            start = max(bound_start_ms, end - 1000)
        return item.model_copy(update={"start_ms": start, "end_ms": end})

    @classmethod
    def _repair_chunk_windows(
        cls,
        summary: ChunkSummary,
        segment_windows: dict[int, tuple[int, int]],
        *,
        expected_start_ms: int,
        expected_end_ms: int,
    ) -> ChunkSummary:
        return summary.model_copy(
            update={
                "timeline_candidates": [
                    cls._repair_window(
                        item,
                        segment_windows,
                        bound_start_ms=expected_start_ms,
                        bound_end_ms=expected_end_ms,
                        require_evidence_overlap=True,
                        max_duration_ms=MAX_TIMELINE_EVENT_DURATION_MS,
                    )
                    for item in summary.timeline_candidates
                ],
                "highlight_candidates": [
                    cls._repair_window(
                        item,
                        segment_windows,
                        bound_start_ms=expected_start_ms,
                        bound_end_ms=expected_end_ms,
                        require_evidence_overlap=False,
                    )
                    for item in summary.highlight_candidates
                ],
            }
        )

    @classmethod
    def _repair_final_windows(
        cls,
        summary: FinalSummary,
        segment_windows: dict[int, tuple[int, int]],
    ) -> FinalSummary:
        if not segment_windows:
            return summary
        bound_start = min(window[0] for window in segment_windows.values())
        bound_end = max(window[1] for window in segment_windows.values())
        return summary.model_copy(
            update={
                "timeline": [
                    cls._repair_window(
                        item,
                        segment_windows,
                        bound_start_ms=bound_start,
                        bound_end_ms=bound_end,
                        require_evidence_overlap=True,
                        max_duration_ms=MAX_TIMELINE_EVENT_DURATION_MS,
                    )
                    for item in summary.timeline
                ]
            }
        )

    @staticmethod
    def _schema_retry_prompt(
        prompt: str,
        error: ValidationError | ExternalServiceError,
    ) -> str:
        if isinstance(error, ValidationError):
            details = []
            for item in error.errors(include_url=False)[:8]:
                location = ".".join(str(part) for part in item["loc"])
                details.append(f"{location or '<root>'}: {item['msg']}")
            error_detail = "；".join(details)
        else:
            error_detail = error.message
        return (
            prompt
            + "\n\n<previous_response_validation_error>\n"
            + error_detail[:1200]
            + "\n上一次响应未通过验证。请重新生成完整 JSON，不要省略任何顶层键，"
            "并严格遵守字段类型、时间窗口和字幕证据约束。\n"
            "</previous_response_validation_error>"
        )

    @staticmethod
    def _validate_evidence(
        evidence_ids: list[int],
        valid_ids: set[int],
        label: str,
    ) -> None:
        if not evidence_ids or any(item not in valid_ids for item in evidence_ids):
            raise ExternalServiceError(
                "llm_invalid_evidence",
                f"模型{label}引用了不存在的字幕证据",
                True,
            )

    @classmethod
    def _validate_chunk_evidence(
        cls,
        summary: ChunkSummary,
        valid_ids: set[int],
        segment_windows: dict[int, tuple[int, int]],
        *,
        expected_start_ms: int,
        expected_end_ms: int,
    ) -> None:
        if (
            summary.start_ms != expected_start_ms
            or summary.end_ms != expected_end_ms
        ):
            raise ExternalServiceError(
                "llm_invalid_chunk_window",
                "模型分段总结改写了输入时间窗口",
                True,
            )
        cls._validate_evidence(
            summary.evidence_segment_ids, valid_ids, "分段总结"
        )
        for item in summary.timeline_candidates:
            cls._validate_evidence(
                item.evidence_segment_ids, valid_ids, "分段时间线"
            )
            cls._validate_candidate_window(
                item,
                expected_start_ms,
                expected_end_ms,
                "分段时间线",
            )
            cls._validate_timeline_granularity(
                item,
                segment_windows,
                "分段时间线",
            )
        for item in summary.highlight_candidates:
            cls._validate_evidence(
                item.evidence_segment_ids, valid_ids, "分段高光"
            )
            cls._validate_candidate_window(
                item,
                expected_start_ms,
                expected_end_ms,
                "分段高光",
            )

    @staticmethod
    def _validate_candidate_window(
        item: SummaryCandidate,
        start_ms: int,
        end_ms: int,
        label: str,
    ) -> None:
        if (
            item.start_ms < start_ms
            or item.end_ms > end_ms
            or item.end_ms <= item.start_ms
        ):
            raise ExternalServiceError(
                "llm_invalid_chunk_window",
                f"模型{label}超出了输入时间窗口",
                True,
            )

    @staticmethod
    def _validate_timeline_granularity(
        item: SummaryCandidate | TimelineItem,
        segment_windows: dict[int, tuple[int, int]],
        label: str,
    ) -> None:
        if (
            item.end_ms <= item.start_ms
            or item.end_ms - item.start_ms
            > MAX_TIMELINE_EVENT_DURATION_MS
        ):
            raise ExternalServiceError(
                "llm_timeline_too_coarse",
                f"模型{label}时间范围过宽或无效（每条最多 5 分钟）",
                True,
            )
        if not any(
            segment_windows[evidence_id][1] > item.start_ms
            and segment_windows[evidence_id][0] < item.end_ms
            for evidence_id in item.evidence_segment_ids
            if evidence_id in segment_windows
        ):
            raise ExternalServiceError(
                "llm_timeline_evidence_mismatch",
                f"模型{label}时间范围与引用字幕证据不重叠",
                True,
            )

    @classmethod
    def _balanced_timeline(
        cls,
        timeline: list[TimelineItem],
        chunks: list[ChunkSummary],
    ) -> list[TimelineItem]:
        if not chunks:
            return sorted(timeline, key=lambda item: (item.start_ms, item.end_ms))

        selected = list(timeline)

        for chunk in chunks:
            valid_ids = {
                evidence_id
                for evidence_id in (
                    chunk.evidence_segment_ids
                    + [
                        item
                        for candidate in chunk.timeline_candidates
                        for item in candidate.evidence_segment_ids
                    ]
                )
            }
            if any(
                item.end_ms > chunk.start_ms
                and item.start_ms < chunk.end_ms
                and set(item.evidence_segment_ids) & valid_ids
                for item in selected
            ):
                continue
            target = (chunk.start_ms + chunk.end_ms) // 2
            candidates = sorted(
                (
                    cls._timeline_item(candidate)
                    for candidate in chunk.timeline_candidates
                ),
                key=lambda item: (
                    abs(cls._timeline_midpoint(item) - target),
                    -len(item.detail),
                ),
            )
            candidate = next(
                (
                    item
                    for item in candidates
                    if not cls._timeline_duplicate(item, selected)
                ),
                None,
            )
            if candidate is not None:
                selected.append(candidate)

        return sorted(
            selected,
            key=lambda item: (item.start_ms, item.end_ms),
        )

    @staticmethod
    def _timeline_item(candidate: SummaryCandidate) -> TimelineItem:
        return TimelineItem.model_validate(candidate.model_dump())

    @staticmethod
    def _timeline_midpoint(item: TimelineItem) -> int:
        return (item.start_ms + item.end_ms) // 2

    @classmethod
    def _timeline_duplicate(
        cls,
        candidate: TimelineItem,
        selected: list[TimelineItem],
    ) -> bool:
        midpoint = cls._timeline_midpoint(candidate)
        return any(
            (
                candidate.start_ms == item.start_ms
                and candidate.end_ms == item.end_ms
                and candidate.title == item.title
            )
            or (
                abs(midpoint - cls._timeline_midpoint(item)) <= 10_000
                and candidate.title == item.title
            )
            for item in selected
        )
