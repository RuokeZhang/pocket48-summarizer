from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..clients.oss_store import OSSStore
from ..config import Settings
from ..errors import AppError
from ..models import ClipRange, VideoClipExportRecord
from ..repository import JobRepository
from .boundaries import (
    BoundaryKind,
    BoundarySuggestion,
    ClipBoundaryService,
)
from .ffmpeg import FFmpegRunner, VideoDimensions
from .fonts import contains_emoji, emoji_font_status
from .layouts import (
    DEFAULT_LANDSCAPE_SUBTITLE_FONT,
    LANDSCAPE_CANVAS_HEIGHT,
    LANDSCAPE_CANVAS_WIDTH,
    ClipOutputLayout,
    LandscapeSubtitleFont,
)
from .overlays import (
    COVER_DURATION_MS,
    DEFAULT_COVER_STYLE,
    DEFAULT_SUBTITLE_FONT_SCALE,
    ClipOverlayDocument,
    CoverStyle,
    build_cover_overlay,
    build_clip_overlay,
)

def _overlays_need_missing_emoji_font(
    documents: list[ClipOverlayDocument],
) -> bool:
    """Report whether a clip asks for emoji the renderer cannot draw.

    libass drops uncovered codepoints without failing, so without this check
    the export succeeds and the emoji are simply absent from the video.
    """

    if not any(contains_emoji(document.content) for document in documents):
        return False
    return emoji_font_status() == "missing"


ClipStatus = Literal["running", "completed", "failed"]
LEGACY_CLIP_RE = re.compile(
    r"^timeline-(?P<index>\d+)-(?P<start>\d+)-(?P<end>\d+)\.mp4$"
)
SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RENDER_VERSION = "ass-v14"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(slots=True)
class ClipState:
    status: ClipStatus
    output_path: Path
    clip_id: str = ""
    error: str | None = None
    warning: str | None = None
    oss_object_key: str | None = None


class VideoClipService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        oss: OSSStore,
        ffmpeg: FFmpegRunner | None = None,
    ) -> None:
        self.settings = settings
        self.output_dir = settings.data_dir / "clips"
        self.repository = repository
        self.oss = oss
        self.ffmpeg = ffmpeg or FFmpegRunner(settings)
        self.boundaries = ClipBoundaryService(
            settings, repository, self.ffmpeg
        )
        self.logger = logging.getLogger(__name__)
        self._states: dict[str, ClipState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._capacity = asyncio.Semaphore(settings.clip_concurrency)
        self._retry_attempts = settings.clip_retry_attempts
        self._retry_delay_seconds = settings.clip_retry_delay_seconds
        self._ass_supported: bool | None = None
        self._ass_probe_lock = asyncio.Lock()

    async def startup(self) -> None:
        self.repository.recover_running_video_clips()
        self.repository.recover_running_video_clip_exports()
        for path in self.output_dir.glob("*/*.mp4"):
            if not path.is_file() or path.stat().st_size == 0:
                continue
            job_id = path.parent.name
            job = self.repository.get_job(job_id)
            if job is None:
                continue
            record = self.repository.find_video_clip_export_by_filename(
                job_id, path.name
            )
            if record is None:
                record = self._register_legacy_file(job_id, path)
            if record is None:
                continue
            if record.status == "completed" and record.oss_object_key:
                path.unlink(missing_ok=True)
                continue
            record = self.repository.retry_video_clip_export(
                job_id, record.id, allow_completed=True
            )
            self._start_task(record, manifest_url=None, output_path=path)

    def output_path_for(self, record: VideoClipExportRecord) -> Path:
        if not SAFE_ID_RE.fullmatch(record.job_id):
            raise AppError("invalid_job_id", "任务 ID 无效", False)
        if not SAFE_ID_RE.fullmatch(record.id):
            raise AppError("invalid_clip_id", "视频片段 ID 无效", False)
        return self.output_dir / record.job_id / record.filename

    def output_filename(
        self,
        *,
        clip_id: str,
        timeline_index: int,
        start_ms: int,
        end_ms: int,
        subtitle_mode: str,
        include_danmaku: bool,
        output_layout: ClipOutputLayout,
        cover_enabled: bool = False,
        kept_range_count: int = 1,
    ) -> str:
        overlay = subtitle_mode
        if include_danmaku:
            overlay += "-danmaku"
        if output_layout == "landscape":
            overlay = f"landscape-{overlay}"
        if cover_enabled:
            overlay = f"cover-{overlay}"
        if kept_range_count > 1:
            overlay = f"cut-{overlay}"
        return (
            f"timeline-{timeline_index + 1:02d}-"
            f"{start_ms}-{end_ms}-{overlay}-{clip_id[:8]}.mp4"
        )

    def start_export(
        self,
        *,
        job_id: str,
        timeline_index: int,
        timeline_title: str,
        requested_by_user_id: str | None,
        request_id: str,
        manifest_url: str,
        start_ms: int,
        end_ms: int,
        kept_ranges: list[ClipRange] | None = None,
        subtitle_mode: str,
        include_danmaku: bool,
        subtitle_font_scale: int = DEFAULT_SUBTITLE_FONT_SCALE,
        output_layout: ClipOutputLayout = "portrait",
        subtitle_font_family: LandscapeSubtitleFont = (
            DEFAULT_LANDSCAPE_SUBTITLE_FONT
        ),
        cover_enabled: bool = False,
        cover_timestamp_ms: int | None = None,
        cover_title: str = "",
        cover_style: CoverStyle = DEFAULT_COVER_STYLE,
        ai_cover_generation_id: str | None = None,
    ) -> VideoClipExportRecord:
        existing = self.repository.get_video_clip_export_by_request_id(
            job_id, request_id
        )
        if existing is not None:
            return existing
        ai_cover_asset = None
        if ai_cover_generation_id:
            if output_layout != "landscape":
                raise AppError(
                    "ai_cover_landscape_only",
                    "AI 封面只能用于横屏成片；4:3 版本仅供下载",
                    False,
                )
            generation = self.repository.get_ai_cover_generation(
                job_id, ai_cover_generation_id
            )
            if (
                generation is None
                or generation.timeline_index != timeline_index
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
            ai_cover_asset = self.repository.get_ai_cover_asset(
                generation.id,
                "landscape",
            )
            if (
                ai_cover_asset is None
                or ai_cover_asset.status != "completed"
                or not ai_cover_asset.final_oss_object_key
            ):
                raise AppError(
                    "ai_cover_not_ready",
                    "AI 封面图片尚未生成完成",
                    True,
                )
        clip_id = str(uuid.uuid4())
        filename = self.output_filename(
            clip_id=clip_id,
            timeline_index=timeline_index,
            start_ms=start_ms,
            end_ms=end_ms,
            subtitle_mode=subtitle_mode,
            include_danmaku=include_danmaku,
            output_layout=output_layout,
            cover_enabled=cover_enabled or ai_cover_generation_id is not None,
            kept_range_count=len(kept_ranges or []),
        )
        record, created = self.repository.begin_video_clip_export(
            clip_id=clip_id,
            job_id=job_id,
            timeline_index=timeline_index,
            timeline_title=timeline_title,
            requested_by_user_id=requested_by_user_id,
            request_id=request_id,
            start_ms=start_ms,
            end_ms=end_ms,
            kept_ranges=kept_ranges,
            subtitle_mode=subtitle_mode,
            include_danmaku=include_danmaku,
            subtitle_font_scale=subtitle_font_scale,
            output_layout=output_layout,
            subtitle_font_family=subtitle_font_family,
            cover_enabled=cover_enabled,
            cover_timestamp_ms=cover_timestamp_ms,
            cover_title=cover_title,
            cover_style=cover_style,
            ai_cover_generation_id=ai_cover_generation_id,
            ai_cover_asset_id=(
                ai_cover_asset.id if ai_cover_asset else None
            ),
            ai_cover_final_oss_object_key=(
                ai_cover_asset.final_oss_object_key
                if ai_cover_asset
                else None
            ),
            ai_cover_final_sha256=(
                ai_cover_asset.final_sha256
                if ai_cover_asset
                else None
            ),
            ai_cover_text_revision=(
                ai_cover_asset.text_revision
                if ai_cover_asset
                else None
            ),
            render_version=RENDER_VERSION,
            filename=filename,
        )
        if created:
            self._start_task(
                record,
                manifest_url=manifest_url,
                output_path=self.output_path_for(record),
            )
        return record

    def retry_export(
        self,
        *,
        job_id: str,
        clip_id: str,
        manifest_url: str,
    ) -> VideoClipExportRecord:
        record = self.repository.retry_video_clip_export(job_id, clip_id)
        current = self._tasks.get(clip_id)
        if current is None or current.done():
            self._start_task(
                record,
                manifest_url=manifest_url,
                output_path=self.output_path_for(record),
            )
        return record

    async def suggest_boundary(
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
        return await self.boundaries.suggest(
            job_id=job_id,
            manifest_url=manifest_url,
            duration_ms=duration_ms,
            boundary=boundary,
            target_ms=target_ms,
            minimum_ms=minimum_ms,
            maximum_ms=maximum_ms,
        )

    def get_export(
        self, job_id: str, clip_id: str
    ) -> VideoClipExportRecord | None:
        return self.repository.get_video_clip_export(job_id, clip_id)

    def list_exports(
        self, job_id: str, timeline_index: int | None = None
    ) -> list[VideoClipExportRecord]:
        return self.repository.list_video_clip_exports(
            job_id, timeline_index=timeline_index
        )

    def latest_export(
        self,
        job_id: str,
        timeline_index: int,
        *,
        completed_only: bool = False,
    ) -> VideoClipExportRecord | None:
        return self.repository.get_latest_video_clip_export(
            job_id,
            timeline_index,
            completed_only=completed_only,
        )

    def start(
        self,
        *,
        job_id: str,
        timeline_index: int,
        manifest_url: str,
        start_ms: int,
        end_ms: int,
    ) -> ClipState:
        record = self.start_export(
            job_id=job_id,
            timeline_index=timeline_index,
            timeline_title="",
            requested_by_user_id=None,
            request_id=(
                f"legacy-api:{timeline_index}:{start_ms}:{end_ms}"
            ),
            manifest_url=manifest_url,
            start_ms=start_ms,
            end_ms=end_ms,
            subtitle_mode="off",
            include_danmaku=False,
        )
        if record.status == "failed":
            record = self.retry_export(
                job_id=job_id,
                clip_id=record.id,
                manifest_url=manifest_url,
            )
        return self._state_from_record(record)

    def get(
        self,
        *,
        job_id: str,
        timeline_index: int,
        start_ms: int,
        end_ms: int,
    ) -> ClipState | None:
        del start_ms, end_ms
        record = self.latest_export(job_id, timeline_index)
        return self._state_from_record(record) if record else None

    def _start_task(
        self,
        record: VideoClipExportRecord,
        *,
        manifest_url: str | None,
        output_path: Path,
    ) -> ClipState:
        current = self._states.get(record.id)
        if current and current.status == "running":
            return current
        state = ClipState(
            status="running",
            output_path=output_path,
            clip_id=record.id,
        )
        self._states[record.id] = state
        self._tasks[record.id] = asyncio.create_task(
            self._run(record, state, manifest_url)
        )
        return state

    async def _run(
        self,
        record: VideoClipExportRecord,
        state: ClipState,
        manifest_url: str | None,
    ) -> None:
        ass_paths = [
            state.output_path.with_suffix(f".range-{index + 1}.ass")
            for index in range(len(record.kept_ranges))
        ]
        part_paths = [
            state.output_path.with_suffix(f".range-{index + 1}.mp4")
            for index in range(len(record.kept_ranges))
        ]
        cover_ass_path = state.output_path.with_suffix(".cover.ass")
        cover_frame_path = state.output_path.with_suffix(".cover.png")
        ai_cover_frame_path = state.output_path.with_suffix(
            ".ai-cover.png"
        )
        main_output_path = state.output_path.with_suffix(".main.mp4")
        try:
            async with self._capacity:
                warning = record.warning_message
                for attempt in range(1, self._retry_attempts + 1):
                    try:
                        if not (
                            state.output_path.is_file()
                            and state.output_path.stat().st_size > 0
                        ):
                            if not manifest_url:
                                raise AppError(
                                    "video_clip_missing",
                                    "本地视频片段不存在，请重新剪辑",
                                    True,
                                )
                            needs_clip_overlay = (
                                record.subtitle_mode != "off"
                                or record.include_danmaku
                            )
                            needs_ai_cover = (
                                record.ai_cover_generation_id is not None
                            )
                            dimensions: VideoDimensions | None = None
                            if needs_clip_overlay or record.cover_enabled:
                                await self._require_ass_support()
                            if (
                                needs_clip_overlay
                                or record.cover_enabled
                                or needs_ai_cover
                            ):
                                dimensions = (
                                    VideoDimensions(
                                        width=LANDSCAPE_CANVAS_WIDTH,
                                        height=LANDSCAPE_CANVAS_HEIGHT,
                                    )
                                    if record.output_layout == "landscape"
                                    else await self.ffmpeg.probe_video_dimensions(
                                        manifest_url
                                    )
                                )
                            if needs_ai_cover:
                                cover_object_key = (
                                    record.ai_cover_final_oss_object_key
                                )
                                if not cover_object_key:
                                    generation = (
                                        self.repository
                                        .get_ai_cover_generation(
                                            record.job_id,
                                            (
                                                record
                                                .ai_cover_generation_id
                                                or ""
                                            ),
                                        )
                                    )
                                    asset = (
                                        self.repository.get_ai_cover_asset(
                                            generation.id,
                                            record.output_layout,
                                        )
                                        if generation
                                        else None
                                    )
                                    cover_object_key = (
                                        asset.final_oss_object_key
                                        if asset
                                        and asset.status == "completed"
                                        else None
                                    )
                                if not cover_object_key:
                                    raise AppError(
                                        "ai_cover_not_ready",
                                        "AI 封面图片尚未生成完成",
                                        True,
                                    )
                                await self.oss.download_ai_cover_image(
                                    cover_object_key,
                                    ai_cover_frame_path,
                                )
                                if (
                                    record.ai_cover_final_sha256
                                    and file_sha256(ai_cover_frame_path)
                                    != record.ai_cover_final_sha256
                                ):
                                    raise AppError(
                                        "ai_cover_asset_changed",
                                        "AI 封面文件校验失败，请重新选择封面",
                                        False,
                                    )
                            overlay_documents = []
                            if needs_clip_overlay:
                                if dimensions is None:
                                    raise RuntimeError(
                                        "clip overlay dimensions missing"
                                    )
                                transcript = (
                                    self.repository.get_all_transcript(
                                        record.job_id
                                    )
                                )
                                translations = (
                                    self.repository
                                    .get_transcript_translations(
                                        record.job_id, "en"
                                    )
                                )
                                danmaku = self.repository.get_all_danmaku(
                                    record.job_id
                                )
                                job = self.repository.get_job(record.job_id)
                                for clip_range in record.kept_ranges:
                                    overlay_documents.append(
                                        build_clip_overlay(
                                            width=dimensions.width,
                                            height=dimensions.height,
                                            clip_start_ms=clip_range.start_ms,
                                            clip_end_ms=clip_range.end_ms,
                                            subtitle_mode=record.subtitle_mode,
                                            include_danmaku=(
                                                record.include_danmaku
                                            ),
                                            font_name=(
                                                self.settings.clip_font_name
                                            ),
                                            transcript=transcript,
                                            translations=translations,
                                            danmaku=danmaku,
                                            subtitle_font_scale=(
                                                record.subtitle_font_scale
                                            ),
                                            output_layout=(
                                                record.output_layout
                                            ),
                                            subtitle_font_family=(
                                                record.subtitle_font_family
                                            ),
                                            allow_empty_subtitles=True,
                                            live_started_at=(
                                                job.replay_started_at
                                                if job
                                                else None
                                            ),
                                        )
                                    )
                                subtitle_count = sum(
                                    document.subtitle_event_count
                                    for document in overlay_documents
                                )
                                danmaku_count = sum(
                                    document.danmaku_event_count
                                    for document in overlay_documents
                                )
                                if (
                                    record.subtitle_mode != "off"
                                    and subtitle_count == 0
                                ):
                                    raise AppError(
                                        "clip_subtitles_empty",
                                        "所选范围没有可渲染的字幕",
                                        False,
                                    )
                                warnings: list[str] = []
                                if (
                                    record.include_danmaku
                                    and danmaku_count == 0
                                ):
                                    warnings.append("所选范围没有可渲染的弹幕")
                                if _overlays_need_missing_emoji_font(
                                    overlay_documents
                                ):
                                    warnings.append(
                                        "服务器缺少 emoji 字体，"
                                        "画面中的 emoji 无法渲染"
                                    )
                                warning = "；".join(warnings) or None
                            if record.cover_enabled:
                                if (
                                    dimensions is None
                                    or record.cover_timestamp_ms is None
                                ):
                                    raise AppError(
                                        "clip_cover_invalid",
                                        "封面画面时间无效，请重新选择",
                                        False,
                                    )
                                cover_document = build_cover_overlay(
                                    width=dimensions.width,
                                    height=dimensions.height,
                                    title=record.cover_title,
                                    style=record.cover_style,
                                    font_name=self.settings.clip_font_name,
                                    output_layout=record.output_layout,
                                )
                                cover_ass_path.parent.mkdir(
                                    parents=True, exist_ok=True
                                )
                                cover_ass_path.write_text(
                                    cover_document.content,
                                    encoding="utf-8",
                                )
                                await self.ffmpeg.render_cover_frame(
                                    manifest_url,
                                    cover_frame_path,
                                    record.cover_timestamp_ms,
                                    cover_ass_path,
                                    output_layout=record.output_layout,
                                )
                            rendered_output_path = (
                                main_output_path
                                if record.cover_enabled
                                else state.output_path
                            )
                            for index, clip_range in enumerate(
                                record.kept_ranges
                            ):
                                ass_input: Path | None = None
                                if overlay_documents:
                                    ass_input = ass_paths[index]
                                    ass_input.parent.mkdir(
                                        parents=True,
                                        exist_ok=True,
                                    )
                                    ass_input.write_text(
                                        overlay_documents[index].content,
                                        encoding="utf-8",
                                    )
                                clip_output_path = (
                                    rendered_output_path
                                    if len(record.kept_ranges) == 1
                                    else part_paths[index]
                                )
                                clip_kwargs = {
                                    "output_layout": record.output_layout
                                }
                                if needs_ai_cover and index == 0:
                                    clip_kwargs.update(
                                        {
                                            "cover_path": ai_cover_frame_path,
                                            "cover_dimensions": dimensions,
                                        }
                                    )
                                await self.ffmpeg.clip_video(
                                    manifest_url,
                                    clip_output_path,
                                    clip_range.start_ms,
                                    clip_range.end_ms,
                                    ass_input,
                                    **clip_kwargs,
                                )
                            if len(record.kept_ranges) > 1:
                                await self.ffmpeg.concat_clips(
                                    part_paths,
                                    rendered_output_path,
                                )
                            if record.cover_enabled:
                                await self.ffmpeg.prepend_cover(
                                    cover_frame_path,
                                    main_output_path,
                                    state.output_path,
                                    duration_ms=COVER_DURATION_MS,
                                )
                        object_key = self.oss.clip_object_key(
                            record.job_id, state.output_path.name
                        )
                        await self.oss.upload_clip(
                            state.output_path,
                            object_key,
                            state.output_path.name,
                        )
                        self.repository.complete_video_clip_export(
                            record.id, object_key, warning
                        )
                        state.oss_object_key = object_key
                        state.warning = warning
                        state.output_path.unlink(missing_ok=True)
                        break
                    except asyncio.CancelledError:
                        raise
                    except AppError as exc:
                        if (
                            not exc.retryable
                            or exc.code == "video_clip_missing"
                            or attempt == self._retry_attempts
                        ):
                            raise
                        self.logger.warning(
                            "Retrying video clip after transient failure",
                            extra={
                                "job_id": record.job_id,
                                "clip_id": record.id,
                                "attempt": attempt,
                                "error_code": exc.code,
                            },
                        )
                    except Exception:
                        if attempt == self._retry_attempts:
                            raise
                        self.logger.exception(
                            "Retrying video clip after unexpected failure",
                            extra={
                                "job_id": record.job_id,
                                "clip_id": record.id,
                                "attempt": attempt,
                            },
                        )
                    for path in ass_paths:
                        path.unlink(missing_ok=True)
                    for path in part_paths:
                        path.unlink(missing_ok=True)
                    cover_ass_path.unlink(missing_ok=True)
                    cover_frame_path.unlink(missing_ok=True)
                    ai_cover_frame_path.unlink(missing_ok=True)
                    main_output_path.unlink(missing_ok=True)
                    await asyncio.sleep(
                        self._retry_delay_seconds * (2 ** (attempt - 1))
                    )
            state.status = "completed"
        except AppError as exc:
            state.status = "failed"
            state.error = exc.message
            state.output_path.unlink(missing_ok=True)
            self.repository.fail_video_clip_export(record.id, exc.message)
        except asyncio.CancelledError:
            state.status = "failed"
            state.error = "服务重启中，请重新剪辑"
            self.repository.fail_video_clip_export(
                record.id, state.error
            )
            raise
        except Exception:
            self.logger.exception("Unexpected video clipping failure")
            state.status = "failed"
            state.error = "视频剪辑失败，请查看服务日志"
            state.output_path.unlink(missing_ok=True)
            self.repository.fail_video_clip_export(
                record.id, state.error
            )
        finally:
            for path in ass_paths:
                path.unlink(missing_ok=True)
            for path in part_paths:
                path.unlink(missing_ok=True)
            cover_ass_path.unlink(missing_ok=True)
            cover_frame_path.unlink(missing_ok=True)
            ai_cover_frame_path.unlink(missing_ok=True)
            main_output_path.unlink(missing_ok=True)
            self._tasks.pop(record.id, None)

    async def _require_ass_support(self) -> None:
        if self._ass_supported is None:
            async with self._ass_probe_lock:
                if self._ass_supported is None:
                    self._ass_supported = await self.ffmpeg.supports_ass_filter()
        if not self._ass_supported:
            raise AppError(
                "clip_overlay_unavailable",
                "当前 FFmpeg 不支持 ASS 字幕滤镜，无法烧录字幕、弹幕或封面",
                False,
            )

    def _register_legacy_file(
        self, job_id: str, path: Path
    ) -> VideoClipExportRecord | None:
        match = LEGACY_CLIP_RE.fullmatch(path.name)
        if not match:
            return None
        timeline_index = int(match.group("index")) - 1
        if timeline_index < 0:
            return None
        clip_id = (
            f"legacy-file-{job_id.replace('-', '')}-{timeline_index}"
        )
        record, _ = self.repository.begin_video_clip_export(
            clip_id=clip_id,
            job_id=job_id,
            timeline_index=timeline_index,
            timeline_title="",
            requested_by_user_id=None,
            request_id=f"legacy-file:{timeline_index}:{path.name}",
            start_ms=int(match.group("start")) * 1000,
            end_ms=int(match.group("end")) * 1000,
            subtitle_mode="off",
            include_danmaku=False,
            render_version="legacy-v1",
            filename=path.name,
        )
        return record

    def _state_from_record(
        self, record: VideoClipExportRecord
    ) -> ClipState:
        current = self._states.get(record.id)
        if current and current.status == "running":
            return current
        state = ClipState(
            status=record.status,
            output_path=self.output_path_for(record),
            clip_id=record.id,
            error=record.error_message,
            warning=record.warning_message,
            oss_object_key=record.oss_object_key,
        )
        self._states[record.id] = state
        return state

    async def signed_download_url(
        self, record_or_state: VideoClipExportRecord | ClipState
    ) -> str:
        object_key = record_or_state.oss_object_key
        if not object_key:
            raise AppError(
                "video_clip_not_ready",
                "视频片段尚未上传完成",
                True,
            )
        return await self.oss.signed_clip_url(object_key)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
