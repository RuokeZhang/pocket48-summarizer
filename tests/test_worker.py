import asyncio

import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.models import TranscriptSegment
from pocket48_summarizer.translation import SubtitleTranslationRunResult
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

    async def translate_job(self, job_id, language, should_pause=None):
        self.repository.save_transcript_translations(
            job_id,
            language,
            {1: "Automatic translation."},
        )
        return SubtitleTranslationRunResult(
            translated_count=1,
            completed=True,
        )


class PausingTranslator:
    async def translate_job(self, job_id, language, should_pause=None):
        assert should_pause is not None
        assert should_pause() is True
        return SubtitleTranslationRunResult(
            translated_count=0,
            completed=False,
        )


class RecordingMemberCatalog:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    async def sync_if_due(self):
        self.calls += 1
        if self.fail:
            raise AppError(
                "member_catalog_transport_error",
                "catalog unavailable",
                True,
            )


class RecordingVocabulary:
    def __init__(self):
        self.calls = 0

    async def ensure_current(self):
        self.calls += 1
        return None


@pytest.mark.asyncio
async def test_worker_refreshes_catalog_without_blocking_job_claims(settings):
    worker_settings = settings.model_copy(
        update={"worker_poll_seconds": 0.05}
    )
    repository = IdleRepository()
    member_catalog = RecordingMemberCatalog(fail=True)
    vocabulary = RecordingVocabulary()
    worker = DurableWorker(
        worker_settings,
        repository,
        IdlePipeline(),
        member_catalog=member_catalog,
        vocabulary=vocabulary,
    )

    await worker.start()
    await asyncio.sleep(0.15)
    await worker.stop()

    assert member_catalog.calls == 1
    assert vocabulary.calls == 1
    assert repository.claims > 0


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


@pytest.mark.asyncio
async def test_translation_pauses_without_consuming_retry_for_queued_job(
    settings, repository
):
    translated_job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            "index.html?id=990003"
        ),
        "990003",
    )
    claimed_job = repository.claim_next_job("main-worker", 120)
    assert claimed_job and claimed_job.id == translated_job.id
    repository.replace_transcript(
        translated_job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=1000,
                text="自动翻译",
            )
        ],
    )
    repository.mark_completed(translated_job.id)
    repository.request_subtitle_translation(translated_job.id)

    worker = DurableWorker(
        settings,
        repository,
        IdlePipeline(),
        PausingTranslator(),
    )
    request = repository.claim_next_subtitle_translation(
        worker.worker_id,
        settings.worker_lease_seconds,
    )
    assert request and request.retry_count == 1
    queued_job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            "index.html?id=990004"
        ),
        "990004",
    )

    await worker._process_translation(request)

    paused = repository.get_subtitle_translation_request(translated_job.id)
    assert paused and paused.status == "queued"
    assert paused.retry_count == 0
    claimed_queued_job = repository.claim_next_job(
        worker.worker_id,
        settings.worker_lease_seconds,
    )
    assert claimed_queued_job and claimed_queued_job.id == queued_job.id
