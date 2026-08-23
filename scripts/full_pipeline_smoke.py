from __future__ import annotations

import asyncio
import os
import sys

from pocket48_summarizer.config import Settings
from pocket48_summarizer.db import Database
from pocket48_summarizer.models import JobStatus
from pocket48_summarizer.repository import JobRepository
from pocket48_summarizer.security import parse_share_url
from pocket48_summarizer.services import build_services

CONFIRMATION = "I_UNDERSTAND_THIS_UPLOADS_AUDIO_AND_COSTS_MONEY"


async def run(url: str) -> None:
    settings = Settings()
    settings.require_processing_configuration()
    settings.prepare_directories()
    database = Database(settings.database_path)
    database.initialize()
    repository = JobRepository(database)
    services = build_services(settings, repository)
    normalized, live_id = parse_share_url(url)
    job, _ = repository.create_or_get_job(normalized, live_id)
    if job.status == JobStatus.FAILED and job.error_retryable:
        job = repository.retry_job(job.id)
    if job.status == JobStatus.COMPLETED:
        print({"job_id": job.id, "status": job.status, "result": "already complete"})
        await services.close()
        return
    await services.worker.start()
    services.worker.notify()
    try:
        while True:
            await asyncio.sleep(2)
            current = repository.get_job(job.id)
            if current is None:
                raise RuntimeError("Smoke-test job disappeared")
            print(
                {
                    "job_id": current.id,
                    "status": current.status,
                    "stage": current.stage,
                    "progress": current.progress_percent,
                    "message": current.progress_message,
                }
            )
            if current.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
                if current.status == JobStatus.FAILED:
                    raise RuntimeError(
                        f"{current.error_code}: {current.error_message}"
                    )
                break
    finally:
        await services.close()


def main() -> None:
    if os.environ.get("P48_RUN_PAID_SMOKE") != CONFIRMATION:
        raise SystemExit(
            "Refusing paid smoke test. Set P48_RUN_PAID_SMOKE="
            + CONFIRMATION
        )
    if len(sys.argv) != 2:
        raise SystemExit("Usage: full_pipeline_smoke.py <Pocket48 share URL>")
    asyncio.run(run(sys.argv[1]))


if __name__ == "__main__":
    main()
