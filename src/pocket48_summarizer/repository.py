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
    AICoverAssetRecord,
    AICoverGenerationRecord,
    ClipRange,
    ClipBoundarySuggestionRecord,
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
    MemberCatalogGroupRecord,
    MemberCatalogRecord,
    MemberJobFilterRecord,
    ReplayMetadata,
    SubtitleTranslationRequestRecord,
    SubtitleTranslationStatus,
    TranscriptSegment,
    VideoClipExportRecord,
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
    def _video_clip_export(
        row: sqlite3.Row | None,
    ) -> VideoClipExportRecord | None:
        if row is None:
            return None
        payload = dict(row)
        if "subtitle_font_percent" in payload:
            payload["subtitle_font_scale"] = payload[
                "subtitle_font_percent"
            ]
        ranges_payload = payload.pop("kept_ranges_json", "[]")
        try:
            ranges = json.loads(ranges_payload)
        except (TypeError, json.JSONDecodeError):
            ranges = []
        if not ranges:
            ranges = [
                {
                    "start_ms": payload["start_ms"],
                    "end_ms": payload["end_ms"],
                }
            ]
        payload["kept_ranges"] = ranges
        return VideoClipExportRecord.model_validate(payload)

    @staticmethod
    def _clip_boundary_suggestion(
        row: sqlite3.Row | None,
    ) -> ClipBoundarySuggestionRecord | None:
        if row is None:
            return None
        return ClipBoundarySuggestionRecord.model_validate(dict(row))

    @staticmethod
    def _ai_cover_generation(
        row: sqlite3.Row | None,
    ) -> AICoverGenerationRecord | None:
        if row is None:
            return None
        payload = dict(row)
        payload.setdefault("layout_style", "sticker_pop")
        payload.setdefault("highlight_text", "")
        extra_text_json = payload.pop("extra_text_json", "[]")
        try:
            extra_text = json.loads(extra_text_json)
        except (TypeError, json.JSONDecodeError):
            extra_text = []
        payload["extra_text"] = (
            [str(item) for item in extra_text]
            if isinstance(extra_text, list)
            else []
        )
        return AICoverGenerationRecord.model_validate(payload)

    @staticmethod
    def _ai_cover_asset(
        row: sqlite3.Row | None,
    ) -> AICoverAssetRecord | None:
        if row is None:
            return None
        return AICoverAssetRecord.model_validate(dict(row))

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
                    status, ranking, source_active, active,
                    source_present, source,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
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
                    source_active = excluded.source_active,
                    active = excluded.source_active
                        * (1 - member_catalog.admin_disabled),
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
                        int(member.active),
                        now,
                        now,
                    )
                    for member in members
                ],
            )
            connection.execute(
                """
                UPDATE glossary_sync_state
                SET source_url = ?,
                    sync_status = 'success',
                    source_hash = ?,
                    catalog_version = ?,
                    member_count = ?,
                    last_attempt_at = ?,
                    last_success_at = ?,
                    last_error = NULL
                WHERE singleton = 1
                """,
                (
                    source_url,
                    source_hash,
                    source_hash[:16],
                    len(members),
                    now,
                    now,
                ),
            )
            self._update_glossary_fingerprint(connection)
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

    def set_member_admin_disabled(
        self, member_id: str, *, disabled: bool
    ) -> None:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE member_catalog
                SET admin_disabled = ?,
                    active = source_active * (1 - ?)
                WHERE member_id = ?
                """,
                (int(disabled), int(disabled), member_id),
            )
            if cursor.rowcount != 1:
                raise AppError(
                    "member_catalog_member_not_found",
                    "官方成员不存在",
                    False,
                )
            self._update_glossary_fingerprint(connection)

    def set_group_admin_disabled(
        self, group_id: str, *, disabled: bool
    ) -> int:
        """Disable or restore a whole group in one action.

        Groups such as IDFT contribute hundreds of names that never appear in
        the livestreams being transcribed, and every one of them is a chance
        for the recogniser to snap a common word onto a stranger's name.
        """

        group_id = group_id.strip()
        if not group_id:
            raise AppError(
                "member_catalog_group_invalid",
                "请选择要操作的团体",
                False,
            )
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE member_catalog
                SET admin_disabled = ?,
                    active = source_active * (1 - ?)
                WHERE group_id = ? AND admin_disabled != ?
                """,
                (int(disabled), int(disabled), group_id, int(disabled)),
            )
            changed = cursor.rowcount
            self._update_glossary_fingerprint(connection)
        return changed

    def list_member_catalog_groups(self) -> list[MemberCatalogGroupRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT group_id,
                       MAX(group_name) AS group_name,
                       COUNT(*) AS member_count,
                       SUM(admin_disabled) AS disabled_count,
                       SUM(active) AS active_count
                FROM member_catalog
                WHERE source_present = 1
                GROUP BY group_id
                ORDER BY group_id
                """
            ).fetchall()
        return [
            MemberCatalogGroupRecord.model_validate(dict(row)) for row in rows
        ]

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
        """Refresh every value derived from the effective glossary.

        The reported member count belongs here rather than at the call sites,
        because it is derived from exactly the same rows as the fingerprint.
        Maintaining it anywhere else lets the headline number drift away from
        what the glossary actually contains.
        """

        connection.execute(
            """
            UPDATE glossary_sync_state
            SET glossary_fingerprint = ?,
                active_member_count = (
                    SELECT COUNT(*) FROM member_catalog
                    WHERE active = 1 AND source_present = 1
                )
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

    @staticmethod
    def _visible_jobs_condition(
        user_id: str | None,
    ) -> tuple[str, list[object]]:
        if user_id is None:
            return "j.status = ?", [JobStatus.COMPLETED]
        return (
            """
            (
                j.status = ?
                OR EXISTS (
                    SELECT 1 FROM job_access a
                    WHERE a.job_id = j.id AND a.user_id = ?
                )
            )
            """,
            [JobStatus.COMPLETED, user_id],
        )

    def list_visible_jobs(
        self,
        user_id: str | None,
        limit: int = 50,
        *,
        member_id: str | None = None,
    ) -> list[JobRecord]:
        where, parameters = self._visible_jobs_condition(user_id)
        if member_id is not None:
            where = f"{where} AND j.member_id = ?"
            parameters.append(member_id)
        parameters.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT j.* FROM jobs j
                WHERE {where}
                ORDER BY COALESCE(j.replay_started_at, j.created_at) DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
            return [
                job for row in rows if (job := self._job(row)) is not None
            ]

    def list_visible_member_filters(
        self, user_id: str | None
    ) -> list[MemberJobFilterRecord]:
        where, parameters = self._visible_jobs_condition(user_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    j.member_id,
                    COALESCE(
                        MAX(NULLIF(m.canonical_name, '')),
                        MAX(NULLIF(j.member_name, '')),
                        j.member_id
                    ) AS member_name,
                    COALESCE(MAX(m.group_name), '') AS group_name,
                    COUNT(*) AS job_count
                FROM jobs j
                LEFT JOIN member_catalog m ON m.member_id = j.member_id
                WHERE {where}
                  AND j.member_id IS NOT NULL
                  AND j.member_id != ''
                GROUP BY j.member_id
                ORDER BY group_name COLLATE NOCASE,
                         member_name COLLATE NOCASE,
                         j.member_id
                """,
                parameters,
            ).fetchall()
        return [
            MemberJobFilterRecord.model_validate(dict(row)) for row in rows
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

    def begin_video_clip_export(
        self,
        *,
        clip_id: str,
        job_id: str,
        timeline_index: int,
        timeline_title: str,
        requested_by_user_id: str | None,
        request_id: str,
        start_ms: int,
        end_ms: int,
        kept_ranges: list[ClipRange] | None = None,
        subtitle_mode: str,
        include_danmaku: bool,
        render_version: str,
        filename: str,
        subtitle_font_scale: int = 100,
        subtitle_text_color: str = "#FFFFFF",
        subtitle_background_color: str = "#000000",
        output_layout: str = "portrait",
        subtitle_font_family: str = "sans",
        cover_enabled: bool = False,
        cover_timestamp_ms: int | None = None,
        cover_title: str = "",
        cover_style: str = "scrim",
        ai_cover_generation_id: str | None = None,
        ai_cover_asset_id: str | None = None,
        ai_cover_final_oss_object_key: str | None = None,
        ai_cover_final_sha256: str | None = None,
        ai_cover_text_revision: int | None = None,
    ) -> tuple[VideoClipExportRecord, bool]:
        now = utcnow()
        legacy_font_scale = max(
            70,
            min(160, round(subtitle_font_scale * 1.6)),
        )
        normalized_ranges = kept_ranges or [
            ClipRange(start_ms=start_ms, end_ms=end_ms)
        ]
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO video_clip_exports (
                    id, job_id, timeline_index, timeline_title,
                    requested_by_user_id, request_id, start_ms, end_ms,
                    kept_ranges_json,
                    subtitle_mode, include_danmaku,
                    subtitle_font_scale, subtitle_font_percent,
                    subtitle_text_color, subtitle_background_color,
                    output_layout, subtitle_font_family,
                    cover_enabled, cover_timestamp_ms, cover_title,
                    cover_style, ai_cover_generation_id,
                    ai_cover_asset_id, ai_cover_final_oss_object_key,
                    ai_cover_final_sha256, ai_cover_text_revision,
                    render_version, filename,
                    status, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'running', ?, ?
                )
                """,
                (
                    clip_id,
                    job_id,
                    timeline_index,
                    timeline_title,
                    requested_by_user_id,
                    request_id,
                    start_ms,
                    end_ms,
                    json.dumps(
                        [
                            item.model_dump()
                            for item in normalized_ranges
                        ],
                        separators=(",", ":"),
                    ),
                    subtitle_mode,
                    int(include_danmaku),
                    legacy_font_scale,
                    subtitle_font_scale,
                    subtitle_text_color,
                    subtitle_background_color,
                    output_layout,
                    subtitle_font_family,
                    int(cover_enabled),
                    cover_timestamp_ms,
                    cover_title,
                    cover_style,
                    ai_cover_generation_id,
                    ai_cover_asset_id,
                    ai_cover_final_oss_object_key,
                    ai_cover_final_sha256,
                    ai_cover_text_revision,
                    render_version,
                    filename,
                    now,
                    now,
                ),
            ).rowcount
            row = connection.execute(
                """
                SELECT * FROM video_clip_exports
                WHERE job_id = ? AND request_id = ?
                """,
                (job_id, request_id),
            ).fetchone()
        export = self._video_clip_export(row)
        if export is None:
            raise AppError(
                "video_clip_conflict",
                "视频剪辑请求标识冲突，请重新提交",
                True,
            )
        return export, inserted == 1

    def begin_ai_cover_generation(
        self,
        *,
        generation_id: str,
        job_id: str,
        timeline_index: int,
        requested_by_user_id: str | None,
        request_id: str,
        source_timestamp_ms: int,
        provider: str,
        model: str,
        prompt_version: str,
        prompt_template: str,
        shared_seed: int | None,
        title_text: str,
        extra_text: list[str],
        landscape_size: tuple[int, int],
        four_three_size: tuple[int, int],
        layout_style: str = "sticker_pop",
        highlight_text: str = "",
    ) -> tuple[AICoverGenerationRecord, bool]:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO ai_cover_generations (
                    id, job_id, timeline_index, requested_by_user_id,
                    request_id, source_timestamp_ms, provider, model,
                    prompt_version, prompt_template, shared_seed,
                    layout_style, title_text, highlight_text,
                    extra_text_json, status, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'queued', ?, ?
                )
                """,
                (
                    generation_id,
                    job_id,
                    timeline_index,
                    requested_by_user_id,
                    request_id,
                    source_timestamp_ms,
                    provider,
                    model,
                    prompt_version,
                    prompt_template,
                    shared_seed,
                    layout_style,
                    title_text,
                    highlight_text,
                    json.dumps(extra_text, ensure_ascii=False),
                    now,
                    now,
                ),
            ).rowcount
            if inserted:
                for orientation, size in (
                    ("landscape", landscape_size),
                    ("four_three", four_three_size),
                ):
                    connection.execute(
                        """
                        INSERT INTO ai_cover_assets (
                            id, generation_id, orientation, width, height,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            generation_id,
                            orientation,
                            size[0],
                            size[1],
                            now,
                            now,
                        ),
                    )
            row = connection.execute(
                """
                SELECT * FROM ai_cover_generations
                WHERE job_id = ? AND request_id = ?
                """,
                (job_id, request_id),
            ).fetchone()
        generation = self._ai_cover_generation(row)
        if generation is None:
            raise AppError(
                "ai_cover_conflict",
                "AI 封面请求标识冲突，请重新提交",
                True,
            )
        return generation, inserted == 1

    def get_ai_cover_generation(
        self, job_id: str, generation_id: str
    ) -> AICoverGenerationRecord | None:
        with self.database.connect() as connection:
            return self._ai_cover_generation(
                connection.execute(
                    """
                    SELECT * FROM ai_cover_generations
                    WHERE job_id = ? AND id = ?
                    """,
                    (job_id, generation_id),
                ).fetchone()
            )

    def list_ai_cover_generations(
        self,
        job_id: str,
        *,
        timeline_index: int | None = None,
        limit: int = 30,
    ) -> list[AICoverGenerationRecord]:
        limit = max(1, min(limit, 100))
        if timeline_index is None:
            where = "job_id = ?"
            parameters: tuple[object, ...] = (job_id, limit)
        else:
            where = "job_id = ? AND timeline_index = ?"
            parameters = (job_id, timeline_index, limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM ai_cover_generations
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            generation
            for row in rows
            if (
                generation := self._ai_cover_generation(row)
            ) is not None
        ]

    def list_ai_cover_assets(
        self, generation_id: str
    ) -> list[AICoverAssetRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM ai_cover_assets
                WHERE generation_id = ?
                ORDER BY CASE orientation
                    WHEN 'landscape' THEN 0 ELSE 1 END
                """,
                (generation_id,),
            ).fetchall()
        return [
            asset
            for row in rows
            if (asset := self._ai_cover_asset(row)) is not None
        ]

    def get_ai_cover_asset(
        self, generation_id: str, orientation: str
    ) -> AICoverAssetRecord | None:
        with self.database.connect() as connection:
            return self._ai_cover_asset(
                connection.execute(
                    """
                    SELECT * FROM ai_cover_assets
                    WHERE generation_id = ? AND orientation = ?
                    """,
                    (generation_id, orientation),
                ).fetchone()
            )

    def mark_ai_cover_generation_running(
        self, generation_id: str
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ai_cover_generations
                SET status = 'running', error_code = NULL,
                    error_message = NULL, updated_at = ?,
                    completed_at = NULL
                WHERE id = ?
                """,
                (utcnow(), generation_id),
            )

    def mark_ai_cover_asset_running(
        self,
        asset_id: str,
        *,
        provider_task_id: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ai_cover_assets
                SET status = 'running', provider_task_id = ?,
                    provider_request_id = ?, error_code = NULL,
                    error_message = NULL, updated_at = ?,
                    completed_at = NULL
                WHERE id = ?
                """,
                (
                    provider_task_id,
                    provider_request_id,
                    utcnow(),
                    asset_id,
                ),
            )

    def save_ai_cover_asset_background(
        self,
        asset_id: str,
        *,
        background_oss_object_key: str,
        background_sha256: str,
        provider_task_id: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE ai_cover_assets
                SET background_oss_object_key = ?,
                    background_sha256 = ?,
                    provider_task_id = COALESCE(?, provider_task_id),
                    provider_request_id = COALESCE(
                        ?, provider_request_id
                    ),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    background_oss_object_key,
                    background_sha256,
                    provider_task_id,
                    provider_request_id,
                    utcnow(),
                    asset_id,
                ),
            )

    def complete_ai_cover_asset(
        self,
        asset_id: str,
        *,
        background_oss_object_key: str,
        final_oss_object_key: str,
        background_sha256: str,
        final_sha256: str,
        provider_task_id: str | None = None,
        provider_request_id: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT generation_id FROM ai_cover_assets WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                raise AppError(
                    "ai_cover_asset_not_found",
                    "AI 封面图片不存在",
                    False,
                )
            connection.execute(
                """
                UPDATE ai_cover_assets
                SET status = 'completed',
                    provider_task_id = COALESCE(?, provider_task_id),
                    provider_request_id = COALESCE(?, provider_request_id),
                    background_oss_object_key = ?,
                    final_oss_object_key = ?,
                    background_sha256 = ?, final_sha256 = ?,
                    error_code = NULL, error_message = NULL,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    provider_task_id,
                    provider_request_id,
                    background_oss_object_key,
                    final_oss_object_key,
                    background_sha256,
                    final_sha256,
                    now,
                    now,
                    asset_id,
                ),
            )
            self._sync_ai_cover_generation_status(
                connection, row["generation_id"], now
            )

    def fail_ai_cover_asset(
        self, asset_id: str, error_code: str, error_message: str
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT generation_id FROM ai_cover_assets WHERE id = ?
                """,
                (asset_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE ai_cover_assets
                SET status = 'failed', error_code = ?, error_message = ?,
                    updated_at = ?, completed_at = NULL
                WHERE id = ?
                """,
                (error_code, error_message, now, asset_id),
            )
            self._sync_ai_cover_generation_status(
                connection, row["generation_id"], now
            )

    def fail_ai_cover_generation(
        self,
        generation_id: str,
        error_code: str,
        error_message: str,
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE ai_cover_assets
                SET status = 'failed', error_code = ?, error_message = ?,
                    updated_at = ?, completed_at = NULL
                WHERE generation_id = ? AND status != 'completed'
                """,
                (error_code, error_message, now, generation_id),
            )
            connection.execute(
                """
                UPDATE ai_cover_generations
                SET status = 'failed', error_code = ?, error_message = ?,
                    updated_at = ?, completed_at = NULL
                WHERE id = ?
                """,
                (error_code, error_message, now, generation_id),
            )

    def retry_ai_cover_generation(
        self, job_id: str, generation_id: str
    ) -> AICoverGenerationRecord:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM ai_cover_generations
                WHERE job_id = ? AND id = ?
                """,
                (job_id, generation_id),
            ).fetchone()
            generation = self._ai_cover_generation(row)
            if generation is None:
                raise AppError(
                    "ai_cover_not_found",
                    "AI 封面不存在",
                    False,
                )
            if generation.status == "running":
                return generation
            if generation.status == "completed":
                return generation
            connection.execute(
                """
                UPDATE ai_cover_assets
                SET status = 'queued', error_code = NULL,
                    error_message = NULL, updated_at = ?,
                    completed_at = NULL
                WHERE generation_id = ? AND status != 'completed'
                """,
                (now, generation_id),
            )
            connection.execute(
                """
                UPDATE ai_cover_generations
                SET status = 'queued', error_code = NULL,
                    error_message = NULL, updated_at = ?,
                    completed_at = NULL
                WHERE id = ?
                """,
                (now, generation_id),
            )
            row = connection.execute(
                """
                SELECT * FROM ai_cover_generations WHERE id = ?
                """,
                (generation_id,),
            ).fetchone()
        retried = self._ai_cover_generation(row)
        assert retried is not None
        return retried

    def recover_running_ai_cover_generations(self) -> int:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            assets = connection.execute(
                """
                UPDATE ai_cover_assets
                SET status = 'failed', error_code = 'ai_cover_interrupted',
                    error_message = '服务曾重启，请重试 AI 封面',
                    updated_at = ?, completed_at = NULL
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            ).rowcount
            connection.execute(
                """
                UPDATE ai_cover_generations
                SET status = 'failed', error_code = 'ai_cover_interrupted',
                    error_message = '服务曾重启，请重试 AI 封面',
                    updated_at = ?, completed_at = NULL
                WHERE status IN ('queued', 'running')
                """,
                (now,),
            )
        return assets

    @staticmethod
    def _sync_ai_cover_generation_status(
        connection: sqlite3.Connection,
        generation_id: str,
        now: str,
    ) -> None:
        rows = connection.execute(
            """
            SELECT status, error_code, error_message
            FROM ai_cover_assets
            WHERE generation_id = ?
            """,
            (generation_id,),
        ).fetchall()
        statuses = [str(row["status"]) for row in rows]
        generation_row = connection.execute(
            """
            SELECT status FROM ai_cover_generations WHERE id = ?
            """,
            (generation_id,),
        ).fetchone()
        current_status = (
            str(generation_row["status"]) if generation_row else "queued"
        )
        if rows and all(status == "completed" for status in statuses):
            status = "completed"
            error_code = None
            error_message = None
            completed_at = now
        elif any(status == "running" for status in statuses):
            status = "running"
            error_code = None
            error_message = None
            completed_at = None
        elif any(status == "queued" for status in statuses):
            status = "running" if current_status == "running" else "queued"
            error_code = None
            error_message = None
            completed_at = None
        elif any(status == "failed" for status in statuses):
            status = "failed"
            failed = next(row for row in rows if row["status"] == "failed")
            error_code = failed["error_code"]
            error_message = failed["error_message"]
            completed_at = None
        else:
            status = "queued"
            error_code = None
            error_message = None
            completed_at = None
        connection.execute(
            """
            UPDATE ai_cover_generations
            SET status = ?, error_code = ?, error_message = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                error_code,
                error_message,
                now,
                completed_at,
                generation_id,
            ),
        )

    def get_video_clip_export(
        self, job_id: str, clip_id: str
    ) -> VideoClipExportRecord | None:
        with self.database.connect() as connection:
            return self._video_clip_export(
                connection.execute(
                    """
                    SELECT * FROM video_clip_exports
                    WHERE job_id = ? AND id = ?
                    """,
                    (job_id, clip_id),
                ).fetchone()
            )

    def get_video_clip_export_by_request_id(
        self, job_id: str, request_id: str
    ) -> VideoClipExportRecord | None:
        with self.database.connect() as connection:
            return self._video_clip_export(
                connection.execute(
                    """
                    SELECT * FROM video_clip_exports
                    WHERE job_id = ? AND request_id = ?
                    """,
                    (job_id, request_id),
                ).fetchone()
            )

    def find_video_clip_export_by_filename(
        self, job_id: str, filename: str
    ) -> VideoClipExportRecord | None:
        with self.database.connect() as connection:
            return self._video_clip_export(
                connection.execute(
                    """
                    SELECT * FROM video_clip_exports
                    WHERE job_id = ? AND filename = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (job_id, filename),
                ).fetchone()
            )

    def get_latest_video_clip_export(
        self,
        job_id: str,
        timeline_index: int,
        *,
        completed_only: bool = False,
    ) -> VideoClipExportRecord | None:
        condition = "AND status = 'completed'" if completed_only else ""
        with self.database.connect() as connection:
            return self._video_clip_export(
                connection.execute(
                    f"""
                    SELECT * FROM video_clip_exports
                    WHERE job_id = ? AND timeline_index = ?
                    {condition}
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (job_id, timeline_index),
                ).fetchone()
            )

    def list_video_clip_exports(
        self,
        job_id: str,
        *,
        timeline_index: int | None = None,
        limit: int = 200,
    ) -> list[VideoClipExportRecord]:
        limit = max(1, min(limit, 500))
        parameters: tuple[object, ...]
        if timeline_index is None:
            where = "job_id = ?"
            parameters = (job_id, limit)
        else:
            where = "job_id = ? AND timeline_index = ?"
            parameters = (job_id, timeline_index, limit)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM video_clip_exports
                WHERE {where}
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        return [
            export
            for row in rows
            if (export := self._video_clip_export(row)) is not None
        ]

    def complete_video_clip_export(
        self,
        clip_id: str,
        object_key: str,
        warning_message: str | None = None,
    ) -> None:
        now = utcnow()
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE video_clip_exports
                SET status = 'completed', oss_object_key = ?,
                    error_message = NULL, warning_message = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (object_key, warning_message, now, now, clip_id),
            )

    def fail_video_clip_export(
        self, clip_id: str, error_message: str
    ) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                UPDATE video_clip_exports
                SET status = 'failed', error_message = ?,
                    updated_at = ?, completed_at = NULL
                WHERE id = ?
                """,
                (error_message, utcnow(), clip_id),
            )

    def retry_video_clip_export(
        self,
        job_id: str,
        clip_id: str,
        *,
        allow_completed: bool = False,
    ) -> VideoClipExportRecord:
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM video_clip_exports
                WHERE job_id = ? AND id = ?
                """,
                (job_id, clip_id),
            ).fetchone()
            export = self._video_clip_export(row)
            if export is None:
                raise AppError(
                    "video_clip_not_found",
                    "视频片段不存在",
                    False,
                )
            if export.status == "running":
                return export
            if export.status == "completed" and not allow_completed:
                return export
            connection.execute(
                """
                UPDATE video_clip_exports
                SET status = 'running', oss_object_key = NULL,
                    error_message = NULL, warning_message = NULL,
                    updated_at = ?, completed_at = NULL
                WHERE job_id = ? AND id = ?
                """,
                (utcnow(), job_id, clip_id),
            )
            row = connection.execute(
                """
                SELECT * FROM video_clip_exports
                WHERE job_id = ? AND id = ?
                """,
                (job_id, clip_id),
            ).fetchone()
        retried = self._video_clip_export(row)
        assert retried is not None
        return retried

    def recover_running_video_clip_exports(self) -> int:
        with self.database.connect() as connection:
            return connection.execute(
                """
                UPDATE video_clip_exports
                SET status = 'failed',
                    error_message = '服务曾重启，请重试剪辑',
                    updated_at = ?
                WHERE status = 'running'
                """,
                (utcnow(),),
            ).rowcount

    def get_clip_boundary_suggestion(
        self, job_id: str, cache_key: str
    ) -> ClipBoundarySuggestionRecord | None:
        with self.database.connect() as connection:
            return self._clip_boundary_suggestion(
                connection.execute(
                    """
                    SELECT * FROM clip_boundary_suggestions
                    WHERE job_id = ? AND cache_key = ?
                    """,
                    (job_id, cache_key),
                ).fetchone()
            )

    def save_clip_boundary_suggestion(
        self,
        *,
        job_id: str,
        cache_key: str,
        boundary_kind: str,
        segment_sequence: int,
        anchor_ms: int,
        suggested_ms: int,
        silence_start_ms: int | None,
        silence_end_ms: int | None,
        analysis_version: str,
    ) -> ClipBoundarySuggestionRecord:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO clip_boundary_suggestions (
                    job_id, cache_key, boundary_kind, segment_sequence,
                    anchor_ms, suggested_ms, silence_start_ms,
                    silence_end_ms, analysis_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    cache_key,
                    boundary_kind,
                    segment_sequence,
                    anchor_ms,
                    suggested_ms,
                    silence_start_ms,
                    silence_end_ms,
                    analysis_version,
                    utcnow(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM clip_boundary_suggestions
                WHERE job_id = ? AND cache_key = ?
                """,
                (job_id, cache_key),
            ).fetchone()
        suggestion = self._clip_boundary_suggestion(row)
        assert suggestion is not None
        return suggestion

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

    def has_queued_jobs(self) -> bool:
        with self.database.connect() as connection:
            return (
                connection.execute(
                    """
                    SELECT 1 FROM jobs
                    WHERE status = ?
                    LIMIT 1
                    """,
                    (JobStatus.QUEUED,),
                ).fetchone()
                is not None
            )

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

    def pause_owned_subtitle_translation(
        self, job_id: str, language: str, worker_id: str
    ) -> None:
        with self.database.connect() as connection:
            updated = connection.execute(
                """
                UPDATE subtitle_translation_requests
                SET status = ?, worker_id = NULL, lease_expires_at = NULL,
                    retry_count = CASE
                        WHEN retry_count > 0 THEN retry_count - 1
                        ELSE 0
                    END,
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
            ).rowcount
            if updated != 1:
                raise AppError(
                    "translation_lease_lost",
                    "英文字幕任务租约已失效",
                    True,
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
