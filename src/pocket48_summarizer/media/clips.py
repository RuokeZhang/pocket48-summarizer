from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..clients.oss_store import OSSStore
from ..config import Settings
from ..errors import AppError
from ..repository import JobRepository
from .ffmpeg import FFmpegRunner

ClipStatus = Literal["running", "completed", "failed"]
LEGACY_CLIP_RE = re.compile(
    r"^timeline-(?P<index>\d+)-(?P<start>\d+)-(?P<end>\d+)\.mp4$"
)


@dataclass(slots=True)
class ClipState:
    status: ClipStatus
    output_path: Path
    error: str | None = None
    oss_object_key: str | None = None


class VideoClipService:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        oss: OSSStore,
        ffmpeg: FFmpegRunner | None = None,
    ) -> None:
        self.output_dir = settings.data_dir / "clips"
        self.repository = repository
        self.oss = oss
        self.ffmpeg = ffmpeg or FFmpegRunner(settings)
        self.logger = logging.getLogger(__name__)
        self._states: dict[tuple[str, int], ClipState] = {}
        self._tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._capacity = asyncio.Semaphore(settings.clip_concurrency)
        self._retry_attempts = settings.clip_retry_attempts
        self._retry_delay_seconds = settings.clip_retry_delay_seconds

    async def startup(self) -> None:
        self.repository.recover_running_video_clips()
        for path in self.output_dir.glob("*/timeline-*.mp4"):
            match = LEGACY_CLIP_RE.fullmatch(path.name)
            if not match or not path.is_file() or path.stat().st_size == 0:
                continue
            job_id = path.parent.name
            if self.repository.get_job(job_id) is None:
                continue
            timeline_index = int(match.group("index")) - 1
            if timeline_index < 0:
                continue
            record = self.repository.get_video_clip(job_id, timeline_index)
            if record and record.status == "completed":
                path.unlink(missing_ok=True)
                continue
            self._start_task(
                job_id=job_id,
                timeline_index=timeline_index,
                manifest_url=None,
                start_ms=int(match.group("start")) * 1000,
                end_ms=int(match.group("end")) * 1000,
                output_path=path,
            )

    def output_path(
        self,
        job_id: str,
        timeline_index: int,
        start_ms: int,
        end_ms: int,
    ) -> Path:
        if Path(job_id).name != job_id:
            raise AppError("invalid_job_id", "任务 ID 无效", False)
        filename = (
            f"timeline-{timeline_index + 1:02d}-"
            f"{start_ms // 1000}-{end_ms // 1000}.mp4"
        )
        return self.output_dir / job_id / filename

    def start(
        self,
        *,
        job_id: str,
        timeline_index: int,
        manifest_url: str,
        start_ms: int,
        end_ms: int,
    ) -> ClipState:
        key = (job_id, timeline_index)
        output_path = self.output_path(
            job_id, timeline_index, start_ms, end_ms
        )
        current = self._states.get(key)
        if current and current.status == "running":
            return current
        record = self.repository.get_video_clip(job_id, timeline_index)
        if record and record.status == "completed" and record.oss_object_key:
            state = ClipState(
                "completed",
                output_path,
                oss_object_key=record.oss_object_key,
            )
            self._states[key] = state
            return state
        return self._start_task(
            job_id=job_id,
            timeline_index=timeline_index,
            manifest_url=manifest_url,
            start_ms=start_ms,
            end_ms=end_ms,
            output_path=output_path,
        )

    def _start_task(
        self,
        *,
        job_id: str,
        timeline_index: int,
        manifest_url: str | None,
        start_ms: int,
        end_ms: int,
        output_path: Path,
    ) -> ClipState:
        key = (job_id, timeline_index)
        self.repository.begin_video_clip(
            job_id,
            timeline_index,
            start_ms,
            end_ms,
            output_path.name,
        )
        state = ClipState("running", output_path)
        self._states[key] = state
        self._tasks[key] = asyncio.create_task(
            self._run(key, state, manifest_url, start_ms, end_ms)
        )
        return state

    def get(
        self,
        *,
        job_id: str,
        timeline_index: int,
        start_ms: int,
        end_ms: int,
    ) -> ClipState | None:
        key = (job_id, timeline_index)
        output_path = self.output_path(
            job_id, timeline_index, start_ms, end_ms
        )
        current = self._states.get(key)
        if current and current.status == "running":
            return current
        record = self.repository.get_video_clip(job_id, timeline_index)
        if record is not None:
            state = ClipState(
                record.status,
                output_path,
                error=record.error_message,
                oss_object_key=record.oss_object_key,
            )
            self._states[key] = state
            return state
        if output_path.is_file() and output_path.stat().st_size > 0:
            state = ClipState("completed", output_path)
            self._states[key] = state
            return state
        return None

    async def _run(
        self,
        key: tuple[str, int],
        state: ClipState,
        manifest_url: str | None,
        start_ms: int,
        end_ms: int,
    ) -> None:
        try:
            async with self._capacity:
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
                            await self.ffmpeg.clip_video(
                                manifest_url,
                                state.output_path,
                                start_ms,
                                end_ms,
                            )
                        object_key = self.oss.clip_object_key(
                            key[0], state.output_path.name
                        )
                        await self.oss.upload_clip(
                            state.output_path,
                            object_key,
                            state.output_path.name,
                        )
                        self.repository.complete_video_clip(
                            key[0], key[1], object_key
                        )
                        state.oss_object_key = object_key
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
                                "job_id": key[0],
                                "timeline_index": key[1],
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
                                "job_id": key[0],
                                "timeline_index": key[1],
                                "attempt": attempt,
                            },
                        )
                    await asyncio.sleep(
                        self._retry_delay_seconds * (2 ** (attempt - 1))
                    )
            state.status = "completed"
        except AppError as exc:
            state.status = "failed"
            state.error = exc.message
            self.repository.fail_video_clip(key[0], key[1], exc.message)
        except asyncio.CancelledError:
            state.status = "failed"
            state.error = "服务重启中，请重新剪辑"
            self.repository.fail_video_clip(
                key[0], key[1], state.error
            )
            raise
        except Exception:
            self.logger.exception("Unexpected video clipping failure")
            state.status = "failed"
            state.error = "视频剪辑失败，请查看服务日志"
            self.repository.fail_video_clip(
                key[0], key[1], state.error
            )
        finally:
            self._tasks.pop(key, None)

    async def signed_download_url(self, state: ClipState) -> str:
        if not state.oss_object_key:
            raise AppError(
                "video_clip_not_ready",
                "视频片段尚未上传完成",
                True,
            )
        return await self.oss.signed_clip_url(state.oss_object_key)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
