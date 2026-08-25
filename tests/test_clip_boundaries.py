from __future__ import annotations

from pocket48_summarizer.media.boundaries import ClipBoundaryService
from pocket48_summarizer.media.ffmpeg import SilenceInterval
from pocket48_summarizer.models import TranscriptSegment


class FakeFFmpeg:
    def __init__(self, intervals: list[SilenceInterval]) -> None:
        self.intervals = intervals
        self.calls: list[dict] = []

    async def detect_silence(self, manifest_url: str, **kwargs):
        self.calls.append({"manifest_url": manifest_url, **kwargs})
        return self.intervals


async def test_boundary_snap_refines_sentence_with_nearby_silence(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=777001",
        "777001",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=10_000,
                end_ms=12_000,
                text="第一句",
            ),
            TranscriptSegment(
                sequence=2,
                start_ms=13_000,
                end_ms=16_000,
                text="第二句",
            ),
        ],
    )
    ffmpeg = FakeFFmpeg([SilenceInterval(900, 1400)])
    service = ClipBoundaryService(
        settings, repository, ffmpeg  # type: ignore[arg-type]
    )

    suggestion = await service.suggest(
        job_id=job.id,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        duration_ms=60_000,
        boundary="start",
        target_ms=10_700,
    )
    repeated = await service.suggest(
        job_id=job.id,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        duration_ms=60_000,
        boundary="start",
        target_ms=10_500,
    )

    assert suggestion.source == "silence"
    assert suggestion.sentence_ms == 10_000
    assert suggestion.suggested_ms == 9_900
    assert suggestion.silence_start_ms == 9_400
    assert suggestion.silence_end_ms == 9_900
    assert repeated.suggested_ms == suggestion.suggested_ms
    assert len(ffmpeg.calls) == 1
    assert ffmpeg.calls[0]["start_ms"] == 8_500
    assert ffmpeg.calls[0]["end_ms"] == 11_500


async def test_boundary_snap_preserves_manual_value_outside_threshold(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=777002",
        "777002",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=10_000,
                end_ms=12_000,
                text="第一句",
            )
        ],
    )
    ffmpeg = FakeFFmpeg([])
    service = ClipBoundaryService(
        settings, repository, ffmpeg  # type: ignore[arg-type]
    )

    suggestion = await service.suggest(
        job_id=job.id,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        duration_ms=60_000,
        boundary="end",
        target_ms=14_000,
    )

    assert suggestion.source == "manual"
    assert suggestion.suggested_ms == 14_000
    assert ffmpeg.calls == []


async def test_boundary_snap_uses_sentence_when_no_silence(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=777003",
        "777003",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=10_000,
                end_ms=12_000,
                text="第一句",
            )
        ],
    )
    service = ClipBoundaryService(
        settings,
        repository,
        FakeFFmpeg([]),  # type: ignore[arg-type]
    )

    suggestion = await service.suggest(
        job_id=job.id,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        duration_ms=60_000,
        boundary="end",
        target_ms=11_800,
    )

    assert suggestion.source == "sentence"
    assert suggestion.suggested_ms == 12_000


async def test_boundary_snap_does_not_leave_editor_window(
    settings, repository
):
    job, _ = repository.create_or_get_job(
        "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id=777004",
        "777004",
    )
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=9500,
                end_ms=12_000,
                text="窗口外开始的句子",
            )
        ],
    )
    ffmpeg = FakeFFmpeg([SilenceInterval(0, 300)])
    service = ClipBoundaryService(
        settings, repository, ffmpeg  # type: ignore[arg-type]
    )

    suggestion = await service.suggest(
        job_id=job.id,
        manifest_url="https://idol-vod.48.cn/replay.m3u8",
        duration_ms=60_000,
        boundary="start",
        target_ms=10_000,
        minimum_ms=10_000,
        maximum_ms=20_000,
    )

    assert suggestion.source == "manual"
    assert suggestion.suggested_ms == 10_000
    assert ffmpeg.calls == []
