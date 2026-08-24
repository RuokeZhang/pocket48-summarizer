from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from .config import Settings
from .db import Database
from .repository import JobRepository
from .services import build_services


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or Settings()
    settings.prepare_directories()
    database = Database(settings.database_path)
    database.initialize()
    repository = JobRepository(database)
    services = build_services(
        settings,
        repository,
        include_clipper=False,
    )
    if services.worker is None:
        raise RuntimeError("Worker service was not configured")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop_event.set)

    await services.worker.start()
    ready_value = str(Path.cwd().resolve())
    ready_temporary = settings.worker_ready_path.with_suffix(".tmp")
    ready_temporary.write_text(ready_value, encoding="utf-8")
    ready_temporary.replace(settings.worker_ready_path)
    stop_waiter = asyncio.create_task(stop_event.wait())
    worker_waiter = asyncio.create_task(services.worker.wait())
    try:
        done, _ = await asyncio.wait(
            {stop_waiter, worker_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if worker_waiter in done:
            await worker_waiter
            raise RuntimeError("Worker task exited unexpectedly")
    finally:
        try:
            if (
                settings.worker_ready_path.read_text(encoding="utf-8")
                == ready_value
            ):
                settings.worker_ready_path.unlink(missing_ok=True)
        except OSError:
            pass
        await services.close()
        for waiter in (stop_waiter, worker_waiter):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(
            stop_waiter,
            worker_waiter,
            return_exceptions=True,
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
