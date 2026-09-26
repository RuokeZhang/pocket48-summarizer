import json

import httpx
import pytest

from pocket48_summarizer.clients.pocket48 import Pocket48Client
from pocket48_summarizer.errors import AppError


@pytest.mark.asyncio
async def test_resolves_public_replay(settings):
    payload = {
        "status": 200,
        "success": True,
        "message": "OK",
        "content": {
            "liveId": "1297967327104274432",
            "review": True,
            "playStreamPath": "https://idol-vod.48.cn/path/replay.m3u8",
            "msgFilePath": "https://source.48.cn/live/replay.lrc",
            "coverPath": "/covers/replay.jpg",
            "title": "测试直播",
            "ctime": "1787389126152",
            "user": {"userId": "407126", "userName": "成员"},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getLiveOne")
        assert request.headers["referer"] == "https://h5.48.cn/"
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    pocket = Pocket48Client(settings, client)
    metadata = await pocket.resolve_replay("1297967327104274432")
    assert metadata.member_name == "成员"
    assert metadata.media_url.endswith(".m3u8")
    assert metadata.danmaku_url.endswith(".lrc")
    await client.aclose()


@pytest.mark.asyncio
async def test_member_replay_history_paginates_filters_and_deduplicates(settings):
    def entry(live_id, timestamp):
        return {"liveId": live_id, "ctime": str(timestamp),
                "userInfo": {"userId": "407126"}}

    cursors = []

    def handler(request):
        body = json.loads(request.content)
        assert body["userId"] == 407126
        assert body["record"] is True
        cursors.append(body["next"])
        entries = ([entry("300", 3000), entry("200", 2000)]
                   if body["next"] == "0"
                   else [entry("200", 2000), entry("100", 1000)])
        return httpx.Response(200, json={
            "status": 200, "success": True,
            "content": {"liveList": entries,
                        "next": "200" if body["next"] == "0" else "0"},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        entries = await Pocket48Client(settings, client).list_member_replays(407126, 1500)
    assert [entry.live_id for entry in entries] == ["200", "300"]
    assert cursors == ["0", "200"]


@pytest.mark.asyncio
@pytest.mark.parametrize("member_id,next_cursor,expected_code", [
    ("123", "0", "pocket48_replay_member_mismatch"),
    ("407126", "10", "pocket48_replay_pagination_failed"),
    ("407126", "bad", "pocket48_replay_list_invalid"),
])
async def test_replay_history_rejects_wrong_member_and_bad_pagination(
    settings, member_id, next_cursor, expected_code
):
    payload = {"status": 200, "success": True, "content": {
        "liveList": [{"liveId": "10", "ctime": "1000",
                      "userInfo": {"userId": member_id}}],
        "next": next_cursor,
    }}
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json=payload)
    )) as client:
        with pytest.raises(AppError) as error:
            await Pocket48Client(settings, client).list_member_replays(407126, 0)
    assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_rejects_non_replay(settings):
    payload = {
        "status": 200,
        "success": True,
        "content": {
            "liveId": "123456",
            "review": False,
            "playStreamPath": "",
            "user": {"userId": "1", "userName": "成员"},
        },
    }
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload)
        )
    )
    pocket = Pocket48Client(settings, client)
    with pytest.raises(AppError, match="已结束"):
        await pocket.resolve_replay("123456")
    await client.aclose()
