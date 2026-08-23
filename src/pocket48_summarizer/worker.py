from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from .config import Settings
from .errors import AppError
from .pipeline import ReplayPipeline
from .repository import JobRepository


class DurableWorker:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        pipeline: ReplayPipeline,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.pipeline = pipeline
        self.worker_id = str(uuid.uuid4())
        self.logger = logging.getLogger(__name__)
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_cleanup = 0.0

    async def start(self) -> None:
        if self._task is not None:
            return
        await asyncio.to_thread(self.repository.recover_expired_jobs)
        self._task = asyncio.create_task(
            self._run(), name="pocket48-durable-worker"
        )

    async def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self._cleanup_expired_artifacts_if_due()
            job = await asyncio.to_thread(
                self.repository.claim_next_job,
                self.worker_id,
                self.settings.worker_lease_seconds,
            )
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self.settings.worker_poll_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            heartbeat = asyncio.create_task(
                self._heartbeat(job.id),
                name=f"pocket48-heartbeat-{job.id}",
            )
            try:
                await self.pipeline.run(job.id)
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    self.repository.release_owned_job,
                    job.id,
                    self.worker_id,
                )
                raise
            except AppError as exc:
                await asyncio.to_thread(
                    self.repository.mark_failed,
                    job.id,
                    exc.code,
                    exc.message,
                    exc.retryable,
                )
            except Exception:
                self.logger.exception("Unexpected worker failure", extra={"job_id": job.id})
                await asyncio.to_thread(
                    self.repository.mark_failed,
                    job.id,
                    "internal_error",
                    "处理任务时发生未预期内部错误",
                    True,
                )
            finally:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass

    async def _heartbeat(self, job_id: str) -> None:
        interval = max(10, self.settings.worker_lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(
                    self.repository.touch_lease,
                    job_id,
                    self.worker_id,
                    self.settings.worker_lease_seconds,
                )
            except AppError:
                self.logger.warning(
                    "Worker heartbeat lost its lease",
                    extra={"job_id": job_id},
                )
                return

    async def _cleanup_expired_artifacts_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 30 * 60:
            return
        self._last_cleanup = now
        cutoff = (
            datetime.now(UTC)
            - timedelta(hours=self.settings.failed_audio_retention_hours)
        ).isoformat()
        jobs = await asyncio.to_thread(
            self.repository.list_failed_artifact_jobs, cutoff
        )
        for job in jobs:
            try:
                await self.pipeline.cleanup_failed_artifacts(job.id)
            except AppError as exc:
                self.logger.warning(
                    "Failed artifact cleanup did not complete",
                    extra={"job_id": job.id, "error_code": exc.code},
                )
