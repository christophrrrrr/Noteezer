"""Migration 002 changes a table that already holds data and keeps every row."""

import shutil
from pathlib import Path

import pytest

from app.db import connect
from app.migrate import MIGRATIONS_DIR, migrate
from tests.conftest import TEST_DB, _drop, _server

SCRATCH_DB = TEST_DB + "_m002"
ROWS = [
    # item_id, user_id, remind_at, status, snoozed_until, repeat_daily, repeat_until
    (1, 7, "2026-11-01T08:00:00+00:00", "scheduled", None, 0, None),
    (2, 7, "2026-11-02T08:00:00+00:00", "snoozed", "2026-11-02T09:00:00+00:00", 0, None),
    (3, 8, "2026-10-05T08:13:00+00:00", "due", None, 1, "2026-10-10"),
    (4, 8, "2026-09-28T14:18:00+00:00", "done", None, 0, None),
]
OLD_COLUMNS = "item_id, user_id, remind_at, status, snoozed_until, repeat_daily, repeat_until"


@pytest.fixture
def scratch():
    server = _server()
    try:
        _drop(server, SCRATCH_DB)
        server.execute(f"CREATE DATABASE [{SCRATCH_DB}]")
    finally:
        server.close()
    connection = connect(SCRATCH_DB)
    yield connection
    connection.close()
    server = _server()
    try:
        _drop(server, SCRATCH_DB)
    finally:
        server.close()


def test_002_keeps_every_row_and_backfills_the_snoozed_one(scratch, tmp_path: Path) -> None:
    # The database as it was on Azure before 002: only 001, with data in it.
    shutil.copy(MIGRATIONS_DIR / "001_init.sql", tmp_path / "001_init.sql")
    assert migrate(scratch, tmp_path) == ["001_init.sql"]
    for row in ROWS:
        scratch.execute(f"INSERT INTO reminders ({OLD_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)", row)
    scratch.commit()

    # Then the real folder: only 002 is new.
    assert migrate(scratch, MIGRATIONS_DIR) == ["002_snoozed_count.sql"]

    after = scratch.execute(f"SELECT {OLD_COLUMNS}, snoozed_count FROM reminders ORDER BY item_id").fetchall()
    assert [tuple(row)[:7] for row in after] == ROWS
    assert [row["snoozed_count"] for row in after] == [0, 1, 0, 0]
    # A second start applies nothing.
    assert migrate(scratch, MIGRATIONS_DIR) == []


def test_new_rows_start_at_zero_snoozes(scratch) -> None:
    migrate(scratch, MIGRATIONS_DIR)
    scratch.execute(f"INSERT INTO reminders ({OLD_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)", ROWS[0])
    scratch.commit()
    assert scratch.execute("SELECT snoozed_count FROM reminders").fetchone()["snoozed_count"] == 0
