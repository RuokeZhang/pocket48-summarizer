from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Iterable

from .db import Database
from .errors import AppError
from .models import (
    DanmakuEntry,
    DanmakuPeak,
    GlossaryAliasRecord,
    GlossarySyncStateRecord,
    GlossaryTermRecord,
    GlossaryTermType,
    JobRecord,
    JobStage,
    JobStatus,
    MemberCatalogEntry,
    MemberCatalogRecord,
    ReplayMetadata,
    SubtitleTranslationRequestRecord,
    SubtitleTranslationStatus,
    TranscriptSegment,
    VideoClipRecord,
)
from .security import strip_control_chars

GLOSSARY_TERM_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


def normalize_glossary_text(value: str) -> tuple[str, str]:
    cleaned = strip_control_chars(unicodedata.normalize("NFKC", value))
    normalized = re.sub(r"\s+", "", cleaned).casefold()
    if not cleaned or not normalized:
        raise AppError("glossary_text_invalid", "词库文本不能为空", False)
    if len(cleaned) > 160:
        raise AppError(
            "glossary_text_too_long",
            "词库文本不能超过 160 个字符",
            False,
        )
    return cleaned, normalized


class JobRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _job(row: sqlite3.Row | None) -> JobRecord | None:
        if row is None:
            return None
        return JobRecord.model_validate(dict(row))

    @staticmethod
    def _video_clip(
        row: sqlite3.Row | None,
    ) -> VideoClipRecord | None:
        if row is None:
            return None
        return VideoClipRecord.model_validate(dict(row))

    @staticmethod
    def _subtitle_translation(
        row: sqlite3.Row | None,
    ) -> SubtitleTranslationRequestRecord | None:
        if row is None:
            return None
        return SubtitleTranslationRequestRecord.model_validate(dict(row))

    @staticmethod
    def _member_catalog(
        row: sqlite3.Row | None,
    ) -> MemberCatalogRecord | None:
        if row is None:
            return None
        return MemberCatalogRecord.model_validate(dict(row))

    @staticmethod
    def _glossary_term(
        row: sqlite3.Row | None,
    ) -> GlossaryTermRecord | None:
        if row is None:
            return None
        return GlossaryTermRecord.model_validate(dict(row))

    @staticmethod
    def _glossary_alias(
        row: sqlite3.Row | None,
    ) -> GlossaryAliasRecord | None:
        if row is None:
            return None
        return GlossaryAliasRecord.model_validate(dict(row))

    @staticmethod
    def _glossary_sync_state(
        row: sqlite3.Row | None,
    ) -> GlossarySyncStateRecord:
        if row is None:
            raise AppError(
                "glossary_sync_state_missing",
                "词库同步状态记录缺失",
                False,
            )
        return GlossarySyncStateRecord.model_validate(dict(row))

    @staticmethod
    def _calculate_glossary_fingerprint(
        connection: sqlite3.Connection,
    ) -> str:
        members = [
            dict(row)
            for row in connection.execute(
                """
                SELECT member_id, canonical_name, pinyin,
                       group_id, group_name, team_id, team_name
                FROM member_catalog
                WHERE active = 1 AND source_present = 1
                ORDER BY member_id
                """
            ).fetchall()
        ]
        terms = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, canonical_text, term_type,
                       description_zh, description_en
                FROM glossary_terms
                WHERE active = 1
                ORDER BY id
                """
            ).fetchall()
        ]
        aliases = [
            dict(row)
            for row in connection.execute(
                """
                SELECT a.member_id, a.term_id, a.alias
                FROM glossary_aliases a
                LEFT JOIN member_catalog m ON m.member_id = a.member_id
                LEFT JOIN glossary_terms t ON t.id = a.term_id
                WHERE a.active = 1
                  AND (
                      (
                          a.member_id IS NOT NULL
                          AND m.active = 1
                          AND m.source_present = 1
                      )
                      OR (
                          a.term_id IS NOT NULL
                          AND t.active = 1
                      )
                  )
                ORDER BY a.alias_normalized
                """
            ).fetchall()
        ]
        encoded = json.dumps(
            {
                "members": members,
                "terms": terms,
                "aliases": aliases,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def get_glossary_sync_state(self) -> GlossarySyncStateRecord:
        with self.database.connect() as connection:
            return self._glossary_sync_state(
                connection.execute(
                    """
                    SELECT source_url, sync_status, source_hash,
                           catalog_version, glossary_fingerprint,
                           member_count, active_member_count,
                           last_attempt_at, last_success_at, last_error,
                           active_vocabulary_id, vocabulary_fingerprint,
                           vocabulary_updated_at, vocabulary_error
                    FROM glossary_sync_state
                    WHERE singleton = 1
                    """
                ).fetchone()
            )

    def replace_member_catalog(
        self,
        members: list[MemberCatalogEntry],
        *,
        source_url: str,
        source_hash: str,
    ) -> GlossarySyncStateRecord:
        if not members or len({member.member_id for member in members}) != len(
            members
        ):
            raise AppError(
                "member_catalog_invalid",
                "官方成员目录为空或包含重复成员",
                False,
            )
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE member_catalog
                SET active = 0, source_present = 0
                WHERE source = 'snh48_official'
                """
            )
            connection.executemany(
                """
                INSERT INTO member_catalog (
                    member_id, canonical_name, pinyin,
                    group_id, group_name, team_id, team_name,
                    status, ranking, active, source_present, source,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                          'snh48_official', ?, ?)
                ON CONFLICT(member_id) DO UPDATE SET
                    canonical_name = excluded.canonical_name,
                    pinyin = excluded.pinyin,
                    group_id = excluded.group_id,
                    group_name = excluded.group_name,
                    team_id = excluded.team_id,
                    team_name = excluded.team_name,
                    status = excluded.status,
                    ranking = excluded.ranking,
                    active = excluded.active,
                    source_present = 1,
                    source = excluded.source,
                    last_seen_at = excluded.last_seen_at
                """,
                [
                    (
                        member.member_id,
                        member.canonical_name,
                        member.pinyin,
                        member.group_id,
                        member.group_name,
                        member.team_id,
                        member.team_name,
                        member.status,
                        member.ranking,
                        int(member.active),
                        now,
                        now,
                    )
                    for member in members
                ],
            )
            fingerprint = self._calculate_glossary_fingerprint(connection)
            connection.execute(
                """
                UPDATE glossary_sync_state
                SET source_url = ?,
                    sync_status = 'success',
                    source_hash = ?,
                    catalog_version = ?,
                    glossary_fingerprint = ?,
                    member_count = ?,
                    active_member_count = ?,
                    last_attempt_at = ?,
                    last_success_at = ?,
                    last_error = NULL
                WHERE singleton = 1
                """,
                (
                    source_url,
                    source_hash,
                    source_hash[:16],
                    fingerprint,
                    len(members),
                    sum(member.active for member in members),
                    now,
                    now,
                ),
            )
        return self.get_glossary_sync_state()

    def record_member_catalog_sync_failure(self, message: str) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE glossary_sync_state
                SET sync_status = 'failed',
                    last_attempt_at = ?,
                    last_error = ?
                WHERE singleton = 1
                """,
                (now, strip_control_chars(message)[:1000]),
            )

    def list_member_catalog(
        self,
        *,
        active_only: bool = False,
        limit: int = 1000,
    ) -> list[MemberCatalogRecord]:
        where = "WHERE active = 1 AND source_present = 1" if active_only else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM member_catalog
                {where}
                ORDER BY active DESC, group_name, team_name, canonical_name
                LIMIT ?
                """,
                (max(1, min(limit, 2000)),),
            ).fetchall()
        return [
            member
            for row in rows
            if (member := self._member_catalog(row)) is not None
        ]

    def get_member_catalog(
        self, member_id: str
    ) -> MemberCatalogRecord | None:
        with self.database.connect() as connection:
            return self._member_catalog(
                connection.execute(
                    "SELECT * FROM member_catalog WHERE member_id = ?",
                    (member_id,),
                ).fetchone()
            )

    def list_glossary_terms(self) -> list[GlossaryTermRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM glossary_terms
                ORDER BY active DESC, term_type, canonical_text
                """
            ).fetchall()
        return [
            term
            for row in rows
            if (term := self._glossary_term(row)) is not None
        ]

    def list_glossary_aliases(self) -> list[GlossaryAliasRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT a.*,
                       COALESCE(m.canonical_name, t.canonical_text)
                           AS target_text,
                       CASE
                           WHEN a.member_id IS NOT NULL THEN 'member'
                           ELSE t.term_type
                       END AS target_type
                FROM glossary_aliases a
                LEFT JOIN member_catalog m ON m.member_id = a.member_id
                LEFT JOIN glossary_terms t ON t.id = a.term_id
                ORDER BY a.active DESC, target_text, a.alias
                """
            ).fetchall()
        return [
            alias
            for row in rows
            if (alias := self._glossary_alias(row)) is not None
        ]

    def list_active_vocabulary_texts(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT text
                FROM (
                    SELECT 0 AS priority,
                           canonical_name AS text,
                           member_id AS order_key
                    FROM member_catalog
                    WHERE active = 1 AND source_present = 1

                    UNION ALL

                    SELECT 1 AS priority,
                           a.alias AS text,
                           a.alias_normalized AS order_key
                    FROM glossary_aliases a
                    JOIN member_catalog m ON m.member_id = a.member_id
                    WHERE a.active = 1
                      AND m.active = 1
                      AND m.source_present = 1

                    UNION ALL

                    SELECT 2 AS priority,
                           canonical_text AS text,
                           canonical_normalized AS order_key
                    FROM glossary_terms
                    WHERE active = 1

                    UNION ALL

                    SELECT 3 AS priority,
                           a.alias AS text,
                           a.alias_normalized AS order_key
                    FROM glossary_aliases a
                    JOIN glossary_terms t ON t.id = a.term_id
                    WHERE a.active = 1 AND t.active = 1

                    UNION ALL

                    SELECT 4 AS priority,
                           group_name AS text,
                           group_name AS order_key
                    FROM member_catalog
                    WHERE active = 1
                      AND source_present = 1
                      AND group_name != ''
                    GROUP BY group_name

                    UNION ALL

                    SELECT 5 AS priority,
                           team_name AS text,
                           team_name AS order_key
                    FROM member_catalog
                    WHERE active = 1
                      AND source_present = 1
                      AND team_name != ''
                    GROUP BY team_name
                )
                ORDER BY priority, order_key
                """
            ).fetchall()
        return [str(row["text"]) for row in rows]

    def activate_vocabulary(
        self, vocabulary_id: str, fingerprint: str
    ) -> GlossarySyncStateRecord:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE glossary_sync_state
                SET active_vocabulary_id = ?,
                    vocabulary_fingerprint = ?,
                    vocabulary_updated_at = ?,
                    vocabulary_error = NULL
                WHERE singleton = 1
                """,
                (vocabulary_id, fingerprint, now),
            )
        return self.get_glossary_sync_state()

    def record_vocabulary_error(self, message: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE glossary_sync_state
                SET vocabulary_error = ?
                WHERE singleton = 1
                """,
                (strip_control_chars(message)[:1000],),
            )

    def create_glossary_term(
        self,
        *,
        canonical_text: str,
        term_type: str,
        description_zh: str,
        description_en: str,
        user_id: str,
    ) -> GlossaryTermRecord:
        canonical_text, canonical_normalized = normalize_glossary_text(
            canonical_text
        )
        valid_types = {item.value for item in GlossaryTermType}
        if (
            term_type not in valid_types
            or not GLOSSARY_TERM_TYPE_RE.fullmatch(term_type)
        ):
            raise AppError(
                "glossary_term_type_invalid",
                "词库术语类型无效",
                False,
            )
        description_zh = strip_control_chars(description_zh)[:1000]
        description_en = strip_control_chars(description_en)[:1000]
        term_id = str(uuid.uuid4())
        now = utcnow()
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO glossary_terms (
                        id, canonical_text, canonical_normalized,
                        term_type, description_zh, description_en,
                        source, active, created_by_user_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'admin', 1, ?, ?, ?)
                    """,
                    (
                        term_id,
                        canonical_text,
                        canonical_normalized,
                        term_type,
                        description_zh,
                        description_en,
                        user_id,
                        now,
                        now,
                    ),
                )
                self._update_glossary_fingerprint(connection)
        except sqlite3.IntegrityError as exc:
            raise AppError(
                "glossary_term_exists",
                "相同类型的规范术语已经存在",
                False,
            ) from exc
        with self.database.connect() as connection:
            term = self._glossary_term(
                connection.execute(
                    "SELECT * FROM glossary_terms WHERE id = ?",
                    (term_id,),
                ).fetchone()
            )
        if term is None:
            raise AppError(
                "glossary_term_create_failed",
                "词库术语创建后无法读取",
                False,
            )
        return term

    def create_glossary_alias(
        self,
        *,
        alias: str,
        user_id: str,
        member_id: str | None = None,
        term_id: str | None = None,
    ) -> GlossaryAliasRecord:
        if (member_id is None) == (term_id is None):
            raise AppError(
                "glossary_alias_target_invalid",
                "别名必须关联一个成员或术语",
                False,
            )
        alias, alias_normalized = normalize_glossary_text(alias)
        alias_id = str(uuid.uuid4())
        now = utcnow()
        try:
            with self.database.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if member_id is not None:
                    target = connection.execute(
                        """
                        SELECT canonical_name AS target_text
                        FROM member_catalog
                        WHERE member_id = ?
                        """,
                        (member_id,),
                    ).fetchone()
                else:
                    target = connection.execute(
                        """
                        SELECT canonical_text AS target_text
                        FROM glossary_terms
                        WHERE id = ?
                        """,
                        (term_id,),
                    ).fetchone()
                if target is None:
                    raise AppError(
                        "glossary_alias_target_missing",
                        "别名关联的成员或术语不存在",
                        False,
                    )
                _, target_normalized = normalize_glossary_text(
                    target["target_text"]
                )
                if alias_normalized == target_normalized:
                    raise AppError(
                        "glossary_alias_redundant",
                        "别名不能与规范名称相同",
                        False,
                    )
                canonical_rows = connection.execute(
                    """
                    SELECT canonical_name AS canonical_text
                    FROM member_catalog
                    UNION ALL
                    SELECT canonical_text
                    FROM glossary_terms
                    """
                ).fetchall()
                if any(
                    normalize_glossary_text(row["canonical_text"])[1]
                    == alias_normalized
                    for row in canonical_rows
                ):
                    raise AppError(
                        "glossary_alias_conflicts_with_canonical",
                        "别名与已有规范成员名或术语冲突",
                        False,
                    )
                connection.execute(
                    """
                    INSERT INTO glossary_aliases (
                        id, member_id, term_id, alias, alias_normalized,
                        active, created_by_user_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        alias_id,
                        member_id,
                        term_id,
                        alias,
                        alias_normalized,
                        user_id,
                        now,
                        now,
                    ),
                )
                self._update_glossary_fingerprint(connection)
        except sqlite3.IntegrityError as exc:
            raise AppError(
                "glossary_alias_exists",
                "这个别名已经关联到其他成员或术语",
                False,
            ) from exc
        created = next(
            (
                item
                for item in self.list_glossary_aliases()
                if item.id == alias_id
            ),
            None,
        )
        if created is None:
            raise AppError(
                "glossary_alias_create_failed",
                "词库别名创建后无法读取",
                False,
            )
        return created

    def set_glossary_term_active(
        self, term_id: str, *, active: bool
    ) -> None:
        self._set_glossary_record_active(
            "glossary_terms", term_id, active=active
        )

    def set_glossary_alias_active(
        self, alias_id: str, *, active: bool
    ) -> None:
        self._set_glossary_record_active(
            "glossary_aliases", alias_id, active=active
        )

    def _set_glossary_record_active(
        self, table: str, record_id: str, *, active: bool
    ) -> None:
        if table not in {"glossary_terms", "glossary_aliases"}:
            raise ValueError("unsupported glossary table")
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                f"""
                UPDATE {table}
                SET active = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(active), now, record_id),
            )
            if cursor.rowcount != 1:
                raise AppError(
                    "glossary_record_not_found",
                    "词库记录不存在",
                    False,
                )
            self._update_glossary_fingerprint(connection)

    def _update_glossary_fingerprint(
        self, connection: sqlite3.Connection
    ) -> None:
        connection.execute(
            """
            UPDATE glossary_sync_state
            SET glossary_fingerprint = ?
            WHERE singleton = 1
            """,
            (self._calculate_glossary_fingerprint(connection),),
        )

    def create_or_get_job(
        self,
        source_url: str,
        live_id: str,
        user_id: str = "local",
        *,
        daily_limit: int | None = None,
        quota_start: str | None = None,
    ) -> tuple[JobRecord, bool]:
        now = utcnow()
        job_id = str(uuid.uuid4())
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT j.* FROM jobs j
                JOIN job_access a ON a.job_id = j.id
                WHERE j.live_id = ? AND a.user_id = ?
                """,
                (live_id, user_id),
            ).fetchone()
            if existing is not None:
                return self._job(existing), False  # type: ignore[return-value]
            shared = connection.execute(
                "SELECT * FROM jobs WHERE live_id = ?", (live_id,)
            ).fetchone()
            if (
                shared is not None
                and shared["status"] == JobStatus.COMPLETED
            ):
                return self._job(shared), False  # type: ignore[return-value]
            if daily_limit is not None and quota_start is not None:
                used = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM job_access
                    WHERE user_id = ? AND created_at >= ?
                    """,
                    (user_id, quota_start),
                ).fetchone()["count"]
                if int(used) >= daily_limit:
                    raise AppError(
                        "daily_quota_exceeded",
                        f"今日任务额度已用完（每天 {daily_limit} 个）",
                        False,
                    )
            if shared is not None:
                connection.execute(
                    """
                    INSERT INTO job_access (job_id, user_id, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (shared["id"], user_id, now),
                )
                return self._job(shared), False  # type: ignore[return-value]
            connection.execute(
                """
                INSERT INTO jobs (
                    id, source_url, live_id, status, stage,
                    progress_percent, progress_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    job_id,
                    source_url,
                    live_id,
                    JobStatus.QUEUED,
                    JobStage.QUEUED,
                    "等待处理",
                    now,
                    now,
                ),
            )
            self._event(
                connection, job_id, JobStage.QUEUED, "info", "任务已创建", now
            )
            connection.execute(
                """
                INSERT INTO job_access (job_id, user_id, created_at)
                VALUES (?, ?, ?)
                """,
                (job_id, user_id, now),
            )
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._job(row), True  # type: ignore[return-value]

    def get_job(self, job_id: str) -> JobRecord | None:
        with self.database.connect() as connection:
            return self._job(
                connection.execute(
                    "SELECT * FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()
            )

    def get_job_by_live_id(self, live_id: str) -> JobRecord | None:
        with self.database.connect() as connection:
            return self._job(
                connection.execute(
                    "SELECT * FROM jobs WHERE live_id = ?", (live_id,)
                ).fetchone()
            )

    def get_job_for_user(
        self, job_id: str, user_id: str
    ) -> JobRecord | None:
        with self.database.connect() as connection:
            return self._job(
                connection.execute(
                    """
                    SELECT j.* FROM jobs j
                    JOIN job_access a ON a.job_id = j.id
                    WHERE j.id = ? AND a.user_id = ?
                    """,
                    (job_id, user_id),
                ).fetchone()
            )

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                job for row in rows if (job := self._job(row)) is not None
            ]

    def list_jobs_for_user(
        self, user_id: str, limit: int = 50
    ) -> list[JobRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT j.* FROM jobs j
                JOIN job_access a ON a.job_id = j.id
                WHERE a.user_id = ?
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [
                job for row in rows if (job := self._job(row)) is not None
            ]

    def list_visible_jobs(
        self, user_id: str | None, limit: int = 50
    ) -> list[JobRecord]:
        with self.database.connect() as connection:
            if user_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = ?
                    ORDER BY COALESCE(replay_started_at, created_at) DESC
                    LIMIT ?
                    """,
                    (JobStatus.COMPLETED, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT DISTINCT j.* FROM jobs j
                    LEFT JOIN job_access a
                      ON a.job_id = j.id AND a.user_id = ?
                    WHERE j.status = ? OR a.user_id IS NOT NULL
                    ORDER BY COALESCE(j.replay_started_at, j.created_at) DESC
                    LIMIT ?
                    """,
                    (user_id, JobStatus.COMPLETED, limit),
                ).fetchall()
            return [
                job for row in rows if (job := self._job(row)) is not None
            ]

    def require_job_access(self, job_id: str, user_id: str) -> None:
        if self.get_job_for_user(job_id, user_id) is None:
            raise AppError("job_not_found", "任务不存在", False)

    def get_video_clip(
        self, job_id: str, timeline_index: int
    ) -> VideoClipRecord | None:
        with self.database.connect() as connection:
            return self._video_clip(
                connection.execute(
                    """
                    SELECT * FROM video_clips
                    WHERE job_id = ? AND timeline_index = ?
                    """,
                    (job_id, timeline_index),
                ).fetchone()
            )

    def begin_video_clip(
        self,
        job_id: str,
        timeline_index: int,
        start_ms: int,
        end_ms: int,
        filename: str,
    ) -> VideoClipRecord:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO video_clips (
                    job_id, timeline_index, start_ms, end_ms, filename,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                ON CONFLICT(job_id, timeline_index) DO UPDATE SET
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    filename = excluded.filename,
                    status = 'running',
                    oss_object_key = NULL,
                    error_message = NULL,
                    updated_at = excluded.updated_at,
                    completed_at = NULL
                """,
                (
                    job_id,
                    timeline_index,
                    start_ms,
                    end_ms,
                    filename,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM video_clips
                WHERE job_id = ? AND timeline_index = ?
                """,
                (job_id, timeline_index),
            ).fetchone()
        clip = self._video_clip(row)
        assert clip is not None
        return clip

    def complete_video_clip(
        self, job_id: str, timeline_index: int, object_key: str
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE video_clips
                SET status = 'completed', oss_object_key = ?,
                    error_message = NULL, updated_at = ?, completed_at = ?
                WHERE job_id = ? AND timeline_index = ?
                """,
                (object_key, now, now, job_id, timeline_index),
            )

    def fail_video_clip(
        self, job_id: str, timeline_index: int, error_message: str
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE video_clips
                SET status = 'failed', error_message = ?, updated_at = ?
                WHERE job_id = ? AND timeline_index = ?
                """,
                (error_message, utcnow(), job_id, timeline_index),
            )

    def recover_running_video_clips(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                """
                UPDATE video_clips
                SET status = 'failed',
                    error_message = '服务曾重启，请重试剪辑',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (utcnow(),),
            ).rowcount

    def recover_expired_jobs(self) -> int:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT id, stage FROM jobs
                WHERE status = ? AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (JobStatus.RUNNING, now),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                        progress_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        JobStatus.QUEUED,
                        "应用重启后等待恢复",
                        now,
                        row["id"],
                    ),
                )
                self._event(
                    connection,
                    row["id"],
                    row["stage"],
                    "warning",
                    "检测到过期 Worker 租约，任务已重新排队",
                    now,
                )
            return len(rows)

    def release_owned_job(self, job_id: str, worker_id: str) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT stage FROM jobs
                WHERE id = ? AND status = ? AND worker_id = ?
                """,
                (job_id, JobStatus.RUNNING, worker_id),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                    progress_message = ?, updated_at = ?
                WHERE id = ? AND status = ? AND worker_id = ?
                """,
                (
                    JobStatus.QUEUED,
                    "应用停止，任务已安全重新排队",
                    now,
                    job_id,
                    JobStatus.RUNNING,
                    worker_id,
                ),
            )
            self._event(
                connection,
                job_id,
                row["stage"],
                "warning",
                "Worker 停止，任务已重新排队",
                now,
            )

    def list_failed_artifact_jobs(self, updated_before: str) -> list[JobRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ? AND updated_at < ?
                  AND (audio_path IS NOT NULL OR oss_object_key IS NOT NULL)
                ORDER BY updated_at
                """,
                (JobStatus.FAILED, updated_before),
            ).fetchall()
            return [
                job for row in rows if (job := self._job(row)) is not None
            ]

    def claim_next_job(
        self, worker_id: str, lease_seconds: int
    ) -> JobRecord | None:
        now = datetime.now(UTC)
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        now_text = now.isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY created_at
                LIMIT 1
                """,
                (JobStatus.QUEUED,),
            ).fetchone()
            if row is None:
                return None
            stage = (
                JobStage.RESOLVING
                if row["stage"] == JobStage.QUEUED
                else row["stage"]
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, worker_id = ?,
                    lease_expires_at = ?, started_at = COALESCE(started_at, ?),
                    error_code = NULL, error_message = NULL,
                    error_retryable = 0, updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.RUNNING,
                    stage,
                    worker_id,
                    lease,
                    now_text,
                    now_text,
                    row["id"],
                    JobStatus.QUEUED,
                ),
            )
            claimed = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (row["id"],)
            ).fetchone()
            return self._job(claimed)

    def touch_lease(
        self, job_id: str, worker_id: str, lease_seconds: int
    ) -> None:
        now = datetime.now(UTC)
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE jobs
                SET lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND status = ? AND worker_id = ?
                """,
                (lease, now.isoformat(), job_id, JobStatus.RUNNING, worker_id),
            ).rowcount
            if updated != 1:
                raise AppError(
                    "worker_lease_lost",
                    "Worker lease is no longer valid for this job",
                    True,
                )

    def set_stage(
        self,
        job_id: str,
        stage: JobStage,
        progress_percent: int,
        message: str,
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE jobs
                SET stage = ?, progress_percent = ?, progress_message = ?,
                    updated_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    stage,
                    max(0, min(100, progress_percent)),
                    message,
                    now,
                    job_id,
                    JobStatus.RUNNING,
                ),
            ).rowcount
            if updated != 1:
                raise AppError(
                    "invalid_job_transition",
                    "Cannot update the stage of a non-running job",
                    False,
                )
            self._event(connection, job_id, stage, "info", message, now)

    def save_replay_metadata(
        self, job_id: str, metadata: ReplayMetadata
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET
                    member_id = ?, member_name = ?, title = ?, cover_url = ?,
                    replay_started_at = ?, duration_ms = ?, media_url = ?,
                    danmaku_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    metadata.member_id,
                    metadata.member_name,
                    metadata.title,
                    metadata.cover_url,
                    metadata.replay_started_at,
                    metadata.duration_ms,
                    metadata.media_url,
                    metadata.danmaku_url,
                    now,
                    job_id,
                ),
            )

    def set_media_details(
        self, job_id: str, media_url: str, duration_ms: int
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET media_url = ?, duration_ms = ?, updated_at = ?
                WHERE id = ?
                """,
                (media_url, duration_ms, utcnow(), job_id),
            )

    def replace_danmaku(
        self, job_id: str, entries: Iterable[DanmakuEntry]
    ) -> None:
        rows = list(entries)
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM danmaku_entries WHERE job_id = ?", (job_id,)
            )
            connection.executemany(
                """
                INSERT INTO danmaku_entries (
                    job_id, sequence, timestamp_ms, author, text
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        entry.sequence,
                        entry.timestamp_ms,
                        entry.author,
                        entry.text,
                    )
                    for entry in rows
                ],
            )
            connection.execute(
                """
                UPDATE jobs SET danmaku_loaded_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, job_id),
            )

    def replace_danmaku_peaks(
        self, job_id: str, peaks: Iterable[DanmakuPeak]
    ) -> None:
        rows = list(peaks)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM danmaku_peaks WHERE job_id = ?", (job_id,)
            )
            connection.executemany(
                """
                INSERT INTO danmaku_peaks (
                    job_id, rank, start_ms, end_ms,
                    message_count, score, samples_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        peak.rank,
                        peak.start_ms,
                        peak.end_ms,
                        peak.message_count,
                        peak.score,
                        json.dumps(
                            peak.samples, ensure_ascii=False, separators=(",", ":")
                        ),
                    )
                    for peak in rows
                ],
            )

    def get_danmaku(
        self,
        job_id: str,
        limit: int = 500,
        after_ms: int = -1,
    ) -> list[DanmakuEntry]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, timestamp_ms, author, text
                FROM danmaku_entries
                WHERE job_id = ? AND timestamp_ms > ?
                ORDER BY timestamp_ms, sequence
                LIMIT ?
                """,
                (job_id, after_ms, limit),
            ).fetchall()
            return [DanmakuEntry.model_validate(dict(row)) for row in rows]

    def get_all_danmaku(self, job_id: str) -> list[DanmakuEntry]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, timestamp_ms, author, text
                FROM danmaku_entries
                WHERE job_id = ?
                ORDER BY timestamp_ms, sequence
                """,
                (job_id,),
            ).fetchall()
            return [DanmakuEntry.model_validate(dict(row)) for row in rows]

    def get_danmaku_peaks(self, job_id: str) -> list[DanmakuPeak]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT rank, start_ms, end_ms, message_count, score, samples_json
                FROM danmaku_peaks WHERE job_id = ? ORDER BY rank
                """,
                (job_id,),
            ).fetchall()
            return [
                DanmakuPeak(
                    rank=row["rank"],
                    start_ms=row["start_ms"],
                    end_ms=row["end_ms"],
                    message_count=row["message_count"],
                    score=row["score"],
                    samples=json.loads(row["samples_json"]),
                )
                for row in rows
            ]

    def set_audio_path(self, job_id: str, path: str | None) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET audio_path = ?,
                    audio_extracted_at = CASE WHEN ? IS NULL
                        THEN audio_extracted_at ELSE ? END,
                    updated_at = ?
                WHERE id = ?
                """,
                (path, path, now, now, job_id),
            )

    def set_oss_object(self, job_id: str, key: str | None) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET oss_object_key = ?,
                    oss_uploaded_at = CASE WHEN ? IS NULL
                        THEN oss_uploaded_at ELSE ? END,
                    updated_at = ?
                WHERE id = ?
                """,
                (key, key, now, now, job_id),
            )

    def set_dashscope_task(
        self,
        job_id: str,
        task_id: str,
        task_status: str,
        *,
        vocabulary_id: str | None = None,
        glossary_fingerprint: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET dashscope_task_id = ?, dashscope_task_status = ?,
                    asr_vocabulary_id = ?,
                    asr_glossary_fingerprint = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    task_id,
                    task_status,
                    vocabulary_id,
                    glossary_fingerprint,
                    now,
                    job_id,
                ),
            )

    def set_dashscope_status(self, job_id: str, task_status: str) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET dashscope_task_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (task_status, utcnow(), job_id),
            )

    def save_asr_raw(self, job_id: str, payload: str) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET asr_raw_json = ?, asr_completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (payload, now, now, job_id),
            )

    def replace_transcript(
        self, job_id: str, segments: Iterable[TranscriptSegment]
    ) -> None:
        rows = list(segments)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM transcript_segments WHERE job_id = ?", (job_id,)
            )
            connection.executemany(
                """
                INSERT INTO transcript_segments (
                    job_id, sequence, start_ms, end_ms, speaker_id, text
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        job_id,
                        segment.sequence,
                        segment.start_ms,
                        segment.end_ms,
                        segment.speaker_id,
                        segment.text,
                    )
                    for segment in rows
                ],
            )

    def get_transcript(
        self, job_id: str, limit: int = 500, offset: int = 0
    ) -> list[TranscriptSegment]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, start_ms, end_ms, speaker_id, text
                FROM transcript_segments
                WHERE job_id = ?
                ORDER BY sequence
                LIMIT ? OFFSET ?
                """,
                (job_id, limit, offset),
            ).fetchall()
            return [
                TranscriptSegment.model_validate(dict(row)) for row in rows
            ]

    def get_all_transcript(self, job_id: str) -> list[TranscriptSegment]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, start_ms, end_ms, speaker_id, text
                FROM transcript_segments
                WHERE job_id = ?
                ORDER BY sequence
                """,
                (job_id,),
            ).fetchall()
            return [
                TranscriptSegment.model_validate(dict(row)) for row in rows
            ]

    def count_transcript(self, job_id: str) -> int:
        with self.database.connect() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM transcript_segments WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()["count"]
            )

    def request_subtitle_translation(
        self, job_id: str, language: str = "en"
    ) -> SubtitleTranslationRequestRecord:
        if language != "en":
            raise AppError(
                "unsupported_translation_language",
                "当前仅支持英文字幕",
                False,
            )
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if job is None:
                raise AppError("job_not_found", "任务不存在", False)
            if job["status"] != JobStatus.COMPLETED:
                raise AppError(
                    "translation_not_ready",
                    "直播处理完成后才能生成英文字幕",
                    True,
                )
            transcript_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM transcript_segments
                    WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()["count"]
            )
            if transcript_count == 0:
                raise AppError(
                    "transcript_not_ready",
                    "字幕尚未生成，无法翻译",
                    True,
                )
            row = connection.execute(
                """
                SELECT * FROM subtitle_translation_requests
                WHERE job_id = ? AND language = ?
                """,
                (job_id, language),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO subtitle_translation_requests (
                        job_id, language, status, retry_count,
                        requested_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, ?)
                    """,
                    (
                        job_id,
                        language,
                        SubtitleTranslationStatus.QUEUED,
                        now,
                        now,
                    ),
                )
            elif row["status"] == SubtitleTranslationStatus.FAILED:
                connection.execute(
                    """
                    UPDATE subtitle_translation_requests
                    SET status = ?, error_message = NULL, worker_id = NULL,
                        lease_expires_at = NULL, requested_at = ?,
                        updated_at = ?, completed_at = NULL
                    WHERE job_id = ? AND language = ?
                    """,
                    (
                        SubtitleTranslationStatus.QUEUED,
                        now,
                        now,
                        job_id,
                        language,
                    ),
                )
            updated = connection.execute(
                """
                SELECT * FROM subtitle_translation_requests
                WHERE job_id = ? AND language = ?
                """,
                (job_id, language),
            ).fetchone()
            translation = self._subtitle_translation(updated)
            if translation is None:
                raise RuntimeError(
                    "Subtitle translation request disappeared"
                )
            return translation

    def get_subtitle_translation_request(
        self, job_id: str, language: str = "en"
    ) -> SubtitleTranslationRequestRecord | None:
        with self.database.connect() as connection:
            return self._subtitle_translation(
                connection.execute(
                    """
                    SELECT * FROM subtitle_translation_requests
                    WHERE job_id = ? AND language = ?
                    """,
                    (job_id, language),
                ).fetchone()
            )

    def claim_next_subtitle_translation(
        self, worker_id: str, lease_seconds: int
    ) -> SubtitleTranslationRequestRecord | None:
        now = datetime.now(UTC)
        now_text = now.isoformat()
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM subtitle_translation_requests
                WHERE status = ?
                   OR (
                       status = ?
                       AND lease_expires_at IS NOT NULL
                       AND lease_expires_at <= ?
                   )
                ORDER BY requested_at
                LIMIT 1
                """,
                (
                    SubtitleTranslationStatus.QUEUED,
                    SubtitleTranslationStatus.RUNNING,
                    now_text,
                ),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET status = ?, worker_id = ?, lease_expires_at = ?,
                    retry_count = retry_count + 1,
                    started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ? AND language = ?
                """,
                (
                    SubtitleTranslationStatus.RUNNING,
                    worker_id,
                    lease,
                    now_text,
                    now_text,
                    row["job_id"],
                    row["language"],
                ),
            )
            claimed = connection.execute(
                """
                SELECT * FROM subtitle_translation_requests
                WHERE job_id = ? AND language = ?
                """,
                (row["job_id"], row["language"]),
            ).fetchone()
            return self._subtitle_translation(claimed)

    def touch_subtitle_translation_lease(
        self,
        job_id: str,
        language: str,
        worker_id: str,
        lease_seconds: int,
    ) -> None:
        lease = (
            datetime.now(UTC) + timedelta(seconds=lease_seconds)
        ).isoformat()
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND language = ? AND worker_id = ?
                  AND status = ?
                """,
                (
                    lease,
                    utcnow(),
                    job_id,
                    language,
                    worker_id,
                    SubtitleTranslationStatus.RUNNING,
                ),
            ).rowcount
            if updated != 1:
                raise AppError(
                    "translation_lease_lost",
                    "英文字幕任务租约已失效",
                    True,
                )

    def recover_expired_subtitle_translations(self) -> int:
        now = utcnow()
        with self.database.connect() as connection:
            return connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE status = ? AND lease_expires_at <= ?
                """,
                (
                    SubtitleTranslationStatus.QUEUED,
                    now,
                    SubtitleTranslationStatus.RUNNING,
                    now,
                ),
            ).rowcount

    def release_owned_subtitle_translation(
        self, job_id: str, language: str, worker_id: str
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE job_id = ? AND language = ? AND worker_id = ?
                  AND status = ?
                """,
                (
                    SubtitleTranslationStatus.QUEUED,
                    utcnow(),
                    job_id,
                    language,
                    worker_id,
                    SubtitleTranslationStatus.RUNNING,
                ),
            )

    def save_transcript_translations(
        self, job_id: str, language: str, translations: dict[int, str]
    ) -> None:
        if not translations:
            return
        now = utcnow()
        with self.database.connect() as connection:
            connection.executemany(
                """
                INSERT INTO transcript_translations (
                    job_id, sequence, language, text, created_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(job_id, sequence, language) DO UPDATE SET
                    text = excluded.text,
                    created_at = excluded.created_at
                """,
                [
                    (job_id, sequence, language, text, now)
                    for sequence, text in translations.items()
                ],
            )

    def get_transcript_translations(
        self, job_id: str, language: str = "en"
    ) -> dict[int, str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT sequence, text FROM transcript_translations
                WHERE job_id = ? AND language = ?
                ORDER BY sequence
                """,
                (job_id, language),
            ).fetchall()
            return {int(row["sequence"]): str(row["text"]) for row in rows}

    def mark_subtitle_translation_completed(
        self, job_id: str, language: str, worker_id: str
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            source_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM transcript_segments
                    WHERE job_id = ?
                    """,
                    (job_id,),
                ).fetchone()["count"]
            )
            translation_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM transcript_translations
                    WHERE job_id = ? AND language = ?
                    """,
                    (job_id, language),
                ).fetchone()["count"]
            )
            if source_count == 0 or source_count != translation_count:
                raise AppError(
                    "translation_incomplete",
                    "英文字幕尚未完整生成",
                    True,
                )
            updated = connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET status = ?, error_message = NULL, worker_id = NULL,
                    lease_expires_at = NULL, completed_at = ?, updated_at = ?
                WHERE job_id = ? AND language = ? AND worker_id = ?
                  AND status = ?
                """,
                (
                    SubtitleTranslationStatus.COMPLETED,
                    now,
                    now,
                    job_id,
                    language,
                    worker_id,
                    SubtitleTranslationStatus.RUNNING,
                ),
            ).rowcount
            if updated != 1:
                raise AppError(
                    "translation_lease_lost",
                    "英文字幕任务租约已失效",
                    True,
                )

    def mark_subtitle_translation_failed(
        self,
        job_id: str,
        language: str,
        worker_id: str,
        message: str,
        *,
        retry: bool,
    ) -> None:
        status = (
            SubtitleTranslationStatus.QUEUED
            if retry
            else SubtitleTranslationStatus.FAILED
        )
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET status = ?, error_message = ?, worker_id = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ? AND language = ? AND worker_id = ?
                  AND status = ?
                """,
                (
                    status,
                    message,
                    utcnow(),
                    job_id,
                    language,
                    worker_id,
                    SubtitleTranslationStatus.RUNNING,
                ),
            )

    def save_summary_chunk(
        self,
        job_id: str,
        chunk_index: int,
        start_ms: int,
        end_ms: int,
        prompt_version: str,
        input_hash: str,
        response_json: str,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO summary_chunks (
                    job_id, chunk_index, start_ms, end_ms, prompt_version,
                    input_hash, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, chunk_index, prompt_version) DO UPDATE SET
                    start_ms = excluded.start_ms,
                    end_ms = excluded.end_ms,
                    input_hash = excluded.input_hash,
                    response_json = excluded.response_json,
                    created_at = excluded.created_at
                """,
                (
                    job_id,
                    chunk_index,
                    start_ms,
                    end_ms,
                    prompt_version,
                    input_hash,
                    response_json,
                    utcnow(),
                ),
            )

    def get_summary_chunks(
        self, job_id: str, prompt_version: str
    ) -> dict[int, tuple[str, str]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT chunk_index, input_hash, response_json
                FROM summary_chunks
                WHERE job_id = ? AND prompt_version = ?
                """,
                (job_id, prompt_version),
            ).fetchall()
            return {
                row["chunk_index"]: (row["input_hash"], row["response_json"])
                for row in rows
            }

    def save_summary(
        self, job_id: str, summary_json: str, summary_markdown: str
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET summary_json = ?, summary_markdown = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary_json, summary_markdown, utcnow(), job_id),
            )

    def set_cleanup_warning(self, job_id: str, warning: str | None) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET cleanup_warning = ?, updated_at = ?
                WHERE id = ?
                """,
                (warning, utcnow(), job_id),
            )

    def mark_completed(self, job_id: str) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, stage = ?, progress_percent = 100,
                    progress_message = ?, completed_at = ?, updated_at = ?,
                    worker_id = NULL, lease_expires_at = NULL,
                    error_code = NULL, error_message = NULL,
                    error_retryable = 0
                WHERE id = ? AND status = ?
                """,
                (
                    JobStatus.COMPLETED,
                    JobStage.COMPLETED,
                    "处理完成",
                    now,
                    now,
                    job_id,
                    JobStatus.RUNNING,
                ),
            )
            self._event(
                connection,
                job_id,
                JobStage.COMPLETED,
                "info",
                "任务处理完成",
                now,
            )

    def mark_failed(
        self, job_id: str, code: str, message: str, retryable: bool
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT stage FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, error_code = ?, error_message = ?,
                    error_retryable = ?, progress_message = ?, updated_at = ?,
                    worker_id = NULL, lease_expires_at = NULL
                WHERE id = ?
                """,
                (
                    JobStatus.FAILED,
                    code,
                    message,
                    int(retryable),
                    message,
                    now,
                    job_id,
                ),
            )
            self._event(
                connection, job_id, row["stage"], "error", message, now
            )

    def retry_job(self, job_id: str) -> JobRecord:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                raise AppError("job_not_found", "Job not found", False)
            if row["status"] != JobStatus.FAILED:
                raise AppError(
                    "job_not_retryable",
                    "Only failed jobs can be retried",
                    False,
                )
            if not row["error_retryable"]:
                raise AppError(
                    "job_not_retryable",
                    "This failure requires configuration or input changes",
                    False,
                )
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, retry_count = retry_count + 1,
                    error_code = NULL, error_message = NULL,
                    error_retryable = 0, progress_message = ?,
                    worker_id = NULL, lease_expires_at = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (JobStatus.QUEUED, "等待重试", now, job_id),
            )
            self._event(
                connection,
                job_id,
                row["stage"],
                "info",
                "用户请求重试",
                now,
            )
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._job(updated)  # type: ignore[return-value]

    def retry_job_for_user(self, job_id: str, user_id: str) -> JobRecord:
        self.require_job_access(job_id, user_id)
        return self.retry_job(job_id)

    def list_events(self, job_id: str, limit: int = 100) -> list[dict]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, stage, level, message, created_at
                FROM job_events WHERE job_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (job_id, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        job_id: str,
        stage: str,
        level: str,
        message: str,
        created_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_events (
                job_id, stage, level, message, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, stage, level, message, created_at),
        )
