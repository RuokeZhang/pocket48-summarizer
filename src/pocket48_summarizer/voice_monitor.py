from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import random
import shutil
import socket
import stat
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .clients.pocket48_auth import (
    load_pa_generator,
    load_room_voice_credentials,
)
from .clients.pocket48_voice import (
    Pocket48VoiceClient,
    Pocket48VoiceCredentials,
    RoomVoiceStatus,
)
from .config import Settings
from .errors import AppError, ConfigurationError
from .media.room_voice import (
    RollingProcess,
    RoomVoiceRollingRecorder,
)
from .security import inspect_room_voice_stream_url

LOGGER = logging.getLogger(__name__)
RECORDING_STATES = {"starting", "recording"}
FINAL_SESSION_STATES = {
    "completed",
    "partial",
    "interrupted",
    "max_duration",
    "max_bytes",
    "failed",
    "ended",
}
ALLOWED_STREAM_PORTS = {None, 1935, 443}


class VoiceClient(Protocol):
    async def fetch_status(
        self, channel_id: int, server_id: int
    ) -> RoomVoiceStatus: ...

    async def close(self) -> None: ...


ClientFactory = Callable[
    [Settings, Pocket48VoiceCredentials], VoiceClient
]
DnsResolver = Callable[[str, int], Awaitable[set[str]]]


class RoomVoiceMonitor:
    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        recorder: RoomVoiceRollingRecorder | None = None,
        dns_resolver: DnsResolver | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
        recording_check_seconds: float = 1.0,
    ) -> None:
        self.settings = settings
        self.client_factory = client_factory or Pocket48VoiceClient
        self.recorder = recorder or RoomVoiceRollingRecorder(settings)
        self.dns_resolver = dns_resolver or self._resolve_dns
        self.sleeper = sleeper
        self.jitter = jitter
        self.now = now or (lambda: datetime.now(UTC))
        self.monotonic = monotonic
        self.uuid_factory = uuid_factory
        self.recording_check_seconds = recording_check_seconds
        self._client: VoiceClient | None = None
        self._loaded_private_mtimes: tuple[int, int] | None = None
        self._auth_paused_mtime: int | None = None
        self._await_inactive_fingerprint: str | None = None
        self._recording_cooldown_until: datetime | None = None
        self._consecutive_recording_failures = 0
        self._ready_value: str | None = None
        self._secured_segments: set[Path] = set()

    async def run(self, stop_event: asyncio.Event) -> None:
        self.settings.prepare_directories()
        self.recover_stale_sessions()
        self._restore_duplicate_guard()
        self._write_readiness()
        self._write_monitor_status("waiting")
        try:
            while not stop_event.is_set():
                try:
                    await self.poll_once(stop_event)
                except AppError as exc:
                    await self._handle_app_error(exc)
                if not stop_event.is_set():
                    await self._sleep_until_next_poll(stop_event)
        finally:
            await self.close()
            self._remove_readiness()

    async def poll_once(self, stop_event: asyncio.Event) -> None:
        target = self._configured_target()
        if target is None:
            self._write_monitor_status("waiting_configuration")
            return
        member_id, channel_id, server_id = target
        private_mtimes = self._private_file_mtimes()
        if private_mtimes is None:
            await self._replace_client(None)
            self._write_monitor_status("waiting_credentials")
            return
        credentials_mtime = private_mtimes[0]
        if (
            self._auth_paused_mtime is not None
            and credentials_mtime == self._auth_paused_mtime
        ):
            self._write_monitor_status("auth_paused")
            return
        if private_mtimes != self._loaded_private_mtimes:
            await self._load_client(private_mtimes)
        if self._client is None:
            raise ConfigurationError(
                "房间上麦监控客户端未能安全初始化"
            )

        self._write_monitor_status("polling")
        status = await self._client.fetch_status(channel_id, server_id)
        if status.stream_url is None:
            if not status.active:
                self._await_inactive_fingerprint = None
                self._recording_cooldown_until = None
                self._consecutive_recording_failures = 0
            self._write_monitor_status(
                "inactive" if not status.active else "active_without_stream"
            )
            return

        stream_url = status.stream_url.get_secret_value()
        endpoint = await self._validate_stream_url(stream_url)
        fingerprint = hashlib.sha256(
            stream_url.encode("utf-8")
        ).hexdigest()
        if fingerprint == self._await_inactive_fingerprint:
            self._write_monitor_status("awaiting_inactive")
            return
        if (
            self._recording_cooldown_until is not None
            and self.now() < self._recording_cooldown_until
        ):
            self._write_monitor_status("recording_cooldown")
            return
        storage_error, recording_byte_limit = self._recording_byte_budget()
        if storage_error is not None:
            self._write_monitor_status(
                "storage_limit", error_code=storage_error
            )
            return
        outcome = await self._record_session(
            stop_event=stop_event,
            member_id=member_id,
            channel_id=channel_id,
            server_id=server_id,
            stream_url=stream_url,
            endpoint=endpoint,
            fingerprint=fingerprint,
            recording_byte_limit=recording_byte_limit,
        )
        if outcome in {"completed", "max_duration", "max_bytes"}:
            self._await_inactive_fingerprint = fingerprint
        else:
            self._await_inactive_fingerprint = None
        if outcome == "failed":
            self._consecutive_recording_failures += 1
        else:
            self._consecutive_recording_failures = 0
        cooldown_seconds = self.settings.pocket48_voice_poll_seconds
        if self._consecutive_recording_failures:
            cooldown_seconds = min(
                3600,
                cooldown_seconds
                * 2
                ** min(self._consecutive_recording_failures - 1, 6),
            )
        self._recording_cooldown_until = self.now() + timedelta(
            seconds=cooldown_seconds
        )

    async def close(self) -> None:
        await self._replace_client(None)

    def recover_stale_sessions(self) -> None:
        root = self.settings.room_voice_path
        if not root.exists():
            return
        for state_path in sorted(root.glob("*/session.json")):
            try:
                state = self._read_json_object(state_path)
            except (OSError, json.JSONDecodeError, ConfigurationError) as exc:
                LOGGER.warning(
                    "Unable to inspect room voice session state at %s: %s",
                    state_path,
                    exc,
                )
                continue
            if state.get("status") not in RECORDING_STATES:
                continue
            segment_count, total_bytes = self._segment_stats(
                state_path.parent
            )
            state.update(
                {
                    "status": "interrupted",
                    "ended_at": self.now().isoformat(),
                    "segment_count": segment_count,
                    "total_bytes": total_bytes,
                }
            )
            self._append_error(
                state,
                "monitor_restarted",
                "监控进程重启时发现未完成的录音会话",
            )
            self._write_private_json(state_path, state)

    async def _handle_app_error(self, exc: AppError) -> None:
        if exc.code == "room_voice_auth_required":
            credentials_path = (
                self.settings.pocket48_voice_credentials_path
            )
            try:
                self._auth_paused_mtime = (
                    credentials_path.stat().st_mtime_ns
                )
            except FileNotFoundError:
                self._auth_paused_mtime = None
            await self._replace_client(None)
            self._write_monitor_status(
                "auth_paused", error_code=exc.code
            )
            LOGGER.error(
                "Room voice credentials require manual replacement"
            )
            return
        self._write_monitor_status("error", error_code=exc.code)
        LOGGER.warning(
            "Room voice monitor operation failed (%s)",
            exc.code,
        )

    async def _load_client(
        self, private_mtimes: tuple[int, int]
    ) -> None:
        pa_generator = load_pa_generator(
            self.settings.pocket48_pa_signing_seed_path
        )
        credentials = load_room_voice_credentials(
            self.settings.pocket48_voice_credentials_path,
            pa_provider=pa_generator.generate,
        )
        await self._replace_client(
            self.client_factory(self.settings, credentials)
        )
        self._loaded_private_mtimes = private_mtimes
        self._auth_paused_mtime = None

    async def _replace_client(
        self, client: VoiceClient | None
    ) -> None:
        previous = self._client
        self._client = client
        if previous is not None:
            await previous.close()
        if client is None:
            self._loaded_private_mtimes = None

    def _configured_target(
        self,
    ) -> tuple[str | None, int, int] | None:
        channel_value = self.settings.pocket48_voice_channel_id
        server_value = self.settings.pocket48_voice_server_id
        if not channel_value and not server_value:
            return None
        if not channel_value or not server_value:
            raise ConfigurationError(
                "房间上麦监控必须同时配置 channel ID 和 server ID"
            )
        channel_id = self._positive_id(channel_value, "channel")
        server_id = self._positive_id(server_value, "server")
        member_value = self.settings.pocket48_voice_member_id
        member_id = (
            str(self._positive_id(member_value, "member"))
            if member_value
            else None
        )
        return member_id, channel_id, server_id

    @staticmethod
    def _positive_id(value: str, label: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"房间上麦监控 {label} ID 必须是正整数"
            ) from exc
        if parsed <= 0:
            raise ConfigurationError(
                f"房间上麦监控 {label} ID 必须是正整数"
            )
        return parsed

    def _private_file_mtimes(self) -> tuple[int, int] | None:
        credentials_path = (
            self.settings.pocket48_voice_credentials_path
        )
        pa_path = self.settings.pocket48_pa_signing_seed_path
        try:
            return (
                credentials_path.stat().st_mtime_ns,
                pa_path.stat().st_mtime_ns,
            )
        except FileNotFoundError:
            return None

    async def _validate_stream_url(
        self, stream_url: str
    ) -> tuple[str, str, int | None]:
        scheme, hostname, port = inspect_room_voice_stream_url(
            stream_url
        )
        if port not in ALLOWED_STREAM_PORTS:
            raise AppError(
                "invalid_room_voice_stream_port",
                "房间上麦流端口不在允许范围内",
                False,
            )
        if (
            hostname
            in self.settings.pocket48_voice_stream_host_list
        ):
            return scheme, hostname, port
        if not self.settings.pocket48_voice_allow_public_stream_hosts:
            raise AppError(
                "unapproved_room_voice_stream_host",
                "房间上麦流主机尚未加入本地允许列表",
                False,
            )
        try:
            addresses = await self.dns_resolver(
                hostname,
                port or (443 if scheme == "rtmps" else 1935),
            )
            public_addresses = [
                ipaddress.ip_address(address).is_global
                for address in addresses
            ]
        except (OSError, ValueError) as exc:
            raise AppError(
                "unsafe_room_voice_stream_host",
                "房间上麦流主机无法安全解析",
                True,
            ) from exc
        if not public_addresses or not all(public_addresses):
            raise AppError(
                "unsafe_room_voice_stream_host",
                "房间上麦流主机未解析到仅包含公网地址的结果",
                False,
            )
        return scheme, hostname, port

    async def _record_session(
        self,
        *,
        stop_event: asyncio.Event,
        member_id: str | None,
        channel_id: int,
        server_id: int,
        stream_url: str,
        endpoint: tuple[str, str, int | None],
        fingerprint: str,
        recording_byte_limit: int,
    ) -> str:
        session_id = str(self.uuid_factory())
        session_path = self.settings.room_voice_path / session_id
        segment_path = session_path / "segments"
        session_path.mkdir(parents=True, exist_ok=False, mode=0o700)
        session_path.chmod(0o700)
        segment_path.mkdir(mode=0o700)
        segment_path.chmod(0o700)
        state_path = session_path / "session.json"
        state: dict[str, Any] = {
            "version": 1,
            "session_id": session_id,
            "member_id": member_id,
            "channel_id": str(channel_id),
            "server_id": str(server_id),
            "member_name": (
                self.settings.pocket48_voice_member_name or None
            ),
            "started_at": self.now().isoformat(),
            "ended_at": None,
            "status": "starting",
            "stream": {
                "scheme": endpoint[0],
                "host": endpoint[1],
                "port": endpoint[2],
            },
            "stream_sha256": fingerprint,
            "segment_count": 0,
            "total_bytes": 0,
            "errors": [],
        }
        self._write_private_json(state_path, state)
        duration_seconds = int(
            self.settings.pocket48_voice_max_recording_hours * 3600
        )
        try:
            process = await self.recorder.start(
                stream_url,
                session_path,
                duration_seconds=duration_seconds,
                segment_seconds=self.settings.pocket48_voice_segment_seconds,
            )
        except AppError as exc:
            self._finalize_state(
                state_path,
                state,
                "failed",
                error=(
                    exc.code,
                    "房间上麦录音进程未能安全启动",
                ),
            )
            raise
        except OSError as exc:
            self._finalize_state(
                state_path,
                state,
                "failed",
                error=(
                    "room_voice_ffmpeg_start_failed",
                    "无法启动 FFmpeg 房间上麦录音进程",
                ),
            )
            raise AppError(
                "room_voice_ffmpeg_start_failed",
                "无法启动 FFmpeg 房间上麦录音进程",
                True,
            ) from exc

        state["status"] = "recording"
        self._write_private_json(state_path, state)
        self._write_monitor_status(
            "recording", session_id=session_id
        )
        started = self.monotonic()
        outcome: str | None = None
        wait_task = asyncio.create_task(process.wait())
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            while outcome is None:
                await asyncio.wait(
                    {wait_task, stop_task},
                    timeout=self.recording_check_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                segment_count, total_bytes = self._segment_stats(
                    session_path
                )
                if (
                    state["segment_count"] != segment_count
                    or state["total_bytes"] != total_bytes
                ):
                    state["segment_count"] = segment_count
                    state["total_bytes"] = total_bytes
                    self._write_private_json(state_path, state)
                    self._write_monitor_status(
                        "recording", session_id=session_id
                    )
                if stop_task.done() and stop_task.result():
                    outcome = "interrupted"
                elif (
                    total_bytes
                    >= recording_byte_limit
                ):
                    outcome = "max_bytes"
                elif self.monotonic() - started >= (
                    duration_seconds
                    - min(
                        2.0,
                        duration_seconds * 0.1,
                        max(0.1, self.recording_check_seconds),
                    )
                ):
                    outcome = "max_duration"
                elif wait_task.done():
                    returncode = wait_task.result()
                    if returncode == 0 and segment_count > 0:
                        outcome = "ended"
                    elif segment_count > 0:
                        outcome = "partial"
                    else:
                        outcome = "failed"
                if outcome in {
                    "interrupted",
                    "max_bytes",
                    "max_duration",
                }:
                    await self._terminate_process(process, wait_task)
        except asyncio.CancelledError:
            await self._terminate_process(process, wait_task)
            self._finalize_state(
                state_path,
                state,
                "interrupted",
                error=(
                    "monitor_cancelled",
                    "监控进程取消时终止了当前录音",
                ),
            )
            raise
        finally:
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)

        error: tuple[str, str] | None = None
        if outcome == "failed":
            error = (
                "room_voice_ffmpeg_failed",
                "FFmpeg 未生成可保留的房间上麦录音分段",
            )
        elif outcome == "partial":
            error = (
                "room_voice_ffmpeg_partial",
                "FFmpeg 异常退出，已保留完成的房间上麦录音分段",
            )
        self._finalize_state(
            state_path, state, outcome or "failed", error=error
        )
        self._write_monitor_status(
            "waiting_next_poll", session_id=session_id
        )
        return outcome or "failed"

    def _recording_byte_budget(self) -> tuple[str | None, int]:
        total_bytes = 0
        root = self.settings.room_voice_path
        if root.is_dir():
            for session_path in root.iterdir():
                if not session_path.is_dir():
                    continue
                _, session_bytes = self._segment_stats(session_path)
                total_bytes += session_bytes
                if (
                    total_bytes
                    >= self.settings.pocket48_voice_max_total_bytes
                ):
                    return "room_voice_total_cap_reached", 0
        remaining_total_bytes = (
            self.settings.pocket48_voice_max_total_bytes - total_bytes
        )
        try:
            disk_path = root if root.exists() else self.settings.data_dir
            free_bytes = shutil.disk_usage(disk_path).free
        except OSError:
            return "room_voice_disk_check_failed", 0
        remaining_disk_bytes = (
            free_bytes - self.settings.pocket48_voice_min_free_bytes
        )
        if remaining_disk_bytes <= 0:
            return "room_voice_insufficient_disk", 0
        return None, min(
            self.settings.pocket48_voice_max_local_bytes,
            remaining_total_bytes,
            remaining_disk_bytes,
        )

    async def _terminate_process(
        self,
        process: RollingProcess,
        wait_task: asyncio.Task[int],
    ) -> None:
        if wait_task.done():
            return
        process.terminate()
        try:
            await asyncio.wait_for(
                asyncio.shield(wait_task), timeout=10
            )
        except TimeoutError:
            process.kill()
            await wait_task

    def _finalize_state(
        self,
        state_path: Path,
        state: dict[str, Any],
        outcome: str,
        *,
        error: tuple[str, str] | None = None,
    ) -> None:
        if outcome not in FINAL_SESSION_STATES:
            raise ConfigurationError(
                "房间上麦录音会话结束状态无效"
            )
        segment_count, total_bytes = self._segment_stats(
            state_path.parent
        )
        state.update(
            {
                "status": outcome,
                "ended_at": self.now().isoformat(),
                "segment_count": segment_count,
                "total_bytes": total_bytes,
            }
        )
        if error is not None:
            self._append_error(state, error[0], error[1])
        self._write_private_json(state_path, state)

    @staticmethod
    def _append_error(
        state: dict[str, Any], code: str, message: str
    ) -> None:
        errors = state.setdefault("errors", [])
        if not isinstance(errors, list):
            state["errors"] = errors = []
        errors.append({"code": code, "message": message})

    def _segment_stats(self, session_path: Path) -> tuple[int, int]:
        segment_path = session_path / "segments"
        if not segment_path.is_dir():
            return 0, 0
        count = 0
        total_bytes = 0
        for path in segment_path.glob("segment-*.mp3"):
            try:
                metadata = path.stat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                continue
            if path not in self._secured_segments:
                path.chmod(0o600)
                self._secured_segments.add(path)
            count += 1
            total_bytes += metadata.st_size
        return count, total_bytes

    def _restore_duplicate_guard(self) -> None:
        newest: tuple[int, str] | None = None
        for state_path in self.settings.room_voice_path.glob(
            "*/session.json"
        ):
            try:
                state = self._read_json_object(state_path)
                modified = state_path.stat().st_mtime_ns
            except (
                OSError,
                json.JSONDecodeError,
                ConfigurationError,
            ):
                continue
            fingerprint = state.get("stream_sha256")
            if (
                state.get("status")
                in {
                    "completed",
                    "max_duration",
                    "max_bytes",
                }
                and isinstance(fingerprint, str)
                and len(fingerprint) == 64
                and (newest is None or modified > newest[0])
            ):
                newest = (modified, fingerprint)
        if newest is not None:
            self._await_inactive_fingerprint = newest[1]

    async def _sleep_until_next_poll(
        self, stop_event: asyncio.Event
    ) -> None:
        delay = self.settings.pocket48_voice_poll_seconds
        jitter_limit = (
            self.settings.pocket48_voice_poll_jitter_seconds
        )
        delay += self.jitter(0, jitter_limit)
        delay = max(30.0, min(delay, 3600.0 + jitter_limit))
        sleep_task = asyncio.create_task(self.sleeper(delay))
        stop_task = asyncio.create_task(stop_event.wait())
        try:
            await asyncio.wait(
                {sleep_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (sleep_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                sleep_task, stop_task, return_exceptions=True
            )

    def _write_readiness(self) -> None:
        self._ready_value = str(Path.cwd().resolve())
        self._write_private_text(
            self.settings.room_voice_monitor_ready_path,
            self._ready_value,
        )

    def _remove_readiness(self) -> None:
        path = self.settings.room_voice_monitor_ready_path
        if self._ready_value is None:
            return
        try:
            if path.read_text(encoding="utf-8") == self._ready_value:
                path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning(
                "Unable to remove room voice monitor readiness file"
            )

    def _write_monitor_status(
        self,
        phase: str,
        *,
        error_code: str | None = None,
        session_id: str | None = None,
    ) -> None:
        payload = {
            "version": 1,
            "phase": phase,
            "updated_at": self.now().isoformat(),
            "channel_id": self.settings.pocket48_voice_channel_id,
            "server_id": self.settings.pocket48_voice_server_id,
            "session_id": session_id,
            "error_code": error_code,
        }
        self._write_private_json(
            self.settings.room_voice_monitor_status_path,
            payload,
        )

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ConfigurationError(
                "房间上麦录音会话状态文件格式无效"
            )
        return payload

    @classmethod
    def _write_private_json(
        cls, path: Path, payload: dict[str, Any]
    ) -> None:
        cls._write_private_text(
            path,
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )

    @staticmethod
    def _write_private_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_path = path.with_name(path.name + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary_path, flags, 0o600)
        try:
            with os.fdopen(
                descriptor, "w", encoding="utf-8"
            ) as handle:
                handle.write(value)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        temporary_path.chmod(0o600)
        temporary_path.replace(path)
        path.chmod(0o600)

    @staticmethod
    async def _resolve_dns(hostname: str, port: int) -> set[str]:
        results = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
        return {str(result[4][0]) for result in results}
