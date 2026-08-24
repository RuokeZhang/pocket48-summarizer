from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..config import Settings
from ..errors import AppError
from .ffmpeg import FFmpegRunner

ClipStatus = Literal["running", "completed", "failed"]


@dataclass(slots=True)
class ClipState:
    status: ClipStatus
    output_path: Path
    error: str | None = None


class VideoClipService:
    def __init__(
        self,
        settings: Settings,
        ffmpeg: FFmpegRunner | None = None,
    ) -> None:
        self.output_dir = settings.data_dir / "clips"
        self.ffmpeg = ffmpeg or FFmpegRunner(settings)
        self.logger = logging.getLogger(__name__)
        self._states: dict[tuple[str, int], ClipState] = {}
        self._tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._capacity = asyncio.Semaphore(settings.clip_concurrency)

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
        if output_path.is_file() and output_path.stat().st_size > 0:
            state = ClipState("completed", output_path)
            self._states[key] = state
            return state
        current = self._states.get(key)
        if current and current.status == "running":
            return current
        state = ClipState("running", output_path)
        self._states[key] = state
        self._tasks[key] = asyncio.create_task(
            self._run(
                key,
                state,
                manifest_url,
                start_ms,
                end_ms,
            )
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
        if output_path.is_file() and output_path.stat().st_size > 0:
            state = ClipState("completed", output_path)
            self._states[key] = state
            return state
        return self._states.get(key)

    async def _run(
        self,
        key: tuple[str, int],
        state: ClipState,
        manifest_url: str,
        start_ms: int,
        end_ms: int,
    ) -> None:
        try:
            async with self._capacity:
                await self.ffmpeg.clip_video(
                    manifest_url,
                    state.output_path,
                    start_ms,
                    end_ms,
                )
            state.status = "completed"
        except AppError as exc:
            state.status = "failed"
            state.error = exc.message
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("Unexpected video clipping failure")
            state.status = "failed"
            state.error = "视频剪辑失败，请查看服务日志"
        finally:
            self._tasks.pop(key, None)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
