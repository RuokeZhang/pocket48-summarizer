from pathlib import Path

from pocket48_summarizer.media.ffmpeg import FFmpegRunner


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
