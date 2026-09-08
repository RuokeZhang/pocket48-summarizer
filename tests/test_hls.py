import httpx
import pytest

from pocket48_summarizer.errors import AppError
from pocket48_summarizer.media.hls import HLSInspector


@pytest.mark.asyncio
async def test_inspects_vod_manifest(settings):
    manifest = """#EXTM3U
#EXT-X-TARGETDURATION:6
#EXTINF:5.5,
/fragments/a.ts
#EXTINF:6.0,
segment-b.ts
#EXT-X-ENDLIST
"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["origin"] == "https://h5.48.cn"
        return httpx.Response(200, text=manifest)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    inspector = HLSInspector(settings, client)
    result = await inspector.inspect(
        "https://idol-vod.48.cn/path/replay.m3u8"
    )
    assert result.duration_ms == 11_500
    assert result.segment_count == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_rejects_unfinished_manifest(settings):
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, text="#EXTM3U\n#EXTINF:6,\nsegment.ts\n"
            )
        )
    )
    inspector = HLSInspector(settings, client)
    with pytest.raises(AppError, match="完整回放"):
        await inspector.inspect(
            "https://idol-vod.48.cn/path/replay.m3u8"
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_allows_long_replay_when_hour_limit_is_disabled(settings):
    manifest = """#EXTM3U
#EXTINF:14400,
segment.ts
#EXT-X-ENDLIST
"""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=manifest)
        )
    )
    settings.max_replay_hours = 0
    inspector = HLSInspector(settings, client)

    result = await inspector.inspect(
        "https://idol-vod.48.cn/path/replay.m3u8"
    )

    assert result.duration_ms == 14_400_000
    await client.aclose()


@pytest.mark.asyncio
async def test_optional_hour_limit_can_still_be_enabled(settings):
    manifest = """#EXTM3U
#EXTINF:14400,
segment.ts
#EXT-X-ENDLIST
"""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=manifest)
        )
    )
    settings.max_replay_hours = 3
    inspector = HLSInspector(settings, client)

    with pytest.raises(AppError, match="3 小时上限"):
        await inspector.inspect(
            "https://idol-vod.48.cn/path/replay.m3u8"
        )
    await client.aclose()


@pytest.mark.asyncio
async def test_resolve_segment_at_finds_containing_segment(settings):
    manifest = """#EXTM3U
#EXT-X-DISCONTINUITY
#EXTINF:6.000,
/fragments/a.ts
#EXTINF:6.000,
/fragments/b.ts
#EXTINF:6.000,
/fragments/c.ts
#EXT-X-ENDLIST
"""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=manifest)
        )
    )
    inspector = HLSInspector(settings, client)

    segment_url, offset = await inspector.resolve_segment_at(
        "https://idol-vod.48.cn/path/replay.m3u8", 13_500
    )
    assert segment_url == "https://idol-vod.48.cn/fragments/c.ts"
    assert offset == pytest.approx(1.5, abs=1e-6)
    await client.aclose()


@pytest.mark.asyncio
async def test_resolve_segment_at_zero_lands_on_first_segment(settings):
    manifest = """#EXTM3U
#EXTINF:6.000,
/fragments/a.ts
#EXTINF:6.000,
/fragments/b.ts
#EXT-X-ENDLIST
"""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=manifest)
        )
    )
    inspector = HLSInspector(settings, client)

    segment_url, offset = await inspector.resolve_segment_at(
        "https://idol-vod.48.cn/path/replay.m3u8", 0
    )
    assert segment_url == "https://idol-vod.48.cn/fragments/a.ts"
    assert offset == 0.0
    await client.aclose()


@pytest.mark.asyncio
async def test_resolve_segment_at_rejects_out_of_range(settings):
    manifest = """#EXTM3U
#EXTINF:6.000,
/fragments/a.ts
#EXT-X-ENDLIST
"""
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text=manifest)
        )
    )
    inspector = HLSInspector(settings, client)

    with pytest.raises(AppError, match="超过回放长度"):
        await inspector.resolve_segment_at(
            "https://idol-vod.48.cn/path/replay.m3u8", 30_000
        )
    await client.aclose()
