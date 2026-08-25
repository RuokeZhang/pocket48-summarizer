from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from ..config import Settings
from ..errors import AppError
from ..models import TranscriptSegment
from ..repository import JobRepository
from .ffmpeg import FFmpegRunner, SilenceInterval

BoundaryKind = Literal["start", "end"]
SuggestionSource = Literal["manual", "sentence", "silence"]
ANALYSIS_VERSION = "silence-v1"


@dataclass(frozen=True, slots=True)
class BoundarySuggestion:
    boundary: BoundaryKind
    requested_ms: int
    sentence_sequence: int | None
    sentence_ms: int | None
    suggested_ms: int
    source: SuggestionSource
    silence_start_ms: int | None = None
    silence_end_ms: int | None = None


class ClipBoundaryService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        ffmpeg: FFmpegRunner,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.ffmpeg = ffmpeg
        self._capacity = asyncio.Semaphore(
            settings.clip_analysis_concurrency
        )

    async def suggest(
        self,
        *,
        job_id: str,
        manifest_url: str,
        duration_ms: int,
        boundary: BoundaryKind,
        target_ms: int,
        minimum_ms: int = 0,
        maximum_ms: int | None = None,
    ) -> BoundarySuggestion:
        upper_bound = duration_ms if maximum_ms is None else maximum_ms
        if (
            minimum_ms < 0
            or upper_bound > duration_ms
            or upper_bound <= minimum_ms
            or target_ms < minimum_ms
            or target_ms > upper_bound
        ):
            raise AppError(
                "clip_boundary_out_of_range",
                "剪辑边界超出回放时长",
                False,
            )
        nearest = self._nearest_segment_boundary(
            self.repository.get_all_transcript(job_id),
            boundary,
            target_ms,
            minimum_ms=minimum_ms,
            maximum_ms=upper_bound,
        )
        if nearest is None:
            return BoundarySuggestion(
                boundary=boundary,
                requested_ms=target_ms,
                sentence_sequence=None,
                sentence_ms=None,
                suggested_ms=target_ms,
                source="manual",
            )
        segment, anchor_ms = nearest
        cache_key = self._cache_key(
            job_id,
            boundary,
            segment.sequence,
            anchor_ms,
            minimum_ms,
            upper_bound,
        )
        cached = self.repository.get_clip_boundary_suggestion(
            job_id, cache_key
        )
        if cached is not None:
            return BoundarySuggestion(
                boundary=boundary,
                requested_ms=target_ms,
                sentence_sequence=cached.segment_sequence,
                sentence_ms=cached.anchor_ms,
                suggested_ms=cached.suggested_ms,
                source=(
                    "silence"
                    if cached.silence_start_ms is not None
                    and cached.silence_end_ms is not None
                    else "sentence"
                ),
                silence_start_ms=cached.silence_start_ms,
                silence_end_ms=cached.silence_end_ms,
            )

        window_start_ms = max(
            minimum_ms,
            anchor_ms - self.settings.clip_silence_search_ms,
        )
        window_end_ms = min(
            upper_bound,
            anchor_ms + self.settings.clip_silence_search_ms,
        )
        async with self._capacity:
            intervals = await self.ffmpeg.detect_silence(
                manifest_url,
                start_ms=window_start_ms,
                end_ms=window_end_ms,
                noise_db=self.settings.clip_silence_noise_db,
                min_duration_ms=self.settings.clip_silence_min_duration_ms,
                timeout_seconds=self.settings.clip_analysis_timeout_seconds,
            )
        absolute_intervals = [
            SilenceInterval(
                start_ms=window_start_ms + interval.start_ms,
                end_ms=window_start_ms + interval.end_ms,
            )
            for interval in intervals
        ]
        selected = self._select_silence(
            boundary, anchor_ms, absolute_intervals
        )
        suggested_ms = (
            selected.end_ms
            if selected is not None and boundary == "start"
            else selected.start_ms
            if selected is not None
            else anchor_ms
        )
        suggested_ms = max(
            window_start_ms, min(suggested_ms, window_end_ms)
        )
        cached = self.repository.save_clip_boundary_suggestion(
            job_id=job_id,
            cache_key=cache_key,
            boundary_kind=boundary,
            segment_sequence=segment.sequence,
            anchor_ms=anchor_ms,
            suggested_ms=suggested_ms,
            silence_start_ms=(
                selected.start_ms if selected is not None else None
            ),
            silence_end_ms=(
                selected.end_ms if selected is not None else None
            ),
            analysis_version=self._analysis_version(),
        )
        return BoundarySuggestion(
            boundary=boundary,
            requested_ms=target_ms,
            sentence_sequence=segment.sequence,
            sentence_ms=anchor_ms,
            suggested_ms=cached.suggested_ms,
            source="silence" if selected is not None else "sentence",
            silence_start_ms=cached.silence_start_ms,
            silence_end_ms=cached.silence_end_ms,
        )

    def _nearest_segment_boundary(
        self,
        segments: list[TranscriptSegment],
        boundary: BoundaryKind,
        target_ms: int,
        *,
        minimum_ms: int = 0,
        maximum_ms: int | None = None,
    ) -> tuple[TranscriptSegment, int] | None:
        candidates = [
            (
                segment,
                segment.start_ms if boundary == "start" else segment.end_ms,
            )
            for segment in segments
            if minimum_ms
            <= (
                segment.start_ms
                if boundary == "start"
                else segment.end_ms
            )
            <= (maximum_ms if maximum_ms is not None else float("inf"))
        ]
        if not candidates:
            return None
        segment, timestamp_ms = min(
            candidates,
            key=lambda item: (
                abs(item[1] - target_ms),
                item[0].sequence,
            ),
        )
        if (
            abs(timestamp_ms - target_ms)
            > self.settings.clip_sentence_snap_threshold_ms
        ):
            return None
        return segment, timestamp_ms

    @staticmethod
    def _select_silence(
        boundary: BoundaryKind,
        anchor_ms: int,
        intervals: list[SilenceInterval],
    ) -> SilenceInterval | None:
        if not intervals:
            return None
        return min(
            intervals,
            key=lambda interval: (
                abs(
                    (
                        interval.end_ms
                        if boundary == "start"
                        else interval.start_ms
                    )
                    - anchor_ms
                ),
                interval.start_ms,
                interval.end_ms,
            ),
        )

    def _analysis_version(self) -> str:
        return (
            f"{ANALYSIS_VERSION}:"
            f"{self.settings.clip_silence_search_ms}:"
            f"{self.settings.clip_silence_noise_db:g}:"
            f"{self.settings.clip_silence_min_duration_ms}"
        )

    def _cache_key(
        self,
        job_id: str,
        boundary: BoundaryKind,
        sequence: int,
        anchor_ms: int,
        minimum_ms: int,
        maximum_ms: int,
    ) -> str:
        payload = json.dumps(
            {
                "job_id": job_id,
                "boundary": boundary,
                "sequence": sequence,
                "anchor_ms": anchor_ms,
                "minimum_ms": minimum_ms,
                "maximum_ms": maximum_ms,
                "analysis_version": self._analysis_version(),
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
