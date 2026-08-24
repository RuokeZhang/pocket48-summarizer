from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import Settings
from ..errors import AppError, ConfigurationError
from ..security import MEDIA_HOSTS, redact_url, validate_https_url

Heartbeat = Callable[[], Awaitable[None]]


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
            f"{start_ms / 1000:.3f}",
            "-i",
            manifest_url,
            "-t",
            f"{(end_ms - start_ms) / 1000:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
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

    async def clip_video(
        self,
        manifest_url: str,
        output_path: Path,
        start_ms: int,
        end_ms: int,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp4")
        temporary_path.unlink(missing_ok=True)
        try:
            await self._run_command(
                self.build_clip_command(
                    manifest_url,
                    temporary_path,
                    start_ms,
                    end_ms,
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
        return output_path

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
