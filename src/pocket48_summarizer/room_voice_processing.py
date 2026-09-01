from __future__ import annotations

import json
import logging
from pathlib import Path

from .clients.dashscope import DashScopeClient
from .clients.oss_store import OSSStore
from .config import Settings
from .errors import AppError
from .media.ffmpeg import FFmpegRunner
from .models import RoomVoiceProcessingRecord, RoomVoiceProcessingStage
from .parsing.transcript import normalize_asr_result
from .repository import JobRepository
from .room_voice_admin import (
    list_processable_capture_sessions,
    safe_capture_segment_path,
)
from .security import redact_signed_urls
from .summarization.service import SummarizationService
from .vocabulary import VocabularyManager

LOGGER = logging.getLogger(__name__)


class RoomVoiceSummaryRepository:
    def __init__(self, repository: JobRepository) -> None:
        self.repository = repository

    def get_summary_chunks(
        self, session_id: str, prompt_version: str
    ) -> dict[int, tuple[str, str]]:
        return self.repository.get_room_voice_summary_chunks(
            session_id, prompt_version
        )

    def save_summary_chunk(
        self,
        session_id: str,
        chunk_index: int,
        start_ms: int,
        end_ms: int,
        prompt_version: str,
        input_hash: str,
        response_json: str,
    ) -> None:
        self.repository.save_room_voice_summary_chunk(
            session_id,
            chunk_index,
            start_ms,
            end_ms,
            prompt_version,
            input_hash,
            response_json,
        )


class RoomVoiceProcessingService:
    def __init__(
        self,
        *,
        settings: Settings,
        repository: JobRepository,
        ffmpeg: FFmpegRunner,
        oss: OSSStore,
        dashscope: DashScopeClient,
        summarizer: SummarizationService,
        vocabulary: VocabularyManager | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.ffmpeg = ffmpeg
        self.oss = oss
        self.dashscope = dashscope
        self.summarizer = summarizer
        self.vocabulary = vocabulary

    def discover_sessions(self) -> int:
        discovered = 0
        for session in list_processable_capture_sessions(
            self.settings.room_voice_path
        ):
            existing = self.repository.get_room_voice_processing(
                session.session_id
            )
            if existing is not None:
                continue
            self.repository.enqueue_room_voice_processing(
                session_id=session.session_id,
                monitor_id=session.monitor_id,
                member_name=session.member_name,
                segment_count=len(session.segments),
                total_bytes=sum(
                    segment.size_bytes for segment in session.segments
                ),
            )
            discovered += 1
        return discovered

    async def run(self, session_id: str) -> None:
        job = self._require_job(session_id)
        if not job.asr_raw_json:
            audio_path = (
                Path(job.audio_path)
                if job.audio_path and Path(job.audio_path).is_file()
                else None
            )
            if audio_path is None:
                self.repository.set_room_voice_processing_stage(
                    session_id,
                    RoomVoiceProcessingStage.PREPARING_AUDIO,
                    10,
                    "正在合并上麦录音分段",
                )
                segment_paths = self._segment_paths(job)
                audio_path = (
                    self.settings.temp_dir
                    / "room-voice"
                    / session_id
                    / "audio.mp3"
                ).resolve()
                await self.ffmpeg.concat_audio_segments(
                    segment_paths, audio_path
                )
                self.repository.set_room_voice_audio_path(
                    session_id, str(audio_path)
                )
                job = self._require_job(session_id)

            if not job.oss_object_key:
                self.repository.set_room_voice_processing_stage(
                    session_id,
                    RoomVoiceProcessingStage.UPLOADING_AUDIO,
                    25,
                    "正在上传临时识别音频",
                )
                object_key = self.oss.room_voice_object_key(session_id)
                await self.oss.upload(audio_path, object_key)
                self.repository.set_room_voice_oss_object(
                    session_id, object_key
                )
                job = self._require_job(session_id)

            if not job.dashscope_task_id:
                if not job.oss_object_key:
                    raise AppError(
                        "room_voice_oss_object_missing",
                        "上麦录音临时 OSS 记录缺失",
                        True,
                    )
                signed_url = await self.oss.signed_get_url(
                    job.oss_object_key
                )
                self.repository.set_room_voice_processing_stage(
                    session_id,
                    RoomVoiceProcessingStage.TRANSCRIBING,
                    35,
                    "正在提交上麦录音语音识别",
                )
                active_vocabulary = (
                    await self.vocabulary.ensure_current()
                    if self.vocabulary
                    else None
                )
                task_id, status = await self.dashscope.submit(
                    signed_url,
                    vocabulary_id=(
                        active_vocabulary.vocabulary_id
                        if active_vocabulary
                        else None
                    ),
                )
                self.repository.set_room_voice_dashscope_task(
                    session_id, task_id, status
                )
                job = self._require_job(session_id)

            if not job.dashscope_task_id:
                raise AppError(
                    "room_voice_dashscope_task_missing",
                    "上麦录音语音识别任务 ID 缺失",
                    True,
                )
            self.repository.set_room_voice_processing_stage(
                session_id,
                RoomVoiceProcessingStage.TRANSCRIBING,
                45,
                "正在等待上麦录音语音识别",
            )

            async def on_status(status: str) -> None:
                self.repository.set_room_voice_dashscope_status(
                    session_id, status
                )

            asr_payload = await self.dashscope.wait_for_result(
                job.dashscope_task_id,
                on_status=on_status,
            )
            asr_payload = redact_signed_urls(asr_payload)
            self.repository.save_room_voice_asr_raw(
                session_id,
                json.dumps(
                    asr_payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            )
            await self._cleanup_media(session_id)
            job = self._require_job(session_id)

        if self.repository.count_room_voice_transcript(session_id) == 0:
            self.repository.set_room_voice_processing_stage(
                session_id,
                RoomVoiceProcessingStage.NORMALIZING_TRANSCRIPT,
                62,
                "正在生成上麦录音时间戳字幕",
            )
            if not job.asr_raw_json:
                raise AppError(
                    "room_voice_asr_result_missing",
                    "上麦录音语音识别结果缺失",
                    True,
                )
            try:
                asr_payload = json.loads(job.asr_raw_json)
            except json.JSONDecodeError as exc:
                raise AppError(
                    "invalid_room_voice_asr_result",
                    "已保存的上麦录音识别结果不是有效 JSON",
                    False,
                ) from exc
            self.repository.replace_room_voice_transcript(
                session_id,
                normalize_asr_result(asr_payload),
            )
            job = self._require_job(session_id)

        if not job.summary_json:
            segments = self.repository.get_all_room_voice_transcript(
                session_id
            )
            if not segments:
                raise AppError(
                    "room_voice_transcript_empty",
                    "上麦录音未识别到可总结的语音内容",
                    False,
                )
            self.repository.set_room_voice_processing_stage(
                session_id,
                RoomVoiceProcessingStage.SUMMARIZING_CHUNKS,
                72,
                "正在分段总结上麦字幕",
            )

            async def on_chunk(completed: int, total: int) -> None:
                progress = 72 + round(16 * completed / max(1, total))
                self.repository.set_room_voice_processing_stage(
                    session_id,
                    RoomVoiceProcessingStage.SUMMARIZING_CHUNKS,
                    progress,
                    f"正在总结上麦字幕分段 {completed}/{total}",
                )

            title = f"{job.member_name or '成员'}的上麦录音"
            summary, markdown = await self.summarizer.summarize(
                job_id=session_id,
                live_id=session_id,
                title=title,
                member_name=job.member_name or "未知成员",
                segments=segments,
                peaks=[],
                on_progress=on_chunk,
            )
            self.repository.set_room_voice_processing_stage(
                session_id,
                RoomVoiceProcessingStage.SUMMARIZING_FINAL,
                92,
                "正在生成上麦录音结构化总结",
            )
            self.repository.save_room_voice_summary(
                session_id,
                summary.model_dump_json(),
                markdown,
            )

        self.repository.set_room_voice_processing_stage(
            session_id,
            RoomVoiceProcessingStage.CLEANING_UP,
            97,
            "正在清理上麦识别临时音频",
        )
        await self._cleanup_media(session_id)
        self.repository.mark_room_voice_processing_completed(session_id)

    async def cleanup_failed_artifacts(self, session_id: str) -> None:
        await self._cleanup_media(session_id)

    def _segment_paths(
        self, job: RoomVoiceProcessingRecord
    ) -> list[Path]:
        session_path = self.settings.room_voice_path / job.session_id
        names = sorted(
            path.name
            for path in (session_path / "segments").glob(
                "segment-[0-9][0-9][0-9][0-9][0-9][0-9].mp3"
            )
        )
        paths = [
            safe_capture_segment_path(
                self.settings.room_voice_path,
                job.session_id,
                name,
            )
            for name in names
        ]
        safe_paths = [path for path in paths if path is not None]
        if len(safe_paths) != job.segment_count:
            raise AppError(
                "room_voice_audio_segments_changed",
                "上麦录音分段数量与入队时不一致",
                False,
            )
        return safe_paths

    async def _cleanup_media(self, session_id: str) -> None:
        job = self._require_job(session_id)
        if job.oss_object_key:
            try:
                await self.oss.delete(job.oss_object_key)
            except AppError as exc:
                LOGGER.warning(
                    "Unable to delete room voice ASR object",
                    extra={
                        "session_id": session_id,
                        "error_code": exc.code,
                    },
                )
            else:
                self.repository.set_room_voice_oss_object(session_id, None)
        if job.audio_path:
            path = Path(job.audio_path)
            try:
                path.unlink(missing_ok=True)
                parent = path.parent
                if parent.exists():
                    parent.rmdir()
            except OSError:
                LOGGER.warning(
                    "Unable to delete room voice ASR audio",
                    extra={"session_id": session_id},
                )
            else:
                self.repository.set_room_voice_audio_path(session_id, None)

    def _require_job(
        self, session_id: str
    ) -> RoomVoiceProcessingRecord:
        job = self.repository.get_room_voice_processing(session_id)
        if job is None:
            raise AppError(
                "room_voice_processing_not_found",
                "上麦录音处理任务不存在",
                False,
            )
        return job
