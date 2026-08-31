from __future__ import annotations

import asyncio
from pathlib import Path

from ..config import Settings
from ..errors import AppError, ConfigurationError
from ..security import redact_url, validate_room_voice_stream_url


class RoomVoiceProbeRecorder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_command(
        self,
        stream_url: str,
        output_path: Path,
        *,
        duration_seconds: int,
        allowed_hosts: set[str],
    ) -> list[str]:
        validated_url = validate_room_voice_stream_url(
            stream_url, allowed_hosts
        )
        executable = self.settings.ffmpeg_executable()
        if not executable:
            raise ConfigurationError(
                "FFmpeg 未安装或 FFMPEG_PATH 无效，无法执行上麦短录音探针"
            )
        return [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            "file,http,https,tcp,tls,crypto,rtmp,rtmps",
            "-rw_timeout",
            "15000000",
            "-i",
            validated_url,
            "-t",
            str(duration_seconds),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-f",
            "mp3",
            "-y",
            str(output_path),
        ]

    async def record(
        self,
        stream_url: str,
        output_path: Path,
        *,
        duration_seconds: int,
        allowed_hosts: set[str],
    ) -> Path:
        if duration_seconds < 5 or duration_seconds > 60:
            raise AppError(
                "invalid_room_voice_probe_duration",
                "上麦短录音探针时长必须在 5 到 60 秒之间",
                False,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = output_path.with_suffix(".part.mp3")
        temporary_path.unlink(missing_ok=True)
        command = self.build_command(
            stream_url,
            temporary_path,
            duration_seconds=duration_seconds,
            allowed_hosts=allowed_hosts,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(
                process.communicate(), timeout=duration_seconds + 30
            )
        except TimeoutError as exc:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await process.wait()
            temporary_path.unlink(missing_ok=True)
            raise AppError(
                "room_voice_probe_record_timeout",
                "上麦短录音探针超时",
                True,
            ) from exc
        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="replace")[-2000:]
            error = error.replace(stream_url, redact_url(stream_url))
            temporary_path.unlink(missing_ok=True)
            raise AppError(
                "room_voice_probe_record_failed",
                f"上麦短录音失败：{error or '未知错误'}",
                True,
            )
        if (
            not temporary_path.is_file()
            or temporary_path.stat().st_size <= 0
        ):
            temporary_path.unlink(missing_ok=True)
            raise AppError(
                "room_voice_probe_record_empty",
                "上麦短录音未生成有效音频",
                True,
            )
        if (
            temporary_path.stat().st_size
            > self.settings.pocket48_voice_probe_max_bytes
        ):
            temporary_path.unlink(missing_ok=True)
            raise AppError(
                "room_voice_probe_record_too_large",
                "上麦短录音超过允许大小",
                False,
            )
        temporary_path.chmod(0o600)
        temporary_path.replace(output_path)
        return output_path
