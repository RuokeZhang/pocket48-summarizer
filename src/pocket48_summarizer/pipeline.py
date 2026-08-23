from __future__ import annotations

import json
from pathlib import Path

from .clients.dashscope import DashScopeClient
from .clients.oss_store import OSSStore
from .clients.pocket48 import Pocket48Client
from .config import Settings
from .errors import AppError
from .media.ffmpeg import FFmpegRunner
from .media.hls import HLSInspector
from .models import JobRecord, JobStage
from .parsing.lrc import detect_danmaku_peaks, parse_lrc
from .parsing.transcript import normalize_asr_result
from .repository import JobRepository
from .security import redact_signed_urls
from .summarization.service import SummarizationService


class ReplayPipeline:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: JobRepository,
        pocket48: Pocket48Client,
        hls: HLSInspector,
        ffmpeg: FFmpegRunner,
        oss: OSSStore,
        dashscope: DashScopeClient,
        summarizer: SummarizationService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.pocket48 = pocket48
        self.hls = hls
        self.ffmpeg = ffmpeg
        self.oss = oss
        self.dashscope = dashscope
        self.summarizer = summarizer

    async def run(self, job_id: str) -> None:
        job = self._require_job(job_id)
        if not job.media_url:
            self.repository.set_stage(
                job_id, JobStage.RESOLVING, 5, "正在解析公开回放"
            )
            metadata = await self.pocket48.resolve_replay(job.live_id)
            self.repository.save_replay_metadata(job_id, metadata)
            job = self._require_job(job_id)

        if not job.danmaku_loaded_at:
            self.repository.set_stage(
                job_id, JobStage.FETCHING_DANMAKU, 12, "正在解析回放弹幕"
            )
            entries = []
            if job.danmaku_url:
                entries = parse_lrc(
                    await self.pocket48.fetch_danmaku(job.danmaku_url)
                )
            self.repository.replace_danmaku(job_id, entries)
            self.repository.replace_danmaku_peaks(
                job_id, detect_danmaku_peaks(entries)
            )
            job = self._require_job(job_id)

        if not job.asr_raw_json:
            audio_path = (
                Path(job.audio_path)
                if job.audio_path and Path(job.audio_path).is_file()
                else None
            )
            if audio_path is None:
                self.repository.set_stage(
                    job_id, JobStage.EXTRACTING_AUDIO, 20, "正在检查 HLS 回放"
                )
                if not job.media_url:
                    raise AppError(
                        "media_url_missing", "回放媒体地址缺失", True
                    )
                manifest = await self.hls.inspect(job.media_url)
                self.repository.set_media_details(
                    job_id, manifest.url, manifest.duration_ms
                )
                audio_path = (
                    self.settings.temp_dir / job_id / "audio.mp3"
                ).resolve()
                self.repository.set_stage(
                    job_id,
                    JobStage.EXTRACTING_AUDIO,
                    28,
                    "正在从 HLS 提取语音音频",
                )
                await self.ffmpeg.extract_audio(
                    manifest.url, audio_path, manifest.duration_ms
                )
                self.repository.set_audio_path(job_id, str(audio_path))
                job = self._require_job(job_id)

            if not job.oss_object_key:
                self.repository.set_stage(
                    job_id,
                    JobStage.UPLOADING_AUDIO,
                    38,
                    "正在上传临时音频到私有 OSS",
                )
                object_key = self.oss.object_key(job_id)
                await self.oss.upload(audio_path, object_key)
                self.repository.set_oss_object(job_id, object_key)
                job = self._require_job(job_id)

            if not job.dashscope_task_id:
                if not job.oss_object_key:
                    raise AppError(
                        "oss_object_missing", "OSS 临时音频记录缺失", True
                    )
                signed_url = await self.oss.signed_get_url(job.oss_object_key)
                self.repository.set_stage(
                    job_id,
                    JobStage.TRANSCRIBING,
                    45,
                    "正在提交 DashScope 识别任务",
                )
                task_id, status = await self.dashscope.submit(signed_url)
                self.repository.set_dashscope_task(job_id, task_id, status)
                job = self._require_job(job_id)

            self.repository.set_stage(
                job_id,
                JobStage.TRANSCRIBING,
                50,
                "正在等待 DashScope 语音识别",
            )

            async def on_status(status: str) -> None:
                self.repository.set_dashscope_status(job_id, status)

            if not job.dashscope_task_id:
                raise AppError(
                    "dashscope_task_missing", "DashScope 任务 ID 缺失", True
                )
            asr_payload = await self.dashscope.wait_for_result(
                job.dashscope_task_id, on_status=on_status
            )
            asr_payload = redact_signed_urls(asr_payload)
            self.repository.save_asr_raw(
                job_id,
                json.dumps(
                    asr_payload, ensure_ascii=False, separators=(",", ":")
                ),
            )
            await self._cleanup_media(job_id)
            job = self._require_job(job_id)

        if self.repository.count_transcript(job_id) == 0:
            self.repository.set_stage(
                job_id,
                JobStage.NORMALIZING_TRANSCRIPT,
                68,
                "正在生成时间戳字幕",
            )
            if not job.asr_raw_json:
                raise AppError(
                    "asr_result_missing", "语音识别结果缺失", True
                )
            try:
                asr_payload = json.loads(job.asr_raw_json)
            except json.JSONDecodeError as exc:
                raise AppError(
                    "invalid_asr_result",
                    "已保存的语音识别结果不是有效 JSON",
                    False,
                ) from exc
            segments = normalize_asr_result(asr_payload)
            self.repository.replace_transcript(job_id, segments)
            job = self._require_job(job_id)

        if not job.summary_json or not job.summary_markdown:
            segments = self.repository.get_all_transcript(job_id)
            peaks = self.repository.get_danmaku_peaks(job_id)
            self.repository.set_stage(
                job_id,
                JobStage.SUMMARIZING_CHUNKS,
                75,
                "正在分段总结字幕",
            )

            async def on_chunk(completed: int, total: int) -> None:
                progress = 75 + round(12 * completed / max(1, total))
                self.repository.set_stage(
                    job_id,
                    JobStage.SUMMARIZING_CHUNKS,
                    progress,
                    f"正在总结字幕分段 {completed}/{total}",
                )

            summary, markdown = await self.summarizer.summarize(
                job_id=job_id,
                live_id=job.live_id,
                title=job.title or "未命名直播",
                member_name=job.member_name or "未知主播",
                segments=segments,
                peaks=peaks,
                on_progress=on_chunk,
            )
            self.repository.set_stage(
                job_id,
                JobStage.SUMMARIZING_FINAL,
                90,
                "正在生成整场结构化总结",
            )
            self.repository.save_summary(
                job_id, summary.model_dump_json(), markdown
            )

        self.repository.set_stage(
            job_id, JobStage.CLEANING_UP, 96, "正在清理临时音频"
        )
        await self._cleanup_media(job_id)
        self.repository.mark_completed(job_id)

    async def cleanup_failed_artifacts(self, job_id: str) -> None:
        await self._cleanup_media(job_id)

    async def _cleanup_media(self, job_id: str) -> None:
        job = self._require_job(job_id)
        warnings: list[str] = []
        if job.oss_object_key:
            try:
                await self.oss.delete(job.oss_object_key)
            except AppError as exc:
                warnings.append(exc.message)
            else:
                self.repository.set_oss_object(job_id, None)
        if job.audio_path:
            path = Path(job.audio_path)
            try:
                path.unlink(missing_ok=True)
                parent = path.parent
                if parent != self.settings.temp_dir and parent.exists():
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
            except OSError:
                warnings.append("删除本地临时音频失败")
            else:
                self.repository.set_audio_path(job_id, None)
        self.repository.set_cleanup_warning(
            job_id, "；".join(warnings) if warnings else None
        )

    def _require_job(self, job_id: str) -> JobRecord:
        job = self.repository.get_job(job_id)
        if job is None:
            raise AppError("job_not_found", "任务不存在", False)
        return job
