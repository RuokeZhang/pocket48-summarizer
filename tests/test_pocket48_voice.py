from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from pocket48_summarizer.clients.pocket48_voice import (
    Pocket48VoiceClient,
    Pocket48VoiceCredentials,
)
from pocket48_summarizer.errors import AppError, ConfigurationError
from pocket48_summarizer.media.room_voice import RoomVoiceProbeRecorder
from pocket48_summarizer.security import (
    inspect_room_voice_stream_url,
    validate_room_voice_stream_url,
)


def credentials() -> Pocket48VoiceCredentials:
    return Pocket48VoiceCredentials(
        token=SecretStr("test-token"),
        pa=SecretStr("test-pa"),
        app_info=SecretStr('{"deviceId":"test-device"}'),
        user_agent="PocketFans201807/test",
    )


@pytest.mark.asyncio
async def test_resolves_room_server_with_private_headers(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/im/team/room/info")
        assert json.loads(request.content) == {"channelId": "1001"}
        assert request.headers["token"] == "test-token"
        assert request.headers["pa"] == "test-pa"
        assert request.headers["appinfo"] == '{"deviceId":"test-device"}'
        assert request.headers["p-sign-type"] == "V0"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "success": True,
                "content": {"serverId": 2002},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48VoiceClient(settings, credentials(), http)
    assert await client.resolve_server_id(1001) == 2002
    await http.aclose()


def test_rejects_invalid_app_info_before_request():
    invalid = Pocket48VoiceCredentials(
        token=SecretStr("test-token"),
        pa=SecretStr("test-pa"),
        app_info=SecretStr("not-json"),
        user_agent="PocketFans201807/test",
    )
    with pytest.raises(ConfigurationError, match="JSON 对象"):
        invalid.request_headers()


@pytest.mark.asyncio
async def test_fetches_redacted_active_voice_status(settings):
    stream_url = (
        "rtmps://voice.example.test/live/stream"
        "?token=do-not-print-this"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/team/voice/operate")
        assert json.loads(request.content) == {
            "channelId": 1001,
            "serverId": 2002,
            "operateCode": 2,
        }
        return httpx.Response(
            200,
            json={
                "status": 200,
                "content": {
                    "streamUrl": stream_url,
                    "voiceUserList": [
                        {
                            "userId": 3003,
                            "userName": "成员\u0000",
                            "voiceStatus": True,
                        }
                    ],
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48VoiceClient(settings, credentials(), http)
    status = await client.fetch_status(1001, 2002)

    assert status.active is True
    assert status.participants[0].user_id == "3003"
    assert status.participants[0].nickname == "成员"
    assert status.redacted_summary() == {
        "active": True,
        "channel_id": 1001,
        "server_id": 2002,
        "participant_count": 1,
        "stream": {
            "scheme": "rtmps",
            "host": "voice.example.test",
            "port": None,
        },
    }
    assert "do-not-print-this" not in repr(status)
    await http.aclose()


@pytest.mark.asyncio
async def test_tolerates_null_voice_user_list(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": 200,
                    "content": {
                        "streamUrl": "",
                        "voiceUserList": None,
                    },
                },
            )
        )
    )
    client = Pocket48VoiceClient(settings, credentials(), http)
    status = await client.fetch_status(1001, 2002)
    assert status.active is False
    assert status.participants == ()
    await http.aclose()


@pytest.mark.asyncio
async def test_rejects_auth_failure_without_echoing_response(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403,
                json={
                    "status": 403,
                    "message": "test-token do-not-print",
                },
            )
        )
    )
    client = Pocket48VoiceClient(settings, credentials(), http)
    with pytest.raises(AppError) as captured:
        await client.fetch_status(1001, 2002)
    assert captured.value.code == "room_voice_auth_required"
    assert "test-token" not in str(captured.value)
    await http.aclose()


@pytest.mark.asyncio
async def test_rejects_oversized_or_malformed_voice_payload(settings):
    settings.max_api_response_bytes = 8
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=b'{"status":200}')
        )
    )
    client = Pocket48VoiceClient(settings, credentials(), http)
    with pytest.raises(AppError) as captured:
        await client.fetch_status(1001, 2002)
    assert captured.value.code == "room_voice_api_changed"
    await http.aclose()


@pytest.mark.parametrize(
    "value",
    [
        "https://voice.example.test/live/stream",
        "rtmp://user:password@voice.example.test/live/stream",
        "rtmp://voice.example.test",
        "rtmp://voice.example.test/live/stream#fragment",
    ],
)
def test_rejects_unsafe_room_voice_stream_urls(value):
    with pytest.raises(AppError, match="RTMP"):
        inspect_room_voice_stream_url(value)


def test_requires_explicit_stream_host_allowlist():
    stream_url = "rtmps://voice.example.test/live/stream?token=secret"
    with pytest.raises(AppError) as captured:
        validate_room_voice_stream_url(stream_url, set())
    assert captured.value.code == "unapproved_room_voice_stream_host"
    assert (
        validate_room_voice_stream_url(
            stream_url, {"voice.example.test"}
        )
        == stream_url
    )


def test_builds_bounded_ffmpeg_probe_command(settings, tmp_path):
    settings.ffmpeg_path = sys.executable
    recorder = RoomVoiceProbeRecorder(settings)
    output_path = tmp_path / "probe.part.mp3"
    command = recorder.build_command(
        "rtmps://voice.example.test/live/stream?token=secret",
        output_path,
        duration_seconds=60,
        allowed_hosts={"voice.example.test"},
    )

    assert command[0] == sys.executable
    assert "-nostdin" in command
    assert command[command.index("-t") + 1] == "60"
    assert command[command.index("-i") + 1].startswith("rtmps://")
    assert command[-1] == str(output_path)
