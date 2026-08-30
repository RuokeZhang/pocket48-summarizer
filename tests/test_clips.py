import asyncio
import hashlib
from pathlib import Path

import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.clips import VideoClipService
from pocket48_summarizer.media.ffmpeg import VideoDimensions
from pocket48_summarizer.models import ClipRange, TranscriptSegment


class FakeFFmpeg:
    async def clip_video(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
        ass_path: Path | None = None,
        output_layout: str = "portrait",
        landscape_theme: str = "cream",
        cover_path: Path | None = None,
        cover_dimensions: VideoDimensions | None = None,
    ) -> Path:
        del ass_path, output_layout, cover_path, cover_dimensions
        del landscape_theme
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return output_path

    async def render_cover_frame(
        self,
        manifest_url: str,
        output_path: Path,
        timestamp_ms: int,
        ass_path: Path,
        output_layout: str = "portrait",
        landscape_theme: str = "cream",
    ) -> Path:
        del manifest_url, timestamp_ms, ass_path, output_layout
        del landscape_theme
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"cover")
        return output_path

    async def prepend_cover(
        self,
        cover_path: Path,
        clip_path: Path,
        output_path: Path,
        *,
        duration_ms: int,
    ) -> Path:
        del duration_ms
        output_path.write_bytes(
            cover_path.read_bytes() + clip_path.read_bytes()
        )
        return output_path

    async def concat_clips(
        self,
        input_paths: list[Path],
        output_path: Path,
    ) -> Path:
        output_path.write_bytes(
            b"".join(path.read_bytes() for path in input_paths)
        )
        return output_path


class FakeOSS:
    def __init__(self, *, cover_bytes: bytes = b"ai-cover"):
        self.uploads = []
        self.cover_downloads = []
        self.cover_bytes = cover_bytes

    def clip_object_key(self, job_id: str, filename: str) -> str:
        return f"clips/{job_id}/{filename}"

    async def upload_clip(
        self, path: Path, key: str, filename: str
    ) -> None:
        self.uploads.append((key, filename, path.read_bytes()))

    async def signed_clip_url(self, key: str) -> str:
        return f"https://oss.example/{key}?signed=1"

    async def download_ai_cover_image(
        self, key: str, path: Path
    ) -> None:
        self.cover_downloads.append(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.cover_bytes)


class FlakyOSS(FakeOSS):
    def __init__(self):
        super().__init__()
        self.attempts = 0

    async def upload_clip(
        self, path: Path, key: str, filename: str
    ) -> None:
        self.attempts += 1
        if self.attempts == 1:
            raise AppError("oss_upload_failed", "temporary OSS failure", True)
        await super().upload_clip(path, key, filename)


class OverlayFFmpeg(FakeFFmpeg):
    def __init__(self):
        self.ass_content = ""
        self.cover_ass_content = ""
        self.cover_timestamp_ms = None
        self.cover_duration_ms = None
        self.output_layout = ""
        self.ai_cover_bytes = None
        self.ai_cover_dimensions = None

    async def supports_ass_filter(self) -> bool:
        return True

    async def probe_video_dimensions(
        self, manifest_url: str
    ) -> VideoDimensions:
        return VideoDimensions(width=1080, height=1920)

    async def clip_video(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
        ass_path: Path | None = None,
        output_layout: str = "portrait",
        landscape_theme: str = "cream",
        cover_path: Path | None = None,
        cover_dimensions: VideoDimensions | None = None,
    ) -> Path:
        if ass_path is not None:
            self.ass_content = ass_path.read_text(encoding="utf-8")
        self.output_layout = output_layout
        self.ai_cover_bytes = (
            cover_path.read_bytes() if cover_path is not None else None
        )
        self.ai_cover_dimensions = cover_dimensions
        return await super().clip_video(
            manifest_url,
            output_path,
            start_ms,
            end_ms,
            output_layout=output_layout,
            landscape_theme=landscape_theme,
            cover_path=cover_path,
            cover_dimensions=cover_dimensions,
        )

    async def render_cover_frame(
        self,
        manifest_url: str,
        output_path: Path,
        timestamp_ms: int,
        ass_path: Path,
        output_layout: str = "portrait",
        landscape_theme: str = "cream",
    ) -> Path:
        self.cover_ass_content = ass_path.read_text(encoding="utf-8")
        self.cover_timestamp_ms = timestamp_ms
        self.output_layout = output_layout
        return await super().render_cover_frame(
            manifest_url,
            output_path,
            timestamp_ms,
            ass_path,
            output_layout,
            landscape_theme,
        )

    async def prepend_cover(
        self,
        cover_path: Path,
        clip_path: Path,
        output_path: Path,
        *,
        duration_ms: int,
    ) -> Path:
        self.cover_duration_ms = duration_ms
        return await super().prepend_cover(
            cover_path,
            clip_path,
            output_path,
            duration_ms=duration_ms,
        )


class MultiRangeFFmpeg(OverlayFFmpeg):
    def __init__(self):
        super().__init__()
        self.clip_calls = []
        self.ass_documents = []
        self.concat_inputs = []

    async def clip_video(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
        ass_path: Path | None = None,
        output_layout: str = "portrait",
        landscape_theme: str = "cream",
        cover_path: Path | None = None,
        cover_dimensions: VideoDimensions | None = None,
    ) -> Path:
        self.clip_calls.append((start_ms, end_ms))
        if ass_path is not None:
            self.ass_documents.append(
                ass_path.read_text(encoding="utf-8")
            )
        return await FakeFFmpeg.clip_video(
            self,
            manifest_url,
            output_path,
            start_ms,
            end_ms,
            ass_path,
            output_layout=output_layout,
            landscape_theme=landscape_theme,
            cover_path=cover_path,
            cover_dimensions=cover_dimensions,
        )

    async def concat_clips(
        self,
        input_paths: list[Path],
        output_path: Path,
    ) -> Path:
        self.concat_inputs = list(input_paths)
        return await super().concat_clips(input_paths, output_path)


class SlowFFmpeg:
    async def clip_video(self, *args, **kwargs):
        await asyncio.Event().wait()


class FlakyFFmpeg(FakeFFmpeg):
    def __init__(self):
        self.calls = 0

    async def clip_video(self, *args, **kwargs):
        self.calls += 1
        if self.calls < 3:
            raise AppError(
                "video_clip_failed",
                "temporary HLS failure",
                True,
            )
        return await super().clip_video(*args, **kwargs)


@pytest.mark.asyncio
async def test_clip_uploads_to_oss_persists_and_removes_local_file(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991100",
        "991100",
    )
    oss = FakeOSS()
    service = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
    )

    state = service.start(
        job_id=job.id,
        timeline_index=0,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
    )
    await service._tasks[state.clip_id]

    record = repository.get_video_clip_export(job.id, state.clip_id)
    assert state.status == "completed"
    assert state.oss_object_key == f"clips/{job.id}/{state.output_path.name}"
    assert not state.output_path.exists()
    assert oss.uploads == [
        (state.oss_object_key, state.output_path.name, b"video")
    ]
    assert record is not None
    assert record.status == "completed"
    assert record.oss_object_key == state.oss_object_key

    restored = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
    ).get(
        job_id=job.id,
        timeline_index=0,
        start_ms=1000,
        end_ms=5000,
    )
    assert restored is not None
    assert restored.status == "completed"
    assert (
        await service.signed_download_url(restored)
        == f"https://oss.example/{record.oss_object_key}?signed=1"
    )


@pytest.mark.asyncio
async def test_startup_migrates_existing_local_clip(settings, repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991101",
        "991101",
    )
    oss = FakeOSS()
    service = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=FakeFFmpeg(),  # type: ignore[arg-type]
    )
    path = (
        settings.data_dir
        / "clips"
        / job.id
        / "timeline-01-10-20.mp4"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(b"legacy video")

    await service.startup()
    await next(iter(service._tasks.values()))

    record = repository.get_latest_video_clip_export(job.id, 0)
    assert record is not None
    assert record.status == "completed"
    assert oss.uploads[0][2] == b"legacy video"
    assert not path.exists()


@pytest.mark.asyncio
async def test_shutdown_marks_running_clip_failed(settings, repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991102",
        "991102",
    )
    service = VideoClipService(
        settings,
        repository,
        FakeOSS(),  # type: ignore[arg-type]
        ffmpeg=SlowFFmpeg(),  # type: ignore[arg-type]
    )
    service.start(
        job_id=job.id,
        timeline_index=0,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
    )

    await asyncio.sleep(0)
    await service.close()

    record = repository.get_latest_video_clip_export(job.id, 0)
    assert record is not None
    assert record.status == "failed"


@pytest.mark.asyncio
async def test_transient_clip_failure_is_retried(settings, repository):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991103",
        "991103",
    )
    ffmpeg = FlakyFFmpeg()
    service = VideoClipService(
        settings.model_copy(
            update={
                "clip_retry_attempts": 3,
                "clip_retry_delay_seconds": 0,
            }
        ),
        repository,
        FakeOSS(),  # type: ignore[arg-type]
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )

    state = service.start(
        job_id=job.id,
        timeline_index=0,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
    )
    await service._tasks[state.clip_id]

    assert ffmpeg.calls == 3
    assert state.status == "completed"
    assert (
        repository.get_video_clip_export(job.id, state.clip_id).status
        == "completed"
    )


@pytest.mark.asyncio
async def test_overlay_export_keeps_warning_across_upload_retry(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991104",
        "991104",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=1000,
                end_ms=4000,
                text="测试字幕",
            )
        ],
    )
    oss = FlakyOSS()
    ffmpeg = OverlayFFmpeg()
    service = VideoClipService(
        settings.model_copy(
            update={
                "clip_retry_attempts": 2,
                "clip_retry_delay_seconds": 0,
            }
        ),
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )

    record = service.start_export(
        job_id=job.id,
        timeline_index=0,
        timeline_title="测试",
        requested_by_user_id=None,
        request_id="overlay-request",
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="zh",
        include_danmaku=True,
        subtitle_font_scale=125,
    )
    await service._tasks[record.id]

    completed = repository.get_video_clip_export(job.id, record.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.warning_message == "所选范围没有可渲染的弹幕"
    assert oss.attempts == 2
    assert "[Events]" in ffmpeg.ass_content
    assert "测试字幕" in ffmpeg.ass_content
    assert (
        "Style: SubtitleZh,Noto Sans CJK SC,131,"
        "&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,7,0,2,"
        in ffmpeg.ass_content
    )


@pytest.mark.asyncio
async def test_landscape_export_uses_fixed_canvas_overlay(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991105",
        "991105",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=1000,
                end_ms=4000,
                text="横屏字幕",
            )
        ],
    )
    ffmpeg = OverlayFFmpeg()
    service = VideoClipService(
        settings,
        repository,
        FakeOSS(),  # type: ignore[arg-type]
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )

    record = service.start_export(
        job_id=job.id,
        timeline_index=0,
        timeline_title="横屏测试",
        requested_by_user_id=None,
        request_id="landscape-request",
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="zh",
        include_danmaku=False,
        output_layout="landscape",
        subtitle_font_family="serif",
    )
    await service._tasks[record.id]

    completed = repository.get_video_clip_export(job.id, record.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.output_layout == "landscape"
    assert completed.subtitle_font_family == "serif"
    assert ffmpeg.output_layout == "landscape"
    assert "PlayResX: 1920" in ffmpeg.ass_content
    assert "Style: LandscapeSubtitleZh,Noto Serif CJK SC,52," in (
        ffmpeg.ass_content
    )


@pytest.mark.asyncio
async def test_landscape_export_prepends_selected_custom_cover(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991106",
        "991106",
    )
    ffmpeg = OverlayFFmpeg()
    oss = FakeOSS()
    service = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )

    export_kwargs = dict(
        job_id=job.id,
        timeline_index=0,
        timeline_title="封面测试",
        requested_by_user_id=None,
        request_id="cover-request",
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="off",
        include_danmaku=False,
        output_layout="landscape",
        cover_enabled=True,
        cover_timestamp_ms=2300,
        cover_title="灯光亮起时",
        cover_style="badge",
    )
    record = service.start_export(**export_kwargs)
    await service._tasks[record.id]

    completed = repository.get_video_clip_export(job.id, record.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.cover_enabled
    assert completed.cover_timestamp_ms == 2300
    assert completed.cover_title == "灯光亮起时"
    assert completed.cover_style == "badge"
    assert ffmpeg.cover_timestamp_ms == 2300
    assert ffmpeg.cover_duration_ms == 1500
    assert "灯光亮起时" in ffmpeg.cover_ass_content
    assert r"\pos(692,670)\p1" in ffmpeg.cover_ass_content
    assert oss.uploads[0][2] == b"covervideo"


@pytest.mark.asyncio
async def test_ai_cover_replaces_only_first_encoded_frame(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991109",
        "991109",
    )
    generation, _ = repository.begin_ai_cover_generation(
        generation_id="ai-cover-clip-1",
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,
        request_id="ai-cover-clip-request-1",
        source_timestamp_ms=2300,
        provider="seedream",
        model="seedream-test",
        prompt_version="variety-v1",
        prompt_template="测试提示词 {title}",
        shared_seed=42,
        title_text="灯光亮起时",
        extra_text=[],
        landscape_size=(2560, 1440),
        four_three_size=(2048, 1536),
    )
    repository.mark_ai_cover_generation_running(generation.id)
    for asset in repository.list_ai_cover_assets(generation.id):
        repository.mark_ai_cover_asset_running(asset.id)
        repository.complete_ai_cover_asset(
            asset.id,
            background_oss_object_key=(
                f"covers/{asset.orientation}-background.png"
            ),
            final_oss_object_key=f"covers/{asset.orientation}-final.png",
            background_sha256=f"background-{asset.orientation}",
            final_sha256=hashlib.sha256(b"ai-cover").hexdigest(),
        )
    ffmpeg = OverlayFFmpeg()
    oss = FakeOSS()
    service = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )

    export_kwargs = dict(
        job_id=job.id,
        timeline_index=0,
        timeline_title="封面测试",
        requested_by_user_id=None,
        request_id="ai-cover-export-request",
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="off",
        include_danmaku=False,
        output_layout="landscape",
        ai_cover_generation_id=generation.id,
    )
    record = service.start_export(**export_kwargs)
    duplicate = service.start_export(**export_kwargs)
    assert duplicate.id == record.id
    for asset in repository.list_ai_cover_assets(generation.id):
        repository.complete_ai_cover_asset(
            asset.id,
            background_oss_object_key=(
                asset.background_oss_object_key or "covers/background.png"
            ),
            final_oss_object_key=(
                f"covers/{asset.orientation}-final-r1.png"
            ),
            background_sha256=asset.background_sha256 or "background",
            final_sha256=hashlib.sha256(b"ai-cover").hexdigest(),
        )
    await service._tasks[record.id]

    completed = repository.get_video_clip_export(job.id, record.id)
    assert completed is not None
    assert completed.status == "completed"
    assert not completed.cover_enabled
    assert completed.ai_cover_generation_id == generation.id
    assert completed.ai_cover_asset_id is not None
    assert completed.ai_cover_text_revision == 0
    assert completed.ai_cover_final_oss_object_key == (
        "covers/landscape-final.png"
    )
    assert ffmpeg.ai_cover_bytes == b"ai-cover"
    assert ffmpeg.ai_cover_dimensions == VideoDimensions(
        width=1920,
        height=1080,
    )
    assert ffmpeg.cover_duration_ms is None
    assert oss.cover_downloads == ["covers/landscape-final.png"]
    assert oss.uploads[0][2] == b"video"
    with pytest.raises(AppError, match="只能用于横屏成片"):
        service.start_export(
            **{
                **export_kwargs,
                "request_id": "ai-cover-portrait-export",
                "output_layout": "portrait",
            }
        )
    await service.close()


@pytest.mark.asyncio
async def test_ai_cover_export_rejects_hash_mismatch(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991110",
        "991110",
    )
    generation, _ = repository.begin_ai_cover_generation(
        generation_id="ai-cover-clip-hash",
        job_id=job.id,
        timeline_index=0,
        requested_by_user_id=None,
        request_id="ai-cover-clip-request-hash",
        source_timestamp_ms=2300,
        provider="seedream",
        model="seedream-test",
        prompt_version="variety-v1",
        prompt_template="测试提示词 {title}",
        shared_seed=42,
        title_text="封面校验",
        extra_text=[],
        landscape_size=(2560, 1440),
        four_three_size=(2048, 1536),
    )
    repository.mark_ai_cover_generation_running(generation.id)
    for asset in repository.list_ai_cover_assets(generation.id):
        repository.mark_ai_cover_asset_running(asset.id)
        repository.complete_ai_cover_asset(
            asset.id,
            background_oss_object_key=(
                f"covers/{asset.orientation}-background.png"
            ),
            final_oss_object_key=f"covers/{asset.orientation}-final.png",
            background_sha256=f"background-{asset.orientation}",
            final_sha256=hashlib.sha256(b"expected").hexdigest(),
        )
    oss = FakeOSS(cover_bytes=b"tampered")
    service = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=OverlayFFmpeg(),  # type: ignore[arg-type]
    )

    record = service.start_export(
        job_id=job.id,
        timeline_index=0,
        timeline_title="封面校验",
        requested_by_user_id=None,
        request_id="ai-cover-export-hash",
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=5000,
        subtitle_mode="off",
        include_danmaku=False,
        output_layout="landscape",
        ai_cover_generation_id=generation.id,
    )
    await service._tasks[record.id]

    failed = repository.get_video_clip_export(job.id, record.id)
    assert failed is not None
    assert failed.status == "failed"
    assert failed.error_message == "AI 封面文件校验失败，请重新选择封面"
    assert oss.uploads == []
    await service.close()


@pytest.mark.asyncio
async def test_multi_range_export_renders_and_concatenates_kept_ranges(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=991107",
        "991107",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=1000,
                end_ms=2500,
                text="第一段字幕",
            ),
            TranscriptSegment(
                sequence=2,
                start_ms=6000,
                end_ms=7500,
                text="第二段字幕",
            ),
        ],
    )
    ffmpeg = MultiRangeFFmpeg()
    oss = FakeOSS()
    service = VideoClipService(
        settings,
        repository,
        oss,  # type: ignore[arg-type]
        ffmpeg=ffmpeg,  # type: ignore[arg-type]
    )
    ranges = [
        ClipRange(start_ms=1000, end_ms=3000),
        ClipRange(start_ms=6000, end_ms=8000),
    ]

    record = service.start_export(
        job_id=job.id,
        timeline_index=0,
        timeline_title="删除中段",
        requested_by_user_id=None,
        request_id="multi-range-request",
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        start_ms=1000,
        end_ms=8000,
        kept_ranges=ranges,
        subtitle_mode="zh",
        include_danmaku=False,
    )
    await service._tasks[record.id]

    completed = repository.get_video_clip_export(job.id, record.id)
    assert completed is not None
    assert completed.status == "completed"
    assert completed.kept_ranges == ranges
    assert ffmpeg.clip_calls == [(1000, 3000), (6000, 8000)]
    assert len(ffmpeg.concat_inputs) == 2
    assert "第一段字幕" in ffmpeg.ass_documents[0]
    assert "第二段字幕" in ffmpeg.ass_documents[1]
    assert "0:00:00.00,0:00:01.50" in ffmpeg.ass_documents[0]
    assert "0:00:00.00,0:00:01.50" in ffmpeg.ass_documents[1]
    assert oss.uploads[0][2] == b"videovideo"
