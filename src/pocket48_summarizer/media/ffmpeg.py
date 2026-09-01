from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from collections.abc import Awaitable, Callable
from functools import lru_cache
from pathlib import Path

from ..config import Settings
from ..errors import AppError, ConfigurationError
from ..security import MEDIA_HOSTS, redact_url, validate_https_url
from .layouts import (
    ClipOutputLayout,
    landscape_video_filters,
    resolve_landscape_theme,
)
from .raster_overlays import RasterOverlayBundle, RenderedRasterCue

Heartbeat = Callable[[], Awaitable[None]]
SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
SILENCE_END_RE = re.compile(
    r"silence_end:\s*(-?\d+(?:\.\d+)?)"
    r"(?:\s*\|\s*silence_duration:\s*(\d+(?:\.\d+)?))?"
)
FFMPEG_VERSION_RE = re.compile(r"^ffmpeg version n?(\d+)")


@dataclass(frozen=True, slots=True)
class SilenceInterval:
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class VideoDimensions:
    width: int
    height: int


@lru_cache(maxsize=8)
def _filter_complex_file_option(executable: str) -> str:
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "-filter_complex_script"
    version_match = FFMPEG_VERSION_RE.search(result.stdout)
    if version_match is not None and int(version_match.group(1)) >= 7:
        return "-/filter_complex"
    return "-filter_complex_script"


class FFmpegRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(__name__)

    def require_executable(self) -> str:
        executable = self.settings.ffmpeg_executable()
        if not executable:
            raise ConfigurationError(
                "FFmpeg 未安装或 FFMPEG_PATH 无效；应用不会自动下载二进制文件"
            )
        return executable

    def require_ffprobe_executable(self) -> str:
        executable = self.settings.ffprobe_executable()
        if not executable:
            raise ConfigurationError(
                "FFprobe 未安装或 FFPROBE_PATH 无效，无法渲染字幕或弹幕"
            )
        return executable

    def filter_complex_file_option(self) -> str:
        return _filter_complex_file_option(self.require_executable())

    @staticmethod
    def require_ytdlp_executable() -> str:
        environment_executable = Path(sys.prefix) / "bin" / "yt-dlp"
        if environment_executable.is_file():
            return str(environment_executable)
        alongside_entrypoint = Path(sys.argv[0]).with_name("yt-dlp")
        if alongside_entrypoint.is_file():
            return str(alongside_entrypoint)
        executable = shutil.which("yt-dlp")
        if not executable:
            raise ConfigurationError(
                "yt-dlp 未安装，无法并行下载 HLS 分片"
            )
        return executable

    def build_extract_command(
        self, manifest_url: str, output_path: Path
    ) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-user_agent",
            "pocket48-summarizer/0.1",
            "-rw_timeout",
            "30000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "5",
            "-headers",
            "Origin: https://h5.48.cn\r\nReferer: https://h5.48.cn/\r\n",
            "-i",
            manifest_url,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            "-y",
            str(output_path),
        ]

    def build_download_command(
        self, manifest_url: str, output_path: Path
    ) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        return [
            self.require_ytdlp_executable(),
            "--no-config",
            "--no-cache-dir",
            "--no-playlist",
            "--quiet",
            "--no-warnings",
            "--force-overwrites",
            "--hls-use-mpegts",
            "--concurrent-fragments",
            str(self.settings.hls_concurrent_fragments),
            "--socket-timeout",
            str(round(self.settings.request_timeout_seconds)),
            "--retries",
            str(self.settings.external_retry_attempts),
            "--fragment-retries",
            str(self.settings.external_retry_attempts),
            "--add-header",
            "Origin: https://h5.48.cn",
            "--add-header",
            "Referer: https://h5.48.cn/",
            "--add-header",
            "User-Agent: pocket48-summarizer/0.1",
            "--output",
            str(output_path),
            manifest_url,
        ]

    def build_local_extract_command(
        self, source_path: Path, output_path: Path
    ) -> list[str]:
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            "-y",
            str(output_path),
        ]

    def build_clip_command(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
        ass_path: Path | None = None,
        output_layout: ClipOutputLayout = "portrait",
        landscape_theme: str | None = None,
        cover_path: Path | None = None,
        cover_dimensions: VideoDimensions | None = None,
        raster_bundle: RasterOverlayBundle | None = None,
        filter_script_path: Path | None = None,
    ) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        if start_ms < 0 or end_ms <= start_ms:
            raise AppError(
                "invalid_clip_range",
                "视频剪辑时间范围无效",
                False,
            )
        max_duration_ms = round(
            self.settings.max_clip_minutes * 60 * 1000
        )
        if end_ms - start_ms > max_duration_ms:
            raise AppError(
                "clip_too_long",
                (
                    "单个视频片段最长 "
                    f"{self.settings.max_clip_minutes:g} 分钟"
                ),
                False,
            )
        command = [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-user_agent",
            "pocket48-summarizer/0.1",
            "-rw_timeout",
            "30000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "5",
            "-headers",
            "Origin: https://h5.48.cn\r\nReferer: https://h5.48.cn/\r\n",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            manifest_url,
        ]
        if raster_bundle is not None:
            if filter_script_path is None:
                raise AppError(
                    "clip_overlay_invalid",
                    "彩色 emoji 滤镜脚本路径无效",
                    False,
                )
            for atlas_path in raster_bundle.atlas_paths:
                command.extend(["-i", str(atlas_path)])
        if cover_path is not None:
            if (
                cover_dimensions is None
                or cover_dimensions.width <= 0
                or cover_dimensions.height <= 0
            ):
                raise AppError(
                    "ai_cover_dimensions_invalid",
                    "AI 封面输出尺寸无效",
                    False,
                )
            command.extend(["-i", str(cover_path)])
        command.extend(
            ["-t", f"{(end_ms - start_ms) / 1000:.3f}"]
        )
        filters: list[str] = []
        if output_layout == "landscape":
            filters.extend(
                landscape_video_filters(
                    resolve_landscape_theme(landscape_theme)
                )
            )
        elif output_layout != "portrait":
            raise AppError(
                "clip_layout_invalid",
                "视频画面方向无效",
                False,
            )
        if ass_path is not None:
            filters.append(
                f"ass=filename='{self._escape_filter_path(ass_path)}'"
            )
        if raster_bundle is not None:
            command.extend(
                [
                    self.filter_complex_file_option(),
                    str(filter_script_path),
                    "-map",
                    "[v]",
                    "-map",
                    "0:a:0?",
                ]
            )
        elif cover_path is not None:
            base_filter = ",".join(filters) if filters else "null"
            width = cover_dimensions.width
            height = cover_dimensions.height
            filter_complex = (
                f"[0:v]{base_filter}[base];"
                f"[1:v]scale={width}:{height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1[cover];"
                "[base][cover]overlay=0:0:eof_action=pass:"
                "repeatlast=0:enable='eq(n,0)'[v]"
            )
            command.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[v]",
                    "-map",
                    "0:a:0?",
                ]
            )
        else:
            command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
        if filters and cover_path is None and raster_bundle is None:
            command.extend(
                [
                    "-vf",
                    ",".join(filters),
                ]
            )
        command.extend(
            [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            "-y",
            str(output_path),
            ]
        )
        return command

    def build_raster_filter_complex(
        self,
        *,
        filters: list[str],
        bundle: RasterOverlayBundle,
        cover_input_index: int | None = None,
        cover_dimensions: VideoDimensions | None = None,
    ) -> str:
        graph: list[str] = [
            f"[0:v]{','.join(filters) if filters else 'null'}[rbase0]"
        ]
        atlas_cues: dict[int, list[tuple[int, RenderedRasterCue]]] = {}
        for cue_index, cue in enumerate(bundle.cues):
            atlas_cues.setdefault(cue.atlas_index, []).append(
                (cue_index, cue)
            )
        for atlas_index, cues in atlas_cues.items():
            input_index = atlas_index + 1
            labels = "".join(
                f"[ratlas{cue_index}]" for cue_index, _ in cues
            )
            if len(cues) == 1:
                graph.append(
                    f"[{input_index}:v]loop=loop=-1:size=1:start=0,"
                    f"setpts=N/30/TB,format=rgba{labels}"
                )
            else:
                graph.append(
                    f"[{input_index}:v]loop=loop=-1:size=1:start=0,"
                    "setpts=N/30/TB,format=rgba,"
                    f"split={len(cues)}{labels}"
                )
            for cue_index, cue in cues:
                operations = [
                    (
                        f"crop={cue.width}:{cue.height}:"
                        f"{cue.crop_x}:{cue.crop_y}"
                    ),
                    "setpts=PTS-STARTPTS",
                ]
                if cue.fade_in_ms:
                    operations.append(
                        "fade=t=in:alpha=1:"
                        f"st={cue.start_ms / 1000:.3f}:"
                        f"d={cue.fade_in_ms / 1000:.3f}"
                    )
                graph.append(
                    f"[ratlas{cue_index}]"
                    f"{','.join(operations)}[rcue{cue_index}]"
                )
        previous = "rbase0"
        for cue_index, cue in enumerate(bundle.cues):
            output = f"rbase{cue_index + 1}"
            enable = (
                f"gte(t,{cue.start_ms / 1000:.3f})*"
                f"lt(t,{cue.end_ms / 1000:.3f})"
            )
            graph.append(
                f"[{previous}][rcue{cue_index}]overlay="
                f"x={cue.x}:y='{self._raster_y_expression(cue)}':"
                f"enable='{enable}':eof_action=repeat:shortest=0"
                f"[{output}]"
            )
            previous = output
        if cover_input_index is not None:
            if cover_dimensions is None:
                raise AppError(
                    "ai_cover_dimensions_invalid",
                    "AI 封面输出尺寸无效",
                    False,
                )
            width = cover_dimensions.width
            height = cover_dimensions.height
            graph.append(
                f"[{cover_input_index}:v]scale={width}:{height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1[rcover]"
            )
            graph.append(
                f"[{previous}][rcover]overlay=0:0:eof_action=pass:"
                "repeatlast=0:enable='eq(n,0)'[v]"
            )
        else:
            graph.append(f"[{previous}]null[v]")
        return ";\n".join(graph) + "\n"

    @staticmethod
    def _raster_y_expression(cue: RenderedRasterCue) -> str:
        expression = str(cue.placements[-1].y_to)
        for placement in reversed(cue.placements):
            start = placement.start_ms / 1000
            end = placement.end_ms / 1000
            if placement.move_ms and placement.y_from != placement.y_to:
                move_end = (
                    placement.start_ms + placement.move_ms
                ) / 1000
                value = (
                    f"if(lt(t,{move_end:.3f}),"
                    f"{placement.y_from}+"
                    f"({placement.y_to - placement.y_from})*"
                    f"(t-{start:.3f})/"
                    f"{placement.move_ms / 1000:.3f},"
                    f"{placement.y_to})"
                )
            else:
                value = str(placement.y_to)
            expression = (
                f"if(between(t,{start:.3f},{end:.3f}),"
                f"{value},{expression})"
            )
        return expression

    def build_extract_cover_source_command(
        self,
        manifest_url: str,
        output_path: Path,
        timestamp_ms: int,
    ) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        if timestamp_ms < 0:
            raise AppError(
                "ai_cover_timestamp_invalid",
                "AI 封面标记时间无效",
                False,
            )
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-user_agent",
            "pocket48-summarizer/0.1",
            "-rw_timeout",
            "30000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "5",
            "-headers",
            "Origin: https://h5.48.cn\r\nReferer: https://h5.48.cn/\r\n",
            "-ss",
            f"{timestamp_ms / 1000:.3f}",
            "-i",
            manifest_url,
            "-frames:v",
            "1",
            "-an",
            "-y",
            str(output_path),
        ]

    def build_normalize_cover_image_command(
        self,
        input_path: Path,
        output_path: Path,
        *,
        width: int,
        height: int,
    ) -> list[str]:
        if width <= 0 or height <= 0 or width > 8192 or height > 8192:
            raise AppError(
                "ai_cover_dimensions_invalid",
                "AI 封面输出尺寸无效",
                False,
            )
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-vf",
            (
                f"scale={width}:{height}:"
                "force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1"
            ),
            "-frames:v",
            "1",
            "-an",
            "-y",
            str(output_path),
        ]

    def build_cover_frame_command(
        self,
        manifest_url: str,
        output_path: Path,
        timestamp_ms: int,
        ass_path: Path,
        output_layout: ClipOutputLayout = "portrait",
        landscape_theme: str | None = None,
        raster_bundle: RasterOverlayBundle | None = None,
        filter_script_path: Path | None = None,
    ) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        if timestamp_ms < 0:
            raise AppError(
                "clip_cover_invalid",
                "封面画面时间无效",
                False,
            )
        filters: list[str] = []
        if output_layout == "landscape":
            filters.extend(
                landscape_video_filters(
                    resolve_landscape_theme(landscape_theme)
                )
            )
        elif output_layout != "portrait":
            raise AppError(
                "clip_layout_invalid",
                "视频画面方向无效",
                False,
            )
        filters.append(
            f"ass=filename='{self._escape_filter_path(ass_path)}'"
        )
        command = [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-user_agent",
            "pocket48-summarizer/0.1",
            "-rw_timeout",
            "30000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "5",
            "-headers",
            "Origin: https://h5.48.cn\r\nReferer: https://h5.48.cn/\r\n",
            "-ss",
            f"{timestamp_ms / 1000:.3f}",
            "-i",
            manifest_url,
        ]
        if raster_bundle is not None:
            if filter_script_path is None:
                raise AppError(
                    "clip_overlay_invalid",
                    "彩色 emoji 滤镜脚本路径无效",
                    False,
                )
            for atlas_path in raster_bundle.atlas_paths:
                command.extend(["-i", str(atlas_path)])
            command.extend(
                [
                    self.filter_complex_file_option(),
                    str(filter_script_path),
                    "-map",
                    "[v]",
                ]
            )
        else:
            command.extend(["-vf", ",".join(filters)])
        command.extend(
            [
            "-frames:v",
            "1",
            "-an",
            "-y",
            str(output_path),
            ]
        )
        return command

    def build_prepend_cover_command(
        self,
        cover_path: Path,
        clip_path: Path,
        output_path: Path,
        *,
        duration_ms: int,
    ) -> list[str]:
        if duration_ms <= 0:
            raise AppError(
                "clip_cover_invalid",
                "封面停留时间无效",
                False,
            )
        duration = f"{duration_ms / 1000:.3f}"
        filter_complex = (
            f"[0:v]trim=duration={duration},setpts=PTS-STARTPTS,"
            "fps=30,format=yuv420p,setsar=1[cover];"
            "[1:v]setpts=PTS-STARTPTS,fps=30,"
            "format=yuv420p,setsar=1[main];"
            "[cover][main]concat=n=2:v=1:a=0[v]"
        )
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-loop",
            "1",
            "-framerate",
            "30",
            "-t",
            duration,
            "-i",
            str(cover_path),
            "-i",
            str(clip_path),
            "-itsoffset",
            duration,
            "-i",
            str(clip_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "2:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            "-y",
            str(output_path),
        ]

    def build_concat_clips_command(
        self,
        manifest_path: Path,
        output_path: Path,
    ) -> list[str]:
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ]

    def build_concat_audio_command(
        self,
        manifest_path: Path,
        output_path: Path,
    ) -> list[str]:
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(manifest_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            "-y",
            str(output_path),
        ]

    def build_probe_command(self, manifest_url: str) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        return [
            self.require_ffprobe_executable(),
            "-v",
            "error",
            "-user_agent",
            "pocket48-summarizer/0.1",
            "-headers",
            "Origin: https://h5.48.cn\r\nReferer: https://h5.48.cn/\r\n",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            manifest_url,
        ]

    async def probe_video_dimensions(
        self, manifest_url: str, timeout_seconds: int = 30
    ) -> VideoDimensions:
        stdout, stderr = await self._run_capture_both(
            self.build_probe_command(manifest_url),
            timeout_seconds=timeout_seconds,
            error_code="clip_video_probe_failed",
            error_message="读取回放视频尺寸失败",
            redact_value=manifest_url,
        )
        try:
            payload = json.loads(stdout)
            stream = payload["streams"][0]
            width = int(stream["width"])
            height = int(stream["height"])
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(
                "clip_video_probe_invalid",
                "FFprobe 没有返回有效的视频尺寸",
                True,
            ) from exc
        if width <= 0 or height <= 0 or width > 8192 or height > 8192:
            raise AppError(
                "clip_video_probe_invalid",
                "回放视频尺寸超出允许范围",
                False,
            )
        return VideoDimensions(width=width, height=height)

    async def supports_ass_filter(self, timeout_seconds: int = 15) -> bool:
        stdout, _ = await self._run_capture_both(
            [
                self.require_executable(),
                "-nostdin",
                "-hide_banner",
                "-filters",
            ],
            timeout_seconds=timeout_seconds,
            error_code="clip_overlay_probe_failed",
            error_message="检查 FFmpeg 字幕滤镜失败",
        )
        return any(
            line.split()[1:2] == ["ass"] for line in stdout.splitlines()
        )

    def build_silence_command(
        self,
        manifest_url: str,
        *,
        start_ms: int,
        end_ms: int,
        noise_db: float,
        min_duration_ms: int,
    ) -> list[str]:
        validate_https_url(
            manifest_url,
            MEDIA_HOSTS,
            code="invalid_media_url",
            label="回放媒体",
        )
        if start_ms < 0 or end_ms <= start_ms:
            raise AppError(
                "invalid_silence_range",
                "静音分析时间范围无效",
                False,
            )
        return [
            self.require_executable(),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "info",
            "-user_agent",
            "pocket48-summarizer/0.1",
            "-rw_timeout",
            "30000000",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_on_network_error",
            "1",
            "-reconnect_on_http_error",
            "4xx,5xx",
            "-reconnect_delay_max",
            "5",
            "-headers",
            "Origin: https://h5.48.cn\r\nReferer: https://h5.48.cn/\r\n",
            "-ss",
            f"{start_ms / 1000:.3f}",
            "-i",
            manifest_url,
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            (
                "silencedetect="
                f"noise={noise_db:g}dB:"
                f"d={min_duration_ms / 1000:.3f}"
            ),
            "-f",
            "null",
            "-",
        ]

    @staticmethod
    def parse_silence_intervals(stderr: str) -> list[SilenceInterval]:
        intervals: list[SilenceInterval] = []
        pending_start_ms: int | None = None
        for line in stderr.splitlines():
            start_match = SILENCE_START_RE.search(line)
            if start_match:
                pending_start_ms = max(
                    0, round(float(start_match.group(1)) * 1000)
                )
                continue
            end_match = SILENCE_END_RE.search(line)
            if not end_match:
                continue
            end_ms = max(0, round(float(end_match.group(1)) * 1000))
            duration_ms = (
                round(float(end_match.group(2)) * 1000)
                if end_match.group(2)
                else None
            )
            start_ms = pending_start_ms
            if start_ms is None and duration_ms is not None:
                start_ms = max(0, end_ms - duration_ms)
            if start_ms is not None and end_ms >= start_ms:
                intervals.append(SilenceInterval(start_ms, end_ms))
            pending_start_ms = None
        return intervals

    async def detect_silence(
        self,
        manifest_url: str,
        *,
        start_ms: int,
        end_ms: int,
        noise_db: float,
        min_duration_ms: int,
        timeout_seconds: int,
    ) -> list[SilenceInterval]:
        stderr = await self._run_command_capture(
            self.build_silence_command(
                manifest_url,
                start_ms=start_ms,
                end_ms=end_ms,
                noise_db=noise_db,
                min_duration_ms=min_duration_ms,
            ),
            timeout_seconds=timeout_seconds,
            heartbeat=None,
            error_code="clip_silence_analysis_failed",
            error_message="分析剪辑边界静音失败",
            redact_value=manifest_url,
        )
        return self.parse_silence_intervals(stderr)

    async def clip_video(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
        ass_path: Path | None = None,
        output_layout: ClipOutputLayout = "portrait",
        landscape_theme: str | None = None,
        cover_path: Path | None = None,
        cover_dimensions: VideoDimensions | None = None,
        raster_bundle: RasterOverlayBundle | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp4")
        filter_script_path = output_path.with_suffix(
            ".emoji-filter.txt"
        )
        temporary_path.unlink(missing_ok=True)
        filter_script_path.unlink(missing_ok=True)
        try:
            if raster_bundle is not None:
                filters: list[str] = []
                if output_layout == "landscape":
                    filters.extend(
                        landscape_video_filters(
                            resolve_landscape_theme(landscape_theme)
                        )
                    )
                if ass_path is not None:
                    filters.append(
                        "ass=filename='"
                        f"{self._escape_filter_path(ass_path)}'"
                    )
                cover_input_index = (
                    1 + len(raster_bundle.atlas_paths)
                    if cover_path is not None
                    else None
                )
                filter_script_path.write_text(
                    self.build_raster_filter_complex(
                        filters=filters,
                        bundle=raster_bundle,
                        cover_input_index=cover_input_index,
                        cover_dimensions=cover_dimensions,
                    ),
                    encoding="utf-8",
                )
            await self._run_command(
                self.build_clip_command(
                    manifest_url,
                    temporary_path,
                    start_ms,
                    end_ms,
                    ass_path,
                    output_layout=output_layout,
                    landscape_theme=landscape_theme,
                    cover_path=cover_path,
                    cover_dimensions=cover_dimensions,
                    raster_bundle=raster_bundle,
                    filter_script_path=filter_script_path,
                ),
                timeout_seconds=max(
                    15 * 60, int((end_ms - start_ms) / 1000 * 2 + 300)
                ),
                heartbeat=None,
                error_code="video_clip_failed",
                error_message="FFmpeg 视频剪辑失败",
                redact_value=manifest_url,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "video_clip_missing",
                    "FFmpeg 未生成视频片段",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            filter_script_path.unlink(missing_ok=True)
        return output_path

    async def extract_cover_source_frame(
        self,
        manifest_url: str,
        output_path: Path,
        timestamp_ms: int,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.png")
        temporary_path.unlink(missing_ok=True)
        try:
            await self._run_command(
                self.build_extract_cover_source_command(
                    manifest_url,
                    temporary_path,
                    timestamp_ms,
                ),
                timeout_seconds=10 * 60,
                heartbeat=None,
                error_code="ai_cover_source_failed",
                error_message="提取 AI 封面参考画面失败",
                redact_value=manifest_url,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "ai_cover_source_missing",
                    "FFmpeg 未生成 AI 封面参考画面",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return output_path

    async def normalize_cover_image(
        self,
        input_path: Path,
        output_path: Path,
        *,
        width: int,
        height: int,
    ) -> Path:
        if not input_path.is_file():
            raise AppError(
                "ai_cover_image_missing",
                "AI 封面图片不存在",
                True,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.png")
        temporary_path.unlink(missing_ok=True)
        try:
            await self._run_command(
                self.build_normalize_cover_image_command(
                    input_path,
                    temporary_path,
                    width=width,
                    height=height,
                ),
                timeout_seconds=5 * 60,
                heartbeat=None,
                error_code="ai_cover_normalize_failed",
                error_message="规范化 AI 封面图片失败",
                redact_value=None,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "ai_cover_image_missing",
                    "FFmpeg 未生成规范化 AI 封面",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return output_path

    async def render_cover_frame(
        self,
        manifest_url: str,
        output_path: Path,
        timestamp_ms: int,
        ass_path: Path,
        output_layout: ClipOutputLayout = "portrait",
        landscape_theme: str | None = None,
        raster_bundle: RasterOverlayBundle | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.png")
        filter_script_path = output_path.with_suffix(
            ".emoji-filter.txt"
        )
        temporary_path.unlink(missing_ok=True)
        filter_script_path.unlink(missing_ok=True)
        try:
            if raster_bundle is not None:
                filters: list[str] = []
                if output_layout == "landscape":
                    filters.extend(
                        landscape_video_filters(
                            resolve_landscape_theme(landscape_theme)
                        )
                    )
                filters.append(
                    f"ass=filename='{self._escape_filter_path(ass_path)}'"
                )
                filter_script_path.write_text(
                    self.build_raster_filter_complex(
                        filters=filters,
                        bundle=raster_bundle,
                    ),
                    encoding="utf-8",
                )
            await self._run_command(
                self.build_cover_frame_command(
                    manifest_url,
                    temporary_path,
                    timestamp_ms,
                    ass_path,
                    output_layout=output_layout,
                    landscape_theme=landscape_theme,
                    raster_bundle=raster_bundle,
                    filter_script_path=filter_script_path,
                ),
                timeout_seconds=10 * 60,
                heartbeat=None,
                error_code="clip_cover_frame_failed",
                error_message="提取封面画面失败",
                redact_value=manifest_url,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "clip_cover_frame_missing",
                    "FFmpeg 未生成封面画面",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            filter_script_path.unlink(missing_ok=True)
        return output_path

    async def concat_clips(
        self,
        input_paths: list[Path],
        output_path: Path,
    ) -> Path:
        if len(input_paths) < 2:
            raise AppError(
                "clip_concat_invalid",
                "至少需要两个视频片段才能拼接",
                False,
            )
        if any(not path.is_file() for path in input_paths):
            raise AppError(
                "clip_concat_input_missing",
                "待拼接的视频片段不存在",
                True,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = output_path.with_suffix(".concat.txt")
        temporary_path = output_path.with_suffix(".part.mp4")
        manifest_path.unlink(missing_ok=True)
        temporary_path.unlink(missing_ok=True)
        try:
            lines: list[str] = []
            for path in input_paths:
                resolved = str(path.resolve())
                if "\n" in resolved or "\r" in resolved:
                    raise AppError(
                        "clip_concat_invalid",
                        "视频片段路径无效",
                        False,
                    )
                lines.append(
                    "file '" + resolved.replace("'", "'\\''") + "'"
                )
            manifest_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
            )
            await self._run_command(
                self.build_concat_clips_command(
                    manifest_path,
                    temporary_path,
                ),
                timeout_seconds=15 * 60,
                heartbeat=None,
                error_code="clip_concat_failed",
                error_message="拼接视频片段失败",
                redact_value=None,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "clip_concat_output_missing",
                    "FFmpeg 未生成拼接视频",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            manifest_path.unlink(missing_ok=True)
        return output_path

    async def concat_audio_segments(
        self,
        input_paths: list[Path],
        output_path: Path,
    ) -> Path:
        if not input_paths:
            raise AppError(
                "room_voice_audio_missing",
                "上麦录音没有可处理的音频分段",
                False,
            )
        if any(not path.is_file() for path in input_paths):
            raise AppError(
                "room_voice_audio_segment_missing",
                "上麦录音分段不存在",
                True,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = output_path.with_suffix(".concat.txt")
        temporary_path = output_path.with_suffix(".part.mp3")
        manifest_path.unlink(missing_ok=True)
        temporary_path.unlink(missing_ok=True)
        try:
            lines: list[str] = []
            for path in input_paths:
                resolved = str(path.resolve())
                if "\n" in resolved or "\r" in resolved:
                    raise AppError(
                        "room_voice_audio_path_invalid",
                        "上麦录音分段路径无效",
                        False,
                    )
                lines.append(
                    "file '" + resolved.replace("'", "'\\''") + "'"
                )
            manifest_path.write_text(
                "\n".join(lines) + "\n",
                encoding="utf-8",
            )
            await self._run_command(
                self.build_concat_audio_command(
                    manifest_path,
                    temporary_path,
                ),
                timeout_seconds=30 * 60,
                heartbeat=None,
                error_code="room_voice_audio_concat_failed",
                error_message="合并上麦录音分段失败",
                redact_value=None,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "room_voice_audio_concat_missing",
                    "FFmpeg 未生成上麦识别音频",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        finally:
            manifest_path.unlink(missing_ok=True)
        return output_path

    async def prepend_cover(
        self,
        cover_path: Path,
        clip_path: Path,
        output_path: Path,
        *,
        duration_ms: int,
    ) -> Path:
        if not cover_path.is_file() or not clip_path.is_file():
            raise AppError(
                "clip_cover_input_missing",
                "封面或视频片段不存在",
                True,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp4")
        temporary_path.unlink(missing_ok=True)
        try:
            await self._run_command(
                self.build_prepend_cover_command(
                    cover_path,
                    clip_path,
                    temporary_path,
                    duration_ms=duration_ms,
                ),
                timeout_seconds=15 * 60,
                heartbeat=None,
                error_code="clip_cover_prepend_failed",
                error_message="合成视频封面失败",
                redact_value=None,
            )
            if (
                not temporary_path.is_file()
                or temporary_path.stat().st_size == 0
            ):
                raise AppError(
                    "clip_cover_output_missing",
                    "FFmpeg 未生成带封面的视频",
                    True,
                )
            temporary_path.replace(output_path)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return output_path

    @staticmethod
    def _escape_filter_path(path: Path) -> str:
        return (
            str(path)
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace(",", "\\,")
            .replace("[", "\\[")
            .replace("]", "\\]")
        )

    async def extract_audio(
        self,
        manifest_url: str,
        output_path: Path,
        duration_ms: int,
        heartbeat: Heartbeat | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.unlink(missing_ok=True)
        expected_bytes = max(
            16 * 1024 * 1024,
            round(duration_ms / 1000 * 8_000 * 1.5),
        )
        if shutil.disk_usage(output_path.parent).free < expected_bytes:
            raise AppError(
                "insufficient_disk_space",
                "可用磁盘空间不足以提取该回放音频",
                True,
            )
        source_path = output_path.with_name("source.ts")
        self._cleanup_download_files(source_path)
        try:
            try:
                await self._run_command(
                    self.build_download_command(manifest_url, source_path),
                    timeout_seconds=max(
                        15 * 60, int(duration_ms / 1000 + 300)
                    ),
                    heartbeat=heartbeat,
                    error_code="hls_parallel_download_failed",
                    error_message="并行下载 HLS 分片失败",
                    redact_value=manifest_url,
                )
                if (
                    not source_path.is_file()
                    or source_path.stat().st_size == 0
                ):
                    raise AppError(
                        "hls_parallel_download_missing",
                        "并行下载未生成媒体文件",
                        True,
                    )
                await self._run_command(
                    self.build_local_extract_command(
                        source_path, output_path
                    ),
                    timeout_seconds=max(
                        15 * 60, int(duration_ms / 1000 / 2 + 300)
                    ),
                    heartbeat=heartbeat,
                    error_code="ffmpeg_local_extract_failed",
                    error_message="FFmpeg 本地音频提取失败",
                )
            except AppError as exc:
                self.logger.warning(
                    "Parallel HLS extraction failed; falling back to FFmpeg",
                    extra={"error_code": exc.code},
                )
                output_path.unlink(missing_ok=True)
                self._cleanup_download_files(source_path)
                await self._run_command(
                    self.build_extract_command(
                        manifest_url, output_path
                    ),
                    timeout_seconds=max(
                        15 * 60, int(duration_ms / 1000 * 2 + 300)
                    ),
                    heartbeat=heartbeat,
                    error_code="ffmpeg_failed",
                    error_message="FFmpeg 音频提取失败",
                    redact_value=manifest_url,
                )
        finally:
            self._cleanup_download_files(source_path)
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise AppError(
                "audio_missing",
                "FFmpeg 未生成音频文件",
                True,
            )
        if output_path.stat().st_size > self.settings.max_audio_bytes:
            output_path.unlink(missing_ok=True)
            raise AppError(
                "audio_too_large",
                "提取的音频超过允许大小",
                False,
            )
        return output_path

    async def _run_command(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        heartbeat: Heartbeat | None,
        error_code: str,
        error_message: str,
        redact_value: str | None = None,
    ) -> None:
        await self._run_command_capture(
            command,
            timeout_seconds=timeout_seconds,
            heartbeat=heartbeat,
            error_code=error_code,
            error_message=error_message,
            redact_value=redact_value,
        )

    async def _run_command_capture(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        heartbeat: Heartbeat | None,
        error_code: str,
        error_message: str,
        redact_value: str | None = None,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        communicate = asyncio.create_task(process.communicate())
        try:
            elapsed = 0
            while not communicate.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(communicate), timeout=30
                    )
                except TimeoutError:
                    elapsed += 30
                    if heartbeat:
                        await heartbeat()
                    if elapsed >= timeout_seconds:
                        await self._stop_process(process)
                        raise AppError(
                            f"{error_code}_timeout",
                            f"{error_message}：操作超时",
                            True,
                        )
            _, stderr = await communicate
        except asyncio.CancelledError:
            await self._stop_process(process)
            raise
        finally:
            if not communicate.done():
                communicate.cancel()
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")[-2000:]
            if redact_value:
                error = error.replace(
                    redact_value, redact_url(redact_value)
                )
            raise AppError(
                error_code,
                f"{error_message}：{error or '未知错误'}",
                True,
            )
        return stderr.decode("utf-8", errors="replace")[-64_000:]

    async def _run_capture_both(
        self,
        command: list[str],
        *,
        timeout_seconds: int,
        error_code: str,
        error_message: str,
        redact_value: str | None = None,
    ) -> tuple[str, str]:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout_seconds
            )
        except TimeoutError as exc:
            await self._stop_process(process)
            raise AppError(
                f"{error_code}_timeout",
                f"{error_message}：操作超时",
                True,
            ) from exc
        except asyncio.CancelledError:
            await self._stop_process(process)
            raise
        decoded_stdout = stdout.decode("utf-8", errors="replace")[-64_000:]
        decoded_stderr = stderr.decode("utf-8", errors="replace")[-4000:]
        if process.returncode != 0:
            if redact_value:
                decoded_stderr = decoded_stderr.replace(
                    redact_value, redact_url(redact_value)
                )
            raise AppError(
                error_code,
                f"{error_message}：{decoded_stderr or '未知错误'}",
                True,
            )
        return decoded_stdout, decoded_stderr

    @staticmethod
    def _cleanup_download_files(source_path: Path) -> None:
        for path in source_path.parent.glob(source_path.name + "*"):
            if path.is_file():
                path.unlink(missing_ok=True)

    @staticmethod
    async def _stop_process(
        process: asyncio.subprocess.Process,
    ) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
