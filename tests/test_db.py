import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pocket48_summarizer.db import Database
from pocket48_summarizer.models import TranscriptSegment
from pocket48_summarizer.repository import JobRepository


def test_concurrent_database_initialization_is_serialized(tmp_path):
    database_path = tmp_path / "concurrent.sqlite3"

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _: Database(database_path).initialize(),
                range(8),
            )
        )

    with Database(database_path).connect() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert [row["version"] for row in versions] == [
        "001_initial.sql",
        "002_auth_and_access.sql",
        "003_video_clips.sql",
        "004_subtitle_translations.sql",
        "005_backfill_subtitle_translations.sql",
        "006_member_glossary.sql",
        "007_asr_vocabulary.sql",
        "008_configurable_video_clips.sql",
        "009_clip_subtitle_styles.sql",
        "010_clip_output_layout.sql",
        "011_clip_subtitle_fonts.sql",
        "012_clip_covers.sql",
        "013_resummarize_incomplete_timelines.sql",
        "014_clip_ranges.sql",
        "015_resummarize_coarse_timelines.sql",
        "016_retry_normalized_chunk_windows.sql",
        "017_ai_cover_generations.sql",
        "018_retry_repaired_timeline_windows.sql",
        "019_ai_cover_prompt_template.sql",
    ]


def test_clip_ranges_migration_backfills_existing_exports(tmp_path):
    database_path = tmp_path / "clip-ranges.sqlite3"
    migration_dir = (
        Path(__file__).parents[1]
        / "src"
        / "pocket48_summarizer"
        / "migrations"
    )
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in sorted(migration_dir.glob("*.sql")):
        if migration.name == "014_clip_ranges.sql":
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, datetime('now'))
            """,
            (migration.name,),
        )
    connection.execute(
        """
        INSERT INTO jobs (
            id, source_url, live_id, status, stage, created_at, updated_at
        ) VALUES (
            'job-clip-ranges', 'https://example.com/live', 'clip-ranges',
            'completed', 'completed', datetime('now'), datetime('now')
        )
        """
    )
    connection.execute(
        """
        INSERT INTO video_clip_exports (
            id, job_id, timeline_index, request_id, start_ms, end_ms,
            subtitle_mode, render_version, filename, status,
            created_at, updated_at
        ) VALUES (
            'clip-before-ranges', 'job-clip-ranges', 0, 'request-before',
            1000, 5000, 'off', 'ass-v8', 'clip.mp4', 'completed',
            datetime('now'), datetime('now')
        )
        """
    )
    connection.commit()
    connection.close()

    database = Database(database_path)
    database.initialize()
    record = JobRepository(database).get_video_clip_export(
        "job-clip-ranges",
        "clip-before-ranges",
    )

    assert record is not None
    assert [
        item.model_dump() for item in record.kept_ranges
    ] == [{"start_ms": 1000, "end_ms": 5000}]


def test_cover_migration_normalizes_existing_font_scale(tmp_path):
    database_path = tmp_path / "font-scale.sqlite3"
    migration_dir = (
        Path(__file__).parents[1]
        / "src"
        / "pocket48_summarizer"
        / "migrations"
    )

    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in sorted(migration_dir.glob("*.sql")):
        if migration.name == "012_clip_covers.sql":
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, datetime('now'))
            """,
            (migration.name,),
        )
    connection.execute(
        """
        INSERT INTO jobs (
            id, source_url, live_id, status, stage, created_at, updated_at
        ) VALUES (
            'job-font-scale', 'https://example.com/live', 'font-scale',
            'completed', 'completed', datetime('now'), datetime('now')
        )
        """
    )
    for clip_id, scale in (("clip-160", 160), ("clip-100", 100)):
        connection.execute(
            """
            INSERT INTO video_clip_exports (
                id, job_id, timeline_index, request_id,
                start_ms, end_ms, subtitle_mode, render_version,
                filename, status, created_at, updated_at,
                subtitle_font_scale
            ) VALUES (
                ?, 'job-font-scale', 0, ?,
                1000, 5000, 'zh', 'ass-v7',
                ?, 'completed', datetime('now'), datetime('now'), ?
            )
            """,
            (clip_id, f"request-{clip_id}", f"{clip_id}.mp4", scale),
        )
    connection.commit()
    connection.close()

    Database(database_path).initialize()

    with Database(database_path).connect() as migrated:
        rows = migrated.execute(
            """
            SELECT id, subtitle_font_percent, cover_enabled, cover_style
            FROM video_clip_exports
            ORDER BY id
            """
        ).fetchall()

    assert [
        (
            row["id"],
            row["subtitle_font_percent"],
            row["cover_enabled"],
            row["cover_style"],
        )
        for row in rows
    ] == [
        ("clip-100", 63, 0, "scrim"),
        ("clip-160", 100, 0, "scrim"),
    ]

    with Database(database_path).connect() as migrated:
        migrated.execute(
            """
            INSERT INTO video_clip_exports (
                id, job_id, timeline_index, request_id,
                start_ms, end_ms, subtitle_mode, render_version,
                filename, status, created_at, updated_at,
                subtitle_font_scale
            ) VALUES (
                'clip-legacy-insert', 'job-font-scale', 0, 'legacy-insert',
                1000, 5000, 'zh', 'ass-v7',
                'clip-legacy-insert.mp4', 'completed',
                datetime('now'), datetime('now'), 128
            )
            """
        )
        normalized = migrated.execute(
            """
            SELECT subtitle_font_percent
            FROM video_clip_exports
            WHERE id = 'clip-legacy-insert'
            """
        ).fetchone()

    assert normalized["subtitle_font_percent"] == 80


def test_backfill_migration_queues_completed_transcripts(tmp_path):
    database = Database(tmp_path / "backfill.sqlite3")
    database.initialize()
    repository = JobRepository(database)
    job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            "index.html?id=991100"
        ),
        "991100",
    )
    repository.claim_next_job("worker", 120)
    repository.replace_transcript(
        job.id,
        [
            TranscriptSegment(
                sequence=1,
                start_ms=0,
                end_ms=1000,
                text="历史字幕",
            )
        ],
    )
    repository.mark_completed(job.id)

    migration = (
        Path(__file__).parents[1]
        / "src/pocket48_summarizer/migrations/"
        "005_backfill_subtitle_translations.sql"
    )
    with database.connect() as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.executescript(migration.read_text(encoding="utf-8"))

    translation = repository.get_subtitle_translation_request(job.id)
    assert translation and translation.status == "queued"


def test_incomplete_long_timelines_are_queued_for_resummarization(tmp_path):
    database_path = tmp_path / "timeline-coverage.sqlite3"
    migration_dir = (
        Path(__file__).parents[1]
        / "src"
        / "pocket48_summarizer"
        / "migrations"
    )
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in sorted(migration_dir.glob("*.sql")):
        if migration.name == "013_resummarize_incomplete_timelines.sql":
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, datetime('now'))
            """,
            (migration.name,),
        )
    for job_id, timeline_start, timeline_end in (
        ("incomplete-timeline", 0, 4_000_000),
        ("covered-timeline", 10_500_000, 11_000_000),
        ("coarse-timeline", 0, 11_000_000),
    ):
        summary_json = (
            '{"overview":"测试","timeline":[{"start_ms":'
            f"{timeline_start},"
            f'"end_ms":{timeline_end},"title":"测试","detail":"测试",'
            '"evidence_segment_ids":[1]}],"topics":[],"highlights":[]}'
        )
        connection.execute(
            """
            INSERT INTO jobs (
                id, source_url, live_id, status, stage, duration_ms,
                summary_json, summary_markdown, progress_percent,
                progress_message, created_at, updated_at, completed_at
            ) VALUES (
                ?, 'https://example.com/live', ?, 'completed', 'completed',
                12000000, ?, '# 测试', 100, '处理完成',
                datetime('now'), datetime('now'), datetime('now')
            )
            """,
            (job_id, job_id, summary_json),
        )
        connection.execute(
            """
            INSERT INTO transcript_segments (
                job_id, sequence, start_ms, end_ms, text
            ) VALUES (?, 1, 0, 12000000, '完整长直播字幕')
            """,
            (job_id,),
        )
    connection.commit()
    connection.close()

    Database(database_path).initialize()

    with Database(database_path).connect() as migrated:
        incomplete = migrated.execute(
            "SELECT * FROM jobs WHERE id = 'incomplete-timeline'"
        ).fetchone()
        covered = migrated.execute(
            "SELECT * FROM jobs WHERE id = 'covered-timeline'"
        ).fetchone()
        coarse = migrated.execute(
            "SELECT * FROM jobs WHERE id = 'coarse-timeline'"
        ).fetchone()

    assert incomplete["status"] == "queued"
    assert incomplete["stage"] == "queued"
    assert incomplete["summary_json"] is None
    assert incomplete["summary_markdown"] is None
    assert incomplete["completed_at"] is None
    assert covered["status"] == "completed"
    assert covered["summary_json"] is not None
    assert coarse["status"] == "queued"
    assert coarse["summary_json"] is None
    assert coarse["progress_message"] == "等待细化长直播时间线"


def test_failed_chunk_window_job_is_requeued_after_normalization_fix(
    tmp_path,
):
    database_path = tmp_path / "chunk-window-retry.sqlite3"
    migration_dir = (
        Path(__file__).parents[1]
        / "src"
        / "pocket48_summarizer"
        / "migrations"
    )
    connection = sqlite3.connect(database_path)
    connection.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in sorted(migration_dir.glob("*.sql")):
        if migration.name == "016_retry_normalized_chunk_windows.sql":
            break
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO schema_migrations (version, applied_at)
            VALUES (?, datetime('now'))
            """,
            (migration.name,),
        )
    connection.execute(
        """
        INSERT INTO jobs (
            id, source_url, live_id, status, stage, duration_ms,
            progress_percent, progress_message, error_code, error_message,
            error_retryable, created_at, updated_at, started_at
        ) VALUES (
            'chunk-window-failed', 'https://example.com/live',
            'chunk-window-failed', 'failed', 'summarizing_chunks',
            12000000, 70, '分段总结失败', 'llm_invalid_chunk_window',
            '模型分段总结改写了输入时间窗口', 1,
            datetime('now'), datetime('now'), datetime('now')
        )
        """
    )
    connection.execute(
        """
        INSERT INTO transcript_segments (
            job_id, sequence, start_ms, end_ms, text
        ) VALUES (
            'chunk-window-failed', 1, 0, 12000000, '完整长直播字幕'
        )
        """
    )
    connection.commit()
    connection.close()

    Database(database_path).initialize()

    with Database(database_path).connect() as migrated:
        job = migrated.execute(
            "SELECT * FROM jobs WHERE id = 'chunk-window-failed'"
        ).fetchone()

    assert job["status"] == "queued"
    assert job["stage"] == "queued"
    assert job["progress_message"] == "等待继续细化长直播时间线"
    assert job["error_code"] is None
    assert job["error_message"] is None
    assert job["started_at"] is None


def test_configurable_clip_migration_backfills_legacy_rows_idempotently(
    tmp_path,
):
    database = Database(tmp_path / "clip-backfill.sqlite3")
    database.initialize()
    repository = JobRepository(database)
    job, _ = repository.create_or_get_job(
        (
            "https://h5.48.cn/2019appshare/memberLiveShare/"
            "index.html?id=991101"
        ),
        "991101",
    )
    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO video_clips (
                job_id, timeline_index, start_ms, end_ms, filename,
                status, created_at, updated_at, completed_at
            ) VALUES (?, 2, 1000, 5000, 'legacy.mp4',
                      'completed', 'created', 'updated', 'completed')
            """,
            (job.id,),
        )

    migration = (
        Path(__file__).parents[1]
        / "src/pocket48_summarizer/migrations/"
        "008_configurable_video_clips.sql"
    )
    with database.connect() as connection:
        connection.executescript(migration.read_text(encoding="utf-8"))
        connection.executescript(migration.read_text(encoding="utf-8"))
        legacy_count = connection.execute(
            "SELECT COUNT(*) AS count FROM video_clips"
        ).fetchone()["count"]

    exports = repository.list_video_clip_exports(job.id)
    assert legacy_count == 1
    assert len(exports) == 1
    assert exports[0].timeline_index == 2
    assert exports[0].request_id == "legacy:2"
    assert exports[0].subtitle_mode == "off"
    assert exports[0].subtitle_font_scale == 63
    assert exports[0].subtitle_text_color == "#FFFFFF"
    assert exports[0].subtitle_background_color == "#000000"
    assert exports[0].output_layout == "portrait"
    assert exports[0].subtitle_font_family == "sans"
    assert exports[0].status == "completed"
