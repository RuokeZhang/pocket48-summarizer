from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from ..clients.llm import OpenAICompatibleClient
from ..config import Settings
from ..errors import ExternalServiceError
from ..models import ChunkSummary, DanmakuPeak, FinalSummary, TranscriptSegment
from ..repository import JobRepository
from .chunking import build_transcript_chunks
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, chunk_prompt, final_prompt
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
            overlap_segments=self.settings.llm_chunk_overlap_segments,
        )
        saved = self.repository.get_summary_chunks(job_id, PROMPT_VERSION)
        chunk_summaries: list[ChunkSummary] = []
        for chunk in chunks:
            prompt = chunk_prompt(chunk)
            input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            valid_chunk_ids = set(chunk.segment_ids)
            existing = saved.get(chunk.index)
            if existing and existing[0] == input_hash:
                try:
                    chunk_summary = ChunkSummary.model_validate_json(existing[1])
                    self._validate_chunk_evidence(
                        chunk_summary, valid_chunk_ids
                    )
                except (ValidationError, ExternalServiceError):
                    pass
                else:
                    chunk_summaries.append(chunk_summary)
                    if on_progress:
                        await on_progress(len(chunk_summaries), len(chunks))
                    continue
            chunk_summary = await self._request_chunk(
                prompt, valid_chunk_ids
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
            chunk_summaries, peaks, valid_segment_ids
        )
        markdown = render_summary_markdown(
            summary,
            title=title,
            member_name=member_name,
            live_id=live_id,
        )
        return summary, markdown

    async def _request_chunk(
        self, prompt: str, valid_ids: set[int]
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
                self._validate_chunk_evidence(summary, valid_ids)
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
                for item in summary.timeline:
                    self._validate_evidence(
                        item.evidence_segment_ids, valid_ids, "时间线"
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
        cls, summary: ChunkSummary, valid_ids: set[int]
    ) -> None:
        cls._validate_evidence(
            summary.evidence_segment_ids, valid_ids, "分段总结"
        )
        for item in summary.timeline_candidates:
            cls._validate_evidence(
                item.evidence_segment_ids, valid_ids, "分段时间线"
            )
        for item in summary.highlight_candidates:
            cls._validate_evidence(
                item.evidence_segment_ids, valid_ids, "分段高光"
            )
