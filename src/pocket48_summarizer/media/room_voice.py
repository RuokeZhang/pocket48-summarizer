from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..errors import AppError, ConfigurationError
from ..security import (
    inspect_room_voice_stream_url,
    redact_url,
    validate_room_voice_stream_url,
)

ROOM_VOICE_PROTOCOL_WHITELIST = (
    "tcp,tls,rtmp,rtmps"
)


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
            ROOM_VOICE_PROTOCOL_WHITELIST,
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


class RollingProcess(Protocol):
    returncode: int | None

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


@dataclass(slots=True)
class RoomVoiceRollingProcess:
    process: asyncio.subprocess.Process

    @property
    def returncode(self) -> int | None:
        return self.process.returncode

    async def wait(self) -> int:
        return await self.process.wait()

    def terminate(self) -> None:
        self.process.terminate()

    def kill(self) -> None:
        self.process.kill()


class RoomVoiceRollingRecorder:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def build_command(
        self,
        stream_url: str,
        session_path: Path,
        *,
        duration_seconds: int,
        segment_seconds: int,
    ) -> list[str]:
        inspect_room_voice_stream_url(stream_url)
        if duration_seconds <= 0 or segment_seconds <= 0:
            raise AppError(
                "invalid_room_voice_recording_limits",
                "房间上麦录音时长和分段时长必须为正数",
                False,
            )
        session_path = session_path.resolve()
        segment_path = (session_path / "segments").resolve()
        if segment_path.parent != session_path:
            raise ConfigurationError("房间上麦录音目录无效")
        executable = self.settings.ffmpeg_executable()
        if not executable:
            raise ConfigurationError(
                "FFmpeg 未安装或 FFMPEG_PATH 无效，无法录制房间上麦音频"
            )
        return [
            executable,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-protocol_whitelist",
            ROOM_VOICE_PROTOCOL_WHITELIST,
            "-rw_timeout",
            "15000000",
            "-i",
            stream_url,
            "-t",
            str(duration_seconds),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            "-f",
            "segment",
            "-segment_time",
            str(segment_seconds),
            "-segment_format",
            "mp3",
            "-reset_timestamps",
            "1",
            "-y",
            str(segment_path / "segment-%06d.mp3"),
        ]

    async def start(
        self,
        stream_url: str,
        session_path: Path,
        *,
        duration_seconds: int,
        segment_seconds: int,
    ) -> RollingProcess:
        session_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        session_path.chmod(0o700)
        segment_path = session_path / "segments"
        segment_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        segment_path.chmod(0o700)
        command = self.build_command(
            stream_url,
            session_path,
            duration_seconds=duration_seconds,
            segment_seconds=segment_seconds,
        )
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return RoomVoiceRollingProcess(process)
