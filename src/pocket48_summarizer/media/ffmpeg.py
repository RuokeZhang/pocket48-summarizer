from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from ..config import Settings
from ..errors import AppError, ConfigurationError
from ..security import MEDIA_HOSTS, redact_url, validate_https_url

Heartbeat = Callable[[], Awaitable[None]]


class FFmpegRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def require_executable(self) -> str:
        executable = self.settings.ffmpeg_executable()
        if not executable:
            raise ConfigurationError(
                "FFmpeg 未安装或 FFMPEG_PATH 无效；应用不会自动下载二进制文件"
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
        command = self.build_extract_command(manifest_url, output_path)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout_seconds = max(15 * 60, int(duration_ms / 1000 * 2 + 300))
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
                            "ffmpeg_timeout",
                            "FFmpeg 音频提取超时",
                            True,
                        )
            _, stderr = await communicate
        except asyncio.CancelledError:
            await self._stop_process(process)
            output_path.unlink(missing_ok=True)
            raise
        finally:
            if not communicate.done():
                communicate.cancel()
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")[-2000:]
            error = error.replace(manifest_url, redact_url(manifest_url))
            raise AppError(
                "ffmpeg_failed",
                f"FFmpeg 音频提取失败：{error or '未知错误'}",
                True,
            )
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
