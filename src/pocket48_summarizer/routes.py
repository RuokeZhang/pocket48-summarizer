from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .errors import AppError
from .media.clips import ClipState
from .models import FinalSummary, JobRecord, JobStatus
from .parsing.transcript import transcript_to_srt
from .security import parse_share_url
from .summarization.chunking import format_clock

router = APIRouter()


class CreateJobRequest(BaseModel):
    url: str


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
) -> tuple[JobRecord, object]:
    job = request.app.state.services.repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
    if not job.summary_json:
        raise AppError("summary_not_ready", "总结尚未生成", True)
    summary = FinalSummary.model_validate_json(job.summary_json)
    if timeline_index < 0 or timeline_index >= len(summary.timeline):
        raise AppError("timeline_item_not_found", "时间线话题不存在", False)
    if not job.media_url:
        raise AppError("media_not_ready", "回放媒体地址尚未生成", True)
    return job, summary.timeline[timeline_index]


def clip_payload(
    job_id: str, timeline_index: int, state: ClipState
) -> dict:
    payload = {
        "status": state.status,
        "error": state.error,
        "filename": state.output_path.name,
        "saved_path": f"data/clips/{job_id}/{state.output_path.name}",
    }
    if state.status == "completed":
        payload["download_url"] = (
            f"/jobs/{job_id}/clips/{timeline_index}/download"
        )
    return payload


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    services = request.app.state.services
    settings = request.app.state.settings
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "jobs": services.repository.list_jobs(),
            "missing_configuration": settings.missing_processing_configuration(),
        },
    )


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_page(request: Request, job_id: str) -> Response:
    repository = request.app.state.services.repository
    job = repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
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
            "events": repository.list_events(job_id),
            "format_clock": format_clock,
        },
    )


@router.post("/api/jobs")
async def create_job(request: Request, payload: CreateJobRequest) -> Response:
    settings = request.app.state.settings
    settings.require_processing_configuration()
    normalized_url, live_id = parse_share_url(payload.url)
    services = request.app.state.services
    job, created = services.repository.create_or_get_job(
        normalized_url, live_id
    )
    if services.worker is None:
        raise AppError(
            "worker_unavailable",
            "处理 Worker 未启动，请检查配置后重启应用",
            True,
        )
    services.worker.notify()
    return JSONResponse(
        public_job_payload(job), status_code=201 if created else 200
    )


@router.get("/api/jobs/{job_id}/status")
async def job_status(request: Request, job_id: str) -> dict:
    job = request.app.state.services.repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
    return public_job_payload(job)


@router.get("/api/jobs/{job_id}/transcript")
async def transcript_page(
    request: Request, job_id: str, offset: int = 0, limit: int = 200
) -> dict:
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    repository = request.app.state.services.repository
    if repository.get_job(job_id) is None:
        raise AppError("job_not_found", "任务不存在", False)
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
    if repository.get_job(job_id) is None:
        raise AppError("job_not_found", "任务不存在", False)
    entries = repository.get_danmaku(
        job_id, limit=limit, after_ms=max(-1, after_ms)
    )
    return {
        "entries": [entry.model_dump() for entry in entries],
        "next_after_ms": entries[-1].timestamp_ms if entries else after_ms,
        "has_more": len(entries) == limit,
    }


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(request: Request, job_id: str) -> dict:
    services = request.app.state.services
    if services.worker is None:
        raise AppError(
            "worker_unavailable",
            "处理 Worker 未启动，请检查配置后重启应用",
            True,
        )
    job = services.repository.retry_job(job_id)
    services.worker.notify()
    return public_job_payload(job)


@router.post("/api/jobs/{job_id}/clips/{timeline_index}")
async def create_timeline_clip(
    request: Request, job_id: str, timeline_index: int
) -> Response:
    job, item = timeline_clip_context(request, job_id, timeline_index)
    clipper = request.app.state.services.clipper
    if clipper is None:
        raise AppError(
            "clipper_unavailable",
            "视频剪辑服务未启动",
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
    if state is None or state.status != "completed":
        raise AppError(
            "video_clip_not_ready",
            "视频片段尚未生成完成",
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
    job = repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
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
    job = request.app.state.services.repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
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
    job = request.app.state.services.repository.get_job(job_id)
    if job is None:
        raise AppError("job_not_found", "任务不存在", False)
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
        "worker_enabled": request.app.state.services.worker is not None,
        "missing_configuration": settings.missing_processing_configuration(),
    }
