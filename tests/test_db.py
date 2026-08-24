from concurrent.futures import ThreadPoolExecutor

from pocket48_summarizer.db import Database


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
    ]
