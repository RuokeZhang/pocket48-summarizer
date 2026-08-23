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
