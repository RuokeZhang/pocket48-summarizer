from pathlib import Path
import shutil
import subprocess

import pytest
from PIL import Image

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media import ffmpeg
from pocket48_summarizer.media.ffmpeg import (
    FFmpegRunner,
    SilenceInterval,
    VideoDimensions,
)
from pocket48_summarizer.media.raster_overlays import (
    RasterOverlayBundle,
    RasterPlacement,
    RenderedRasterCue,
)


MANIFEST_URL = "https://idol-vod.48.cn/path/replay.m3u8"


def _raster_bundle() -> RasterOverlayBundle:
    return RasterOverlayBundle(
        atlas_paths=(Path("/tmp/emoji-atlas.png"),),
        cues=(
            RenderedRasterCue(
                atlas_index=0,
                crop_x=2,
                crop_y=3,
                width=200,
                height=80,
                x=40,
                placements=(
                    RasterPlacement(
                        start_ms=500,
                        end_ms=1000,
                        y_from=900,
                        y_to=900,
                    ),
                    RasterPlacement(
                        start_ms=1000,
                        end_ms=3000,
                        y_from=900,
                        y_to=800,
                        move_ms=220,
                    ),
                ),
                layer=10,
                fade_in_ms=120,
            ),
        ),
    )


class CapturingFFmpegRunner(FFmpegRunner):
    def __init__(self, settings):
        super().__init__(settings)
        self.command: list[str] = []

    async def _run_command(self, command, **kwargs):
        del kwargs
        self.command = command
        Path(command[-1]).write_bytes(b"rendered")
        return "", ""


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


def test_clip_command_builds_landscape_canvas_before_ass(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_clip_command(
        MANIFEST_URL,
        Path("/tmp/clip.mp4"),
        start_ms=1000,
        end_ms=5000,
        ass_path=Path("/tmp/overlay.ass"),
        output_layout="landscape",
    )

    assert command[command.index("-vf") + 1] == (
        "scale=608:1080:force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
        "pad=608:1080:(ow-iw)/2:(oh-ih)/2:color=0x08090C,"
        "pad=1920:1080:(ow-iw)/2:0:color=0xEBE9E1,"
        "setsar=1,"
        "ass=filename='/tmp/overlay.ass'"
    )


def test_clip_command_uses_selected_landscape_theme(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_clip_command(
        MANIFEST_URL,
        Path("/tmp/clip.mp4"),
        start_ms=1000,
        end_ms=5000,
        ass_path=Path("/tmp/overlay.ass"),
        output_layout="landscape",
        landscape_theme="ink",
    )

    assert (
        "pad=1920:1080:(ow-iw)/2:0:color=0x1C1D22"
        in command[command.index("-vf") + 1]
    )


def test_cover_frame_command_seeks_and_matches_landscape_canvas(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_cover_frame_command(
        MANIFEST_URL,
        Path("/tmp/cover.png"),
        timestamp_ms=45_250,
        ass_path=Path("/tmp/cover title.ass"),
        output_layout="landscape",
    )

    assert command[command.index("-ss") + 1] == "45.250"
    assert command[command.index("-frames:v") + 1] == "1"
    vf = command[command.index("-vf") + 1]
    assert "pad=1920:1080:(ow-iw)/2:0:color=0xEBE9E1" in vf
    assert "ass=filename='/tmp/cover title.ass'" in vf
    assert command[-1] == "/tmp/cover.png"


def test_cover_frame_command_uses_selected_landscape_theme(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_cover_frame_command(
        MANIFEST_URL,
        Path("/tmp/cover.png"),
        timestamp_ms=45_250,
        ass_path=Path("/tmp/cover title.ass"),
        output_layout="landscape",
        landscape_theme="matcha",
    )

    assert (
        "pad=1920:1080:(ow-iw)/2:0:color=0xE6EDD6"
        in command[command.index("-vf") + 1]
    )


@pytest.mark.asyncio
async def test_clip_video_forwards_theme_to_render_command(settings, tmp_path):
    runner = CapturingFFmpegRunner(settings)

    await runner.clip_video(
        MANIFEST_URL,
        tmp_path / "clip.mp4",
        start_ms=1000,
        end_ms=5000,
        output_layout="landscape",
        landscape_theme="ink",
    )

    assert (
        "pad=1920:1080:(ow-iw)/2:0:color=0x1C1D22"
        in runner.command[runner.command.index("-vf") + 1]
    )


@pytest.mark.asyncio
async def test_cover_frame_forwards_theme_to_render_command(
    settings, tmp_path
):
    runner = CapturingFFmpegRunner(settings)

    await runner.render_cover_frame(
        MANIFEST_URL,
        tmp_path / "cover.png",
        timestamp_ms=45_250,
        ass_path=tmp_path / "cover.ass",
        output_layout="landscape",
        landscape_theme="matcha",
    )

    assert (
        "pad=1920:1080:(ow-iw)/2:0:color=0xE6EDD6"
        in runner.command[runner.command.index("-vf") + 1]
    )


def test_prepend_cover_command_delays_audio_until_cover_finishes(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_prepend_cover_command(
        Path("/tmp/cover.png"),
        Path("/tmp/clip.mp4"),
        Path("/tmp/final.mp4"),
        duration_ms=1500,
    )

    assert command[command.index("-loop") + 1] == "1"
    assert command[command.index("-framerate") + 1] == "30"
    assert command[command.index("-t") + 1] == "1.500"
    assert command[command.index("-itsoffset") + 1] == "1.500"
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "[cover][main]concat=n=2:v=1:a=0[v]" in filter_complex
    assert "trim=duration=1.500" in filter_complex
    assert "2:a:0?" in command
    assert command[-1] == "/tmp/final.mp4"


def test_ai_cover_command_overlays_frame_zero_without_audio_offset(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_clip_command(
        MANIFEST_URL,
        Path("/tmp/clip.mp4"),
        start_ms=1000,
        end_ms=5000,
        ass_path=Path("/tmp/overlay.ass"),
        output_layout="landscape",
        cover_path=Path("/tmp/ai-cover.png"),
        cover_dimensions=VideoDimensions(width=1920, height=1080),
    )

    input_indexes = [
        index for index, value in enumerate(command) if value == "-i"
    ]
    assert [command[index + 1] for index in input_indexes] == [
        MANIFEST_URL,
        "/tmp/ai-cover.png",
    ]
    assert command.index("-t") > input_indexes[-1]
    assert command[command.index("-t") + 1] == "4.000"
    filter_complex = command[command.index("-filter_complex") + 1]
    assert "ass=filename='/tmp/overlay.ass'[base]" in filter_complex
    assert "scale=1920:1080" in filter_complex
    assert "enable='eq(n,0)'" in filter_complex
    assert "concat=" not in filter_complex
    assert "-itsoffset" not in command
    assert "0:a:0?" in command


def test_raster_filter_uses_atlas_crops_timing_and_card_motion(settings):
    runner = FFmpegRunner(settings)

    graph = runner.build_raster_filter_complex(
        filters=["setsar=1", "ass=filename='/work/overlay.ass'"],
        bundle=_raster_bundle(),
    )

    assert (
        "[1:v]loop=loop=-1:size=1:start=0,"
        "setpts=N/30/TB,format=rgba[ratlas0]"
        in graph
    )
    assert "crop=200:80:2:3" in graph
    assert "fade=t=in:alpha=1:st=0.500:d=0.120" in graph
    assert "x=40" in graph
    assert "gte(t,0.500)*lt(t,3.000)" in graph
    assert "(t-1.000)/0.220" in graph
    assert "[rbase1]null[v]" in graph


def test_raster_clip_command_keeps_ai_cover_last(settings):
    runner = FFmpegRunner(settings)
    bundle = _raster_bundle()

    command = runner.build_clip_command(
        MANIFEST_URL,
        Path("/tmp/clip.mp4"),
        start_ms=1000,
        end_ms=5000,
        ass_path=Path("/tmp/overlay.ass"),
        output_layout="landscape",
        cover_path=Path("/tmp/ai-cover.png"),
        cover_dimensions=VideoDimensions(width=1920, height=1080),
        raster_bundle=bundle,
        filter_script_path=Path("/tmp/emoji-filter.txt"),
    )
    graph = runner.build_raster_filter_complex(
        filters=["setsar=1", "ass=filename='/tmp/overlay.ass'"],
        bundle=bundle,
        cover_input_index=2,
        cover_dimensions=VideoDimensions(width=1920, height=1080),
    )

    inputs = [
        command[index + 1]
        for index, value in enumerate(command)
        if value == "-i"
    ]
    assert inputs == [
        MANIFEST_URL,
        "/tmp/emoji-atlas.png",
        "/tmp/ai-cover.png",
    ]
    option = runner.filter_complex_file_option()
    assert command[command.index(option) + 1] == (
        "/tmp/emoji-filter.txt"
    )
    assert graph.index("[rbase0][rcue0]overlay") < graph.index(
        "[rbase1][rcover]overlay"
    )
    assert "enable='eq(n,0)'[v]" in graph


def test_ffmpeg_composites_a_timed_rgba_atlas(settings, tmp_path):
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.skip("FFmpeg is not installed")
    runner = FFmpegRunner(
        settings.model_copy(update={"ffmpeg_path": executable})
    )
    atlas_path = tmp_path / "atlas.png"
    output_path = tmp_path / "frame.png"
    filter_path = tmp_path / "filter.txt"
    Image.new("RGBA", (24, 24), (255, 0, 0, 255)).save(atlas_path)
    bundle = RasterOverlayBundle(
        atlas_paths=(atlas_path,),
        cues=(
            RenderedRasterCue(
                atlas_index=0,
                crop_x=2,
                crop_y=2,
                width=20,
                height=20,
                x=10,
                placements=(
                    RasterPlacement(
                        start_ms=0,
                        end_ms=1000,
                        y_from=15,
                        y_to=15,
                    ),
                ),
                layer=10,
                fade_in_ms=0,
            ),
        ),
    )
    filter_path.write_text(
        runner.build_raster_filter_complex(filters=[], bundle=bundle),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=100x100:d=1:r=30",
            "-i",
            str(atlas_path),
            runner.filter_complex_file_option(),
            str(filter_path),
            "-map",
            "[v]",
            "-frames:v",
            "1",
            "-y",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    with Image.open(output_path).convert("RGB") as frame:
        red, green, blue = frame.getpixel((10, 15))
        assert red > 240
        assert green < 10
        assert blue < 10


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("ffmpeg version 6.1.1-3ubuntu5", "-filter_complex_script"),
        ("ffmpeg version 9.0.1", "-/filter_complex"),
    ],
)
def test_filter_complex_file_option_tracks_ffmpeg_major_version(
    monkeypatch, version, expected
):
    ffmpeg._filter_complex_file_option.cache_clear()
    monkeypatch.setattr(
        ffmpeg.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            stdout=f"{version}\n",
            stderr="",
        ),
    )

    assert ffmpeg._filter_complex_file_option("/tmp/ffmpeg") == expected
    ffmpeg._filter_complex_file_option.cache_clear()


def test_ai_cover_source_command_extracts_clean_marked_frame(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_extract_cover_source_command(
        MANIFEST_URL,
        Path("/tmp/source.png"),
        timestamp_ms=12_300,
    )

    assert command[command.index("-ss") + 1] == "12.300"
    assert command[command.index("-frames:v") + 1] == "1"
    assert "-vf" not in command
    assert "-an" in command


def test_concat_command_uses_internal_manifest_without_reencoding(settings):
    runner = FFmpegRunner(settings)

    command = runner.build_concat_clips_command(
        Path("/tmp/clip.concat.txt"),
        Path("/tmp/clip.mp4"),
    )

    assert command[command.index("-f") + 1] == "concat"
    assert command[command.index("-safe") + 1] == "0"
    assert command[command.index("-i") + 1] == "/tmp/clip.concat.txt"
    assert command[command.index("-c") + 1] == "copy"
    assert command[-1] == "/tmp/clip.mp4"
