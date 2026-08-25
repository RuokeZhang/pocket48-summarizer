from pathlib import Path

import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.ffmpeg import (
    FFmpegRunner,
    SilenceInterval,
)


MANIFEST_URL = "https://idol-vod.48.cn/path/replay.m3u8"


def test_parallel_download_command_uses_configured_concurrency(settings):
    settings.hls_concurrent_fragments = 12
    runner = FFmpegRunner(settings)

    command = runner.build_download_command(
        MANIFEST_URL, Path("/tmp/source.ts")
    )

    assert command[0].endswith("yt-dlp")
    assert command[command.index("--concurrent-fragments") + 1] == "12"
    assert command[-1] == MANIFEST_URL


def test_direct_fallback_enables_reconnect_and_user_agent(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_extract_command(
        MANIFEST_URL, Path("/tmp/audio.mp3")
    )

    assert command[command.index("-reconnect") + 1] == "1"
    assert command[command.index("-reconnect_streamed") + 1] == "1"
    assert (
        command[command.index("-user_agent") + 1]
        == "pocket48-summarizer/0.1"
    )


def test_clip_command_uses_exact_range_and_reencodes(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_clip_command(
        MANIFEST_URL,
        Path("/tmp/clip.mp4"),
        start_ms=61_250,
        end_ms=125_750,
    )

    assert command[command.index("-ss") + 1] == "61.250"
    assert command[command.index("-t") + 1] == "64.500"
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command.index("-ss") < command.index("-i")
    assert command[-1] == "/tmp/clip.mp4"


def test_clip_command_rejects_overlong_range(settings):
    settings.max_clip_minutes = 2
    runner = FFmpegRunner(settings)

    with pytest.raises(AppError, match="最长 2 分钟"):
        runner.build_clip_command(
            MANIFEST_URL,
            Path("/tmp/clip.mp4"),
            start_ms=0,
            end_ms=120_001,
        )


def test_silence_command_is_bounded_and_parseable(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_silence_command(
        MANIFEST_URL,
        start_ms=9500,
        end_ms=12_500,
        noise_db=-35,
        min_duration_ms=200,
    )

    assert command[command.index("-ss") + 1] == "9.500"
    assert command[command.index("-t") + 1] == "3.000"
    assert (
        command[command.index("-af") + 1]
        == "silencedetect=noise=-35dB:d=0.200"
    )
    assert FFmpegRunner.parse_silence_intervals(
        """
        [silencedetect @ 0x1] silence_start: 0.4
        [silencedetect @ 0x1] silence_end: 0.95 | silence_duration: 0.55
        [silencedetect @ 0x1] silence_end: 2.0 | silence_duration: 0.25
        """
    ) == [
        SilenceInterval(400, 950),
        SilenceInterval(1750, 2000),
    ]


def test_clip_command_adds_escaped_ass_filter(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_clip_command(
        MANIFEST_URL,
        Path("/tmp/clip.mp4"),
        start_ms=1000,
        end_ms=5000,
        ass_path=Path("/tmp/clip's,overlay.ass"),
    )

    assert command[command.index("-vf") + 1] == (
        r"ass=filename='/tmp/clip\'s\,overlay.ass'"
    )
    assert command.index("-vf") < command.index("-c:v")
