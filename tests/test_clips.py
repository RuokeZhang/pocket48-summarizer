import asyncio
from pathlib import Path

import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.clips import VideoClipService


class FakeFFmpeg:
    async def clip_video(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"video")
        return output_path


class FakeOSS:
    def __init__(self):
        self.uploads = []

    def clip_object_key(self, job_id: str, filename: str) -> str:
        return f"clips/{job_id}/{filename}"

    async def upload_clip(
        self, path: Path, key: str, filename: str
    ) -> None:
        self.uploads.append((key, filename, path.read_bytes()))

    async def signed_clip_url(self, key: str) -> str:
        return f"https://oss.example/{key}?signed=1"


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
    await service._tasks[(job.id, 0)]

    record = repository.get_video_clip(job.id, 0)
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
    await service._tasks[(job.id, 0)]

    record = repository.get_video_clip(job.id, 0)
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

    record = repository.get_video_clip(job.id, 0)
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
    await service._tasks[(job.id, 0)]

    assert ffmpeg.calls == 3
    assert state.status == "completed"
    assert repository.get_video_clip(job.id, 0).status == "completed"
