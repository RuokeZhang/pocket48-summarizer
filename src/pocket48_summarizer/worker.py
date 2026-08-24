from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from .config import Settings
from .errors import AppError
from .glossary import MemberCatalogService
from .models import JobRecord, SubtitleTranslationRequestRecord
from .pipeline import ReplayPipeline
from .repository import JobRepository
from .runtime_lock import shared_runtime_lock
from .translation import SubtitleTranslationService


class DurableWorker:
    def __init__(
        self,
        settings: Settings,
        repository: JobRepository,
        pipeline: ReplayPipeline,
        translator: SubtitleTranslationService | None = None,
        member_catalog: MemberCatalogService | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.pipeline = pipeline
        self.translator = translator
        self.member_catalog = member_catalog
        self.worker_id = str(uuid.uuid4())
        self.logger = logging.getLogger(__name__)
        self._wake = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._last_cleanup = 0.0
        self._last_member_catalog_check = 0.0

    async def start(self) -> None:
        if self._task is not None:
            return
        await asyncio.to_thread(self.repository.recover_expired_jobs)
        if self.translator is not None:
            await asyncio.to_thread(
                self.repository.recover_expired_subtitle_translations
            )
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
            except Exception:
                self.logger.exception("Worker task stopped after failure")
            self._task = None

    async def wait(self) -> None:
        if self._task is None:
            raise RuntimeError("Worker has not been started")
        await self._task

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while not self._stopping.is_set():
            await self._sync_member_catalog_if_due()
            await self._cleanup_expired_artifacts_if_due()
            maintenance_active = False
            translation = None
            with shared_runtime_lock(
                self.settings.worker_operation_lock_path
            ):
                if self.settings.worker_maintenance_path.exists():
                    maintenance_active = True
                    job = None
                else:
                    job = await asyncio.to_thread(
                        self.repository.claim_next_job,
                        self.worker_id,
                        self.settings.worker_lease_seconds,
                    )
                    if job is None and self.translator is not None:
                        translation = await asyncio.to_thread(
                            self.repository.claim_next_subtitle_translation,
                            self.worker_id,
                            self.settings.worker_lease_seconds,
                        )
            if maintenance_active:
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self.settings.worker_poll_seconds,
                    )
                except TimeoutError:
                    pass
                continue
            if job is not None:
                await self._process_job(job)
                continue
            if translation is not None:
                await self._process_translation(translation)
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(
                    self._wake.wait(),
                    timeout=self.settings.worker_poll_seconds,
                )
            except TimeoutError:
                pass

    async def _sync_member_catalog_if_due(self) -> None:
        if self.member_catalog is None:
            return
        now = time.monotonic()
        if now - self._last_member_catalog_check < 60:
            return
        self._last_member_catalog_check = now
        try:
            await self.member_catalog.sync_if_due()
        except AppError as exc:
            self.logger.warning(
                "Official member catalog sync failed: %s (%s)",
                exc.message,
                exc.code,
            )

    async def _process_job(self, job: JobRecord) -> None:
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
            self.logger.exception(
                "Unexpected worker failure", extra={"job_id": job.id}
            )
            await asyncio.to_thread(
                self.repository.mark_failed,
                job.id,
                "internal_error",
                "处理任务时发生未预期内部错误",
                True,
            )
        else:
            if self.translator is not None:
                try:
                    await asyncio.to_thread(
                        self.repository.request_subtitle_translation,
                        job.id,
                    )
                except AppError as exc:
                    self.logger.warning(
                        "Automatic subtitle translation was not queued",
                        extra={"job_id": job.id, "error_code": exc.code},
                    )
                except Exception:
                    self.logger.exception(
                        "Automatic subtitle translation queue failed",
                        extra={"job_id": job.id},
                    )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _process_translation(
        self, request: SubtitleTranslationRequestRecord
    ) -> None:
        if self.translator is None:
            return
        heartbeat = asyncio.create_task(
            self._translation_heartbeat(request.job_id, request.language),
            name=f"pocket48-translation-heartbeat-{request.job_id}",
        )
        try:
            await self.translator.translate_job(
                request.job_id, request.language
            )
            await asyncio.to_thread(
                self.repository.mark_subtitle_translation_completed,
                request.job_id,
                request.language,
                self.worker_id,
            )
        except asyncio.CancelledError:
            await asyncio.to_thread(
                self.repository.release_owned_subtitle_translation,
                request.job_id,
                request.language,
                self.worker_id,
            )
            raise
        except AppError as exc:
            retry = (
                exc.retryable
                and request.retry_count
                < self.settings.translation_retry_attempts
            )
            await asyncio.to_thread(
                self.repository.mark_subtitle_translation_failed,
                request.job_id,
                request.language,
                self.worker_id,
                exc.message,
                retry=retry,
            )
        except Exception:
            self.logger.exception(
                "Unexpected subtitle translation failure",
                extra={"job_id": request.job_id},
            )
            retry = (
                request.retry_count
                < self.settings.translation_retry_attempts
            )
            await asyncio.to_thread(
                self.repository.mark_subtitle_translation_failed,
                request.job_id,
                request.language,
                self.worker_id,
                "英文字幕翻译发生未预期内部错误",
                retry=retry,
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

    async def _translation_heartbeat(
        self, job_id: str, language: str
    ) -> None:
        interval = max(10, self.settings.worker_lease_seconds // 3)
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(
                    self.repository.touch_subtitle_translation_lease,
                    job_id,
                    language,
                    self.worker_id,
                    self.settings.worker_lease_seconds,
                )
            except AppError:
                self.logger.warning(
                    "Translation heartbeat lost its lease",
                    extra={"job_id": job_id, "language": language},
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
