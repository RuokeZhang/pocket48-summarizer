from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

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


class SubtitleTranslationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class GlossaryTermType(StrEnum):
    CP_NAME = "cp_name"
    TEAM_ABBREVIATION = "team_abbreviation"
    STAGE = "stage"
    SONG = "song"
    UNIT = "unit"
    EVENT = "event"
    FANDOM = "fandom"
    OTHER = "other"


class MemberCatalogEntry(BaseModel):
    member_id: str
    canonical_name: str
    pinyin: str = ""
    group_id: str = ""
    group_name: str = ""
    team_id: str = ""
    team_name: str = ""
    status: str
    ranking: int = 0
    active: bool


class MemberCatalogRecord(MemberCatalogEntry):
    source_present: bool
    source: str
    first_seen_at: str
    last_seen_at: str


class GlossaryTermRecord(BaseModel):
    id: str
    canonical_text: str
    canonical_normalized: str
    term_type: str
    description_zh: str
    description_en: str
    source: str
    active: bool
    created_by_user_id: str | None = None
    created_at: str
    updated_at: str


class GlossaryAliasRecord(BaseModel):
    id: str
    member_id: str | None = None
    term_id: str | None = None
    alias: str
    alias_normalized: str
    active: bool
    created_by_user_id: str | None = None
    created_at: str
    updated_at: str
    target_text: str
    target_type: str


class GlossarySyncStateRecord(BaseModel):
    source_url: str
    sync_status: str
    source_hash: str | None = None
    catalog_version: str | None = None
    glossary_fingerprint: str | None = None
    member_count: int
    active_member_count: int
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_error: str | None = None
    active_vocabulary_id: str | None = None
    vocabulary_fingerprint: str | None = None
    vocabulary_updated_at: str | None = None
    vocabulary_error: str | None = None


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
    summary: str = ""


class StrictSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryCandidate(StrictSummaryModel):
    start_ms: int
    end_ms: int
    title: str
    detail: str
    evidence_segment_ids: list[int] = Field(min_length=1)


class ChunkSummary(StrictSummaryModel):
    start_ms: int
    end_ms: int
    summary: str
    topics: list[str] = Field(default_factory=list)
    timeline_candidates: list[SummaryCandidate] = Field(default_factory=list)
    highlight_candidates: list[SummaryCandidate] = Field(default_factory=list)
    verification_needed: list[str] = Field(default_factory=list)
    evidence_segment_ids: list[int] = Field(default_factory=list)


class TimelineItem(StrictSummaryModel):
    start_ms: int
    end_ms: int
    title: str
    detail: str
    evidence_segment_ids: list[int] = Field(min_length=1)


class TopicItem(StrictSummaryModel):
    name: str
    detail: str
    evidence_segment_ids: list[int] = Field(default_factory=list)


class HighlightItem(StrictSummaryModel):
    start_ms: int
    end_ms: int
    title: str
    detail: str
    evidence_segment_ids: list[int] = Field(min_length=1)
    danmaku_evidence: str | None = None


class DanmakuPeakSummary(StrictSummaryModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    summary: str = Field(min_length=1)
    evidence_segment_ids: list[int] = Field(default_factory=list)


class FinalSummary(StrictSummaryModel):
    overview: str
    timeline: list[TimelineItem]
    topics: list[TopicItem]
    highlights: list[HighlightItem]
    danmaku_peak_summaries: list[DanmakuPeakSummary] = Field(
        default_factory=list
    )
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


class VideoClipRecord(BaseModel):
    job_id: str
    timeline_index: int
    start_ms: int
    end_ms: int
    filename: str
    status: Literal["running", "completed", "failed"]
    oss_object_key: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class SubtitleTranslationRequestRecord(BaseModel):
    job_id: str
    language: Literal["en"]
    status: SubtitleTranslationStatus
    retry_count: int
    error_message: str | None = None
    worker_id: str | None = None
    lease_expires_at: str | None = None
    requested_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None


class UserRecord(BaseModel):
    id: str
    username: str
    username_normalized: str
    password_hash: str
    is_admin: bool
    is_active: bool
    failed_login_count: int
    locked_until: str | None = None
    created_at: str
    last_login_at: str | None = None


class SessionRecord(BaseModel):
    id: str
    user_id: str
    token_hash: str
    csrf_token_hash: str
    created_at: str
    expires_at: str
    last_seen_at: str
    user: UserRecord
