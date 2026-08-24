import asyncio

import pytest

from pocket48_summarizer.models import TranscriptSegment
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


class CompletingPipeline:
    def __init__(self, repository):
        self.repository = repository

    async def run(self, job_id):
        self.repository.replace_transcript(
            job_id,
            [
                TranscriptSegment(
                    sequence=1,
                    start_ms=0,
                    end_ms=1000,
                    text="自动翻译",
                )
            ],
        )
        self.repository.mark_completed(job_id)

    async def cleanup_failed_artifacts(self, job_id):
        return None


class CompletingTranslator:
    def __init__(self, repository):
        self.repository = repository

    async def translate_job(self, job_id, language):
        self.repository.save_transcript_translations(
            job_id,
            language,
            {1: "Automatic translation."},
        )
        return 1


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


@pytest.mark.asyncio
async def test_worker_automatically_translates_completed_job(
    settings, repository
):
    worker_settings = settings.model_copy(
        update={"worker_poll_seconds": 0.2}
    )
    job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            "index.html?id=990001"
        ),
        "990001",
    )
    worker = DurableWorker(
        worker_settings,
        repository,
        CompletingPipeline(repository),
        CompletingTranslator(repository),
    )

    await worker.start()
    worker.notify()
    for _ in range(100):
        translation = repository.get_subtitle_translation_request(job.id)
        if translation and translation.status == "completed":
            break
        await asyncio.sleep(0.02)
    await worker.stop()

    completed = repository.get_job(job.id)
    translation = repository.get_subtitle_translation_request(job.id)
    assert completed and completed.status == "completed"
    assert translation and translation.status == "completed"
    assert repository.get_transcript_translations(job.id) == {
        1: "Automatic translation."
    }


@pytest.mark.asyncio
async def test_translation_queue_failure_does_not_fail_completed_job(
    settings, repository, monkeypatch
):
    job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            "index.html?id=990002"
        ),
        "990002",
    )
    claimed = repository.claim_next_job("worker", 120)
    assert claimed and claimed.id == job.id
    worker = DurableWorker(
        settings,
        repository,
        CompletingPipeline(repository),
        CompletingTranslator(repository),
    )

    def fail_to_queue(job_id):
        raise RuntimeError("translation database unavailable")

    monkeypatch.setattr(
        repository,
        "request_subtitle_translation",
        fail_to_queue,
    )
    await worker._process_job(claimed)

    completed = repository.get_job(job.id)
    assert completed and completed.status == "completed"
