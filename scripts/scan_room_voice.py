from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pocket48_summarizer.clients.pocket48_auth import (
    load_pa_generator,
    load_room_voice_credentials,
)
from pocket48_summarizer.clients.pocket48_voice import Pocket48VoiceClient
from pocket48_summarizer.config import Settings
from pocket48_summarizer.errors import AppError, ExternalServiceError

CONFIRMATION = "I_UNDERSTAND_THIS_SCANS_MY_PRIVATE_ACCOUNT_ONCE"
MEMBER_PATH = "/user/api/v1/client/update/group_team_star"
ROOM_MAP_PATH = "/im/api/v1/team/star/server/map/get"
LAST_MESSAGE_PATH = "/im/api/v1/team/classic/last/message/get"
MAX_SCAN_ROOMS = 600
LAST_MESSAGE_BATCH_SIZE = 50


@dataclass(frozen=True, slots=True)
class ScanCandidate:
    member_id: int
    member_name: str
    server_id: int
    channel_id: int
    active_member: bool


def chunks(values: list[int], size: int) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def parse_positive_int(value: Any) -> int | None:
    normalized = str(value or "").strip()
    if not normalized.isdigit():
        return None
    number = int(normalized)
    return number if number > 0 else None


def require_success_content(body: Any, label: str) -> dict[str, Any]:
    if (
        not isinstance(body, dict)
        or body.get("status") != 200
        or body.get("success") is not True
        or not isinstance(body.get("content"), dict)
    ):
        raise ExternalServiceError(
            "room_voice_scan_api_changed",
            f"口袋48{label}响应结构已变化",
            False,
        )
    return body["content"]


async def load_candidates(
    client: Pocket48VoiceClient, interval_seconds: float
) -> list[ScanCandidate]:
    member_response = await client._post(MEMBER_PATH, {})
    member_content = require_success_content(
        member_response.json(), "成员目录"
    )
    raw_members = member_content.get("starInfo")
    if not isinstance(raw_members, list):
        raise ExternalServiceError(
            "room_voice_scan_api_changed",
            "口袋48成员目录缺少 starInfo",
            False,
        )
    members: dict[int, tuple[str, bool]] = {}
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        member_id = parse_positive_int(item.get("userId"))
        name = str(item.get("realName") or "").strip()[:100]
        if member_id is not None and name:
            members[member_id] = (name, item.get("status") == 1)

    await asyncio.sleep(interval_seconds)
    map_response = await client._post(ROOM_MAP_PATH, {})
    map_content = require_success_content(
        map_response.json(), "房间映射"
    )
    raw_mapping = map_content.get("userServerMap")
    if not isinstance(raw_mapping, dict):
        raise ExternalServiceError(
            "room_voice_scan_api_changed",
            "口袋48房间映射缺少 userServerMap",
            False,
        )
    member_servers: dict[int, int] = {}
    for raw_member_id, raw_server_id in raw_mapping.items():
        member_id = parse_positive_int(raw_member_id)
        server_id = parse_positive_int(raw_server_id)
        if member_id is not None and server_id is not None:
            member_servers[member_id] = server_id

    first_channels: dict[int, int] = {}
    server_ids = list(dict.fromkeys(member_servers.values()))
    for server_batch in chunks(server_ids, LAST_MESSAGE_BATCH_SIZE):
        await asyncio.sleep(interval_seconds)
        response = await client._post(
            LAST_MESSAGE_PATH, {"serverIdList": server_batch}
        )
        content = require_success_content(
            response.json(), "批量房间消息"
        )
        rows = content.get("lastMsgList")
        if not isinstance(rows, list):
            raise ExternalServiceError(
                "room_voice_scan_api_changed",
                "口袋48批量房间消息缺少 lastMsgList",
                False,
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            server_id = parse_positive_int(row.get("serverId"))
            channel_id = parse_positive_int(row.get("channelId"))
            if server_id is not None and channel_id is not None:
                first_channels.setdefault(server_id, channel_id)

    candidates = []
    for member_id, server_id in member_servers.items():
        member = members.get(member_id)
        channel_id = first_channels.get(server_id)
        if member is None or channel_id is None:
            continue
        candidates.append(
            ScanCandidate(
                member_id=member_id,
                member_name=member[0],
                server_id=server_id,
                channel_id=channel_id,
                active_member=member[1],
            )
        )
    candidates.sort(
        key=lambda item: (
            not item.active_member,
            item.member_name,
            item.member_id,
        )
    )
    return candidates


async def run() -> None:
    if os.environ.get("P48_RUN_ROOM_VOICE_SCAN") != CONFIRMATION:
        raise SystemExit(
            "Refusing private-account scan. Set "
            f"P48_RUN_ROOM_VOICE_SCAN={CONFIRMATION}."
        )
    try:
        requested_limit = int(
            os.environ.get("P48_ROOM_VOICE_SCAN_MAX_ROOMS", "600")
        )
        interval_seconds = float(
            os.environ.get(
                "P48_ROOM_VOICE_SCAN_INTERVAL_SECONDS", "1.0"
            )
        )
    except ValueError as exc:
        raise SystemExit("Invalid room voice scan limits.") from exc
    if requested_limit < 1 or requested_limit > MAX_SCAN_ROOMS:
        raise SystemExit(
            f"P48_ROOM_VOICE_SCAN_MAX_ROOMS must be 1-{MAX_SCAN_ROOMS}."
        )
    if interval_seconds < 1.0 or interval_seconds > 10:
        raise SystemExit(
            "P48_ROOM_VOICE_SCAN_INTERVAL_SECONDS must be 1-10."
        )

    settings = Settings(enable_worker=False, external_retry_attempts=1)
    generator = load_pa_generator(
        settings.pocket48_pa_signing_seed_path
    )
    credentials = load_room_voice_credentials(
        settings.pocket48_voice_credentials_path,
        pa_provider=generator.generate,
    )
    client = Pocket48VoiceClient(settings, credentials)
    checked = 0
    anomalies = 0
    activity_without_stream = 0
    try:
        candidates = await load_candidates(client, interval_seconds)
        candidates = candidates[:requested_limit]
        print(
            json.dumps(
                {
                    "scan": "started",
                    "candidate_rooms": len(candidates),
                    "interval_seconds": interval_seconds,
                    "stop_condition": "first_recordable_stream",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        for candidate in candidates:
            await asyncio.sleep(interval_seconds)
            try:
                status = await client.fetch_status(
                    candidate.channel_id, candidate.server_id
                )
            except AppError as exc:
                if exc.code == "room_voice_auth_required":
                    raise
                anomalies += 1
                if anomalies >= 3:
                    raise ExternalServiceError(
                        "room_voice_scan_aborted",
                        "连续三个房间返回异常，已停止全量扫描",
                        False,
                    ) from exc
                await asyncio.sleep(5)
                continue
            anomalies = 0
            checked += 1
            endpoint = status.stream_endpoint()
            if status.active and endpoint is None:
                activity_without_stream += 1
            if endpoint is not None:
                print(
                    json.dumps(
                        {
                            "scan": "recordable_stream_found",
                            "checked_rooms": checked,
                            "member_name": candidate.member_name,
                            "member_id": candidate.member_id,
                            "channel_id": candidate.channel_id,
                            "server_id": candidate.server_id,
                            "participant_count": len(
                                status.participants
                            ),
                            "stream": {
                                "scheme": endpoint[0],
                                "host": endpoint[1],
                                "port": endpoint[2],
                            },
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return
            if checked % 25 == 0:
                print(
                    json.dumps(
                        {
                            "scan": "progress",
                            "checked_rooms": checked,
                            "candidate_rooms": len(candidates),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        print(
            json.dumps(
                {
                    "scan": "completed_without_stream",
                    "checked_rooms": checked,
                    "candidate_rooms": len(candidates),
                    "activity_without_stream": activity_without_stream,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    finally:
        await client.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
