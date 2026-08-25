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


def test_clip_exports_keep_versions_and_deduplicate_request(repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=876501",
        "876501",
    )
    first, created = repository.begin_video_clip_export(
        clip_id="clip-1",
        job_id=job.id,
        timeline_index=0,
        timeline_title="第一段",
        requested_by_user_id=None,
        request_id="request-1",
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="zh",
        include_danmaku=False,
        render_version="ass-v1",
        filename="clip-1.mp4",
    )
    duplicate, duplicate_created = repository.begin_video_clip_export(
        clip_id="clip-duplicate",
        job_id=job.id,
        timeline_index=0,
        timeline_title="第一段",
        requested_by_user_id=None,
        request_id="request-1",
        start_ms=2000,
        end_ms=6000,
        subtitle_mode="off",
        include_danmaku=True,
        render_version="ass-v1",
        filename="clip-duplicate.mp4",
    )
    second, second_created = repository.begin_video_clip_export(
        clip_id="clip-2",
        job_id=job.id,
        timeline_index=0,
        timeline_title="第一段",
        requested_by_user_id=None,
        request_id="request-2",
        start_ms=1500,
        end_ms=5500,
        subtitle_mode="bilingual",
        include_danmaku=True,
        render_version="ass-v1",
        filename="clip-2.mp4",
    )

    assert created
    assert not duplicate_created
    assert second_created
    assert duplicate.id == first.id
    assert duplicate.start_ms == first.start_ms
    assert [item.id for item in repository.list_video_clip_exports(job.id)] == [
        second.id,
        first.id,
    ]

    repository.complete_video_clip_export(
        first.id, "clips/clip-1.mp4", "所选范围没有弹幕"
    )
    completed = repository.get_video_clip_export(job.id, first.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.warning_message == "所选范围没有弹幕"
    assert (
        repository.get_latest_video_clip_export(
            job.id, 0, completed_only=True
        ).id
        == first.id
    )
    unchanged = repository.retry_video_clip_export(job.id, first.id)
    assert unchanged.status == "completed"
    assert unchanged.oss_object_key == "clips/clip-1.mp4"


def test_clip_export_retry_and_boundary_cache(repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=876502",
        "876502",
    )
    export, _ = repository.begin_video_clip_export(
        clip_id="clip-retry",
        job_id=job.id,
        timeline_index=1,
        timeline_title="第二段",
        requested_by_user_id=None,
        request_id="request-retry",
        start_ms=10_000,
        end_ms=20_000,
        subtitle_mode="off",
        include_danmaku=False,
        render_version="ass-v1",
        filename="clip-retry.mp4",
    )
    repository.fail_video_clip_export(export.id, "失败")
    retried = repository.retry_video_clip_export(job.id, export.id)
    assert retried.status == "running"
    assert retried.error_message is None
    assert repository.recover_running_video_clip_exports() == 1

    suggestion = repository.save_clip_boundary_suggestion(
        job_id=job.id,
        cache_key="cache-1",
        boundary_kind="start",
        segment_sequence=7,
        anchor_ms=12_000,
        suggested_ms=11_850,
        silence_start_ms=11_300,
        silence_end_ms=11_850,
        analysis_version="silence-v1",
    )
    repeated = repository.save_clip_boundary_suggestion(
        job_id=job.id,
        cache_key="cache-1",
        boundary_kind="start",
        segment_sequence=7,
        anchor_ms=12_000,
        suggested_ms=11_900,
        silence_start_ms=11_400,
        silence_end_ms=11_900,
        analysis_version="silence-v1",
    )
    assert repeated == suggestion
    assert (
        repository.get_clip_boundary_suggestion(job.id, "cache-1")
        == suggestion
    )
