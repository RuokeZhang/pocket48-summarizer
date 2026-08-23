from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStage(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    FETCHING_DANMAKU = "fetching_danmaku"
    EXTRACTING_AUDIO = "extracting_audio"
    UPLOADING_AUDIO = "uploading_audio"
    TRANSCRIBING = "transcribing"
    NORMALIZING_TRANSCRIPT = "normalizing_transcript"
    SUMMARIZING_CHUNKS = "summarizing_chunks"
    SUMMARIZING_FINAL = "summarizing_final"
    CLEANING_UP = "cleaning_up"
    COMPLETED = "completed"


class ReplayMetadata(BaseModel):
    live_id: str
    member_id: str
    member_name: str
    title: str
    cover_url: str | None = None
    replay_started_at: str | None = None
    duration_ms: int | None = None
    media_url: str
    danmaku_url: str | None = None


class TranscriptSegment(BaseModel):
    sequence: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    speaker_id: str | None = None


class DanmakuEntry(BaseModel):
    sequence: int
    timestamp_ms: int = Field(ge=0)
    author: str
    text: str


class DanmakuPeak(BaseModel):
    rank: int
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    message_count: int = Field(ge=0)
    score: float = Field(ge=0)
    samples: list[dict[str, Any]] = Field(default_factory=list)


class SummaryCandidate(BaseModel):
    start_ms: int
    end_ms: int
    title: str
    detail: str
    evidence_segment_ids: list[int] = Field(min_length=1)


class ChunkSummary(BaseModel):
    start_ms: int
    end_ms: int
    summary: str
    topics: list[str] = Field(default_factory=list)
    timeline_candidates: list[SummaryCandidate] = Field(default_factory=list)
    highlight_candidates: list[SummaryCandidate] = Field(default_factory=list)
    verification_needed: list[str] = Field(default_factory=list)
    evidence_segment_ids: list[int] = Field(default_factory=list)


class TimelineItem(BaseModel):
    start_ms: int
    end_ms: int
    title: str
    detail: str
    evidence_segment_ids: list[int] = Field(min_length=1)


class TopicItem(BaseModel):
    name: str
    detail: str
    evidence_segment_ids: list[int] = Field(default_factory=list)


class HighlightItem(BaseModel):
    start_ms: int
    end_ms: int
    title: str
    detail: str
    evidence_segment_ids: list[int] = Field(min_length=1)
    danmaku_evidence: str | None = None


class FinalSummary(BaseModel):
    overview: str
    timeline: list[TimelineItem]
    topics: list[TopicItem]
    highlights: list[HighlightItem]
    verification_needed: list[str] = Field(default_factory=list)


class JobRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_url: str
    live_id: str
    status: JobStatus
    stage: JobStage
    progress_percent: int
    progress_message: str
    member_id: str | None = None
    member_name: str | None = None
    title: str | None = None
    cover_url: str | None = None
    replay_started_at: str | None = None
    duration_ms: int | None = None
    media_url: str | None = None
    danmaku_url: str | None = None
    danmaku_loaded_at: str | None = None
    audio_path: str | None = None
    audio_extracted_at: str | None = None
    oss_object_key: str | None = None
    oss_uploaded_at: str | None = None
    dashscope_task_id: str | None = None
    dashscope_task_status: str | None = None
    asr_raw_json: str | None = None
    asr_completed_at: str | None = None
    summary_json: str | None = None
    summary_markdown: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_retryable: bool = False
    cleanup_warning: str | None = None
    retry_count: int = 0
    worker_id: str | None = None
    lease_expires_at: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
