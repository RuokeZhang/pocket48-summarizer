from __future__ import annotations

import asyncio
import os
import sys

from pocket48_summarizer.clients.pocket48 import Pocket48Client
from pocket48_summarizer.config import Settings
from pocket48_summarizer.media.hls import HLSInspector
from pocket48_summarizer.parsing.lrc import detect_danmaku_peaks, parse_lrc
from pocket48_summarizer.security import parse_share_url


async def run(url: str) -> None:
    _, live_id = parse_share_url(url)
    settings = Settings(enable_worker=False)
    pocket48 = Pocket48Client(settings)
    hls = HLSInspector(settings)
    try:
        metadata = await pocket48.resolve_replay(live_id)
        manifest = await hls.inspect(metadata.media_url)
        entries = (
            parse_lrc(await pocket48.fetch_danmaku(metadata.danmaku_url))
            if metadata.danmaku_url
            else []
        )
        peaks = detect_danmaku_peaks(entries)
        print(
            {
                "live_id": metadata.live_id,
                "member_name": metadata.member_name,
                "title": metadata.title,
                "duration_ms": manifest.duration_ms,
                "segment_count": manifest.segment_count,
                "danmaku_entries": len(entries),
                "danmaku_peaks": len(peaks),
            }
        )
    finally:
        await pocket48.close()
        await hls.close()


def main() -> None:
    if os.environ.get("P48_RUN_NETWORK_SMOKE") != "1":
        raise SystemExit(
            "Refusing network smoke test. Set P48_RUN_NETWORK_SMOKE=1."
        )
    if len(sys.argv) != 2:
        raise SystemExit("Usage: metadata_smoke.py <Pocket48 share URL>")
    asyncio.run(run(sys.argv[1]))


if __name__ == "__main__":
    main()
