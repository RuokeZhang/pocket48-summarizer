from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .clients.pocket48 import Pocket48Client
from .config import Settings
from .errors import AppError
from .repository import JobRepository


async def poll_replays_if_due(
    settings: Settings,
    repository: JobRepository,
    pocket48: Pocket48Client,
) -> int:
    member_id = settings.replay_watch_member_id
    if member_id is None:
        return 0
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    with repository.database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO replay_watch_state (member_id, enabled_at_ms) "
            "VALUES (?, ?)", (member_id, now_ms),
        )
        state = connection.execute(
            "SELECT * FROM replay_watch_state WHERE member_id = ?", (member_id,)
        ).fetchone()
        last_attempt = state["last_attempt_at_ms"]
        if last_attempt is not None and now_ms - last_attempt < 86_400_000:
            return 0
        connection.execute(
            "UPDATE replay_watch_state SET last_attempt_at_ms = ? WHERE member_id = ?",
            (now_ms, member_id),
        )
    queued = 0
    try:
        replays = await pocket48.list_member_replays(member_id, state["enabled_at_ms"])
        for replay in replays:
            existing = await asyncio.to_thread(
                repository.get_job_by_live_id, replay.live_id
            )
            if existing is not None:
                continue
            try:
                metadata = await pocket48.resolve_replay(replay.live_id)
            except AppError as exc:
                if exc.code == "replay_not_ready":
                    continue
                raise
            if metadata.member_id != str(member_id):
                raise AppError(
                    "pocket48_replay_member_mismatch",
                    "回放成员与自动检查目标不一致", False,
                )
            _, created = await asyncio.to_thread(
                repository.create_or_get_job,
                "https://h5.48.cn/2019appshare/memberLiveShare/index.html?id="
                + replay.live_id,
                replay.live_id,
            )
            queued += int(created)
    except AppError as exc:
        with repository.database.connect() as connection:
            connection.execute(
                "UPDATE replay_watch_state SET last_error_code = ? WHERE member_id = ?",
                (exc.code, member_id),
            )
        raise
    with repository.database.connect() as connection:
        connection.execute(
            "UPDATE replay_watch_state SET last_success_at_ms = ?, last_error_code = NULL "
            "WHERE member_id = ?", (now_ms, member_id),
        )
    return queued
