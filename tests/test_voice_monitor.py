from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from pocket48_summarizer.clients.pocket48_auth import (
    save_pa_signing_seed,
    save_room_voice_credentials,
)
from pocket48_summarizer.clients.pocket48_voice import (
    Pocket48VoiceCredentials,
    RoomVoiceParticipant,
    RoomVoiceStatus,
)
from pocket48_summarizer.errors import AppError
from pocket48_summarizer.voice_monitor import RoomVoiceMonitor


STREAM_URL = (
    "rtmps://voice.example.test/live/stream?token=private-query"
)


class FakeClient:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.calls: list[tuple[int, int]] = []
        self.closed = False

    async def fetch_status(self, channel_id: int, server_id: int):
        self.calls.append((channel_id, server_id))
        value = next(self.statuses)
        if isinstance(value, Exception):
            raise value
        return value

    async def close(self):
        self.closed = True


class ImmediateProcess:
    def __init__(self, returncode: int = 0):
        self.returncode = returncode

    async def wait(self):
        return self.returncode

    def terminate(self):
        raise AssertionError("completed process should not be terminated")

    def kill(self):
        raise AssertionError("completed process should not be killed")


class BlockingProcess:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        self.returncode = -15 if self.terminated else -9
        return self.returncode

    def terminate(self):
        self.terminated = True
        self._done.set()

    def kill(self):
        self.killed = True
        self._done.set()


class FakeRecorder:
    def __init__(
        self,
        *,
        process=None,
        segment_bytes: int = 4,
    ):
        self.process = process or ImmediateProcess()
        self.segment_bytes = segment_bytes
        self.calls: list[dict[str, object]] = []

    async def start(
        self,
        stream_url: str,
        session_path: Path,
        *,
        duration_seconds: int,
        segment_seconds: int,
    ):
        self.calls.append(
            {
                "stream_url": stream_url,
                "session_path": session_path,
                "duration_seconds": duration_seconds,
                "segment_seconds": segment_seconds,
            }
        )
        if self.segment_bytes:
            segment = session_path / "segments" / "segment-000000.mp3"
            segment.write_bytes(b"x" * self.segment_bytes)
        return self.process


def inactive_status() -> RoomVoiceStatus:
    return RoomVoiceStatus(
        channel_id=7587624,
        server_id=6227955,
        stream_url=None,
        participants=(),
    )


def active_status(stream_url: str = STREAM_URL) -> RoomVoiceStatus:
    return RoomVoiceStatus(
        channel_id=7587624,
        server_id=6227955,
        stream_url=SecretStr(stream_url),
        participants=(
            RoomVoiceParticipant(
                userId="407126",
                userName="杨晔",
                voiceStatus=True,
            ),
        ),
    )


def provision_private_files(settings) -> None:
    save_room_voice_credentials(
        settings.pocket48_voice_credentials_path,
        Pocket48VoiceCredentials(
            token=SecretStr("private-token"),
            app_info=SecretStr('{"deviceId":"private-device"}'),
            user_agent="PocketFans201807/test",
        ),
    )
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr("private-signing-seed"),
    )


def monitor_settings(settings):
    settings.ffmpeg_path = sys.executable
    settings.pocket48_voice_member_id = "407126"
    settings.pocket48_voice_channel_id = "7587624"
    settings.pocket48_voice_server_id = "6227955"
    settings.pocket48_voice_stream_hosts = "voice.example.test"
    settings.pocket48_voice_min_free_bytes = 0
    return settings


def load_only_session(settings) -> tuple[Path, dict]:
    state_paths = list(settings.room_voice_path.glob("*/session.json"))
    assert len(state_paths) == 1
    return state_paths[0], json.loads(
        state_paths[0].read_text(encoding="utf-8")
    )


def test_monitor_settings_are_safe_and_bounded(tmp_path):
    from pocket48_summarizer.config import Settings

    configured = Settings(data_dir=tmp_path)
    assert configured.pocket48_voice_poll_seconds == 60
    assert configured.pocket48_voice_poll_jitter_seconds == 5
    assert configured.pocket48_voice_max_recording_hours == 4
    assert configured.pocket48_voice_segment_seconds == 300
    assert configured.pocket48_voice_max_local_bytes == 2 * 1024**3
    assert configured.pocket48_voice_max_total_bytes == 20 * 1024**3
    assert configured.pocket48_voice_min_free_bytes == 5 * 1024**3
    assert configured.pocket48_voice_allow_public_stream_hosts is False
    assert configured.room_voice_path == tmp_path / "room-voice"
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path,
            pocket48_voice_poll_seconds=29,
        )
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path,
            pocket48_voice_max_local_bytes=2 * 1024**3,
            pocket48_voice_max_total_bytes=1024**3,
        )


@pytest.mark.asyncio
async def test_inactive_poll_loads_credentials_and_keeps_polling(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    client = FakeClient([inactive_status()])
    monitor = RoomVoiceMonitor(
        settings, client_factory=lambda *_: client
    )

    await monitor.poll_once(asyncio.Event())

    assert client.calls == [(7587624, 6227955)]
    status = json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )
    assert status["phase"] == "inactive"
    await monitor.close()


@pytest.mark.asyncio
async def test_waits_for_and_reloads_changed_credentials(settings):
    monitor_settings(settings)
    clients: list[FakeClient] = []

    def factory(*_):
        client = FakeClient([inactive_status()])
        clients.append(client)
        return client

    monitor = RoomVoiceMonitor(settings, client_factory=factory)
    await monitor.poll_once(asyncio.Event())
    assert clients == []
    assert json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )["phase"] == "waiting_credentials"

    provision_private_files(settings)
    await monitor.poll_once(asyncio.Event())
    assert len(clients) == 1

    credentials_path = settings.pocket48_voice_credentials_path
    original = credentials_path.stat().st_mtime_ns
    os.utime(
        credentials_path,
        ns=(original + 1_000_000, original + 1_000_000),
    )
    await monitor.poll_once(asyncio.Event())
    assert len(clients) == 2
    assert clients[0].closed is True
    await monitor.close()


@pytest.mark.asyncio
async def test_auth_failure_pauses_until_credential_mtime_changes(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    auth_error = AppError(
        "room_voice_auth_required",
        "manual replacement required",
        False,
    )
    clients = [
        FakeClient([auth_error]),
        FakeClient([inactive_status()]),
    ]
    monitor = RoomVoiceMonitor(
        settings, client_factory=lambda *_: clients.pop(0)
    )

    with pytest.raises(AppError) as captured:
        await monitor.poll_once(asyncio.Event())
    await monitor._handle_app_error(captured.value)
    await monitor.poll_once(asyncio.Event())
    assert len(clients) == 1

    path = settings.pocket48_voice_credentials_path
    mtime = path.stat().st_mtime_ns
    os.utime(path, ns=(mtime + 1_000_000, mtime + 1_000_000))
    await monitor.poll_once(asyncio.Event())
    assert clients == []
    await monitor.close()


@pytest.mark.asyncio
async def test_operational_errors_do_not_log_secret_messages(
    settings, caplog
):
    monitor_settings(settings)
    monitor = RoomVoiceMonitor(settings)
    await monitor._handle_app_error(
        AppError(
            "room_voice_lookup_failed",
            f"must not log {STREAM_URL}",
            True,
        )
    )
    assert "private-query" not in caplog.text
    assert STREAM_URL not in caplog.text


@pytest.mark.asyncio
async def test_run_writes_readiness_before_credentials_exist(settings):
    monitor_settings(settings)
    monitor = RoomVoiceMonitor(settings)
    stop_event = asyncio.Event()
    task = asyncio.create_task(monitor.run(stop_event))
    for _ in range(20):
        if settings.room_voice_monitor_ready_path.exists():
            break
        await asyncio.sleep(0)
    assert settings.room_voice_monitor_ready_path.is_file()
    assert json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )["phase"] == "waiting_credentials"
    stop_event.set()
    await task
    assert not settings.room_voice_monitor_ready_path.exists()


@pytest.mark.asyncio
async def test_active_recording_persists_private_redacted_state(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=recorder,
        uuid_factory=lambda: uuid.UUID(int=1),
    )

    await monitor.poll_once(asyncio.Event())

    state_path, state = load_only_session(settings)
    assert state["status"] == "ended"
    assert state["member_id"] == "407126"
    assert state["member_name"] == "杨晔"
    assert state["stream"] == {
        "host": "voice.example.test",
        "port": None,
        "scheme": "rtmps",
    }
    assert state["segment_count"] == 1
    serialized = state_path.read_text()
    assert STREAM_URL not in serialized
    assert "private-query" not in serialized
    assert "private-token" not in serialized
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    segment = state_path.parent / "segments" / "segment-000000.mp3"
    assert stat.S_IMODE(segment.stat().st_mode) == 0o600
    assert recorder.calls[0]["duration_seconds"] == 4 * 60 * 60
    assert recorder.calls[0]["segment_seconds"] == 300
    await monitor.close()


@pytest.mark.asyncio
async def test_capture_state_never_persists_guest_identity(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    guest_status = RoomVoiceStatus(
        channel_id=7587624,
        server_id=6227955,
        stream_url=SecretStr(STREAM_URL),
        participants=(
            RoomVoiceParticipant(
                userId="private-guest-id",
                userName="private-guest-name",
                voiceStatus=True,
            ),
        ),
    )
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([guest_status]),
        recorder=FakeRecorder(),
    )

    await monitor.poll_once(asyncio.Event())

    state_path, state = load_only_session(settings)
    assert state["member_name"] == "杨晔"
    serialized = state_path.read_text(encoding="utf-8")
    assert "private-guest-id" not in serialized
    assert "private-guest-name" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "segment_bytes", "expected"),
    [(-1, 4, "partial"), (-1, 0, "failed")],
)
async def test_process_exit_state_reflects_retained_audio(
    settings, returncode, segment_bytes, expected
):
    monitor_settings(settings)
    provision_private_files(settings)
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(
            process=ImmediateProcess(returncode),
            segment_bytes=segment_bytes,
        ),
    )

    await monitor.poll_once(asyncio.Event())

    _, state = load_only_session(settings)
    assert state["status"] == expected
    assert state["segment_count"] == (1 if segment_bytes else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stream_url",
    [
        "rtmps://user:password@voice.example.test/live/stream",
        "rtmps://voice.example.test/live/stream#fragment",
        "https://voice.example.test/live/stream",
        "rtmps://voice.example.test:444/live/stream",
    ],
)
async def test_rejects_unsafe_stream_urls(settings, stream_url):
    monitor_settings(settings)
    settings.pocket48_voice_stream_hosts = ""
    settings.pocket48_voice_allow_public_stream_hosts = True
    monitor = RoomVoiceMonitor(
        settings,
        dns_resolver=lambda *_: asyncio.sleep(
            0, result={"8.8.8.8"}
        ),
    )
    with pytest.raises(AppError):
        await monitor._validate_stream_url(stream_url)


@pytest.mark.asyncio
async def test_public_host_requires_every_dns_address_to_be_global(settings):
    monitor_settings(settings)
    settings.pocket48_voice_stream_hosts = ""
    settings.pocket48_voice_allow_public_stream_hosts = True
    monitor = RoomVoiceMonitor(
        settings,
        dns_resolver=lambda *_: asyncio.sleep(
            0, result={"8.8.8.8", "127.0.0.1"}
        ),
    )
    with pytest.raises(AppError) as captured:
        await monitor._validate_stream_url(STREAM_URL)
    assert captured.value.code == "unsafe_room_voice_stream_host"

    monitor = RoomVoiceMonitor(
        settings,
        dns_resolver=lambda *_: asyncio.sleep(
            0, result={"8.8.8.8", "1.1.1.1"}
        ),
    )
    assert await monitor._validate_stream_url(STREAM_URL) == (
        "rtmps",
        "voice.example.test",
        None,
    )


@pytest.mark.asyncio
async def test_max_bytes_terminates_and_retains_segments(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    settings.pocket48_voice_max_local_bytes = 4
    process = BlockingProcess()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(process=process, segment_bytes=4),
        recording_check_seconds=0,
    )

    await monitor.poll_once(asyncio.Event())

    _, state = load_only_session(settings)
    assert state["status"] == "max_bytes"
    assert state["total_bytes"] == 4
    assert process.terminated is True


@pytest.mark.asyncio
async def test_existing_sessions_do_not_consume_current_session_cap(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    settings.pocket48_voice_max_local_bytes = 5
    old_segments = (
        settings.room_voice_path / "old" / "segments"
    )
    old_segments.mkdir(parents=True)
    (old_segments / "segment-000000.mp3").write_bytes(b"xxxx")
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=recorder,
    )

    await monitor.poll_once(asyncio.Event())

    assert len(recorder.calls) == 1


@pytest.mark.asyncio
async def test_total_storage_cap_blocks_new_recording(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    settings.pocket48_voice_max_total_bytes = 4
    old_segments = settings.room_voice_path / "old" / "segments"
    old_segments.mkdir(parents=True)
    (old_segments / "segment-000000.mp3").write_bytes(b"xxxx")
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=recorder,
    )

    await monitor.poll_once(asyncio.Event())

    assert recorder.calls == []
    status = json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )
    assert status["phase"] == "storage_limit"
    assert status["error_code"] == "room_voice_total_cap_reached"


@pytest.mark.asyncio
async def test_total_storage_remaining_bytes_bound_current_session(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    settings.pocket48_voice_max_local_bytes = 10
    settings.pocket48_voice_max_total_bytes = 6
    old_segments = settings.room_voice_path / "old" / "segments"
    old_segments.mkdir(parents=True)
    (old_segments / "segment-000000.mp3").write_bytes(b"xxxx")
    process = BlockingProcess()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(process=process, segment_bytes=2),
        recording_check_seconds=0,
    )

    await monitor.poll_once(asyncio.Event())

    _, state = load_only_session(settings)
    assert state["status"] == "max_bytes"
    assert process.terminated is True


@pytest.mark.asyncio
async def test_free_space_reserve_blocks_new_recording(
    settings, monkeypatch
):
    monitor_settings(settings)
    provision_private_files(settings)
    settings.pocket48_voice_min_free_bytes = 2_000
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=recorder,
    )
    monkeypatch.setattr(
        "pocket48_summarizer.voice_monitor.shutil.disk_usage",
        lambda _: shutil._ntuple_diskusage(
            total=10_000, used=9_000, free=1_000
        ),
    )

    await monitor.poll_once(asyncio.Event())

    assert recorder.calls == []
    status = json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )
    assert status["error_code"] == "room_voice_insufficient_disk"


@pytest.mark.asyncio
async def test_max_duration_terminates_recording(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    settings.pocket48_voice_max_recording_hours = 1 / 3600
    process = BlockingProcess()
    monotonic = iter([0.0, 2.0])
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(process=process),
        monotonic=lambda: next(monotonic),
        recording_check_seconds=0,
    )

    await monitor.poll_once(asyncio.Event())

    _, state = load_only_session(settings)
    assert state["status"] == "max_duration"
    assert process.terminated is True


@pytest.mark.asyncio
async def test_stop_event_marks_recording_interrupted(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    process = BlockingProcess()
    stop_event = asyncio.Event()
    stop_event.set()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(process=process),
        recording_check_seconds=0,
    )

    await monitor.poll_once(stop_event)

    _, state = load_only_session(settings)
    assert state["status"] == "interrupted"
    assert process.terminated is True


def test_recovers_stale_recording_without_deleting_segments(settings):
    monitor_settings(settings)
    session_path = settings.room_voice_path / "stale"
    segments = session_path / "segments"
    segments.mkdir(parents=True)
    segment = segments / "segment-000000.mp3"
    segment.write_bytes(b"audio")
    state_path = session_path / "session.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "recording",
                "stream_sha256": "a" * 64,
                "errors": [],
            }
        )
    )
    monitor = RoomVoiceMonitor(
        settings,
        now=lambda: datetime(2026, 8, 31, tzinfo=UTC),
    )

    monitor.recover_stale_sessions()

    state = json.loads(state_path.read_text())
    assert state["status"] == "interrupted"
    assert state["segment_count"] == 1
    assert state["total_bytes"] == 5
    assert segment.exists()


@pytest.mark.asyncio
async def test_same_active_stream_does_not_create_immediate_duplicate(
    settings,
):
    monitor_settings(settings)
    provision_private_files(settings)
    client = FakeClient(
        [
            active_status(),
            active_status(),
            inactive_status(),
            active_status(),
        ]
    )
    recorder = FakeRecorder()
    ids = iter([uuid.UUID(int=1), uuid.UUID(int=2)])
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=recorder,
        uuid_factory=lambda: next(ids),
    )
    stop_event = asyncio.Event()

    await monitor.poll_once(stop_event)
    await monitor.poll_once(stop_event)
    assert len(recorder.calls) == 1
    await monitor.poll_once(stop_event)
    await monitor.poll_once(stop_event)
    assert len(recorder.calls) == 2
    assert len(list(settings.room_voice_path.glob("*/session.json"))) == 2
    await monitor.close()


@pytest.mark.asyncio
async def test_changed_stream_fingerprint_obeys_recording_cooldown(
    settings,
):
    monitor_settings(settings)
    provision_private_files(settings)
    client = FakeClient(
        [
            active_status(),
            active_status(
                "rtmps://voice.example.test/live/stream?token=rotated"
            ),
        ]
    )
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=recorder,
    )

    await monitor.poll_once(asyncio.Event())
    await monitor.poll_once(asyncio.Event())

    assert len(recorder.calls) == 1
    assert json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )["phase"] == "recording_cooldown"


def test_stale_final_session_restores_duplicate_guard(settings):
    monitor_settings(settings)
    session_path = settings.room_voice_path / "previous"
    session_path.mkdir(parents=True)
    state_path = session_path / "session.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "stream_sha256": "b" * 64,
            }
        )
    )
    monitor = RoomVoiceMonitor(settings)
    monitor._restore_duplicate_guard()
    assert monitor._await_inactive_fingerprint == "b" * 64


def test_interrupted_session_does_not_block_restart_capture(settings):
    monitor_settings(settings)
    session_path = settings.room_voice_path / "previous"
    session_path.mkdir(parents=True)
    state_path = session_path / "session.json"
    state_path.write_text(
        json.dumps(
            {
                "status": "interrupted",
                "stream_sha256": "b" * 64,
            }
        )
    )
    monitor = RoomVoiceMonitor(settings)

    monitor._restore_duplicate_guard()

    assert monitor._await_inactive_fingerprint is None


@pytest.mark.asyncio
@pytest.mark.parametrize("returncode", [-1, 1])
async def test_failed_stream_is_retried_after_cooldown(
    settings, returncode
):
    monitor_settings(settings)
    provision_private_files(settings)
    client = FakeClient([active_status(), active_status()])
    recorder = FakeRecorder(
        process=ImmediateProcess(returncode), segment_bytes=0
    )
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=recorder,
    )

    await monitor.poll_once(asyncio.Event())
    monitor._recording_cooldown_until = None
    await monitor.poll_once(asyncio.Event())

    assert len(recorder.calls) == 2


@pytest.mark.asyncio
async def test_repeated_empty_failures_use_bounded_backoff(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    now = datetime(2026, 8, 31, tzinfo=UTC)
    client = FakeClient([active_status(), active_status()])
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=FakeRecorder(
            process=ImmediateProcess(1), segment_bytes=0
        ),
        now=lambda: now,
    )

    await monitor.poll_once(asyncio.Event())
    first_cooldown = monitor._recording_cooldown_until
    monitor._recording_cooldown_until = None
    await monitor.poll_once(asyncio.Event())

    assert first_cooldown == now + timedelta(seconds=60)
    assert monitor._recording_cooldown_until == now + timedelta(
        seconds=120
    )
