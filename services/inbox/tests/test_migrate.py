from pathlib import Path

import pytest

from app.db import connect
from app.migrate import MIGRATIONS_DIR, MigrationError, migrate, migration_files, split_batches
from tests.conftest import TEST_DB, create_database, drop_database

SCRATCH_DB = TEST_DB + "_runner"


@pytest.fixture
def scratch():
    # The runner tests get their own empty database, so they can create and
    # break tables without touching the one the other tests use.
    create_database(SCRATCH_DB)
    connection = connect(SCRATCH_DB)
    yield connection
    connection.close()
    server = connect("master", autocommit=True)
    try:
        drop_database(server, SCRATCH_DB)
    finally:
        server.close()


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def _tables(connection) -> set[str]:
    return {row["name"] for row in connection.execute("SELECT name FROM sys.tables")}


def test_files_run_in_number_order_and_only_once(scratch, tmp_path: Path) -> None:
    _write(tmp_path, "002_add_colour.sql", "ALTER TABLE notes ADD colour NVARCHAR(20) NULL;")
    _write(tmp_path, "001_notes.sql", "CREATE TABLE notes (id INT PRIMARY KEY, body NVARCHAR(100) NOT NULL);")
    _write(tmp_path, "10_later.sql", "INSERT INTO notes (id, body, colour) VALUES (1, 'kept', 'red');")
    _write(tmp_path, "README.md", "not a migration")

    assert migrate(scratch, tmp_path) == ["001_notes.sql", "002_add_colour.sql", "10_later.sql"]
    assert migrate(scratch, tmp_path) == []
    versions = [row["version"] for row in scratch.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == ["001_notes", "002_add_colour", "10_later"]
    assert scratch.execute("SELECT body, colour FROM notes").fetchone()["colour"] == "red"


def test_a_failing_file_is_rolled_back_and_stops_the_run(scratch, tmp_path: Path) -> None:
    _write(tmp_path, "001_notes.sql", "CREATE TABLE notes (id INT PRIMARY KEY);")
    _write(
        tmp_path,
        "002_broken.sql",
        "CREATE TABLE half_done (id INT PRIMARY KEY);\nINSERT INTO missing_table VALUES (1);",
    )
    _write(tmp_path, "003_never.sql", "CREATE TABLE never (id INT PRIMARY KEY);")

    with pytest.raises(MigrationError, match="002_broken.sql"):
        migrate(scratch, tmp_path)
    assert {"notes", "schema_migrations"} == _tables(scratch)
    versions = {row["version"] for row in scratch.execute("SELECT version FROM schema_migrations")}
    assert versions == {"001_notes"}


def test_go_lines_split_a_file_into_batches(scratch, tmp_path: Path) -> None:
    _write(
        tmp_path,
        "001_view.sql",
        "CREATE TABLE notes (id INT PRIMARY KEY);\ngo\nCREATE VIEW note_ids AS SELECT id FROM notes;\nGO\n",
    )
    assert migrate(scratch, tmp_path) == ["001_view.sql"]
    assert scratch.execute("SELECT COUNT(*) AS n FROM note_ids").fetchone()["n"] == 0


def test_two_files_with_the_same_number_are_refused(tmp_path: Path) -> None:
    _write(tmp_path, "001_a.sql", "SELECT 1;")
    _write(tmp_path, "001_b.sql", "SELECT 1;")
    with pytest.raises(MigrationError, match="share a number"):
        migration_files(tmp_path)


def test_split_batches_ignores_go_inside_a_line() -> None:
    assert split_batches("SELECT 'GO home';\nGO\n\nSELECT 2;") == ["SELECT 'GO home';", "SELECT 2;"]


def test_both_services_ship_a_first_migration(scratch) -> None:
    assert [path.name for path in migration_files(MIGRATIONS_DIR)][0] == "001_init.sql"
    reminders_dir = MIGRATIONS_DIR.parents[1] / "reminders" / "migrations"
    assert migrate(scratch, reminders_dir) == ["001_init.sql"]
    assert "reminders" in _tables(scratch)
