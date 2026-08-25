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
    ]


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
    assert exports[0].status == "completed"
