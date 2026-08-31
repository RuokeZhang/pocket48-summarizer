from __future__ import annotations

import hashlib
import json
import logging
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
            ),
        }
    )
    auth_repository = AuthRepository(repository.database)
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
    assert sessions[0].stream_host == "voice.example.test"
    assert sessions[0].error_codes == ("room_voice_ffmpeg_partial",)
    serialized = repr((monitor, sessions))
    for secret in (
        "private-status-token",
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


def test_room_voice_routes_require_admin_and_csrf(settings, repository):
    app = make_admin_app(settings, repository)
    with TestClient(app) as anonymous:
        response = anonymous.get(
            "/admin/room-voice", follow_redirects=False
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    with TestClient(app) as bob:
        login(bob, "bob", "bob also has secure password")
        assert bob.get("/admin/room-voice").status_code == 403
        assert (
            bob.post(
                "/admin/room-voice/sms",
                data={"area": "86", "mobile": "13800138000"},
            ).status_code
            == 403
        )

    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        assert (
            alice.post(
                "/admin/room-voice/sms",
                data={"area": "86", "mobile": "13800138000"},
            ).status_code
            == 403
        )
        assert (
            alice.post(
                "/admin/room-voice/login",
                data={
                    "area": "86",
                    "mobile": "13800138000",
                    "code": "123456",
                },
            ).status_code
            == 403
        )
        page = alice.get("/admin/room-voice")
        assert page.status_code == 200
        assert "杨晔" in page.text
        assert "407126" in page.text
        assert "7587624" in page.text
        assert "6227955" in page.text
        assert "王睿琦" in page.text
        assert "530390" in page.text
        assert "wang-ruiqi" in page.text
        assert "待动态解析" in page.text
        assert "账号只能保持一个活跃会话" in page.text


def test_admin_shows_independent_status_and_safe_session_attribution(
    settings, repository
):
    app = make_admin_app(settings, repository)
    configured = app.state.settings
    configured.prepare_directories()
    wang = configured.room_voice_monitor_settings()[1]
    wang.room_voice_monitor_status_path.write_text(
        json.dumps(
            {
                "monitor_id": "wang-ruiqi",
                "phase": "inactive",
                "updated_at": "2026-08-31T20:00:00+00:00",
                "channel_id": "1279498",
                "server_id": "7654321",
                "session_id": None,
                "error_code": None,
                "stream_url": "rtmps://private.example/live?token=secret",
            }
        ),
        encoding="utf-8",
    )
    wang.room_voice_monitor_status_path.chmod(0o600)
    session_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    session_dir = configured.room_voice_path / session_id
    session_dir.mkdir(mode=0o700)
    state_path = session_dir / "session.json"
    state_path.write_text(
        json.dumps(
            {
                "session_id": session_id,
                "monitor_id": "wang-ruiqi",
                "member_name": "王睿琦",
                "status": "completed",
                "segment_count": 1,
                "total_bytes": 42,
                "participants": [
                    {
                        "name": "private guest",
                        "token": "private participant token",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    state_path.chmod(0o600)

    with TestClient(app) as alice:
        login(alice, "alice", "alice has a secure password")
        page = alice.get("/admin/room-voice")

    assert page.status_code == 200
    assert page.text.count("杨晔") >= 1
    assert page.text.count("王睿琦") >= 2
    assert "1279498" in page.text
    assert "7654321" in page.text
    assert "wang-ruiqi" in page.text
    assert "private guest" not in page.text
    assert "private participant token" not in page.text
    assert "private.example" not in page.text


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
        with TestClient(app) as alice:
            login(alice, "alice", "alice has a secure password")
            csrf = alice.cookies.get("p48_csrf")
            challenge = alice.post(
                "/admin/room-voice/sms",
                data={
                    "_csrf": csrf,
                    "area": "86",
                    "mobile": phone,
                },
                follow_redirects=False,
            )
            assert challenge.status_code == 303
            challenge_page = alice.get(challenge.headers["location"])
            assert "请选择 A 或 B" in challenge_page.text
            assert phone not in challenge_page.text

            cooldown = alice.post(
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
            sent = alice.post(
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

            logged_in = alice.post(
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
            page = alice.get(logged_in.headers["location"])
            assert "监控进程会热加载凭证" in page.text
            assert "/admin/room-voice" in alice.get("/").text

    combined = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (phone, code, token, "reviewed-seed"):
        assert secret not in combined
        assert secret not in page.text
