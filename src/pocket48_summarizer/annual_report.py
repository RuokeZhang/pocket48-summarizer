"""Public annual report pipeline.

Steps for one report: historical nicknames -> archived room messages -> the
selected member's replay list -> danmaku matched by nickname -> statistics.
The browser renders the poster from the JSON result, so the server never
renders images and never calls a model.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import hmac
import json
import logging
import secrets
import uuid
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from .annual_report_stats import (
    REPORT_VERSION,
    Replay,
    RoomMessage,
    build_report,
    china_today,
    parse_lrc_lines,
    scope_start_ms,
)
from .clients.msg48 import MemberIndexEntry, Msg48Client
from .clients.pocket48 import Pocket48Client
from .config import Settings
from .db import Database
from .errors import AppError
from .parsing.lrc import parse_lrc_timestamp

LIVE_LIST_TTL = timedelta(hours=6)
MEMBER_INDEX_TTL = timedelta(hours=24)
MEMBER_INDEX_RETRY = timedelta(minutes=15)
MEMBER_SNAPSHOT_PATH = Path(__file__).with_name("annual_report_members.json")


@dataclass(slots=True)
class AnnualReportJob:
    id: str
    uid: str
    member_pocket_id: str
    member_name: str
    scope: str
    extra_nicknames: list[str]
    status: str
    progress_text: str
    result: dict | None
    error_code: str | None
    error_message: str | None
    created_at: str
    queue_position: int = 0


@dataclass(slots=True)
class ReportMember:
    pocket_id: str
    name: str
    group_name: str
    team_name: str
    in_group: bool
    pinyin: str = ""
    aliases: str = ""
    avatar: str = ""

    @classmethod
    def from_index(cls, entry: MemberIndexEntry) -> "ReportMember":
        return cls(str(entry.pocket_id), entry.name, entry.group_name, entry.team_name,
                   entry.in_group, entry.pinyin, entry.aliases, entry.avatar)

    @property
    def search_text(self) -> str:
        return " ".join([self.name, self.pinyin, self.aliases, self.group_name, self.team_name]).casefold()


def _now() -> datetime:
    return datetime.now(UTC)


class AnnualReportRepository:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self._secret: bytes | None = None

    def _hash(self, value: str) -> str:
        if self._secret is None:
            path = self.settings.annual_report_secret_path
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(secrets.token_hex(32), encoding="utf-8")
                path.chmod(0o600)
            self._secret = path.read_text(encoding="utf-8").strip().encode()
        return hmac.new(self._secret, value.encode(), hashlib.sha256).hexdigest()

    def _job(self, row) -> AnnualReportJob:
        return AnnualReportJob(
            id=row["id"],
            uid=row["uid"],
            member_pocket_id=row["member_pocket_id"],
            member_name=row["member_name"],
            scope=row["scope"],
            extra_nicknames=json.loads(row["extra_nicknames_json"]),
            status=row["status"],
            progress_text=row["progress_text"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
        )

    def enqueue(
        self,
        *,
        uid: str,
        member: ReportMember,
        scope: str,
        extra_nicknames: list[str],
        requester: str,
        unlimited: bool = False,
    ) -> AnnualReportJob:
        cache_key = self._hash(
            json.dumps([REPORT_VERSION, uid, member.pocket_id, scope, sorted(extra_nicknames)])
        )
        requester_hash = self._hash(f"requester:{requester}")
        now = _now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reusable = connection.execute(
                """
                SELECT id FROM annual_report_jobs
                WHERE cache_key = ?
                  AND (status IN ('queued', 'running')
                       OR (status = 'completed' AND created_at >= ?))
                ORDER BY created_at DESC LIMIT 1
                """,
                (cache_key, (now - timedelta(hours=self.settings.annual_report_cache_hours)).isoformat()),
            ).fetchone()
            if reusable is not None:
                job_id = reusable["id"]
            else:
                recent = connection.execute(
                    """
                    SELECT COUNT(*) FROM annual_report_jobs
                    WHERE requester_hash = ? AND created_at >= ?
                    """,
                    (requester_hash, (now - timedelta(hours=1)).isoformat()),
                ).fetchone()[0]
                if not unlimited and recent >= self.settings.annual_report_hourly_limit:
                    raise AppError("report_rate_limited", "生成太频繁了，请一小时后再试", True)
                pending = connection.execute(
                    "SELECT COUNT(*) FROM annual_report_jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                if not unlimited and pending >= self.settings.annual_report_queue_limit:
                    raise AppError("report_queue_full", "现在排队的人太多了，请稍后再试", True)
                job_id = uuid.uuid4().hex
                connection.execute(
                    """
                    INSERT INTO annual_report_jobs (
                        id, cache_key, uid, member_pocket_id, member_name, scope,
                        extra_nicknames_json, requester_hash, status, progress_text,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', '排队中', ?)
                    """,
                    (job_id, cache_key, uid, member.pocket_id, member.name, scope,
                     json.dumps(extra_nicknames, ensure_ascii=False), requester_hash,
                     now.isoformat()),
                )
        job = self.get(job_id)
        assert job is not None
        return job

    def get(self, job_id: str) -> AnnualReportJob | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM annual_report_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return None
            job = self._job(row)
            if job.status == "queued":
                job.queue_position = connection.execute(
                    """
                    SELECT COUNT(*) FROM annual_report_jobs
                    WHERE status = 'running'
                       OR (status = 'queued' AND created_at < ?)
                    """,
                    (row["created_at"],),
                ).fetchone()[0] + 1
        return job

    def claim_next(self, worker_id: str, lease_seconds: int) -> AnnualReportJob | None:
        now = _now()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT id FROM annual_report_jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE annual_report_jobs
                SET status = 'running', worker_id = ?, lease_expires_at = ?,
                    started_at = ?, progress_text = '准备中'
                WHERE id = ? AND status = 'queued'
                """,
                (worker_id, (now + timedelta(seconds=lease_seconds)).isoformat(),
                 now.isoformat(), row["id"]),
            )
        return self.get(row["id"])

    def heartbeat(self, job_id: str, worker_id: str, lease_seconds: int, progress_text: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE annual_report_jobs SET lease_expires_at = ?, progress_text = ?
                WHERE id = ? AND worker_id = ? AND status = 'running'
                """,
                ((_now() + timedelta(seconds=lease_seconds)).isoformat(), progress_text,
                 job_id, worker_id),
            )

    def complete(self, job_id: str, result: dict) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE annual_report_jobs
                SET status = 'completed', result_json = ?, uid = '', progress_text = '已完成',
                    worker_id = NULL, lease_expires_at = NULL, finished_at = ?
                WHERE id = ?
                """,
                (json.dumps(result, ensure_ascii=False), _now().isoformat(), job_id),
            )

    def fail(self, job_id: str, code: str, message: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE annual_report_jobs
                SET status = 'failed', error_code = ?, error_message = ?, uid = '',
                    progress_text = '生成失败', worker_id = NULL, lease_expires_at = NULL,
                    finished_at = ?
                WHERE id = ?
                """,
                (code, message, _now().isoformat(), job_id),
            )

    def recover_expired(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                """
                UPDATE annual_report_jobs
                SET status = 'queued', worker_id = NULL, lease_expires_at = NULL,
                    progress_text = '排队中'
                WHERE status = 'running' AND lease_expires_at < ?
                """,
                (_now().isoformat(),),
            ).rowcount

    def delete_expired(self) -> int:
        cutoff = (_now() - timedelta(days=self.settings.annual_report_retention_days)).isoformat()
        with self.database.connect() as connection:
            return connection.execute(
                "DELETE FROM annual_report_jobs WHERE status IN ('completed', 'failed') AND created_at < ?",
                (cutoff,),
            ).rowcount

    def cached_replays(self, member_pocket_id: str) -> dict[str, tuple[str, str, int, list]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM annual_report_live_cache WHERE member_pocket_id = ?",
                (member_pocket_id,),
            ).fetchall()
        cached = {}
        for row in rows:
            lines = json.loads(zlib.decompress(row["lines_blob"])) if row["lines_blob"] else []
            cached[row["live_id"]] = (row["status"], row["title"], row["started_at_ms"], lines)
        return cached

    def store_replay(
        self, member_pocket_id: str, live_id: str, started_at_ms: int, title: str,
        lines: list[tuple[int, str, str]] | None,
    ) -> None:
        blob = (
            zlib.compress(json.dumps(lines, ensure_ascii=False).encode(), 6)
            if lines is not None else None
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO annual_report_live_cache (
                    live_id, member_pocket_id, started_at_ms, title, status, lines_blob, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (live_id, member_pocket_id, started_at_ms, title,
                 "read" if lines is not None else "no_file", blob, _now().isoformat()),
            )

    def replace_member_index(self, members: list[ReportMember]) -> None:
        now = _now().isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM annual_report_member_index")
            connection.executemany(
                """
                INSERT INTO annual_report_member_index (
                    pocket_id, name, group_name, team_name, in_group, pinyin, aliases, avatar, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(member.pocket_id, member.name, member.group_name, member.team_name,
                  int(member.in_group), member.pinyin, member.aliases, member.avatar, now)
                 for member in members],
            )

    def list_members(self) -> list[ReportMember]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM annual_report_member_index
                ORDER BY in_group DESC, group_name, team_name, name
                """
            ).fetchall()
        return [
            ReportMember(row["pocket_id"], row["name"], row["group_name"], row["team_name"],
                         bool(row["in_group"]), row["pinyin"], row["aliases"], row["avatar"])
            for row in rows
        ]

    def get_member(self, pocket_id: str) -> ReportMember | None:
        return next((member for member in self.list_members() if member.pocket_id == pocket_id), None)

    def get_state(self, key: str) -> tuple[str, datetime] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT value, updated_at FROM annual_report_state WHERE key = ?", (key,)
            ).fetchone()
        return (row["value"], datetime.fromisoformat(row["updated_at"])) if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO annual_report_state (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, _now().isoformat()),
            )


def pixelate_avatar(image_bytes: bytes, size: int = 32) -> str:
    """Shrink a profile picture to a small square PNG data URL.

    Embedding the image keeps the browser canvas exportable (remote images
    would taint it) and the tiny size matches the pixel-art poster.
    """
    with Image.open(io.BytesIO(image_bytes)) as image:
        if image.width * image.height > 25_000_000:
            raise ValueError("avatar too large")
        image = image.convert("RGB")
        side = min(image.size)
        left, top = (image.width - side) // 2, (image.height - side) // 2
        square = image.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.BOX)
        output = io.BytesIO()
        square.save(output, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def load_member_snapshot() -> list[ReportMember]:
    """Bundled copy of the public member index, used until the first
    successful refresh so the page never starts with an empty list."""
    rows = json.loads(MEMBER_SNAPSHOT_PATH.read_text(encoding="utf-8"))["roomId"]
    return [ReportMember.from_index(MemberIndexEntry.model_validate(row)) for row in rows]


class AnnualReportWorker:
    def __init__(
        self,
        settings: Settings,
        repository: AnnualReportRepository,
        pocket48: Pocket48Client,
        msg48: Msg48Client,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.pocket48 = pocket48
        self.msg48 = msg48
        self.worker_id = f"report-{uuid.uuid4()}"
        self.logger = logging.getLogger(__name__)
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._progress = ""

    async def start(self) -> None:
        if self._task is None:
            await asyncio.to_thread(self.repository.recover_expired)
            members = await asyncio.to_thread(self.repository.list_members)
            if not members or not any(member.avatar for member in members):
                await asyncio.to_thread(self.repository.replace_member_index, load_member_snapshot())
            self._task = asyncio.create_task(self._run(), name="pocket48-annual-report-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self.msg48.close()

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self._refresh_member_index_if_due()
                await asyncio.to_thread(self.repository.recover_expired)
                await asyncio.to_thread(self.repository.delete_expired)
                job = None
                if not self.settings.worker_maintenance_path.exists():
                    job = await asyncio.to_thread(
                        self.repository.claim_next, self.worker_id, self.settings.worker_lease_seconds
                    )
                if job is not None:
                    await self._process(job)
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                self.logger.exception("Annual report loop iteration failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.worker_poll_seconds * 5)
            except TimeoutError:
                pass

    async def _refresh_member_index_if_due(self) -> None:
        state = await asyncio.to_thread(self.repository.get_state, "member_index")
        if state is not None:
            wait = MEMBER_INDEX_RETRY if state[0] == "failed" else MEMBER_INDEX_TTL
            if _now() - state[1] < wait:
                return
        try:
            entries = await self.msg48.member_index()
        except AppError as exc:
            self.logger.warning("Member index refresh failed: %s", exc.code)
            await asyncio.to_thread(self.repository.set_state, "member_index", "failed")
            return
        members = [ReportMember.from_index(entry) for entry in entries]
        await asyncio.to_thread(self.repository.replace_member_index, members)
        await asyncio.to_thread(self.repository.set_state, "member_index", str(len(members)))

    async def _heartbeat(self, job_id: str) -> None:
        while True:
            await asyncio.to_thread(
                self.repository.heartbeat, job_id, self.worker_id,
                self.settings.worker_lease_seconds, self._progress,
            )
            await asyncio.sleep(max(5, self.settings.worker_lease_seconds // 4))

    def _set_progress(self, text: str) -> None:
        self._progress = text

    async def _process(self, job: AnnualReportJob) -> None:
        self._set_progress("正在获取历史昵称")
        heartbeat = asyncio.create_task(self._heartbeat(job.id))
        try:
            result = await self._generate(job)
            await asyncio.to_thread(self.repository.complete, job.id, result)
        except AppError as exc:
            await asyncio.to_thread(self.repository.fail, job.id, exc.code, exc.message)
        except Exception:
            self.logger.exception("Annual report %s failed", job.id)
            await asyncio.to_thread(self.repository.fail, job.id, "report_failed", "生成失败，请稍后再试")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _generate(self, job: AnnualReportJob) -> dict:
        nicknames = await self.msg48.user_nicknames(job.uid)
        self._set_progress("正在读取房间留言")
        messages, truncated = await self.msg48.user_messages(job.uid)
        if not nicknames and not messages and not job.extra_nicknames:
            raise AppError(
                "report_user_not_found",
                "公开存档里没有找到这个账号的发言，请确认 UID，或在高级选项里填写你用过的昵称",
                False,
            )
        self._set_progress(f"正在获取{job.member_name}的直播列表")
        live_list = await self._member_replays(job.member_pocket_id)
        since_ms = scope_start_ms(job.scope)
        scoped = [(live_id, started) for live_id, started in live_list if started >= since_ms]
        replays, failed = await self._load_danmaku(job.member_pocket_id, scoped)
        self._set_progress("正在统计")
        result = await asyncio.to_thread(
            build_report,
            member_name=job.member_name,
            scope=job.scope,
            data_end=china_today(),
            archive_nicknames=[(item.nickname, item.first_used_ms) for item in nicknames],
            room_messages=[
                RoomMessage(message.timestamp_ms, message.room_info.owner_name,
                            message.nickname, message.msg_type, message.content)
                for message in messages
            ],
            replays=replays,
            extra_nicknames=job.extra_nicknames,
        )
        result["notes"] = {"archive_truncated": truncated, "unreadable_lives": failed}
        latest_avatar = next((message.avatar for message in reversed(messages) if message.avatar), "")
        member = await asyncio.to_thread(self.repository.get_member, job.member_pocket_id)
        result["fan_avatar"] = await self._pixel_avatar(latest_avatar)
        result["member_avatar"] = await self._pixel_avatar(member.avatar if member else "")
        result["generated_at"] = _now().isoformat()
        return result

    async def _pixel_avatar(self, url: str) -> str | None:
        if not url:
            return None
        try:
            return await asyncio.to_thread(pixelate_avatar, await self.msg48.fetch_avatar(url))
        except Exception as exc:
            self.logger.info("Avatar skipped: %s", getattr(exc, "code", type(exc).__name__))
            return None

    async def _member_replays(self, member_pocket_id: str) -> list[tuple[str, int]]:
        key = f"lives:{member_pocket_id}"
        state = await asyncio.to_thread(self.repository.get_state, key)
        if state is not None and _now() - state[1] < LIVE_LIST_TTL:
            return [tuple(item) for item in json.loads(state[0])]
        entries = await self.pocket48.list_member_replays(
            int(member_pocket_id), scope_start_ms("2025-2026")
        )
        live_list = [(entry.live_id, entry.started_at_ms) for entry in entries]
        await asyncio.to_thread(self.repository.set_state, key, json.dumps(live_list))
        return live_list

    async def _load_danmaku(
        self, member_pocket_id: str, live_list: list[tuple[str, int]]
    ) -> tuple[list[Replay], int]:
        cached = await asyncio.to_thread(self.repository.cached_replays, member_pocket_id)
        replays: list[Replay] = []
        missing = []
        for live_id, started_at_ms in live_list:
            if live_id in cached:
                status, title, cached_start, lines = cached[live_id]
                if status == "read":
                    replays.append(Replay(live_id, cached_start, title, [tuple(line) for line in lines]))
            else:
                missing.append((live_id, started_at_ms))
        semaphore = asyncio.Semaphore(self.settings.annual_report_danmaku_concurrency)
        done = 0
        failed = 0

        async def fetch(live_id: str, started_at_ms: int) -> None:
            nonlocal done, failed
            async with semaphore:
                try:
                    metadata = await self.pocket48.resolve_replay_danmaku(live_id)
                    lines = None
                    if metadata.danmaku_url:
                        text = await self.pocket48.fetch_danmaku(metadata.danmaku_url)
                        lines = await asyncio.to_thread(parse_lrc_lines, text, parse_lrc_timestamp)
                    await asyncio.to_thread(
                        self.repository.store_replay, member_pocket_id, live_id,
                        started_at_ms, metadata.title, lines,
                    )
                    if lines is not None:
                        replays.append(Replay(live_id, started_at_ms, metadata.title, lines))
                except AppError as exc:
                    if exc.code != "replay_not_ready":
                        failed += 1
                finally:
                    done += 1
                    self._set_progress(f"正在读取弹幕 {len(live_list) - len(missing) + done}/{len(live_list)}")
                await asyncio.sleep(0.2)

        if missing:
            self._set_progress(f"正在读取弹幕 {len(live_list) - len(missing)}/{len(live_list)}")
            await asyncio.gather(*(fetch(live_id, started) for live_id, started in missing))
        return replays, failed
