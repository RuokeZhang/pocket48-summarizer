from __future__ import annotations

import asyncio
import logging
import signal

from .config import Settings
from .voice_monitor import RoomVoiceMonitor


async def run_voice_monitor(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)

    monitor = RoomVoiceMonitor(settings)
    try:
        await monitor.run(stop_event)
    finally:
        await monitor.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run_voice_monitor())


if __name__ == "__main__":
    main()
