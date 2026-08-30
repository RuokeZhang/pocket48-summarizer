from datetime import UTC, datetime, timedelta

from pocket48_summarizer.models import ClipRange, JobStage, JobStatus


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
        kept_ranges=[
            ClipRange(start_ms=1000, end_ms=2500),
            ClipRange(start_ms=3500, end_ms=5000),
        ],
        subtitle_mode="zh",
        include_danmaku=False,
        subtitle_font_scale=125,
        subtitle_text_color="#E43D12",
        subtitle_background_color="#EBE9E1",
        output_layout="landscape",
        subtitle_font_family="serif",
        landscape_theme="matcha",
        cover_enabled=True,
        cover_timestamp_ms=2500,
        cover_title="第一段封面",
        cover_style="display",
        render_version="ass-v2",
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
    assert duplicate.subtitle_font_scale == 125
    assert duplicate.subtitle_text_color == "#E43D12"
    assert duplicate.subtitle_background_color == "#EBE9E1"
    assert duplicate.output_layout == "landscape"
    assert duplicate.subtitle_font_family == "serif"
    assert duplicate.landscape_theme == "matcha"
    assert duplicate.cover_enabled
    assert duplicate.cover_timestamp_ms == 2500
    assert duplicate.cover_title == "第一段封面"
    assert duplicate.cover_style == "display"
    assert duplicate.kept_ranges == [
        ClipRange(start_ms=1000, end_ms=2500),
        ClipRange(start_ms=3500, end_ms=5000),
    ]
    with repository.database.connect() as connection:
        stored_scale = connection.execute(
            """
            SELECT subtitle_font_scale, subtitle_font_percent
            FROM video_clip_exports
            WHERE id = ?
            """,
            (first.id,),
        ).fetchone()
    assert stored_scale["subtitle_font_scale"] == 160
    assert stored_scale["subtitle_font_percent"] == 125
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


def test_ai_cover_generations_keep_paired_assets_and_text_revisions(
    repository,
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=876502",
        "876502",
    )
    generation, created = repository.begin_ai_cover_generation(
        generation_id="cover-1",
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,
        request_id="cover-request-1",
        source_timestamp_ms=12_300,
        provider="seedream",
        model="seedream-test",
        prompt_version="variety-v1",
        prompt_template="测试提示词 {title}",
        shared_seed=42,
        layout_style="editorial_arc",
        title_text="第一段封面",
        highlight_text="重点文字",
        extra_text=["名场面"],
        landscape_size=(1920, 1080),
        four_three_size=(1600, 1200),
    )
    duplicate, duplicate_created = repository.begin_ai_cover_generation(
        generation_id="cover-duplicate",
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,
        request_id="cover-request-1",
        source_timestamp_ms=99_999,
        provider="seedream",
        model="other-model",
        prompt_version="other-prompt",
        prompt_template="测试提示词 {title}",
        shared_seed=None,
        layout_style="banner_energy",
        title_text="不会覆盖",
        highlight_text="不会覆盖重点",
        extra_text=[],
        landscape_size=(1280, 720),
        four_three_size=(1200, 900),
    )

    assert created
    assert not duplicate_created
    assert duplicate.id == generation.id
    assert duplicate.source_timestamp_ms == 12_300
    assert duplicate.layout_style == "editorial_arc"
    assert duplicate.highlight_text == "重点文字"
    assert duplicate.extra_text == ["名场面"]
    assets = repository.list_ai_cover_assets(generation.id)
    assert [
        (asset.orientation, asset.width, asset.height, asset.status)
        for asset in assets
    ] == [
        ("landscape", 1920, 1080, "queued"),
        ("four_three", 1600, 1200, "queued"),
    ]

    repository.mark_ai_cover_generation_running(generation.id)
    repository.mark_ai_cover_asset_running(assets[0].id)
    repository.fail_ai_cover_asset(
        assets[0].id,
        "temporary_failure",
        "第一张暂时失败",
    )
    still_running = repository.get_ai_cover_generation(
        job.id, generation.id
    )
    assert still_running is not None
    assert still_running.status == "running"
    for asset in assets:
        repository.mark_ai_cover_asset_running(asset.id)
        repository.complete_ai_cover_asset(
            asset.id,
            background_oss_object_key=(
                f"covers/{generation.id}/{asset.orientation}-background.png"
            ),
            final_oss_object_key=(
                f"covers/{generation.id}/{asset.orientation}-final.png"
            ),
            background_sha256=f"background-{asset.orientation}",
            final_sha256=f"final-{asset.orientation}",
            provider_request_id=f"request-{asset.orientation}",
        )

    completed = repository.get_ai_cover_generation(job.id, generation.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.completed_at is not None

    assert completed.prompt_template == "测试提示词 {title}"

    completed = repository.get_ai_cover_generation(job.id, generation.id)
    assert completed is not None
    assert completed.status == "completed"
    assert [
        item.id
        for item in repository.list_ai_cover_generations(job.id)
    ] == [generation.id]


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
