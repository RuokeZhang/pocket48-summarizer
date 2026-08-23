from datetime import UTC, datetime, timedelta

from pocket48_summarizer.models import JobStage, JobStatus


def test_job_claim_failure_and_retry(repository):
    job, created = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=123456",
        "123456",
    )
    assert created
    claimed = repository.claim_next_job("worker", 120)
    assert claimed is not None
    assert claimed.status == JobStatus.RUNNING
    repository.set_stage(claimed.id, JobStage.RESOLVING, 5, "解析中")
    repository.mark_failed(claimed.id, "temporary", "暂时失败", True)
    failed = repository.get_job(claimed.id)
    assert failed and failed.status == JobStatus.FAILED
    retried = repository.retry_job(claimed.id)
    assert retried.status == JobStatus.QUEUED
    assert retried.retry_count == 1


def test_recovers_expired_worker_lease(repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=654321",
        "654321",
    )
    repository.claim_next_job("worker", 120)
    expired = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with repository.database.connect() as connection:
        connection.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
            (expired, job.id),
        )
    assert repository.recover_expired_jobs() == 1
    recovered = repository.get_job(job.id)
    assert recovered and recovered.status == JobStatus.QUEUED


def test_releases_owned_job_on_shutdown(repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=765432",
        "765432",
    )
    repository.claim_next_job("worker", 120)
    repository.release_owned_job(job.id, "worker")
    released = repository.get_job(job.id)
    assert released and released.status == JobStatus.QUEUED
    assert released.worker_id is None
