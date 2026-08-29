from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from pydantic import BaseModel, Field, field_validator, model_validator

from .auth import AuthContext
from .errors import AppError
from .media.clips import ClipState
from .media.fonts import emoji_font_family
from .media.ai_covers import (
    AI_COVER_PROMPT_MAX_LENGTH,
    DEFAULT_AI_COVER_PROMPT,
)
from .media.overlays import (
    AI_COVER_EXTRA_TEXT_MAX_ITEMS,
    AI_COVER_HIGHLIGHT_MAX_LENGTH,
    AI_COVER_TITLE_MAX_LENGTH,
    COVER_DURATION_MS,
    DEFAULT_SUBTITLE_FONT_SCALE,
    SUBTITLE_FONT_SCALE_MAX,
    SUBTITLE_FONT_SCALE_MIN,
    normalize_ai_cover_extra_text,
    normalize_ai_cover_highlight,
    normalize_ai_cover_layout_style,
    normalize_ai_cover_title,
)
from .models import (
    AICoverLayoutStyle,
    AICoverAssetRecord,
    AICoverGenerationRecord,
    ClipRange,
    FinalSummary,
    GlossaryTermType,
    JobRecord,
    JobStatus,
    SubtitleTranslationRequestRecord,
    TimelineItem,
    VideoClipExportRecord,
)
from .parsing.transcript import transcript_to_srt
from .runtime_lock import shared_runtime_lock
from .security import parse_share_url
from .summarization.chunking import format_clock

router = APIRouter()


class CreateJobRequest(BaseModel):
    url: str


class ClipBoundarySuggestionRequest(BaseModel):
    timeline_index: int = Field(ge=0)
    boundary: Literal["start", "end"]
    target_ms: int = Field(ge=0)


class CreateClipExportRequest(BaseModel):
    request_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    timeline_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    kept_ranges: list[ClipRange] | None = Field(
        default=None,
        min_length=1,
        max_length=32,
    )
    subtitle_mode: Literal["off", "zh", "en", "bilingual"]
    include_danmaku: bool = False
    subtitle_font_scale: int = Field(
        default=DEFAULT_SUBTITLE_FONT_SCALE,
        ge=SUBTITLE_FONT_SCALE_MIN,
        le=SUBTITLE_FONT_SCALE_MAX,
    )
    output_layout: Literal["portrait", "landscape"] = "portrait"
    subtitle_font_family: Literal["wenkai", "serif", "sans"] = "wenkai"
    ai_cover_generation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @model_validator(mode="after")
    def validate_clip_style(self) -> CreateClipExportRequest:
        ranges = self.kept_ranges or [
            ClipRange(start_ms=self.start_ms, end_ms=self.end_ms)
        ]
        previous_end: int | None = None
        for item in ranges:
            if previous_end is not None and item.start_ms < previous_end:
                raise ValueError("clip ranges must be ordered and non-overlapping")
            previous_end = item.end_ms
        if (
            ranges[0].start_ms != self.start_ms
            or ranges[-1].end_ms != self.end_ms
        ):
            raise ValueError("clip range bounds must match start and end")
        self.kept_ranges = ranges
        return self


class CreateAICoverRequest(BaseModel):
    request_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    timeline_index: int = Field(ge=0)
    source_timestamp_ms: int = Field(ge=0)
    layout_style: AICoverLayoutStyle = "sticker_pop"
    title_text: str = Field(
        min_length=1,
        max_length=AI_COVER_TITLE_MAX_LENGTH,
    )
    highlight_text: str = Field(
        default="",
        max_length=AI_COVER_HIGHLIGHT_MAX_LENGTH,
    )
    extra_text: list[str] = Field(
        default_factory=list,
        max_length=AI_COVER_EXTRA_TEXT_MAX_ITEMS,
    )
    prompt_template: str | None = Field(
        default=None,
        max_length=AI_COVER_PROMPT_MAX_LENGTH,
    )

    @field_validator("title_text")
    @classmethod
    def clean_title(cls, value: str) -> str:
        return normalize_ai_cover_title(value)

    @field_validator("layout_style")
    @classmethod
    def clean_layout_style(
        cls, value: AICoverLayoutStyle
    ) -> AICoverLayoutStyle:
        return normalize_ai_cover_layout_style(value)

    @field_validator("highlight_text")
    @classmethod
    def clean_highlight(cls, value: str) -> str:
        return normalize_ai_cover_highlight(value)

    @field_validator("extra_text")
    @classmethod
    def clean_extra_text(cls, value: list[str]) -> list[str]:
        return normalize_ai_cover_extra_text(value)


class RegenerateAICoverRequest(BaseModel):
    request_id: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    title_text: str | None = Field(
        default=None,
        max_length=AI_COVER_TITLE_MAX_LENGTH,
    )
    prompt_template: str | None = Field(
        default=None,
        max_length=AI_COVER_PROMPT_MAX_LENGTH,
    )

    @field_validator("title_text")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_ai_cover_title(value)


def format_china_datetime(value: str | None) -> str:
    if not value:
        return "时间未知"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return "时间未知"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M"
    )


def require_auth(request: Request) -> AuthContext:
    return request.app.state.auth.authenticate(request)


def optional_auth(request: Request) -> AuthContext | None:
    return request.app.state.auth.optional_context(request)


def require_admin(request: Request) -> AuthContext:
    context = require_auth(request)
    if not context.user.is_admin:
        raise AppError("admin_required", "仅管理员可以执行此操作", False)
    return context


def require_ai_cover_service(request: Request):
    service = request.app.state.services.ai_covers
    if service is None:
        raise AppError(
            "ai_cover_configuration_missing",
            "管理员尚未配置 Seedream，AI 封面暂不可用",
            True,
        )
    return service


def reject_ai_cover_maintenance(request: Request) -> None:
    if request.app.state.settings.clip_maintenance_path.exists():
        raise AppError(
            "ai_cover_maintenance",
            "服务正在发布新版本，请稍后再生成封面",
            True,
        )


async def signed_ai_cover_download_url(
    request: Request, asset: AICoverAssetRecord
) -> str:
    service = request.app.state.services.ai_covers
    if service is not None:
        return await service.signed_download_url(asset)
    clipper = request.app.state.services.clipper
    if (
        clipper is None
        or asset.status != "completed"
        or not asset.final_oss_object_key
    ):
        raise AppError(
            "ai_cover_not_ready",
            "AI 封面尚未生成完成",
            True,
        )
    return await clipper.oss.signed_ai_cover_url(
        asset.final_oss_object_key
    )


def require_owned_job(
    request: Request, job_id: str
) -> tuple[AuthContext, JobRecord]:
    context = require_auth(request)
    repository = request.app.state.services.repository
    job = (
        repository.get_job(job_id)
        if context.user.is_admin
        else repository.get_job_for_user(job_id, context.user.id)
    )
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
    return context, job


def require_readable_job(
    request: Request, job_id: str
) -> tuple[AuthContext | None, JobRecord]:
    repository = request.app.state.services.repository
    job = repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
    context = optional_auth(request)
    if (
        not request.app.state.settings.auth_required
        or job.status == JobStatus.COMPLETED
    ):
        return context, job
    if context and (
        context.user.is_admin
        or repository.get_job_for_user(job_id, context.user.id) is not None
    ):
        return context, job
    raise AppError("job_not_found", "任务不存在", False)


async def parse_form(request: Request) -> dict[str, str]:
    body = await request.body()
    if len(body) > 8192:
        raise AppError("form_too_large", "表单内容过大", False)
    values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: items[-1] for key, items in values.items() if items}


def public_job_payload(job: JobRecord) -> dict:
    return {
        "id": job.id,
        "source_url": job.source_url,
        "live_id": job.live_id,
        "status": job.status,
        "stage": job.stage,
        "progress_percent": job.progress_percent,
        "progress_message": job.progress_message,
        "member_name": job.member_name,
        "title": job.title,
        "replay_started_at": job.replay_started_at,
        "duration_ms": job.duration_ms,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "error_retryable": job.error_retryable,
        "cleanup_warning": job.cleanup_warning,
        "has_transcript": job.asr_raw_json is not None,
        "has_summary": job.summary_markdown is not None,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
    }


def timeline_clip_context(
    request: Request, job_id: str, timeline_index: int
) -> tuple[JobRecord, TimelineItem]:
    _, job = require_readable_job(request, job_id)
    if not job.summary_json:
        raise AppError("summary_not_ready", "总结尚未生成", True)
    summary = FinalSummary.model_validate_json(job.summary_json)
    if timeline_index < 0 or timeline_index >= len(summary.timeline):
        raise AppError("timeline_item_not_found", "时间线话题不存在", False)
    if not job.media_url:
        raise AppError("media_not_ready", "回放媒体地址尚未生成", True)
    return job, summary.timeline[timeline_index]


def clip_editor_bounds(
    request: Request, job: JobRecord, item: TimelineItem
) -> tuple[int, int]:
    if not job.duration_ms or job.duration_ms <= 0:
        raise AppError(
            "media_duration_missing",
            "回放时长尚未准备好",
            True,
        )
    context_ms = round(
        request.app.state.settings.clip_editor_context_minutes
        * 60
        * 1000
    )
    return (
        max(0, item.start_ms - context_ms),
        min(job.duration_ms, item.end_ms + context_ms),
    )


def validate_clip_range(
    request: Request,
    job: JobRecord,
    item: TimelineItem,
    start_ms: int,
    end_ms: int,
) -> None:
    lower_bound, upper_bound = clip_editor_bounds(request, job, item)
    if (
        start_ms < lower_bound
        or end_ms > upper_bound
        or end_ms <= start_ms
    ):
        raise AppError(
            "invalid_clip_range",
            "剪辑范围超出当前时间线条目的可编辑窗口",
            False,
        )
    max_duration_ms = round(
        request.app.state.settings.max_clip_minutes * 60 * 1000
    )
    if end_ms - start_ms > max_duration_ms:
        raise AppError(
            "clip_too_long",
            (
                "单个视频片段最长 "
                f"{request.app.state.settings.max_clip_minutes:g} 分钟"
            ),
            False,
        )


def validate_clip_ranges(
    request: Request,
    job: JobRecord,
    item: TimelineItem,
    ranges: list[ClipRange],
) -> None:
    if not ranges:
        raise AppError(
            "invalid_clip_range",
            "至少需要保留一个视频片段",
            False,
        )
    lower_bound, upper_bound = clip_editor_bounds(request, job, item)
    previous_end: int | None = None
    selected_duration_ms = 0
    for clip_range in ranges:
        if (
            clip_range.start_ms < lower_bound
            or clip_range.end_ms > upper_bound
            or clip_range.end_ms <= clip_range.start_ms
        ):
            raise AppError(
                "invalid_clip_range",
                "剪辑范围超出当前时间线条目的可编辑窗口",
                False,
            )
        if (
            previous_end is not None
            and clip_range.start_ms < previous_end
        ):
            raise AppError(
                "invalid_clip_range",
                "保留片段必须按原始时间排序且不能重叠",
                False,
            )
        previous_end = clip_range.end_ms
        selected_duration_ms += clip_range.end_ms - clip_range.start_ms
    max_duration_ms = round(
        request.app.state.settings.max_clip_minutes * 60 * 1000
    )
    if selected_duration_ms > max_duration_ms:
        raise AppError(
            "clip_too_long",
            (
                "单个视频片段最长 "
                f"{request.app.state.settings.max_clip_minutes:g} 分钟"
            ),
            False,
        )


def validate_clip_subtitles(
    request: Request,
    job_id: str,
    *,
    ranges: list[ClipRange],
    subtitle_mode: str,
) -> None:
    if subtitle_mode == "off":
        return
    repository = request.app.state.services.repository
    selected = [
        segment
        for segment in repository.get_all_transcript(job_id)
        if any(
            segment.end_ms > clip_range.start_ms
            and segment.start_ms < clip_range.end_ms
            for clip_range in ranges
        )
    ]
    if not selected:
        raise AppError(
            "clip_subtitles_empty",
            "所选范围没有可渲染的字幕",
            False,
        )
    if subtitle_mode not in {"en", "bilingual"}:
        return
    translation = repository.get_subtitle_translation_request(job_id, "en")
    translations = repository.get_transcript_translations(job_id, "en")
    if (
        translation is None
        or translation.status != "completed"
        or any(
            not translations.get(segment.sequence, "").strip()
            for segment in selected
        )
    ):
        raise AppError(
            "clip_english_subtitles_not_ready",
            "所选范围的英文字幕尚未完整生成",
            True,
        )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> Response:
    if not request.app.state.settings.auth_required:
        return RedirectResponse("/", status_code=303)
    try:
        require_auth(request)
    except AppError:
        pass
    else:
        return RedirectResponse("/", status_code=303)
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"login_error": None},
    )


@router.post("/login")
async def login(request: Request) -> Response:
    if not request.app.state.settings.auth_required:
        return RedirectResponse("/", status_code=303)
    form = await parse_form(request)
    try:
        _, session_token, csrf_token = request.app.state.auth.login(
            form.get("username", ""), form.get("password", "")
        )
    except AppError as exc:
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"login_error": exc.message},
            status_code=401,
        )
    settings = request.app.state.settings
    max_age = settings.session_ttl_days * 24 * 60 * 60
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=max_age,
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        csrf_token,
        max_age=max_age,
        secure=settings.session_cookie_secure,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout")
async def logout(request: Request) -> Response:
    context = require_auth(request)
    form = await parse_form(request)
    request.app.state.auth.logout(
        request, context, form.get("_csrf")
    )
    settings = request.app.state.settings
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return response


def clip_payload(
    job_id: str, timeline_index: int, state: ClipState
) -> dict:
    payload = {
        "status": state.status,
        "error": state.error,
        "filename": state.output_path.name,
    }
    if state.status == "completed":
        payload["download_url"] = (
            f"/jobs/{job_id}/clips/{timeline_index}/download"
        )
    return payload


def clip_export_payload(
    job_id: str, record: VideoClipExportRecord
) -> dict:
    payload = {
        "id": record.id,
        "timeline_index": record.timeline_index,
        "timeline_title": record.timeline_title,
        "start_ms": record.start_ms,
        "end_ms": record.end_ms,
        "kept_ranges": [
            item.model_dump() for item in record.kept_ranges
        ],
        "duration_ms": (
            sum(
                item.end_ms - item.start_ms
                for item in record.kept_ranges
            )
            + (COVER_DURATION_MS if record.cover_enabled else 0)
        ),
        "subtitle_mode": record.subtitle_mode,
        "include_danmaku": record.include_danmaku,
        "subtitle_font_scale": record.subtitle_font_scale,
        "output_layout": record.output_layout,
        "subtitle_font_family": record.subtitle_font_family,
        "cover_enabled": record.cover_enabled,
        "cover_timestamp_ms": record.cover_timestamp_ms,
        "cover_title": record.cover_title,
        "cover_style": record.cover_style,
        "ai_cover_generation_id": record.ai_cover_generation_id,
        "ai_cover_text_revision": record.ai_cover_text_revision,
        "cover_duration_ms": (
            COVER_DURATION_MS if record.cover_enabled else 0
        ),
        "filename": record.filename,
        "status": record.status,
        "error": record.error_message,
        "warning": record.warning_message,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "completed_at": record.completed_at,
    }
    if record.status == "completed":
        payload["download_url"] = (
            f"/jobs/{job_id}/clip-exports/{record.id}/download"
        )
    return payload


def ai_cover_asset_payload(
    job_id: str,
    generation_id: str,
    asset: AICoverAssetRecord,
) -> dict:
    payload = {
        "id": asset.id,
        "orientation": asset.orientation,
        "width": asset.width,
        "height": asset.height,
        "status": asset.status,
        "text_revision": asset.text_revision,
        "error": (
            {
                "code": asset.error_code,
                "message": asset.error_message,
            }
            if asset.error_code or asset.error_message
            else None
        ),
        "updated_at": asset.updated_at,
        "completed_at": asset.completed_at,
    }
    if asset.status == "completed" and asset.final_oss_object_key:
        payload["image_url"] = (
            f"/jobs/{job_id}/ai-covers/{generation_id}/"
            f"{asset.orientation}/download"
        )
        payload["download_url"] = payload["image_url"]
    return payload


def ai_cover_payload(
    repository,
    generation: AICoverGenerationRecord,
) -> dict:
    return {
        "id": generation.id,
        "timeline_index": generation.timeline_index,
        "source_timestamp_ms": generation.source_timestamp_ms,
        "provider": generation.provider,
        "model": generation.model,
        "prompt_version": generation.prompt_version,
        "prompt_template": (
            generation.prompt_template or DEFAULT_AI_COVER_PROMPT
        ),
        "layout_style": generation.layout_style,
        "title_text": generation.title_text,
        "highlight_text": generation.highlight_text,
        "extra_text": generation.extra_text,
        "status": generation.status,
        "error": (
            {
                "code": generation.error_code,
                "message": generation.error_message,
            }
            if generation.error_code or generation.error_message
            else None
        ),
        "assets": [
            ai_cover_asset_payload(
                generation.job_id,
                generation.id,
                asset,
            )
            for asset in repository.list_ai_cover_assets(generation.id)
        ],
        "created_at": generation.created_at,
        "updated_at": generation.updated_at,
        "completed_at": generation.completed_at,
    }


def translation_payload(
    translation: SubtitleTranslationRequestRecord | None,
) -> dict:
    if translation is None:
        return {
            "language": "en",
            "status": "not_requested",
            "error": None,
        }
    return {
        "language": translation.language,
        "status": translation.status,
        "error": (
            "英文字幕生成失败，请登录后重试"
            if translation.status == "failed"
            else None
        ),
        "retry_count": translation.retry_count,
        "completed_at": translation.completed_at,
    }


@router.get("/admin/glossary", response_class=HTMLResponse)
async def glossary_admin_page(request: Request) -> Response:
    context = require_admin(request)
    repository = request.app.state.services.repository
    terms = repository.list_glossary_terms()
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="glossary_admin.html",
        context={
            "current_user": context.user,
            "csrf_token": context.csrf_token,
            "sync_state": repository.get_glossary_sync_state(),
            "members": repository.list_member_catalog(limit=2000),
            "active_members": repository.list_member_catalog(
                active_only=True, limit=2000
            ),
            "terms": terms,
            "aliases": repository.list_glossary_aliases(),
            "term_types": [
                GlossaryTermType.CP_NAME,
                GlossaryTermType.TEAM_ABBREVIATION,
                GlossaryTermType.STAGE,
                GlossaryTermType.SONG,
                GlossaryTermType.UNIT,
                GlossaryTermType.EVENT,
                GlossaryTermType.FANDOM,
                GlossaryTermType.OTHER,
            ],
            "saved": request.query_params.get("saved"),
        },
    )


@router.post("/admin/glossary/sync")
async def sync_member_catalog(request: Request) -> Response:
    context = require_admin(request)
    form = await parse_form(request)
    request.app.state.auth.require_csrf(
        request, context, form.get("_csrf")
    )
    service = request.app.state.services.member_catalog
    if service is None:
        raise AppError(
            "member_catalog_unavailable",
            "官方成员目录同步服务不可用",
            True,
        )
    await service.sync_if_due(force=True)
    return RedirectResponse(
        "/admin/glossary?saved=sync", status_code=303
    )


@router.post("/admin/glossary/terms")
async def create_glossary_term(request: Request) -> Response:
    context = require_admin(request)
    form = await parse_form(request)
    request.app.state.auth.require_csrf(
        request, context, form.get("_csrf")
    )
    request.app.state.services.repository.create_glossary_term(
        canonical_text=form.get("canonical_text", ""),
        term_type=form.get("term_type", ""),
        description_zh=form.get("description_zh", ""),
        description_en=form.get("description_en", ""),
        user_id=context.user.id,
    )
    return RedirectResponse(
        "/admin/glossary?saved=term", status_code=303
    )


@router.post("/admin/glossary/aliases")
async def create_glossary_alias(request: Request) -> Response:
    context = require_admin(request)
    form = await parse_form(request)
    request.app.state.auth.require_csrf(
        request, context, form.get("_csrf")
    )
    target_kind = form.get("target_kind")
    target_id = form.get("target_id", "").strip()
    if target_kind not in {"member", "term"} or not target_id:
        raise AppError(
            "glossary_alias_target_invalid",
            "请选择别名关联的成员或术语",
            False,
        )
    request.app.state.services.repository.create_glossary_alias(
        alias=form.get("alias", ""),
        user_id=context.user.id,
        member_id=target_id if target_kind == "member" else None,
        term_id=target_id if target_kind == "term" else None,
    )
    return RedirectResponse(
        "/admin/glossary?saved=alias", status_code=303
    )


@router.post("/admin/glossary/terms/{term_id}/active")
async def set_glossary_term_active(
    request: Request, term_id: str
) -> Response:
    context = require_admin(request)
    form = await parse_form(request)
    request.app.state.auth.require_csrf(
        request, context, form.get("_csrf")
    )
    active = form.get("active")
    if active not in {"0", "1"}:
        raise AppError(
            "glossary_active_state_invalid",
            "词库启用状态无效",
            False,
        )
    request.app.state.services.repository.set_glossary_term_active(
        term_id, active=active == "1"
    )
    return RedirectResponse(
        "/admin/glossary?saved=term-state", status_code=303
    )


@router.post("/admin/glossary/aliases/{alias_id}/active")
async def set_glossary_alias_active(
    request: Request, alias_id: str
) -> Response:
    context = require_admin(request)
    form = await parse_form(request)
    request.app.state.auth.require_csrf(
        request, context, form.get("_csrf")
    )
    active = form.get("active")
    if active not in {"0", "1"}:
        raise AppError(
            "glossary_active_state_invalid",
            "词库启用状态无效",
            False,
        )
    request.app.state.services.repository.set_glossary_alias_active(
        alias_id, active=active == "1"
    )
    return RedirectResponse(
        "/admin/glossary?saved=alias-state", status_code=303
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, member: str | None = None) -> Response:
    context = optional_auth(request)
    services = request.app.state.services
    settings = request.app.state.settings
    missing_configuration = settings.missing_processing_configuration()
    user_id = context.user.id if context else None
    member_filters = services.repository.list_visible_member_filters(user_id)
    requested_member_id = (member or "").strip()
    visible_member_ids = {
        member_filter.member_id for member_filter in member_filters
    }
    selected_member_id = (
        requested_member_id
        if requested_member_id in visible_member_ids
        else None
    )
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "jobs": services.repository.list_visible_jobs(
                user_id,
                member_id=selected_member_id,
            ),
            "member_filters": member_filters,
            "selected_member_id": selected_member_id,
            "missing_configuration": missing_configuration,
            "processing_ready": not missing_configuration,
            "current_user": context.user if context else None,
            "csrf_token": context.csrf_token if context else "",
            "daily_job_limit": settings.daily_job_limit,
            "has_unlimited_jobs": (
                context is not None
                and context.user.username_normalized
                in settings.unlimited_job_username_set
            ),
            "format_china_datetime": format_china_datetime,
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str) -> Response:
    context, job = require_readable_job(request, job_id)
    repository = request.app.state.services.repository
    can_manage_job = bool(
        context
        and (
            context.user.is_admin
            or repository.get_job_for_user(job_id, context.user.id)
            is not None
        )
    )
    summary = (
        FinalSummary.model_validate_json(job.summary_json)
        if job.summary_json
        else None
    )
    peaks = repository.get_danmaku_peaks(job_id)
    if summary:
        summaries_by_window = {
            (item.start_ms, item.end_ms): item.summary
            for item in summary.danmaku_peak_summaries
        }
        peaks = [
            peak.model_copy(
                update={
                    "summary": summaries_by_window.get(
                        (peak.start_ms, peak.end_ms), ""
                    )
                }
            )
            for peak in peaks
        ]
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="job.html",
        context={
            "job": job,
            "summary": summary,
            "transcript": repository.get_transcript(job_id, limit=200),
            "danmaku": repository.get_danmaku(job_id, limit=200),
            "peaks": peaks,
            "events": (
                repository.list_events(job_id) if can_manage_job else []
            ),
            "format_clock": format_clock,
            "format_china_datetime": format_china_datetime,
            "current_user": context.user if context else None,
            "csrf_token": context.csrf_token if context else "",
            "can_manage_job": can_manage_job,
            "can_create_clips": (
                context is not None
                and request.app.state.services.clipper is not None
                and not request.app.state.settings.clip_maintenance_path.exists()
            ),
            "can_preview_clips": (
                request.app.state.services.clipper is not None
                and job.media_url is not None
            ),
            "can_request_translation": context is not None,
            "can_generate_ai_covers": bool(
                context and context.user.is_admin
            ),
            "ai_cover_default_prompt": DEFAULT_AI_COVER_PROMPT,
            "ai_cover_prompt_max_length": AI_COVER_PROMPT_MAX_LENGTH,
            "ai_cover_configured": not (
                request.app.state.settings
                .missing_ai_cover_configuration()
            ),
            "clip_context_ms": round(
                request.app.state.settings.clip_editor_context_minutes
                * 60
                * 1000
            ),
            "max_clip_ms": round(
                request.app.state.settings.max_clip_minutes * 60 * 1000
            ),
            "clip_snap_threshold_ms": (
                request.app.state.settings
                .clip_sentence_snap_threshold_ms
            ),
        },
    )


@router.post("/api/jobs")
async def create_job(request: Request, payload: CreateJobRequest) -> Response:
    context = require_auth(request)
    request.app.state.auth.require_csrf(request, context)
    settings = request.app.state.settings
    normalized_url, live_id = parse_share_url(payload.url)
    services = request.app.state.services
    existing = services.repository.get_job_by_live_id(live_id)
    if existing is not None and existing.status == JobStatus.COMPLETED:
        return JSONResponse(public_job_payload(existing), status_code=200)
    settings.require_processing_configuration()
    job, created = services.repository.create_or_get_job(
        normalized_url,
        live_id,
        context.user.id,
        daily_limit=(
            None
            if context.user.username_normalized
            in settings.unlimited_job_username_set
            else settings.daily_job_limit
        ),
        quota_start=request.app.state.auth.quota_day_start_utc(),
    )
    if services.worker:
        services.worker.notify()
    return JSONResponse(
        public_job_payload(job), status_code=201 if created else 200
    )


@router.get("/api/jobs/{job_id}/status")
async def job_status(request: Request, job_id: str) -> dict:
    _, job = require_readable_job(request, job_id)
    return public_job_payload(job)


@router.get("/api/jobs/{job_id}/transcript")
async def transcript_page(
    request: Request, job_id: str, offset: int = 0, limit: int = 200
) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    repository = request.app.state.services.repository
    require_readable_job(request, job_id)
    segments = repository.get_transcript(job_id, limit=limit, offset=offset)
    return {
        "segments": [segment.model_dump() for segment in segments],
        "next_offset": offset + len(segments),
        "has_more": len(segments) == limit,
    }


@router.get("/api/jobs/{job_id}/danmaku")
async def danmaku_page(
    request: Request,
    job_id: str,
    after_ms: int = -1,
    limit: int = 200,
) -> dict:
    limit = max(1, min(limit, 500))
    repository = request.app.state.services.repository
    require_readable_job(request, job_id)
    entries = repository.get_danmaku(
        job_id, limit=limit, after_ms=max(-1, after_ms)
    )
    return {
        "entries": [entry.model_dump() for entry in entries],
        "next_after_ms": entries[-1].timestamp_ms if entries else after_ms,
        "has_more": len(entries) == limit,
    }


@router.get("/api/jobs/{job_id}/playback-track")
async def playback_track(request: Request, job_id: str) -> dict:
    require_readable_job(request, job_id)
    repository = request.app.state.services.repository
    translation = repository.get_subtitle_translation_request(job_id, "en")
    translations = repository.get_transcript_translations(job_id, "en")
    return {
        "translation": translation_payload(translation),
        "subtitles": [
            {
                "sequence": segment.sequence,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "zh": segment.text,
                "en": translations.get(segment.sequence),
            }
            for segment in repository.get_all_transcript(job_id)
        ],
        "danmaku": [
            {
                "sequence": entry.sequence,
                "timestamp_ms": entry.timestamp_ms,
                "author": entry.author,
                "text": entry.text,
            }
            for entry in repository.get_all_danmaku(job_id)
        ],
    }


@router.get("/api/jobs/{job_id}/translations/en")
async def subtitle_translation_status(
    request: Request, job_id: str
) -> dict:
    require_readable_job(request, job_id)
    translation = (
        request.app.state.services.repository
        .get_subtitle_translation_request(job_id, "en")
    )
    return translation_payload(translation)


@router.post("/api/jobs/{job_id}/translations/en")
async def request_subtitle_translation(
    request: Request, job_id: str
) -> Response:
    context = require_auth(request)
    request.app.state.auth.require_csrf(request, context)
    _, job = require_readable_job(request, job_id)
    if job.status != JobStatus.COMPLETED:
        raise AppError(
            "translation_not_ready",
            "直播处理完成后才能生成英文字幕",
            True,
        )
    services = request.app.state.services
    translation = services.repository.request_subtitle_translation(
        job_id, "en"
    )
    if services.worker is not None:
        services.worker.notify()
    return JSONResponse(
        translation_payload(translation),
        status_code=200 if translation.status == "completed" else 202,
    )


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: str) -> dict:
    context, _ = require_owned_job(request, job_id)
    request.app.state.auth.require_csrf(request, context)
    services = request.app.state.services
    job = (
        services.repository.retry_job(job_id)
        if context.user.is_admin
        else services.repository.retry_job_for_user(
            job_id, context.user.id
        )
    )
    if services.worker:
        services.worker.notify()
    return public_job_payload(job)


@router.post("/api/jobs/{job_id}/ai-covers")
async def create_ai_cover(
    request: Request,
    job_id: str,
    payload: CreateAICoverRequest,
) -> Response:
    context = require_admin(request)
    request.app.state.auth.require_csrf(request, context)
    job, item = timeline_clip_context(
        request, job_id, payload.timeline_index
    )
    lower_bound, upper_bound = clip_editor_bounds(request, job, item)
    if not lower_bound <= payload.source_timestamp_ms <= upper_bound:
        raise AppError(
            "ai_cover_timestamp_out_of_window",
            "AI 封面标记时间超出当前时间线条目的可编辑窗口",
            False,
        )
    service = require_ai_cover_service(request)
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        reject_ai_cover_maintenance(request)
        generation = service.start_generation(
            job_id=job.id,
            timeline_index=payload.timeline_index,
            requested_by_user_id=context.user.id,
            request_id=payload.request_id,
            source_timestamp_ms=payload.source_timestamp_ms,
            layout_style=payload.layout_style,
            title_text=payload.title_text,
            highlight_text=payload.highlight_text,
            extra_text=payload.extra_text,
            prompt_template=payload.prompt_template,
            manifest_url=job.media_url,
        )
    return JSONResponse(
        ai_cover_payload(
            request.app.state.services.repository, generation
        ),
        status_code=(
            200 if generation.status == "completed" else 202
        ),
    )


@router.get("/api/jobs/{job_id}/ai-covers")
async def list_ai_covers(
    request: Request,
    job_id: str,
    timeline_index: int | None = None,
) -> dict:
    require_readable_job(request, job_id)
    if timeline_index is not None and timeline_index < 0:
        raise AppError(
            "timeline_item_not_found",
            "时间线话题不存在",
            False,
        )
    repository = request.app.state.services.repository
    return {
        "covers": [
            ai_cover_payload(repository, generation)
            for generation in repository.list_ai_cover_generations(
                job_id,
                timeline_index=timeline_index,
                limit=30,
            )
        ],
        "configured": not (
            request.app.state.settings.missing_ai_cover_configuration()
        ),
    }


@router.get("/api/jobs/{job_id}/ai-covers/{generation_id}")
async def ai_cover_status(
    request: Request,
    job_id: str,
    generation_id: str,
) -> dict:
    require_readable_job(request, job_id)
    repository = request.app.state.services.repository
    generation = repository.get_ai_cover_generation(
        job_id, generation_id
    )
    if generation is None:
        raise AppError(
            "ai_cover_not_found",
            "AI 封面不存在",
            False,
        )
    return ai_cover_payload(repository, generation)


@router.post(
    "/api/jobs/{job_id}/ai-covers/{generation_id}/retry"
)
async def retry_ai_cover(
    request: Request,
    job_id: str,
    generation_id: str,
) -> Response:
    context = require_admin(request)
    request.app.state.auth.require_csrf(request, context)
    _, job = require_readable_job(request, job_id)
    if not job.media_url:
        raise AppError("media_not_ready", "回放媒体地址尚未生成", True)
    service = require_ai_cover_service(request)
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        reject_ai_cover_maintenance(request)
        generation = service.retry_generation(
            job_id=job_id,
            generation_id=generation_id,
            manifest_url=job.media_url,
        )
    return JSONResponse(
        ai_cover_payload(
            request.app.state.services.repository, generation
        ),
        status_code=(
            200 if generation.status == "completed" else 202
        ),
    )


@router.post(
    "/api/jobs/{job_id}/ai-covers/{generation_id}/regenerate"
)
async def regenerate_ai_cover(
    request: Request,
    job_id: str,
    generation_id: str,
    payload: RegenerateAICoverRequest,
) -> Response:
    context = require_admin(request)
    request.app.state.auth.require_csrf(request, context)
    _, job = require_readable_job(request, job_id)
    repository = request.app.state.services.repository
    source = repository.get_ai_cover_generation(job_id, generation_id)
    if source is None:
        raise AppError(
            "ai_cover_not_found",
            "AI 封面不存在",
            False,
        )
    if not job.media_url:
        raise AppError("media_not_ready", "回放媒体地址尚未生成", True)
    service = require_ai_cover_service(request)
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        reject_ai_cover_maintenance(request)
        generation = service.start_generation(
            job_id=job_id,
            timeline_index=source.timeline_index,
            requested_by_user_id=context.user.id,
            request_id=payload.request_id,
            source_timestamp_ms=source.source_timestamp_ms,
            layout_style=source.layout_style,
            title_text=payload.title_text or source.title_text,
            highlight_text=source.highlight_text,
            extra_text=source.extra_text,
            prompt_template=(
                payload.prompt_template
                if payload.prompt_template is not None
                else source.prompt_template
            ),
            manifest_url=job.media_url,
        )
    return JSONResponse(
        ai_cover_payload(repository, generation),
        status_code=202,
    )


@router.get(
    "/jobs/{job_id}/ai-covers/{generation_id}/{orientation}/download"
)
async def download_ai_cover(
    request: Request,
    job_id: str,
    generation_id: str,
    orientation: Literal["landscape", "four_three"],
) -> Response:
    require_readable_job(request, job_id)
    repository = request.app.state.services.repository
    generation = repository.get_ai_cover_generation(
        job_id, generation_id
    )
    if generation is None:
        raise AppError(
            "ai_cover_not_found",
            "AI 封面不存在",
            False,
        )
    asset = repository.get_ai_cover_asset(
        generation.id, orientation
    )
    if asset is None:
        raise AppError(
            "ai_cover_asset_not_found",
            "AI 封面图片不存在",
            False,
        )
    return RedirectResponse(
        await signed_ai_cover_download_url(request, asset),
        status_code=303,
    )


@router.post("/api/jobs/{job_id}/clip-boundaries/suggest")
async def suggest_clip_boundary(
    request: Request,
    job_id: str,
    payload: ClipBoundarySuggestionRequest,
) -> dict:
    context = require_auth(request)
    request.app.state.auth.require_csrf(request, context)
    job, item = timeline_clip_context(
        request, job_id, payload.timeline_index
    )
    lower_bound, upper_bound = clip_editor_bounds(request, job, item)
    if not lower_bound <= payload.target_ms <= upper_bound:
        raise AppError(
            "clip_boundary_out_of_window",
            "剪辑边界超出当前时间线条目的可编辑窗口",
            False,
        )
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        if settings.clip_maintenance_path.exists():
            raise AppError(
                "clipper_maintenance",
                "服务正在发布新版本，请稍后再剪辑",
                True,
            )
        suggestion = await clipper.suggest_boundary(
            job_id=job.id,
            manifest_url=job.media_url,
            duration_ms=job.duration_ms,
            boundary=payload.boundary,
            target_ms=payload.target_ms,
            minimum_ms=lower_bound,
            maximum_ms=upper_bound,
        )
    return {
        "boundary": suggestion.boundary,
        "requested_ms": suggestion.requested_ms,
        "sentence_sequence": suggestion.sentence_sequence,
        "sentence_ms": suggestion.sentence_ms,
        "suggested_ms": suggestion.suggested_ms,
        "source": suggestion.source,
        "silence_start_ms": suggestion.silence_start_ms,
        "silence_end_ms": suggestion.silence_end_ms,
    }


@router.post("/api/jobs/{job_id}/clip-exports")
async def create_clip_export(
    request: Request,
    job_id: str,
    payload: CreateClipExportRequest,
) -> Response:
    context = require_auth(request)
    request.app.state.auth.require_csrf(request, context)
    job, item = timeline_clip_context(
        request, job_id, payload.timeline_index
    )
    validate_clip_ranges(
        request,
        job,
        item,
        payload.kept_ranges,
    )
    validate_clip_subtitles(
        request,
        job.id,
        ranges=payload.kept_ranges,
        subtitle_mode=payload.subtitle_mode,
    )
    existing = (
        request.app.state.services.repository
        .get_video_clip_export_by_request_id(job.id, payload.request_id)
    )
    if existing is not None:
        return JSONResponse(
            clip_export_payload(job.id, existing),
            status_code=(
                200 if existing.status == "completed" else 202
            ),
        )
    if payload.ai_cover_generation_id:
        if not context.user.is_admin:
            raise AppError(
                "admin_required",
                "仅管理员可以把 AI 封面用于视频剪辑",
                False,
            )
        if payload.output_layout != "landscape":
            raise AppError(
                "ai_cover_landscape_only",
                "AI 封面只能用于横屏成片；4:3 版本仅供下载",
                False,
            )
        generation = (
            request.app.state.services.repository.get_ai_cover_generation(
                job.id, payload.ai_cover_generation_id
            )
        )
        if (
            generation is None
            or generation.timeline_index != payload.timeline_index
        ):
            raise AppError(
                "ai_cover_not_found",
                "AI 封面不存在或不属于当前时间线条目",
                False,
            )
        if generation.status != "completed":
            raise AppError(
                "ai_cover_not_ready",
                "AI 封面尚未生成完成",
                True,
            )
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        if settings.clip_maintenance_path.exists():
            raise AppError(
                "clipper_maintenance",
                "服务正在发布新版本，请稍后再剪辑",
                True,
            )
        record = clipper.start_export(
            job_id=job.id,
            timeline_index=payload.timeline_index,
            timeline_title=item.title,
            requested_by_user_id=context.user.id,
            request_id=payload.request_id,
            manifest_url=job.media_url,
            start_ms=payload.start_ms,
            end_ms=payload.end_ms,
            kept_ranges=payload.kept_ranges,
            subtitle_mode=payload.subtitle_mode,
            include_danmaku=payload.include_danmaku,
            subtitle_font_scale=payload.subtitle_font_scale,
            output_layout=payload.output_layout,
            subtitle_font_family=payload.subtitle_font_family,
            ai_cover_generation_id=payload.ai_cover_generation_id,
        )
    return JSONResponse(
        clip_export_payload(job.id, record),
        status_code=200 if record.status == "completed" else 202,
    )


@router.get("/api/jobs/{job_id}/clip-exports")
async def list_clip_exports(
    request: Request,
    job_id: str,
    timeline_index: int | None = None,
) -> dict:
    require_readable_job(request, job_id)
    if timeline_index is not None and timeline_index < 0:
        raise AppError(
            "timeline_item_not_found",
            "时间线话题不存在",
            False,
        )
    records = request.app.state.services.repository.list_video_clip_exports(
        job_id,
        timeline_index=timeline_index,
        limit=500,
    )
    return {
        "clips": [clip_export_payload(job_id, record) for record in records]
    }


@router.get("/api/jobs/{job_id}/clip-exports/{clip_id}")
async def clip_export_status(
    request: Request, job_id: str, clip_id: str
) -> dict:
    require_readable_job(request, job_id)
    record = (
        request.app.state.services.repository.get_video_clip_export(
            job_id, clip_id
        )
    )
    if record is None:
        raise AppError(
            "video_clip_not_found",
            "视频片段不存在",
            False,
        )
    return clip_export_payload(job_id, record)


@router.post("/api/jobs/{job_id}/clip-exports/{clip_id}/retry")
async def retry_clip_export(
    request: Request, job_id: str, clip_id: str
) -> Response:
    context = require_auth(request)
    request.app.state.auth.require_csrf(request, context)
    _, job = require_readable_job(request, job_id)
    if not job.media_url:
        raise AppError("media_not_ready", "回放媒体地址尚未生成", True)
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        if settings.clip_maintenance_path.exists():
            raise AppError(
                "clipper_maintenance",
                "服务正在发布新版本，请稍后再剪辑",
                True,
            )
        record = clipper.retry_export(
            job_id=job.id,
            clip_id=clip_id,
            manifest_url=job.media_url,
        )
    return JSONResponse(
        clip_export_payload(job.id, record),
        status_code=200 if record.status == "completed" else 202,
    )


@router.get("/jobs/{job_id}/clip-exports/{clip_id}/download")
async def download_clip_export(
    request: Request, job_id: str, clip_id: str
) -> Response:
    _, job = require_readable_job(request, job_id)
    record = (
        request.app.state.services.repository.get_video_clip_export(
            job.id, clip_id
        )
    )
    if record is None or record.status != "completed":
        raise AppError(
            "video_clip_not_ready",
            "视频片段尚未生成完成",
            True,
        )
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    if record.oss_object_key:
        return RedirectResponse(
            await clipper.signed_download_url(record),
            status_code=303,
        )
    output_path = clipper.output_path_for(record)
    if not output_path.is_file():
        raise AppError(
            "video_clip_not_ready",
            "视频片段文件不存在，请重新剪辑",
            True,
        )
    return FileResponse(
        output_path,
        media_type="video/mp4",
        filename=record.filename,
    )


@router.post("/api/jobs/{job_id}/clips/{timeline_index}")
async def create_timeline_clip(
    request: Request, job_id: str, timeline_index: int
) -> Response:
    context = require_auth(request)
    request.app.state.auth.require_csrf(request, context)
    job, item = timeline_clip_context(request, job_id, timeline_index)
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    settings = request.app.state.settings
    with shared_runtime_lock(settings.clip_operation_lock_path):
        if settings.clip_maintenance_path.exists():
            raise AppError(
                "clipper_maintenance",
                "服务正在发布新版本，请稍后再剪辑",
                True,
            )
        state = clipper.start(
            job_id=job.id,
            timeline_index=timeline_index,
            manifest_url=job.media_url,
            start_ms=item.start_ms,
            end_ms=item.end_ms,
        )
    return JSONResponse(
        clip_payload(job.id, timeline_index, state),
        status_code=200 if state.status == "completed" else 202,
    )


@router.get("/api/jobs/{job_id}/clips/{timeline_index}")
async def timeline_clip_status(
    request: Request, job_id: str, timeline_index: int
) -> dict:
    job, item = timeline_clip_context(request, job_id, timeline_index)
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    state = clipper.get(
        job_id=job.id,
        timeline_index=timeline_index,
        start_ms=item.start_ms,
        end_ms=item.end_ms,
    )
    if state is None:
        return {"status": "not_started", "error": None}
    return clip_payload(job.id, timeline_index, state)


@router.get("/jobs/{job_id}/clips/{timeline_index}/download")
async def download_timeline_clip(
    request: Request, job_id: str, timeline_index: int
) -> Response:
    job, item = timeline_clip_context(request, job_id, timeline_index)
    record = (
        request.app.state.services.repository.get_latest_video_clip_export(
            job.id, timeline_index, completed_only=True
        )
    )
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
            True,
        )
    if record is not None:
        if record.oss_object_key:
            return RedirectResponse(
                await clipper.signed_download_url(record),
                status_code=303,
            )
        output_path = clipper.output_path_for(record)
        if output_path.is_file():
            return FileResponse(
                output_path,
                media_type="video/mp4",
                filename=record.filename,
            )
    state = clipper.get(
        job_id=job.id,
        timeline_index=timeline_index,
        start_ms=item.start_ms,
        end_ms=item.end_ms,
    )
    if state is None or state.status != "completed":
        raise AppError(
            "video_clip_not_ready",
            "视频片段尚未生成完成",
            True,
        )
    if state.oss_object_key:
        return RedirectResponse(
            await clipper.signed_download_url(state),
            status_code=303,
        )
    if not state.output_path.is_file():
        raise AppError(
            "video_clip_not_ready",
            "视频片段文件不存在，请重新剪辑",
            True,
        )
    return FileResponse(
        state.output_path,
        media_type="video/mp4",
        filename=state.output_path.name,
    )


@router.get("/jobs/{job_id}/transcript.srt")
async def download_srt(request: Request, job_id: str) -> Response:
    repository = request.app.state.services.repository
    _, job = require_readable_job(request, job_id)
    segments = repository.get_all_transcript(job_id)
    if not segments:
        raise AppError("transcript_not_ready", "字幕尚未生成", True)
    return Response(
        content=transcript_to_srt(segments),
        media_type="application/x-subrip; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pocket48-{job.live_id}.srt"'
            )
        },
    )


@router.get("/jobs/{job_id}/asr.json")
async def download_asr_json(request: Request, job_id: str) -> Response:
    require_auth(request)
    _, job = require_readable_job(request, job_id)
    if not job.asr_raw_json:
        raise AppError("transcript_not_ready", "语音识别结果尚未生成", True)
    return Response(
        content=job.asr_raw_json,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pocket48-{job.live_id}-asr.json"'
            )
        },
    )


@router.get("/jobs/{job_id}/summary.md")
async def download_summary(request: Request, job_id: str) -> Response:
    _, job = require_readable_job(request, job_id)
    if not job.summary_markdown:
        raise AppError("summary_not_ready", "总结尚未生成", True)
    return Response(
        content=job.summary_markdown,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="pocket48-{job.live_id}-summary.md"'
            )
        },
    )


@router.get("/healthz")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "release": settings.app_release,
        "worker_enabled": request.app.state.services.worker is not None,
        "missing_configuration": settings.missing_processing_configuration(),
        # Clip exports drop uncovered glyphs silently, so the renderer's font
        # coverage has to be observable from outside the box.
        "emoji_font": emoji_font_family(),
    }
