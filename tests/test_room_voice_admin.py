from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from pocket48_summarizer.app import create_app
from pocket48_summarizer.auth import AuthRepository, hash_password
from pocket48_summarizer.clients.pocket48_auth import (
    PA_REFERENCE_URL,
    SmsChallenge,
    SmsSendResult,
    save_pa_signing_seed,
)
from pocket48_summarizer.clients.pocket48_voice import (
    Pocket48VoiceCredentials,
)
from pocket48_summarizer.config import AdditionalRoomVoiceTarget
from pocket48_summarizer.errors import AppError
from pocket48_summarizer.models import TranscriptSegment
from pocket48_summarizer.room_voice_admin import (
    PA_REFERENCE_MAX_BYTES,
    PendingChallenge,
    PendingRoomVoiceLogin,
    RoomVoiceAdminService,
    ensure_reviewed_pa_seed,
    inspect_private_file,
    list_safe_capture_sessions,
    load_pending_login,
    read_safe_monitor_status,
    safe_capture_segment_path,
    save_pending_login,
)
from pocket48_summarizer.services import ApplicationServices


class DummyWorker:
    async def start(self):
        return None

    async def stop(self):
        return None


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class FakeAuthClient:
    sms_result = SmsSendResult(sent=True)
    credentials = Pocket48VoiceCredentials(
        token=SecretStr("private-route-token"),
        app_info=SecretStr('{"deviceId":"unused"}'),
        user_agent="unused",
    )
    instances: list[FakeAuthClient] = []

    def __init__(self, settings, identity, *, pa_provider):
        self.settings = settings
        self.identity = identity
        self.pa_provider = pa_provider
        self.sms_calls: list[dict[str, str | None]] = []
        self.login_calls: list[dict[str, str]] = []
        self.closed = False
        type(self).instances.append(self)

    async def send_sms(self, *, area, mobile, challenge_answer=None):
        self.sms_calls.append(
            {
                "area": area,
                "mobile": mobile,
                "challenge_answer": challenge_answer,
            }
        )
        return type(self).sms_result

    async def login_by_code(self, *, area, mobile, code):
        self.login_calls.append(
            {"area": area, "mobile": mobile, "code": code}
        )
        return Pocket48VoiceCredentials(
            token=type(self).credentials.token,
            app_info=SecretStr(
                json.dumps(
                    self.identity.app_info,
                    separators=(",", ":"),
                )
            ),
            user_agent=self.identity.user_agent,
        )

    async def close(self):
        self.closed = True


async def noop_provisioner(path):
    return None


@pytest.fixture(autouse=True)
def reset_fake_auth_client():
    FakeAuthClient.instances = []
    FakeAuthClient.sms_result = SmsSendResult(sent=True)
    FakeAuthClient.credentials = Pocket48VoiceCredentials(
        token=SecretStr("private-route-token"),
        app_info=SecretStr('{"deviceId":"unused"}'),
        user_agent="unused",
    )


def make_admin_app(settings, repository):
    configured = settings.model_copy(
        update={
            "auth_required": True,
            "session_cookie_secure": False,
            "pocket48_voice_member_name": "杨晔",
            "pocket48_voice_member_id": "407126",
            "pocket48_voice_channel_id": "7587624",
            "pocket48_voice_server_id": "6227955",
            "pocket48_voice_additional_targets_json": (
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
            ),
        }
    )
    auth_repository = AuthRepository(repository.database)
    auth_repository.create_user(
        "ruoke",
        "ruoke",
        hash_password("ruoke has a secure password"),
        is_admin=False,
    )
    auth_repository.create_user(
        "alice",
        "alice",
        hash_password("alice has a secure password"),
        is_admin=True,
    )
    auth_repository.create_user(
        "bob",
        "bob",
        hash_password("bob also has secure password"),
        is_admin=False,
    )
    return create_app(
        configured,
        ApplicationServices(
            repository=repository,
            worker=DummyWorker(),
        ),
    )


class FakeRoomVoiceAdmin:
    def __init__(self):
        self.sms_calls = []
        self.login_calls = []

    async def send_sms(self, **kwargs):
        self.sms_calls.append(kwargs)
        return SmsSendResult(sent=True)

    async def complete_login(self, **kwargs):
        self.login_calls.append(kwargs)


def write_capture_session(
    settings,
    session_id,
    *,
    status="completed",
    monitor_id="primary",
    member_name="杨晔",
    segment_names=("segment-000000.mp3",),
    extra_state=None,
):
    session_dir = settings.room_voice_path / session_id
    session_dir.mkdir(mode=0o700)
    segments_dir = session_dir / "segments"
    segments_dir.mkdir(mode=0o700)
    total_bytes = 0
    for index, name in enumerate(segment_names):
        content = f"mp3-{index}".encode()
        segment = segments_dir / name
        segment.write_bytes(content)
        segment.chmod(0o600)
        total_bytes += len(content)
    state = {
        "session_id": session_id,
        "monitor_id": monitor_id,
        "member_name": member_name,
        "status": status,
        "started_at": "2026-08-31T19:00:00+00:00",
        "ended_at": (
            None
            if status in {"starting", "recording"}
            else "2026-08-31T19:05:00+00:00"
        ),
        "segment_count": len(segment_names),
        "total_bytes": total_bytes,
    }
    if extra_state:
        state.update(extra_state)
    state_path = session_dir / "session.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_path.chmod(0o600)
    return session_dir


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def valid_reference(seed: bytes = b"0123456789ABCDEF0123456789ABCDEF"):
    return b'package client\nconst paSecret = "' + seed + b'"\n'


@pytest.mark.asyncio
async def test_provisions_only_pinned_verified_private_pa_seed(
    settings, monkeypatch
):
    content = valid_reference()
    monkeypatch.setattr(
        "pocket48_summarizer.room_voice_admin.PA_REFERENCE_SHA256",
        hashlib.sha256(content).hexdigest(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PA_REFERENCE_URL
        return httpx.Response(200, content=content)

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    await ensure_reviewed_pa_seed(
        settings.pocket48_pa_signing_seed_path, client=http
    )
    await http.aclose()

    path = settings.pocket48_pa_signing_seed_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "0123456789ABCDEF" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "code"),
    [
        (
            httpx.Response(
                302, headers={"Location": "https://example.com"}
            ),
            "pa_reference_download_failed",
        ),
        (
            httpx.Response(
                200,
                content=b"x" * (PA_REFERENCE_MAX_BYTES + 1),
            ),
            "pa_reference_too_large",
        ),
        (
            httpx.Response(200, content=valid_reference()),
            "pa_reference_hash_mismatch",
        ),
    ],
)
async def test_rejects_redirect_oversize_and_hash_mismatch(
    settings, response, code
):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: response)
    )
    with pytest.raises(AppError) as captured:
        await ensure_reviewed_pa_seed(
            settings.pocket48_pa_signing_seed_path, client=http
        )
    await http.aclose()
    assert captured.value.code == code
    assert not settings.pocket48_pa_signing_seed_path.exists()


@pytest.mark.asyncio
async def test_existing_pa_seed_must_have_exact_private_permissions(settings):
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr("reviewed-seed"),
    )
    settings.pocket48_pa_signing_seed_path.chmod(0o640)
    with pytest.raises(AppError, match="0600"):
        await ensure_reviewed_pa_seed(
            settings.pocket48_pa_signing_seed_path
        )


@pytest.mark.asyncio
async def test_sms_cooldown_and_pending_state_never_persist_phone(settings):
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr("reviewed-seed"),
    )
    clock = Clock(datetime(2026, 8, 31, 20, 0, tzinfo=UTC))
    service = RoomVoiceAdminService(
        settings,
        auth_client_factory=FakeAuthClient,
        pa_provisioner=noop_provisioner,
        now=clock,
    )

    await service.send_sms(
        area="86", mobile="13800138000", challenge_answer=None
    )
    pending_text = settings.room_voice_login_pending_path.read_text(
        encoding="utf-8"
    )
    assert set(json.loads(pending_text)) == {
        "version",
        "app_info",
        "user_agent",
        "created_at",
        "last_sms_at",
    }
    assert "13800138000" not in pending_text
    assert "reviewed-seed" not in pending_text
    assert stat.S_IMODE(
        settings.room_voice_login_pending_path.stat().st_mode
    ) == 0o600

    with pytest.raises(AppError) as captured:
        await service.send_sms(
            area="86", mobile="13900139000", challenge_answer=None
        )
    assert captured.value.code == "room_voice_sms_cooldown"
    assert len(FakeAuthClient.instances) == 1


@pytest.mark.asyncio
async def test_challenge_requires_new_explicit_submit_and_reuses_identity(
    settings
):
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr("reviewed-seed"),
    )
    clock = Clock(datetime(2026, 8, 31, 20, 0, tzinfo=UTC))
    service = RoomVoiceAdminService(
        settings,
        auth_client_factory=FakeAuthClient,
        pa_provisioner=noop_provisioner,
        now=clock,
    )
    FakeAuthClient.sms_result = SmsSendResult(
        sent=False,
        challenge=SmsChallenge(
            "请为 13800138000 选择正确答案",
            ("A", "13800138000"),
        ),
    )

    first = await service.send_sms(
        area="86", mobile="13800138000", challenge_answer=None
    )
    assert first.challenge is not None
    pending = load_pending_login(
        settings.room_voice_login_pending_path
    )
    assert pending.challenge == PendingChallenge(
        question="请为 [已隐藏] 选择正确答案",
        options=("A", "[已隐藏]"),
    )
    first_identity = pending.app_info

    clock.value += timedelta(seconds=61)
    FakeAuthClient.sms_result = SmsSendResult(sent=True)
    await service.send_sms(
        area="86", mobile="13800138000", challenge_answer="A"
    )
    pending = load_pending_login(
        settings.room_voice_login_pending_path
    )
    assert pending.app_info == first_identity
    assert pending.challenge is None
    assert FakeAuthClient.instances[-1].sms_calls == [
        {
            "area": "86",
            "mobile": "13800138000",
            "challenge_answer": "A",
        }
    ]
    assert "13800138000" not in (
        settings.room_voice_login_pending_path.read_text(encoding="utf-8")
    )


@pytest.mark.asyncio
async def test_expired_pending_identity_refuses_login_without_network(settings):
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr("reviewed-seed"),
    )
    now = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
    save_pending_login(
        settings.room_voice_login_pending_path,
        PendingRoomVoiceLogin(
            app_info={"deviceId": "EXPIRED"},
            user_agent="PocketFans201807/test",
            created_at=now - timedelta(minutes=10),
            last_sms_at=now - timedelta(minutes=9),
        ),
    )
    service = RoomVoiceAdminService(
        settings,
        auth_client_factory=FakeAuthClient,
        pa_provisioner=noop_provisioner,
        now=lambda: now,
    )

    with pytest.raises(AppError) as captured:
        await service.complete_login(
            area="86", mobile="13800138000", code="123456"
        )
    assert captured.value.code == "room_voice_login_expired"
    assert FakeAuthClient.instances == []
    assert not settings.room_voice_login_pending_path.exists()


@pytest.mark.asyncio
async def test_successful_login_atomically_writes_credentials(settings):
    save_pa_signing_seed(
        settings.pocket48_pa_signing_seed_path,
        SecretStr("reviewed-seed"),
    )
    now = datetime(2026, 8, 31, 20, 0, tzinfo=UTC)
    save_pending_login(
        settings.room_voice_login_pending_path,
        PendingRoomVoiceLogin(
            app_info={"deviceId": "PENDING-DEVICE"},
            user_agent="PocketFans201807/test",
            created_at=now,
            last_sms_at=now,
        ),
    )
    service = RoomVoiceAdminService(
        settings,
        auth_client_factory=FakeAuthClient,
        pa_provisioner=noop_provisioner,
        now=lambda: now + timedelta(minutes=1),
    )

    await service.complete_login(
        area="86", mobile="13800138000", code="123456"
    )
    path = settings.pocket48_voice_credentials_path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert payload["token"] == "private-route-token"
    assert payload["app_info"]["deviceId"] == "PENDING-DEVICE"
    assert not path.with_suffix(".tmp").exists()
    assert not settings.room_voice_login_pending_path.exists()
    assert FakeAuthClient.instances[0].login_calls == [
        {
            "area": "86",
            "mobile": "13800138000",
            "code": "123456",
        }
    ]


def test_safe_status_and_session_readers_redact_unsafe_fields(settings):
    settings.prepare_directories()
    status_path = settings.room_voice_monitor_status_path
    status_path.write_text(
        json.dumps(
            {
                "phase": "recording",
                "updated_at": "2026-08-31T20:00:00+00:00",
                "error_code": "room_voice_safe_error",
                "session_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "channel_id": "7587624",
                "server_id": "6227955",
                "monitor_id": "primary",
                "token": "private-status-token",
            }
        ),
        encoding="utf-8",
    )
    status_path.chmod(0o600)
    session_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    session_dir = settings.room_voice_path / session_id
    session_dir.mkdir(mode=0o700)
    state_path = session_dir / "session.json"
    state_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "monitor_id": "primary",
                "member_name": "杨晔",
                "status": "partial",
                "started_at": "2026-08-31T19:00:00+00:00",
                "ended_at": "2026-08-31T19:05:00+00:00",
                "segment_count": 2,
                "total_bytes": 1234,
                "stream": {
                    "scheme": "rtmps",
                    "host": "voice.example.test",
                    "port": 443,
                    "url": "rtmps://voice.example.test/live?token=secret",
                },
                "stream_sha256": "f" * 64,
                "member_id": "407126",
                "participants": ["private-participant"],
                "errors": [
                    {
                        "code": "room_voice_ffmpeg_partial",
                        "message": "/private/path token=secret",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state_path.chmod(0o600)

    monitor = read_safe_monitor_status(status_path)
    sessions = list_safe_capture_sessions(settings.room_voice_path)
    assert monitor is not None
    assert monitor.monitor_id == "primary"
    assert monitor.phase == "recording"
    assert monitor.channel_id == "7587624"
    assert monitor.server_id == "6227955"
    assert sessions[0].monitor_id == "primary"
    assert sessions[0].member_name == "杨晔"
    serialized = repr((monitor, sessions))
    for secret in (
        "private-status-token",
        "voice.example.test",
        "live?token=secret",
        "407126",
        "private-participant",
        "/private/path",
        "f" * 64,
    ):
        assert secret not in serialized

    state_path.chmod(0o644)
    assert list_safe_capture_sessions(settings.room_voice_path) == []
    assert inspect_private_file(state_path).private is False


def test_safe_session_reader_limits_results_to_twenty(settings):
    settings.prepare_directories()
    for index in range(21):
        session_id = f"00000000-0000-4000-8000-{index:012d}"
        session_dir = settings.room_voice_path / session_id
        session_dir.mkdir(mode=0o700)
        state_path = session_dir / "session.json"
        state_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "segment_count": index,
                    "total_bytes": index,
                }
            ),
            encoding="utf-8",
        )
        state_path.chmod(0o600)
    assert len(list_safe_capture_sessions(settings.room_voice_path)) == 20


def test_public_room_voice_page_redacts_private_state(settings, repository):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    configured.prepare_directories()
    primary = configured.room_voice_monitor_settings()[0]
    primary.room_voice_monitor_status_path.write_text(
        json.dumps(
            {
                "monitor_id": "primary",
                "phase": "recording",
                "updated_at": "2026-08-31T20:00:00+00:00",
                "channel_id": "7587624",
                "server_id": "6227955",
                "stream_host": "private-stream.example",
                "stream_sha256": "f" * 64,
                "guest_id": "private-guest",
            }
        ),
        encoding="utf-8",
    )
    primary.room_voice_monitor_status_path.chmod(0o600)
    wang = configured.room_voice_monitor_settings()[1]
    wang.room_voice_monitor_status_path.write_text(
        json.dumps(
            {
                "monitor_id": "wang-ruiqi",
                "phase": "waiting_credentials",
                "updated_at": "2026-08-31T20:01:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    wang.room_voice_monitor_status_path.chmod(0o600)
    session_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    write_capture_session(
        configured,
        session_id,
        extra_state={
            "stream": {
                "host": "history-stream.example",
                "url": "rtmps://history-stream.example/live?token=secret",
            },
            "stream_sha256": "e" * 64,
            "participants": [{"user_id": "private-guest"}],
        },
    )
    save_pa_signing_seed(
        configured.pocket48_pa_signing_seed_path,
        SecretStr("private-pa-seed"),
    )
    save_pending_login(
        configured.room_voice_login_pending_path,
        PendingRoomVoiceLogin(
            app_info={"deviceId": "PRIVATE-DEVICE"},
            user_agent="private-agent",
            created_at=datetime.now(UTC),
            challenge=PendingChallenge(
                question="private challenge", options=("secret option",)
            ),
        ),
    )

    with TestClient(app) as visitor:
        page = visitor.get("/room-voice")
        styles = visitor.get("/static/styles.css")

    assert page.status_code == 200
    assert "本页公开展示" in page.text
    assert page.text.count('class="back-link"') == 1
    assert 'data-i18n="roomVoiceTitle"' in page.text
    assert 'data-i18n="roomVoiceCurrentStatus"' in page.text
    assert 'data-room-voice-status="recording"' in page.text
    assert 'class="admin-stat room-voice-monitor-card"' in page.text
    assert 'class="room-voice-monitor-details"' in page.text
    assert 'data-i18n="roomVoiceDetails"' in page.text
    assert styles.status_code == 200
    assert '[data-room-voice-status="recording"]' in styles.text
    assert '[data-room-voice-status="error"]' in styles.text
    assert ".room-voice-monitor-card" in styles.text
    assert ".room-voice-monitor-details" in styles.text
    assert "杨晔" in page.text
    assert "王睿琦" in page.text
    assert "杨冰怡" in page.text
    assert "recording" in page.text
    assert "407126" in page.text
    assert "530390" in page.text
    assert "6744" in page.text
    assert "7587624" in page.text
    assert "6227955" in page.text
    segment_url = (
        f"/room-voice/{session_id}/segments/segment-000000.mp3"
    )
    assert f'<audio controls preload="none" src="{segment_url}">' in page.text
    assert f'href="{segment_url}"' in page.text
    assert 'action="/admin/room-voice/sms"' not in page.text
    assert 'action="/admin/room-voice/login"' not in page.text
    assert "监控账号维护" not in page.text
    for secret in (
        "PA 签名种子",
        "监控凭证",
        "private challenge",
        "secret option",
        "private-stream.example",
        "history-stream.example",
        "private-guest",
        "PRIVATE-DEVICE",
        "private-agent",
        "private-pa-seed",
        "waiting_credentials",
        "live?token=secret",
        "f" * 64,
        "e" * 64,
    ):
        assert secret not in page.text


def test_admin_room_voice_redirect_is_public(settings, repository):
    app = make_admin_app(settings, repository)
    with TestClient(app) as visitor:
        response = visitor.get(
            "/admin/room-voice?notice=sms-sent",
            follow_redirects=False,
        )
        assert response.status_code == 307
        assert response.headers["location"] == (
            "/room-voice?notice=sms-sent"
        )


def test_room_voice_analysis_is_public_and_retry_stays_ruoke_only(
    settings, repository
):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    configured.prepare_directories()
    session_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    write_capture_session(configured, session_id)
    repository.enqueue_room_voice_processing(
        session_id=session_id,
        monitor_id="primary",
        member_name="杨晔",
        segment_count=1,
        total_bytes=5,
    )
    claimed = repository.claim_next_room_voice_processing("worker", 120)
    assert claimed
    repository.replace_room_voice_transcript(
        session_id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=1000,
                text="公开字幕",
            )
        ],
    )
    repository.mark_room_voice_processing_failed(
        session_id,
        "temporary_failure",
        "可以重试",
        True,
    )

    with TestClient(app) as visitor:
        history = visitor.get("/room-voice")
        analysis = visitor.get(f"/room-voice/{session_id}/analysis")
        denied = visitor.post(
            f"/admin/room-voice/{session_id}/analysis/retry",
            follow_redirects=False,
        )

    assert history.status_code == 200
    assert f"/room-voice/{session_id}/analysis" in history.text
    assert analysis.status_code == 200
    assert "公开字幕" in analysis.text
    assert "可以重试" in analysis.text
    assert denied.status_code == 303
    assert denied.headers["location"] == "/login"


@pytest.mark.parametrize(
    ("username", "password", "expected"),
    [
        (None, None, 303),
        ("bob", "bob also has secure password", 403),
        ("alice", "alice has a secure password", 403),
    ],
)
def test_only_ruoke_can_invoke_room_voice_activation(
    settings, repository, username, password, expected
):
    app = make_admin_app(settings, repository)
    service = FakeRoomVoiceAdmin()
    app.state.room_voice_admin = service
    with TestClient(app) as client:
        if username:
            login(client, username, password)
        page = client.get("/room-voice")
        assert page.status_code == 200
        assert "监控账号维护" not in page.text
        for path, data in (
            (
                "/admin/room-voice/sms",
                {"area": "86", "mobile": "13800138000"},
            ),
            (
                "/admin/room-voice/login",
                {
                    "area": "86",
                    "mobile": "13800138000",
                    "code": "123456",
                },
            ),
        ):
            response = client.post(path, data=data, follow_redirects=False)
            assert response.status_code == expected
    assert service.sms_calls == []
    assert service.login_calls == []


def test_ruoke_sees_controls_and_posts_with_csrf(
    settings, repository
):
    app = make_admin_app(settings, repository)
    service = FakeRoomVoiceAdmin()
    app.state.room_voice_admin = service

    with TestClient(app) as ruoke:
        login(ruoke, "ruoke", "ruoke has a secure password")
        page = ruoke.get("/room-voice")
        assert page.status_code == 200
        assert "监控账号维护" in page.text
        assert 'action="/admin/room-voice/sms"' in page.text
        assert 'action="/admin/room-voice/login"' in page.text
        assert 'value="13800138000"' not in page.text
        assert 'value="123456"' not in page.text
        csrf = ruoke.cookies["p48_csrf"]
        sent = ruoke.post(
            "/admin/room-voice/sms",
            data={
                "_csrf": csrf,
                "area": "86",
                "mobile": "13800138000",
            },
            follow_redirects=False,
        )
        completed = ruoke.post(
            "/admin/room-voice/login",
            data={
                "_csrf": csrf,
                "area": "86",
                "mobile": "13800138000",
                "code": "123456",
            },
            follow_redirects=False,
        )
    assert sent.status_code == 303
    assert sent.headers["location"] == "/room-voice?notice=sms-sent"
    assert completed.status_code == 303
    assert completed.headers["location"] == (
        "/room-voice?notice=login-success"
    )
    assert service.sms_calls == [
        {
            "area": "86",
            "mobile": "13800138000",
            "challenge_answer": None,
        }
    ]
    assert service.login_calls == [
        {"area": "86", "mobile": "13800138000", "code": "123456"}
    ]


def test_public_segment_serves_finalized_mp3_and_range(
    settings, repository
):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    session_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    write_capture_session(configured, session_id)
    url = f"/room-voice/{session_id}/segments/segment-000000.mp3"

    with TestClient(app) as visitor:
        response = visitor.get(url)
        ranged = visitor.get(url, headers={"Range": "bytes=1-3"})

    assert response.status_code == 200
    assert response.content == b"mp3-0"
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.headers["content-disposition"].startswith("inline;")
    assert ranged.status_code == 206
    assert ranged.content == b"p3-"
    assert ranged.headers["content-range"] == "bytes 1-3/5"


def test_public_segment_rejects_invalid_or_unsafe_paths(
    settings, repository, monkeypatch
):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    session_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    session_dir = write_capture_session(configured, session_id)
    valid_url = (
        f"/room-voice/{session_id}/segments/segment-000000.mp3"
    )

    assert safe_capture_segment_path(
        configured.room_voice_path, session_id, "../session.json"
    ) is None
    with TestClient(app) as visitor:
        assert visitor.get(
            "/room-voice/not-a-uuid/segments/segment-000000.mp3"
        ).status_code == 404
        assert visitor.get(
            "/room-voice/eeeeeeeeeeee4eee8eeeeeeeeeeeeeee/"
            "segments/segment-000000.mp3"
        ).status_code == 404
        assert visitor.get(
            f"/room-voice/{session_id}/segments/not-an-mp3.txt"
        ).status_code == 404

        segment = session_dir / "segments" / "segment-000000.mp3"
        segments_dir = segment.parent
        segments_dir.chmod(0o755)
        assert visitor.get(valid_url).status_code == 404
        segments_dir.chmod(0o700)

        segment.chmod(0o644)
        assert visitor.get(valid_url).status_code == 404
        segment.chmod(0o600)
        segment.write_bytes(b"")
        assert visitor.get(valid_url).status_code == 404
        segment.write_bytes(b"mp3-0")
        segment.chmod(0o600)

        state_path = session_dir / "session.json"
        state_path.chmod(0o644)
        assert visitor.get(valid_url).status_code == 404
        state_path.chmod(0o600)

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "unsafe"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)
        assert visitor.get(valid_url).status_code == 404

        state["status"] = "completed"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)
        state_path.unlink()
        assert visitor.get(valid_url).status_code == 404
        state_path.write_text(json.dumps(state), encoding="utf-8")
        state_path.chmod(0o600)

        monkeypatch.setattr(os, "getuid", lambda: state_path.stat().st_uid + 1)
        assert visitor.get(valid_url).status_code == 404


def test_public_segment_rejects_symlinks_and_active_last_segment(
    settings, repository
):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    active_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    active_dir = write_capture_session(
        configured,
        active_id,
        status="recording",
        segment_names=(
            "segment-000000.mp3",
            "segment-000001.mp3",
        ),
    )
    earlier_url = (
        f"/room-voice/{active_id}/segments/segment-000000.mp3"
    )
    current_url = (
        f"/room-voice/{active_id}/segments/segment-000001.mp3"
    )
    symlink_name = "segment-000002.mp3"
    (
        active_dir / "segments" / symlink_name
    ).symlink_to(active_dir / "segments" / "segment-000000.mp3")
    (active_dir / "segments" / "segment-000001.mp3").chmod(0o644)

    target_id = "11111111-1111-4111-8111-111111111111"
    target_dir = write_capture_session(configured, target_id)
    symlink_id = "22222222-2222-4222-8222-222222222222"
    (configured.room_voice_path / symlink_id).symlink_to(
        target_dir, target_is_directory=True
    )

    with TestClient(app) as visitor:
        assert visitor.get(earlier_url).status_code == 200
        assert visitor.get(current_url).status_code == 404
        assert visitor.get(
            f"/room-voice/{active_id}/segments/{symlink_name}"
        ).status_code == 404
        assert visitor.get(
            f"/room-voice/{symlink_id}/segments/segment-000000.mp3"
        ).status_code == 404
    active_sessions = list_safe_capture_sessions(
        configured.room_voice_path
    )
    active = next(
        session
        for session in active_sessions
        if session.session_id == active_id
    )
    assert [segment.name for segment in active.segments] == [
        "segment-000000.mp3"
    ]


def test_public_history_bounds_segments_and_supports_old_primary(
    settings,
):
    settings.prepare_directories()
    old_id = "33333333-3333-4333-8333-333333333333"
    old_dir = write_capture_session(
        settings,
        old_id,
        monitor_id=None,
        member_name=None,
        segment_names=tuple(
            f"segment-{index:06d}.mp3" for index in range(101)
        ),
    )
    state_path = old_dir / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("monitor_id")
    state.pop("member_name")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    state_path.chmod(0o600)

    sessions = list_safe_capture_sessions(settings.room_voice_path)
    assert sessions[0].monitor_id == "primary"
    assert sessions[0].member_name is None
    assert len(sessions[0].segments) == 100
    assert sessions[0].segments[-1].name == "segment-000099.mp3"


def test_route_challenge_and_login_never_render_secrets(
    settings, repository, caplog
):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    save_pa_signing_seed(
        configured.pocket48_pa_signing_seed_path,
        SecretStr("reviewed-seed"),
    )
    now = datetime.now(UTC)
    service = RoomVoiceAdminService(
        configured,
        auth_client_factory=FakeAuthClient,
        pa_provisioner=noop_provisioner,
        now=lambda: now,
    )
    app.state.room_voice_admin = service
    FakeAuthClient.sms_result = SmsSendResult(
        sent=False,
        challenge=SmsChallenge("请选择 A 或 B", ("A", "B")),
    )
    phone = "13800138000"
    code = "123456"
    token = "private-route-token"

    with caplog.at_level(logging.DEBUG):
        with TestClient(app) as ruoke:
            login(ruoke, "ruoke", "ruoke has a secure password")
            csrf = ruoke.cookies.get("p48_csrf")
            challenge = ruoke.post(
                "/admin/room-voice/sms",
                data={
                    "_csrf": csrf,
                    "area": "86",
                    "mobile": phone,
                },
                follow_redirects=False,
            )
            assert challenge.status_code == 303
            challenge_page = ruoke.get(challenge.headers["location"])
            assert "请选择 A 或 B" in challenge_page.text
            assert phone not in challenge_page.text

            cooldown = ruoke.post(
                "/admin/room-voice/sms",
                data={
                    "_csrf": csrf,
                    "area": "86",
                    "mobile": phone,
                },
            )
            assert cooldown.status_code == 429
            assert phone not in cooldown.text

            now += timedelta(seconds=61)
            FakeAuthClient.sms_result = SmsSendResult(sent=True)
            sent = ruoke.post(
                "/admin/room-voice/sms",
                data={
                    "_csrf": csrf,
                    "area": "86",
                    "mobile": phone,
                    "challenge_answer": "A",
                },
                follow_redirects=False,
            )
            assert sent.status_code == 303

            logged_in = ruoke.post(
                "/admin/room-voice/login",
                data={
                    "_csrf": csrf,
                    "area": "86",
                    "mobile": phone,
                    "code": code,
                },
                follow_redirects=False,
            )
            assert logged_in.status_code == 303
            page = ruoke.get(logged_in.headers["location"])
            assert "监控进程会热加载凭证" in page.text
            assert "/room-voice" in ruoke.get("/").text

    combined = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (phone, code, token, "reviewed-seed"):
        assert secret not in combined
        assert secret not in page.text
