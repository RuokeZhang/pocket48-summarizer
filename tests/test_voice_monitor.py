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
from pydantic_settings import SettingsError

from pocket48_summarizer.clients.pocket48_auth import (
    save_pa_signing_seed,
    save_room_voice_credentials,
)
from pocket48_summarizer.clients.pocket48_voice import (
    MemberRoom,
    Pocket48VoiceCredentials,
    RoomVoiceParticipant,
    RoomVoiceStatus,
)
from pocket48_summarizer.config import AdditionalRoomVoiceTarget, Settings
from pocket48_summarizer.errors import AppError
from pocket48_summarizer.voice_monitor import (
    MEMBER_ROOM_REFRESH_SECONDS,
    RECONNECT_COOLOFF_SECONDS,
    RECONNECT_MAX_ATTEMPTS,
    RECONNECT_POLL_SECONDS,
    RECONNECT_WINDOW_SECONDS,
    RoomVoiceMonitor,
    RoomVoiceStorageCoordinator,
)
from pocket48_summarizer.voice_monitor_cli import run_voice_monitor


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


class ResolvingFakeClient(FakeClient):
    def __init__(self, statuses, room: MemberRoom):
        super().__init__(statuses)
        self.room = room
        self.resolve_calls: list[int] = []

    async def resolve_member_room(self, member_id: int) -> MemberRoom:
        self.resolve_calls.append(member_id)
        return self.room


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
        create_empty_segment: bool = False,
    ):
        self.process = process or ImmediateProcess()
        self.segment_bytes = segment_bytes
        self.create_empty_segment = create_empty_segment
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
        if self.segment_bytes or self.create_empty_segment:
            segment = session_path / "segments" / "segment-000000.mp3"
            segment.write_bytes(b"x" * self.segment_bytes)
        return self.process


class FailingRecorder:
    async def start(self, *_args, **_kwargs):
        raise OSError("ffmpeg unavailable")


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


def test_additional_targets_parse_and_clone_with_safe_paths(tmp_path):
    configured = Settings(
        data_dir=tmp_path,
        pocket48_voice_additional_targets_json=[
            {
                "id": "wang-ruiqi",
                "name": "王睿琦",
                "member_id": "530390",
            },
            {
                "id": "yang-bingyi",
                "name": "杨冰怡",
                "member_id": "6744",
            },
        ],
    )

    primary, wang, yang = configured.room_voice_monitor_settings()

    assert primary.pocket48_voice_monitor_id == "primary"
    assert (
        primary.room_voice_monitor_ready_path
        == tmp_path / "room-voice-monitor-ready"
    )
    assert (
        primary.room_voice_monitor_status_path
        == tmp_path / "room-voice-monitor-status.json"
    )
    assert wang.pocket48_voice_monitor_id == "wang-ruiqi"
    assert wang.pocket48_voice_member_name == "王睿琦"
    assert wang.pocket48_voice_member_id == "530390"
    assert wang.pocket48_voice_channel_id is None
    assert (
        wang.room_voice_monitor_ready_path
        == tmp_path / "room-voice-monitor-wang-ruiqi-ready"
    )
    assert (
        wang.room_voice_monitor_status_path
        == tmp_path / "room-voice-monitor-wang-ruiqi-status.json"
    )
    assert yang.pocket48_voice_monitor_id == "yang-bingyi"
    assert yang.pocket48_voice_member_name == "杨冰怡"
    assert yang.pocket48_voice_member_id == "6744"
    assert yang.pocket48_voice_channel_id is None
    assert (
        yang.room_voice_monitor_ready_path
        == tmp_path / "room-voice-monitor-yang-bingyi-ready"
    )
    assert (
        yang.room_voice_monitor_status_path
        == tmp_path / "room-voice-monitor-yang-bingyi-status.json"
    )
def test_production_target_file_configures_additional_members():
    target_env = (
        Path(__file__).parents[1] / "deploy" / "room-voice-target.env"
    )

    configured = Settings(_env_file=target_env)

    assert configured.pocket48_voice_member_name == "杨晔"
    assert configured.pocket48_voice_channel_id == "7587624"
    assert configured.pocket48_voice_server_id == "6227955"
    assert configured.pocket48_voice_additional_targets_json == (
        AdditionalRoomVoiceTarget(
            id="wang-ruiqi",
            name="王睿琦",
            member_id=530390,
        ),
        AdditionalRoomVoiceTarget(
            id="yang-bingyi",
            name="杨冰怡",
            member_id=6744,
        ),
    )


@pytest.mark.parametrize(
    "target",
    [
        {
            "id": "primary",
            "name": "王睿琦",
            "member_id": 530390,
        },
        {"id": "../wang", "name": "王睿琦", "member_id": 530390},
        {"id": "wang/ruiqi", "name": "王睿琦", "member_id": 530390},
        {"id": "..", "name": "王睿琦", "member_id": 530390},
        {
            "id": "wang-ruiqi",
            "name": "王睿琦",
            "member_id": 0,
        },
        {
            "id": "wang-ruiqi",
            "name": "王睿琦",
            "member_id": 530390,
            "channel_id": 1279498,
        },
        {
            "id": "wang-ruiqi",
            "name": "王睿琦",
            "member_id": 530390,
            "unreviewed": True,
        },
    ],
)
def test_rejects_invalid_additional_targets(tmp_path, target):
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path,
            pocket48_voice_additional_targets_json=[target],
        )


def test_rejects_duplicate_and_too_many_additional_targets(tmp_path):
    duplicate = {
        "id": "wang-ruiqi",
        "name": "王睿琦",
        "member_id": 530390,
    }
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path,
            pocket48_voice_additional_targets_json=[
                duplicate,
                duplicate,
            ],
        )
    with pytest.raises(ValidationError):
        Settings(
            data_dir=tmp_path,
            pocket48_voice_additional_targets_json=[
                {
                    "id": f"target-{index}",
                    "name": f"Target {index}",
                    "member_id": index + 1,
                }
                for index in range(11)
            ],
        )


def test_rejects_malformed_additional_target_json(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(
        "POCKET48_VOICE_ADDITIONAL_TARGETS_JSON", "{not-json"
    )
    with pytest.raises(SettingsError):
        Settings(_env_file=None, data_dir=tmp_path)


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
async def test_member_only_target_resolves_room_before_status(settings):
    settings.pocket48_voice_monitor_id = "wang-ruiqi"
    settings.pocket48_voice_member_name = "王睿琦"
    settings.pocket48_voice_member_id = "530390"
    settings.pocket48_voice_channel_id = None
    settings.pocket48_voice_server_id = None
    provision_private_files(settings)
    first_status = RoomVoiceStatus(
        channel_id=1279498,
        server_id=7654321,
        stream_url=None,
        participants=(),
    )
    second_status = RoomVoiceStatus(
        channel_id=1279500,
        server_id=7654322,
        stream_url=None,
        participants=(),
    )
    client = ResolvingFakeClient(
        [first_status, second_status],
        MemberRoom(
            member_id=530390,
            channel_id=1279498,
            server_id=7654321,
        ),
    )
    monitor = RoomVoiceMonitor(
        settings, client_factory=lambda *_: client
    )

    current = [datetime(2026, 9, 1, tzinfo=UTC)]
    monitor.now = lambda: current[0]
    await monitor.poll_once(asyncio.Event())
    client.room = MemberRoom(
        member_id=530390,
        channel_id=1279500,
        server_id=7654322,
    )
    current[0] += timedelta(seconds=MEMBER_ROOM_REFRESH_SECONDS)
    await monitor.poll_once(asyncio.Event())

    assert client.resolve_calls == [530390, 530390]
    assert client.calls == [
        (1279498, 7654321),
        (1279500, 7654322),
    ]
    safe_status = json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )
    assert safe_status["monitor_id"] == "wang-ruiqi"
    assert safe_status["channel_id"] == "1279500"
    assert safe_status["server_id"] == "7654322"
    await monitor.close()


@pytest.mark.asyncio
async def test_member_room_cache_refreshes_after_lookup_failure(settings):
    settings.pocket48_voice_monitor_id = "wang-ruiqi"
    settings.pocket48_voice_member_name = "王睿琦"
    settings.pocket48_voice_member_id = "530390"
    settings.pocket48_voice_channel_id = None
    settings.pocket48_voice_server_id = None
    provision_private_files(settings)
    lookup_error = AppError(
        "room_voice_lookup_failed", "room moved", True
    )
    client = ResolvingFakeClient(
        [
            lookup_error,
            RoomVoiceStatus(
                channel_id=1279498,
                server_id=7654321,
                stream_url=None,
                participants=(),
            ),
        ],
        MemberRoom(
            member_id=530390,
            channel_id=1279498,
            server_id=7654321,
        ),
    )
    monitor = RoomVoiceMonitor(
        settings, client_factory=lambda *_: client
    )

    with pytest.raises(AppError):
        await monitor.poll_once(asyncio.Event())
    await monitor.poll_once(asyncio.Event())

    assert client.resolve_calls == [530390, 530390]
    await monitor.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [False, True])
async def test_member_room_refresh_failure_uses_cached_room(
    settings, retryable
):
    settings.pocket48_voice_monitor_id = "wang-ruiqi"
    settings.pocket48_voice_member_name = "王睿琦"
    settings.pocket48_voice_member_id = "530390"
    settings.pocket48_voice_channel_id = None
    settings.pocket48_voice_server_id = None
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, tzinfo=UTC)]
    client = ResolvingFakeClient(
        [inactive_status(), inactive_status()],
        MemberRoom(
            member_id=530390,
            channel_id=7587624,
            server_id=6227955,
        ),
    )
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=MEMBER_ROOM_REFRESH_SECONDS)

    async def fail_refresh(_member_id: int) -> MemberRoom:
        raise AppError(
            "room_voice_lookup_failed",
            "temporary room lookup failure",
            retryable,
        )

    client.resolve_member_room = fail_refresh
    await monitor.poll_once(asyncio.Event())

    assert client.calls == [
        (7587624, 6227955),
        (7587624, 6227955),
    ]
    assert monitor._resolved_member_room == MemberRoom(
        member_id=530390,
        channel_id=7587624,
        server_id=6227955,
    )


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
    assert state["monitor_id"] == "primary"
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
async def test_concurrent_monitors_do_not_block_and_cancel_safely(settings):
    monitor_settings(settings)
    settings.pocket48_voice_additional_targets_json = (
        AdditionalRoomVoiceTarget(
            id="wang-ruiqi",
            name="王睿琦",
            member_id=530390,
            channel_id=1279498,
            server_id=7654321,
        ),
    )
    provision_private_files(settings)
    process = BlockingProcess()
    primary_recorder = FakeRecorder(process=process)
    additional_client = FakeClient(
        [
            RoomVoiceStatus(
                channel_id=1279498,
                server_id=7654321,
                stream_url=None,
                participants=(),
            )
        ]
    )

    def factory(target_settings, storage_coordinator):
        if target_settings.pocket48_voice_monitor_id == "primary":
            return RoomVoiceMonitor(
                target_settings,
                client_factory=lambda *_: FakeClient([active_status()]),
                recorder=primary_recorder,
                storage_coordinator=storage_coordinator,
            )
        return RoomVoiceMonitor(
            target_settings,
            client_factory=lambda *_: additional_client,
            storage_coordinator=storage_coordinator,
        )

    task = asyncio.create_task(
        run_voice_monitor(settings, monitor_factory=factory)
    )
    for _ in range(100):
        if primary_recorder.calls and additional_client.calls:
            break
        await asyncio.sleep(0)
    assert primary_recorder.calls
    assert additional_client.calls == [(1279498, 7654321)]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    _, state = load_only_session(settings)
    assert state["status"] == "interrupted"
    assert state["segment_count"] == 1
    assert process.terminated is True
    for target_settings in settings.room_voice_monitor_settings():
        assert not target_settings.room_voice_monitor_ready_path.exists()


@pytest.mark.asyncio
async def test_shared_credential_replacement_wakes_each_monitor(settings):
    monitor_settings(settings)
    settings.pocket48_voice_additional_targets_json = (
        AdditionalRoomVoiceTarget(
            id="wang-ruiqi",
            name="王睿琦",
            member_id=530390,
            channel_id=1279498,
            server_id=7654321,
        ),
    )
    provision_private_files(settings)
    target_settings = settings.room_voice_monitor_settings()
    auth_error = AppError(
        "room_voice_auth_required",
        "manual replacement required",
        False,
    )
    client_queues = {
        "primary": [
            FakeClient([auth_error]),
            FakeClient([inactive_status()]),
        ],
        "wang-ruiqi": [
            FakeClient([auth_error]),
            FakeClient(
                [
                    RoomVoiceStatus(
                        channel_id=1279498,
                        server_id=7654321,
                        stream_url=None,
                        participants=(),
                    )
                ]
            ),
        ],
    }
    monitors = [
        RoomVoiceMonitor(
            item,
            client_factory=(
                lambda *_args, monitor_id=item.pocket48_voice_monitor_id: (
                    client_queues[monitor_id].pop(0)
                )
            ),
        )
        for item in target_settings
    ]

    for monitor in monitors:
        with pytest.raises(AppError) as captured:
            await monitor.poll_once(asyncio.Event())
        await monitor._handle_app_error(captured.value)
        await monitor.poll_once(asyncio.Event())
    assert all(len(queue) == 1 for queue in client_queues.values())

    path = settings.pocket48_voice_credentials_path
    mtime = path.stat().st_mtime_ns
    os.utime(path, ns=(mtime + 1_000_000, mtime + 1_000_000))
    for monitor in monitors:
        await monitor.poll_once(asyncio.Event())
        await monitor.close()
    assert all(not queue for queue in client_queues.values())


@pytest.mark.asyncio
async def test_unexpected_monitor_crash_cancels_sibling(settings):
    settings.pocket48_voice_additional_targets_json = (
        AdditionalRoomVoiceTarget(
            id="wang-ruiqi",
            name="王睿琦",
            member_id=530390,
        ),
    )
    sibling_cancelled = asyncio.Event()

    class FakeMonitor:
        def __init__(self, target_settings, _storage_coordinator):
            self.monitor_id = target_settings.pocket48_voice_monitor_id

        async def run(self, stop_event):
            if self.monitor_id == "primary":
                await asyncio.sleep(0)
                raise RuntimeError("unexpected monitor crash")
            try:
                await asyncio.Event().wait()
            finally:
                sibling_cancelled.set()

        async def close(self):
            return None

    with pytest.raises(ExceptionGroup) as captured:
        await run_voice_monitor(
            settings, monitor_factory=FakeMonitor
        )

    assert any(
        isinstance(error, RuntimeError)
        for error in captured.value.exceptions
    )
    assert sibling_cancelled.is_set()


@pytest.mark.asyncio
async def test_shared_storage_coordinator_reserves_capacity_once(
    settings, monkeypatch
):
    monitor_settings(settings)
    settings.pocket48_voice_max_local_bytes = 8
    settings.pocket48_voice_max_total_bytes = 10
    settings.pocket48_voice_min_free_bytes = 0
    monkeypatch.setattr(
        "pocket48_summarizer.voice_monitor.shutil.disk_usage",
        lambda _: shutil._ntuple_diskusage(
            total=100, used=0, free=100
        ),
    )
    coordinator = RoomVoiceStorageCoordinator()

    first, first_error, first_limit = await coordinator.reserve(
        settings
    )
    second, second_error, second_limit = await coordinator.reserve(
        settings
    )

    assert first is not None
    assert second is not None
    assert first_error is None
    assert second_error is None
    assert first_limit == 8
    assert second_limit == 2
    await coordinator.release(first)
    await coordinator.release(second)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "segment_bytes", "expected"),
    [(-1, 4, "partial"), (-1, 0, None)],
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

    if expected is None:
        assert not list(
            settings.room_voice_path.glob("*/session.json")
        )
        return
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
async def test_same_active_stream_retries_after_short_cooldown(
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
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=recorder,
        uuid_factory=lambda: next(ids),
        now=lambda: current[0],
    )
    stop_event = asyncio.Event()

    await monitor.poll_once(stop_event)
    await monitor.poll_once(stop_event)
    assert len(recorder.calls) == 1
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(stop_event)
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(stop_event)
    assert len(recorder.calls) == 2
    assert len(list(settings.room_voice_path.glob("*/session.json"))) == 2
    await monitor.close()


@pytest.mark.asyncio
async def test_clean_stream_end_enters_fast_reconnect_window(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    now = datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(),
        now=lambda: now,
    )

    await monitor.poll_once(asyncio.Event())

    assert monitor._reconnect_until == now + timedelta(
        seconds=RECONNECT_WINDOW_SECONDS
    )
    assert (
        monitor._reconnect_attempts_remaining
        == RECONNECT_MAX_ATTEMPTS
    )
    assert monitor._recording_cooldown_until == now + timedelta(
        seconds=RECONNECT_POLL_SECONDS
    )
    safe_status = json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )
    assert safe_status["phase"] == "reconnecting"


@pytest.mark.asyncio
async def test_empty_reconnect_failure_keeps_short_cooldown(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    client = FakeClient([active_status(), active_status()])
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=recorder,
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    recorder.process = ImmediateProcess(1)
    recorder.segment_bytes = 0
    await monitor.poll_once(asyncio.Event())

    assert len(recorder.calls) == 2
    assert (
        monitor._reconnect_attempts_remaining
        == RECONNECT_MAX_ATTEMPTS - 1
    )
    assert monitor._consecutive_recording_failures == 0
    assert monitor._recording_cooldown_until == current[0] + timedelta(
        seconds=RECONNECT_POLL_SECONDS
    )


@pytest.mark.asyncio
async def test_exhausted_reconnect_attempts_resume_normal_backoff(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    client = FakeClient([active_status(), active_status()])
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: client,
        recorder=recorder,
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    monitor._reconnect_attempts_remaining = 1
    recorder.process = ImmediateProcess(1)
    recorder.segment_bytes = 0
    await monitor.poll_once(asyncio.Event())

    assert monitor._reconnect_until is None
    assert monitor._reconnect_blocked_until == current[0] + timedelta(
        seconds=RECONNECT_COOLOFF_SECONDS
    )
    assert monitor._consecutive_recording_failures == 1
    assert monitor._recording_cooldown_until == current[0] + timedelta(
        seconds=settings.pocket48_voice_poll_seconds
    )


@pytest.mark.asyncio
async def test_reconnect_window_uses_short_poll_delay(settings):
    delays: list[float] = []

    async def record_delay(delay: float) -> None:
        delays.append(delay)

    monitor = RoomVoiceMonitor(
        settings,
        sleeper=record_delay,
        jitter=lambda *_: 0,
    )
    monitor._start_reconnect_window()

    await monitor._sleep_until_next_poll(asyncio.Event())

    assert delays == [RECONNECT_POLL_SECONDS]


@pytest.mark.asyncio
async def test_two_inactive_polls_end_reconnect_window(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient(
            [active_status(), inactive_status(), inactive_status()]
        ),
        recorder=FakeRecorder(),
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(asyncio.Event())

    assert monitor._reconnect_until is None
    assert monitor._reconnect_blocked_until is None
    safe_status = json.loads(
        settings.room_voice_monitor_status_path.read_text()
    )
    assert safe_status["phase"] == "inactive"


@pytest.mark.asyncio
async def test_reconnect_api_error_consumes_attempt_budget(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    lookup_error = AppError(
        "room_voice_lookup_failed", "temporary failure", True
    )
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient(
            [active_status(), lookup_error]
        ),
        recorder=FakeRecorder(),
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    with pytest.raises(AppError):
        await monitor.poll_once(asyncio.Event())

    assert (
        monitor._reconnect_attempts_remaining
        == RECONNECT_MAX_ATTEMPTS - 1
    )


@pytest.mark.asyncio
async def test_reconnect_validation_error_consumes_attempt_budget(
    settings,
):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient(
            [
                active_status(),
                active_status(
                    "rtmps://voice.example.test:444/live/stream"
                ),
            ]
        ),
        recorder=FakeRecorder(),
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    with pytest.raises(AppError):
        await monitor.poll_once(asyncio.Event())

    assert (
        monitor._reconnect_attempts_remaining
        == RECONNECT_MAX_ATTEMPTS - 1
    )


@pytest.mark.asyncio
async def test_successful_reconnect_resets_inactive_confirmation(
    settings,
):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient(
            [
                active_status(),
                inactive_status(),
                active_status(),
                inactive_status(),
            ]
        ),
        recorder=FakeRecorder(),
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(asyncio.Event())

    assert monitor._reconnect_inactive_polls == 1
    assert monitor._reconnect_until is not None


@pytest.mark.asyncio
async def test_reconnect_window_does_not_rearm_during_cooloff(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient(
            [active_status(), active_status()]
        ),
        recorder=recorder,
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    monitor._reconnect_attempts_remaining = 1
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    await monitor.poll_once(asyncio.Event())

    assert monitor._reconnect_until is None
    assert monitor._reconnect_blocked_until == current[0] + timedelta(
        seconds=RECONNECT_COOLOFF_SECONDS
    )


@pytest.mark.asyncio
async def test_empty_failed_reconnect_session_is_removed(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    current = [datetime(2026, 9, 1, 15, 16, 26, tzinfo=UTC)]
    recorder = FakeRecorder()
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient(
            [active_status(), active_status()]
        ),
        recorder=recorder,
        now=lambda: current[0],
    )

    await monitor.poll_once(asyncio.Event())
    current[0] += timedelta(seconds=RECONNECT_POLL_SECONDS)
    recorder.process = ImmediateProcess(1)
    recorder.segment_bytes = 0
    await monitor.poll_once(asyncio.Event())

    sessions = list(settings.room_voice_path.glob("*/session.json"))
    assert len(sessions) == 1


@pytest.mark.asyncio
async def test_zero_byte_segment_is_not_retained_as_audio(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FakeRecorder(
            process=ImmediateProcess(0),
            segment_bytes=0,
            create_empty_segment=True,
        ),
    )

    await monitor.poll_once(asyncio.Event())

    assert not list(settings.room_voice_path.glob("*/session.json"))


@pytest.mark.asyncio
async def test_recorder_start_failure_removes_empty_session(settings):
    monitor_settings(settings)
    provision_private_files(settings)
    monitor = RoomVoiceMonitor(
        settings,
        client_factory=lambda *_: FakeClient([active_status()]),
        recorder=FailingRecorder(),
    )

    with pytest.raises(AppError) as captured:
        await monitor.poll_once(asyncio.Event())

    assert captured.value.code == "room_voice_ffmpeg_start_failed"
    assert not list(settings.room_voice_path.glob("*/session.json"))


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
