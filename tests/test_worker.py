import asyncio

import pytest

from pocket48_summarizer.worker import DurableWorker


class IdleRepository:
    def __init__(self):
        self.claims = 0

    def recover_expired_jobs(self):
        return 0

    def claim_next_job(self, worker_id, lease_seconds):
        self.claims += 1
        return None

    def list_failed_artifact_jobs(self, cutoff):
        return []


class IdlePipeline:
    async def cleanup_failed_artifacts(self, job_id):
        return None


class BrokenRepository(IdleRepository):
    def claim_next_job(self, worker_id, lease_seconds):
        raise RuntimeError("database unavailable")


@pytest.mark.asyncio
async def test_worker_maintenance_file_pauses_job_claims(settings):
    worker_settings = settings.model_copy(
        update={"worker_poll_seconds": 0.2}
    )
    repository = IdleRepository()
    worker = DurableWorker(worker_settings, repository, IdlePipeline())
    worker_settings.worker_maintenance_path.touch()

    await worker.start()
    await asyncio.sleep(worker_settings.worker_poll_seconds * 1.5)
    assert repository.claims == 0

    worker_settings.worker_maintenance_path.unlink()
    worker.notify()
    await asyncio.sleep(worker_settings.worker_poll_seconds * 1.5)
    await worker.stop()

    assert repository.claims > 0


@pytest.mark.asyncio
async def test_worker_wait_surfaces_background_failure(settings):
    worker = DurableWorker(
        settings,
        BrokenRepository(),
        IdlePipeline(),
    )

    await worker.start()
    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker.wait()
    await worker.stop()
