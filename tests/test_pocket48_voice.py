from __future__ import annotations

import hashlib
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
from pocket48_summarizer.media.room_voice import (
    ROOM_VOICE_PROTOCOL_WHITELIST,
    RoomVoiceProbeRecorder,
    RoomVoiceRollingRecorder,
)
from pocket48_summarizer.security import (
    inspect_room_voice_stream_url,
    validate_room_voice_stream_url,
)


def credentials() -> Pocket48VoiceCredentials:
    return Pocket48VoiceCredentials(
        token=SecretStr("test-token"),
        app_info=SecretStr('{"deviceId":"test-device"}'),
        user_agent="PocketFans201807/test",
        pa=SecretStr("test-pa"),
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


@pytest.mark.asyncio
async def test_resolves_member_room_with_fresh_pa(settings):
    generated = iter(["first-pa"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/im/server/jump")
        assert json.loads(request.content) == {
            "starId": 407126,
            "targetType": 1,
        }
        assert request.headers["pa"] == "first-pa"
        return httpx.Response(
            200,
            json={
                "status": 200,
                "success": True,
                "content": {
                    "channelId": 1001,
                    "jumpServerInfo": {"serverId": 2002},
                },
            },
        )

    dynamic = Pocket48VoiceCredentials(
        token=SecretStr("test-token"),
        app_info=SecretStr('{"deviceId":"test-device"}'),
        user_agent="PocketFans201807/test",
        pa_provider=lambda: SecretStr(next(generated)),
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48VoiceClient(settings, dynamic, http)
    room = await client.resolve_member_room(407126)
    assert room.member_id == 407126
    assert room.channel_id == 1001
    assert room.server_id == 2002
    await http.aclose()


@pytest.mark.asyncio
async def test_resolves_member_room_with_top_level_server_id(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": 200,
                    "success": True,
                    "content": {
                        "channelId": 1001,
                        "serverId": 2002,
                        "jumpServerInfo": None,
                    },
                },
            )
        )
    )
    client = Pocket48VoiceClient(settings, credentials(), http)
    room = await client.resolve_member_room(407126)
    assert room.channel_id == 1001
    assert room.server_id == 2002
    await http.aclose()


def test_rejects_invalid_app_info_before_request():
    invalid = Pocket48VoiceCredentials(
        token=SecretStr("test-token"),
        app_info=SecretStr("not-json"),
        user_agent="PocketFans201807/test",
        pa=SecretStr("test-pa"),
    )
    with pytest.raises(ConfigurationError, match="JSON 对象"):
        invalid.request_headers()


def test_voice_credentials_generate_pa_per_request():
    generated = iter(["first-pa", "second-pa"])
    dynamic = Pocket48VoiceCredentials(
        token=SecretStr("test-token"),
        app_info=SecretStr('{"deviceId":"test-device"}'),
        user_agent="PocketFans201807/test",
        pa_provider=lambda: SecretStr(next(generated)),
    )
    assert dynamic.request_headers()["pa"] == "first-pa"
    assert dynamic.request_headers()["pa"] == "second-pa"


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
@pytest.mark.parametrize(
    "stream_url",
    [
        "rtmps://voice.example.test/live\n-injected",
        "rtmps://voice.example.test/live\tstream",
        "rtmps://voice.example.test/" + ("x" * 4096),
    ],
)
async def test_rejects_control_or_oversized_stream_url(
    settings, stream_url
):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "status": 200,
                    "content": {
                        "streamUrl": stream_url,
                        "voiceUserList": [],
                    },
                },
            )
        )
    )
    client = Pocket48VoiceClient(settings, credentials(), http)
    with pytest.raises(AppError):
        await client.fetch_status(1001, 2002)
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
async def test_retries_http_403_without_echoing_response(settings):
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
    assert captured.value.code == "room_voice_lookup_failed"
    assert "test-token" not in str(captured.value)
    await http.aclose()


@pytest.mark.asyncio
async def test_treats_business_401004_as_expired_token(settings):
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"status": 401004, "message": "token invalid"},
            )
        )
    )
    client = Pocket48VoiceClient(settings, credentials(), http)
    with pytest.raises(AppError) as captured:
        await client.resolve_member_room(407126)
    assert captured.value.code == "room_voice_auth_required"
    assert "重新登录" in str(captured.value)
    await http.aclose()


@pytest.mark.asyncio
async def test_resolves_chatroom_id_across_conversation_pages(settings):
    requests: list[dict[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["nextTime"] == 0:
            content = {
                "conversations": [
                    {"ownerId": "999", "targetId": "111"}
                ],
                "nextTime": 123,
            }
        else:
            content = {
                "conversations": [
                    {"ownerId": "6744", "targetId": "67333093"}
                ],
                "nextTime": 0,
            }
        return httpx.Response(
            200,
            json={"status": 200, "success": True, "content": content},
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48VoiceClient(settings, credentials(), http)

    assert await client.resolve_chatroom_id(6744) == 67333093
    assert requests == [
        {"nextTime": 0, "limit": 100},
        {"nextTime": 123, "limit": 100},
    ]
    await http.aclose()


@pytest.mark.asyncio
async def test_fetches_only_deduplicated_public_fan_text_messages(
    settings,
):
    started_at_ms = 1_000_000
    ended_at_ms = 1_100_000
    fan_ext = json.dumps(
        json.dumps(
            {
                "user": {
                    "userId": "88",
                    "roleId": 0,
                    "nickName": "粉丝\u0000",
                },
            }
        )
    )
    member_ext = json.dumps(
        {
            "user": {
                "userId": "6744",
                "roleId": 3,
                "nickName": "成员",
            },
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/im/api/v1/team/message/list/all")
        assert json.loads(request.content) == {
            "channelId": 1230624,
            "serverId": 6227955,
            "nextTime": 0,
            "limit": 700,
        }
        messages = [
            {
                "msgType": "TEXT",
                "msgTime": started_at_ms + 20_000,
                "msgidClient": "fan-message",
                "bodys": "  大家好\u0000  ",
                "extInfo": fan_ext,
            },
            {
                "msgType": "TEXT",
                "msgTime": started_at_ms + 20_000,
                "msgidClient": "fan-message",
                "bodys": "  大家好\u0000  ",
                "extInfo": fan_ext,
            },
            {
                "msgType": "TEXT",
                "msgTime": started_at_ms + 30_000,
                "msgidClient": "member-message",
                "bodys": "成员本人",
                "extInfo": member_ext,
            },
            {
                "msgType": "IMAGE",
                "msgTime": started_at_ms + 40_000,
                "msgidClient": "image-message",
                "bodys": "图片",
                "extInfo": fan_ext,
            },
            {
                "msgType": "TEXT",
                "msgTime": started_at_ms - 1,
                "msgidClient": "before-window",
                "bodys": "窗口之前",
                "extInfo": fan_ext,
            },
        ]
        return httpx.Response(
            200,
            json={
                "status": 200,
                "success": True,
                "content": {"message": messages, "nextTime": 0},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = Pocket48VoiceClient(settings, credentials(), http)
    messages = await client.fetch_public_room_messages(
        channel_id=1230624,
        server_id=6227955,
        member_id=6744,
        started_at_ms=started_at_ms,
        ended_at_ms=ended_at_ms,
    )

    assert len(messages) == 1
    assert messages[0].message_id == hashlib.sha256(
        f"{started_at_ms + 20_000}\0粉丝\0大家好".encode("utf-8")
    ).hexdigest()
    assert messages[0].timestamp_ms == 20_000
    assert messages[0].nickname == "粉丝"
    assert messages[0].text == "大家好"
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


def test_builds_bounded_rolling_segment_command(settings, tmp_path):
    settings.ffmpeg_path = sys.executable
    recorder = RoomVoiceRollingRecorder(settings)
    session_path = tmp_path / "session"
    command = recorder.build_command(
        "rtmps://voice.example.test/live/stream?token=secret",
        session_path,
        duration_seconds=4 * 60 * 60,
        segment_seconds=300,
    )

    assert command[0] == sys.executable
    assert "-nostdin" in command
    assert command[command.index("-protocol_whitelist") + 1] == (
        ROOM_VOICE_PROTOCOL_WHITELIST
    )
    assert command[command.index("-rw_timeout") + 1] == "15000000"
    assert command[command.index("-t") + 1] == "14400"
    assert command[command.index("-segment_time") + 1] == "300"
    assert command[command.index("-c:a") + 1] == "libmp3lame"
    assert command[command.index("-b:a") + 1] == "192k"
    assert command[-1].endswith(
        "/segments/segment-%06d.mp3"
    )
