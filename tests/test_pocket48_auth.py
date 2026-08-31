from __future__ import annotations

import json
import stat

import httpx
import pytest
from pydantic import SecretStr

from pocket48_summarizer.clients.pocket48_auth import (
    Pocket48AuthClient,
    Pocket48DeviceIdentity,
    Pocket48PaGenerator,
    load_pa_generator,
    load_room_voice_credentials,
    save_pa_signing_seed,
    save_room_voice_credentials,
)
from pocket48_summarizer.errors import AppError


def identity() -> Pocket48DeviceIdentity:
    return Pocket48DeviceIdentity(
        app_info={
            "deviceId": "TEST-DEVICE",
            "appVersion": "7.0.41",
        },
        user_agent="PocketFans201807/test",
    )


def test_generates_dynamic_pa_from_deterministic_vector():
    generator = Pocket48PaGenerator(
        SecretStr("0123456789ABCDEF0123456789ABCDEF"),
        clock_ms=lambda: 1_700_000_000_123,
        nonce=lambda: 42,
    )
    assert generator.generate().get_secret_value() == (
        "MTcwMDAwMDAwMDEyMyw0Miw4NGUxOTBjNzRkMDg2YmQyMmZiZTc3YjEwZjJkNzFiMSw="
    )
    assert "0123456789ABCDEF" not in repr(generator)


@pytest.mark.asyncio
async def test_sends_one_sms_without_exposing_mobile(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/user/api/v1/sms/send2")
        assert json.loads(request.content) == {
            "mobile": "13800138000",
            "area": "86",
        }
        assert "token" not in request.headers
        assert "pa" not in request.headers
        return httpx.Response(
            200, json={"status": 200, "success": True}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48AuthClient(settings, identity(), client=http)
    result = await client.send_sms(
        mobile="13800138000", area="86"
    )
    assert result.sent is True
    await http.aclose()


@pytest.mark.asyncio
async def test_generates_fresh_pa_for_each_auth_request(settings):
    generated = iter(["first-pa", "second-pa"])
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["pa"])
        return httpx.Response(
            200, json={"status": 200, "success": True}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48AuthClient(
        settings,
        identity(),
        pa_provider=lambda: SecretStr(next(generated)),
        client=http,
    )
    await client.send_sms(mobile="13800138000")
    await client.send_sms(
        mobile="13800138000", challenge_answer="A"
    )
    assert seen == ["first-pa", "second-pa"]
    await http.aclose()


@pytest.mark.asyncio
async def test_parses_sms_verification_challenge(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": 2001,
                    "message": json.dumps(
                        {
                            "question": "请选择正确答案",
                            "answer": ["A", "B"],
                        }
                    ),
                },
            )
        )
    )
    client = Pocket48AuthClient(settings, identity(), client=http)
    result = await client.send_sms(mobile="13800138000")
    assert result.sent is False
    assert result.challenge is not None
    assert result.challenge.options == ("A", "B")
    await http.aclose()


@pytest.mark.asyncio
async def test_logs_in_and_keeps_token_secret(settings):
    seen_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_payload
        seen_payload = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": 200,
                "success": True,
                "content": {
                    "token": "private-token",
                    "userInfo": {"nickname": "member"},
                },
            },
        )

    http = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )
    client = Pocket48AuthClient(settings, identity(), client=http)
    credentials = await client.login_by_code(
        mobile="13800138000", code="123456"
    )
    assert seen_payload == {
        "mobile": "13800138000",
        "code": "123456",
        "area": "86",
    }
    assert credentials.token.get_secret_value() == "private-token"
    assert "private-token" not in repr(credentials)
    await http.aclose()


@pytest.mark.asyncio
async def test_reports_pa_requirement_without_response_message(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": 403,
                    "message": "13800138000 private-token",
                },
            )
        )
    )
    client = Pocket48AuthClient(settings, identity(), client=http)
    with pytest.raises(AppError) as captured:
        await client.send_sms(mobile="13800138000")
    assert "可能要求 pa" in str(captured.value)
    assert "13800138000" not in str(captured.value)
    await http.aclose()


@pytest.mark.asyncio
async def test_reports_html_403_as_edge_block(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403,
                headers={"Content-Type": "text/html"},
                content=b"<html>Forbidden</html>",
            )
        )
    )
    client = Pocket48AuthClient(settings, identity(), client=http)
    with pytest.raises(AppError) as captured:
        await client.send_sms(mobile="13800138000")
    assert captured.value.code == "pocket48_auth_edge_blocked"
    assert "CDN/WAF" in str(captured.value)
    await http.aclose()


def test_saves_credentials_with_private_permissions(tmp_path):
    path = tmp_path / "private" / "credentials.json"
    credentials = loadable_credentials()
    save_room_voice_credentials(path, credentials)

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_room_voice_credentials(path)
    assert loaded.token.get_secret_value() == "private-token"
    assert (
        json.loads(loaded.app_info.get_secret_value())["deviceId"]
        == "TEST-DEVICE"
    )


def test_saves_pa_seed_with_private_permissions(tmp_path):
    path = tmp_path / "private" / "pa.json"
    save_pa_signing_seed(path, SecretStr("test-signing-seed"))
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    generator = load_pa_generator(path)
    assert generator.signing_seed.get_secret_value() == "test-signing-seed"
    assert "test-signing-seed" not in repr(generator)


def test_rejects_world_readable_private_files(tmp_path):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(
            {
                "token": "private-token",
                "app_info": {"deviceId": "TEST-DEVICE"},
                "user_agent": "PocketFans201807/test",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o644)
    with pytest.raises(AppError, match="0600"):
        load_room_voice_credentials(path)


def loadable_credentials():
    from pocket48_summarizer.clients.pocket48_voice import (
        Pocket48VoiceCredentials,
    )

    return Pocket48VoiceCredentials(
        token=SecretStr("private-token"),
        app_info=SecretStr('{"deviceId":"TEST-DEVICE"}'),
        user_agent="PocketFans201807/test",
    )
