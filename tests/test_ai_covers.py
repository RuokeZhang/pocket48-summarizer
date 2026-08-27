from pathlib import Path

import pytest

from pocket48_summarizer.errors import ExternalServiceError
from pocket48_summarizer.media.ai_covers import AICoverService
from pocket48_summarizer.media.cover_providers import GeneratedCoverImage


class FakeSeedreamProvider:
    def __init__(self, *, fail=False, fail_calls=()):
        self.fail = fail
        self.fail_calls = set(fail_calls)
        self.calls = []
        self.closed = False

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail or len(self.calls) in self.fail_calls:
            raise ExternalServiceError(
                "ai_cover_moderation_rejected",
                "Seedream 因内容安全审核拒绝了这张参考图",
                False,
            )
        return GeneratedCoverImage(
            content=b"\x89PNG\r\n\x1a\nprovider",
            content_type="image/png",
            provider_request_id=f"request-{len(self.calls)}",
        )

    async def close(self):
        self.closed = True


class FakeCoverFFmpeg:
    def __init__(self, *, fail_render_calls=()):
        self.ass_documents = []
        self.fail_render_calls = set(fail_render_calls)
        self.render_calls = 0

    async def supports_ass_filter(self):
        return True

    async def extract_cover_source_frame(
        self, manifest_url, output_path, timestamp_ms
    ):
        del manifest_url
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(f"source-{timestamp_ms}".encode())
        return output_path

    async def normalize_cover_image(
        self, input_path, output_path, *, width, height
    ):
        output_path.write_bytes(
            input_path.read_bytes() + f"-{width}x{height}".encode()
        )
        return output_path

    async def render_ai_cover_text(
        self, background_path, output_path, ass_path
    ):
        self.render_calls += 1
        if self.render_calls in self.fail_render_calls:
            raise ExternalServiceError(
                "ai_cover_text_render_failed",
                "AI 封面文字渲染失败",
                True,
            )
        document = ass_path.read_text(encoding="utf-8")
        self.ass_documents.append(document)
        output_path.write_bytes(
            background_path.read_bytes() + b"\n" + document.encode()
        )
        return output_path


class FakeCoverOSS:
    def __init__(self):
        self.objects = {}
        self.deleted = []

    def ai_cover_source_object_key(self, job_id, generation_id):
        return f"temporary/ai-cover-sources/{job_id}/{generation_id}/source.png"

    def ai_cover_object_key(
        self, job_id, generation_id, orientation, kind
    ):
        return (
            f"covers/{job_id}/{generation_id}/"
            f"{orientation}-{kind}.png"
        )

    async def upload_ai_cover_image(self, path: Path, key: str):
        self.objects[key] = path.read_bytes()

    async def signed_ai_cover_source_url(self, key):
        return f"https://oss.example/{key}?signed=source"

    async def signed_ai_cover_url(self, key):
        return f"https://oss.example/{key}?signed=download"

    async def download_ai_cover_image(self, key, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[key])

    async def delete(self, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


def cover_settings(settings):
    return settings.model_copy(
        update={
            "ark_seedream_model": "doubao-seedream-test",
            "ai_cover_landscape_width": 2560,
            "ai_cover_landscape_height": 1440,
            "ai_cover_four_three_width": 2048,
            "ai_cover_four_three_height": 1536,
        }
    )


def test_ai_cover_settings_require_exact_output_ratios(settings):
    payload = settings.model_dump()
    with pytest.raises(ValueError, match="16:9"):
        type(settings).model_validate(
            {
                **payload,
                "ai_cover_landscape_width": 2000,
            }
        )
    with pytest.raises(ValueError, match="4:3"):
        type(settings).model_validate(
            {
                **payload,
                "ai_cover_four_three_height": 1400,
            }
        )


@pytest.mark.asyncio
async def test_ai_cover_service_generates_pair_and_rerenders_text(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991201",
        "991201",
    )
    provider = FakeSeedreamProvider()
    oss = FakeCoverOSS()
    ffmpeg = FakeCoverFFmpeg()
    stale_path = settings.data_dir / "ai-covers" / "stale" / "source.png"
    stale_path.parent.mkdir(parents=True)
    stale_path.write_bytes(b"stale")
    service = AICoverService(
        cover_settings(settings),
        repository,
        oss,  # type: ignore[arg-type]
        provider,
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )
    await service.startup()
    assert not stale_path.exists()

    generation = service.start_generation(
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,  # type: ignore[arg-type]
        request_id="ai-cover-request-1",
        source_timestamp_ms=12_300,
        layout_style="sticker_pop",
        title_text="第一版标题",
        highlight_text="第一版重点",
        extra_text=["名场面"],
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
    )
    await service._tasks[generation.id]

    completed = repository.get_ai_cover_generation(job.id, generation.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.layout_style == "sticker_pop"
    assert completed.highlight_text == "第一版重点"
    assert [
        (call["width"], call["height"]) for call in provider.calls
    ] == [(2560, 1440), (2048, 1536)]
    assert all("Do not generate any" in call["prompt"] for call in provider.calls)
    assets = repository.list_ai_cover_assets(generation.id)
    assert {asset.status for asset in assets} == {"completed"}
    assert {asset.orientation for asset in assets} == {
        "landscape",
        "four_three",
    }
    assert any("第一版标题" in item for item in ffmpeg.ass_documents)
    assert oss.deleted == [
        (
            "temporary/ai-cover-sources/"
            f"{job.id}/{generation.id}/source.png"
        )
    ]

    updated = service.update_text(
        job_id=job.id,
        generation_id=generation.id,
        layout_style="banner_energy",
        title_text="第二版标题",
        highlight_text="第二版重点",
        extra_text=["全场爆笑", "高能"],
    )
    await service._tasks[updated.id]

    assert len(provider.calls) == 2
    completed = repository.get_ai_cover_generation(job.id, generation.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.layout_style == "banner_energy"
    assert completed.title_text == "第二版标题"
    assert completed.highlight_text == "第二版重点"
    assert completed.extra_text == ["全场爆笑", "高能"]
    assert {asset.text_revision for asset in (
        repository.list_ai_cover_assets(generation.id)
    )} == {1}
    assert any("第二版标题" in item for item in ffmpeg.ass_documents)
    await service.close()
    assert provider.closed


@pytest.mark.asyncio
async def test_ai_cover_retry_reuses_background_after_text_failure(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991204",
        "991204",
    )
    provider = FakeSeedreamProvider()
    oss = FakeCoverOSS()
    ffmpeg = FakeCoverFFmpeg(fail_render_calls={3})
    service = AICoverService(
        cover_settings(settings),
        repository,
        oss,  # type: ignore[arg-type]
        provider,
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )

    generation = service.start_generation(
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,  # type: ignore[arg-type]
        request_id="ai-cover-local-retry",
        source_timestamp_ms=18_000,
        title_text="初始标题",
        extra_text=[],
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
    )
    await service._tasks[generation.id]
    assert len(provider.calls) == 2

    updated = service.update_text(
        job_id=job.id,
        generation_id=generation.id,
        layout_style="banner_energy",
        title_text="更新标题",
        highlight_text="重点",
        extra_text=["补充文字"],
    )
    await service._tasks[updated.id]
    failed = repository.get_ai_cover_generation(job.id, generation.id)
    assert failed is not None
    assert failed.status == "failed"
    assert len(provider.calls) == 2
    assert all(
        asset.background_oss_object_key
        for asset in repository.list_ai_cover_assets(generation.id)
    )

    retried = service.retry_generation(
        job_id=job.id,
        generation_id=generation.id,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
    )
    await service._tasks[retried.id]
    completed = repository.get_ai_cover_generation(
        job.id, generation.id
    )
    assert completed is not None
    assert completed.status == "completed"
    assert len(provider.calls) == 2
    assert len(oss.deleted) == 1
    await service.close()


@pytest.mark.asyncio
async def test_ai_cover_service_records_moderation_failure_and_cleans_source(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991202",
        "991202",
    )
    provider = FakeSeedreamProvider(fail=True)
    oss = FakeCoverOSS()
    service = AICoverService(
        cover_settings(settings),
        repository,
        oss,  # type: ignore[arg-type]
        provider,
        ffmpeg=FakeCoverFFmpeg(),  # type: ignore[arg-type]
    )

    generation = service.start_generation(
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,  # type: ignore[arg-type]
        request_id="ai-cover-request-failed",
        source_timestamp_ms=12_300,
        title_text="审核测试",
        extra_text=[],
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
    )
    await service._tasks[generation.id]

    failed = repository.get_ai_cover_generation(job.id, generation.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_code == "ai_cover_moderation_rejected"
    assert {
        asset.status
        for asset in repository.list_ai_cover_assets(generation.id)
    } == {"failed"}
    assert oss.deleted == [
        (
            "temporary/ai-cover-sources/"
            f"{job.id}/{generation.id}/source.png"
        )
    ]
    await service.close()


@pytest.mark.asyncio
async def test_ai_cover_service_preserves_successful_asset_on_partial_failure(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991203",
        "991203",
    )
    provider = FakeSeedreamProvider(fail_calls={1})
    oss = FakeCoverOSS()
    service = AICoverService(
        cover_settings(settings),
        repository,
        oss,  # type: ignore[arg-type]
        provider,
        ffmpeg=FakeCoverFFmpeg(),  # type: ignore[arg-type]
    )

    generation = service.start_generation(
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,  # type: ignore[arg-type]
        request_id="ai-cover-request-partial",
        source_timestamp_ms=15_000,
        title_text="保留成功比例",
        extra_text=[],
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
    )
    await service._tasks[generation.id]

    failed = repository.get_ai_cover_generation(job.id, generation.id)
    assert failed is not None
    assert failed.status == "failed"
    assets = {
        asset.orientation: asset
        for asset in repository.list_ai_cover_assets(generation.id)
    }
    assert assets["landscape"].status == "failed"
    assert assets["four_three"].status == "completed"
    assert assets["four_three"].final_oss_object_key in oss.objects
    assert len(provider.calls) == 2
    assert oss.deleted == [
        (
            "temporary/ai-cover-sources/"
            f"{job.id}/{generation.id}/source.png"
        )
    ]
    await service.close()
