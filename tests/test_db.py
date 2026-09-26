import sqlite3
from pathlib import Path

import pytest

from noteezer.db import connect, init_db


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_init_creates_the_five_tables(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    assert _tables(connection) == {"users", "items", "tags", "item_tags", "reminders"}
    connection.close()


def test_init_can_run_twice(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    init_db(connection)
    assert "items" in _tables(connection)
    connection.close()


def test_email_is_unique(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        ("a@example.com", "hash", "2026-09-26T00:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            ("a@example.com", "other", "2026-09-26T00:00:00Z"),
        )
    connection.close()


def test_note_cannot_be_marked_done(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        ("a@example.com", "hash", "2026-09-26T00:00:00Z"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO items (user_id, body, kind, task_done, created_at, updated_at)
            VALUES (1, 'hello', 'note', 1, '2026-09-26T00:00:00Z', '2026-09-26T00:00:00Z')
            """
        )
    connection.close()


def test_one_reminder_row_per_item(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        ("a@example.com", "hash", "2026-09-26T00:00:00Z"),
    )
    connection.execute(
        """
        INSERT INTO items (user_id, body, kind, created_at, updated_at)
        VALUES (1, 'call landlord', 'reminder', '2026-09-26T00:00:00Z', '2026-09-26T00:00:00Z')
        """
    )
    connection.execute(
        """
        INSERT INTO reminders (item_id, remind_at, status)
        VALUES (1, '2026-09-27T15:00:00Z', 'scheduled')
        """
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            INSERT INTO reminders (item_id, remind_at, status)
            VALUES (1, '2026-09-28T15:00:00Z', 'scheduled')
            """
        )
    connection.close()


def test_tag_name_is_unique_per_user_only(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        ("a@example.com", "hash", "2026-09-26T00:00:00Z"),
    )
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        ("b@example.com", "hash", "2026-09-26T00:00:00Z"),
    )
    connection.execute("INSERT INTO tags (user_id, name) VALUES (1, 'groceries')")
    connection.execute("INSERT INTO tags (user_id, name) VALUES (2, 'groceries')")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO tags (user_id, name) VALUES (1, 'groceries')")
    connection.close()


def test_deleting_an_item_removes_its_reminder(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    connection.execute(
        "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
        ("a@example.com", "hash", "2026-09-26T00:00:00Z"),
    )
    connection.execute(
        """
        INSERT INTO items (user_id, body, kind, created_at, updated_at)
        VALUES (1, 'call landlord', 'reminder', '2026-09-26T00:00:00Z', '2026-09-26T00:00:00Z')
        """
    )
    connection.execute(
        """
        INSERT INTO reminders (item_id, remind_at, status)
        VALUES (1, '2026-09-27T15:00:00Z', 'scheduled')
        """
    )
    connection.execute("DELETE FROM items WHERE id = 1")
    remaining = connection.execute("SELECT COUNT(*) AS n FROM reminders").fetchone()
    assert remaining["n"] == 0
    connection.close()
