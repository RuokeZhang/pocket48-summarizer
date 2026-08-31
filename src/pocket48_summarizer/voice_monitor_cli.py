from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Callable

from .config import Settings
from .voice_monitor import (
    RoomVoiceMonitor,
    RoomVoiceStorageCoordinator,
)


async def run_voice_monitor(
    settings: Settings | None = None,
    *,
    monitor_factory: Callable[
        [Settings, RoomVoiceStorageCoordinator], RoomVoiceMonitor
    ] = lambda target_settings, coordinator: RoomVoiceMonitor(
        target_settings, storage_coordinator=coordinator
    ),
) -> None:
    settings = settings or Settings()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)

    storage_coordinator = RoomVoiceStorageCoordinator()
    monitors = [
        monitor_factory(target_settings, storage_coordinator)
        for target_settings in settings.room_voice_monitor_settings()
    ]
    try:
        async with asyncio.TaskGroup() as task_group:
            for monitor in monitors:
                task_group.create_task(monitor.run(stop_event))
    finally:
        await asyncio.gather(
            *(monitor.close() for monitor in monitors),
            return_exceptions=True,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(run_voice_monitor())


if __name__ == "__main__":
    main()
