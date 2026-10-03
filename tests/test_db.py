import sqlite3
from pathlib import Path

import pytest

from noteezer.db import connect, init_db


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row["name"] for row in rows}


def test_init_creates_every_table(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    assert _tables(connection) == {
        "users",
        "folders",
        "items",
        "item_folders",
        "folder_suggestions",
        "folder_checks",
        "tags",
        "item_tags",
        "reminders",
        "sort_results",
        "link_previews",
        "images",
    }
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


OLD_ITEMS = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'task', 'link', 'reminder')),
    task_done INTEGER NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK (kind = 'task' OR task_done = 0));
CREATE TABLE tags (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL, UNIQUE (user_id, name));
CREATE TABLE item_tags (item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY (item_id, tag_id));
CREATE TABLE reminders (item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    remind_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'due', 'snoozed', 'done')),
    snoozed_until TEXT);
INSERT INTO users VALUES (1, 'a@example.com', 'hash', '2026-09-26T00:00:00Z');
INSERT INTO items VALUES (1, 1, 'remind call', 'reminder', 0, '2026-09-26T00:00:00Z', '2026-09-26T00:00:00Z');
INSERT INTO items VALUES (2, 1, 'buy milk #home', 'task', 1, '2026-09-26T00:00:00Z', '2026-09-26T00:00:00Z');
INSERT INTO tags VALUES (1, 1, 'home');
INSERT INTO item_tags VALUES (2, 1);
INSERT INTO reminders VALUES (1, '2026-09-27T15:00:00Z', 'scheduled', NULL);
"""


def test_old_database_gets_folders_and_keeps_its_rows(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    connection.executescript(OLD_ITEMS)
    init_db(connection)

    columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
    assert "folder_id" not in columns
    assert {"split_from", "hidden"} <= columns
    items = connection.execute("SELECT id, kind, task_done, hidden FROM items ORDER BY id").fetchall()
    assert [tuple(row) for row in items] == [(1, "reminder", 0, 0), (2, "task", 1, 0)]
    assert connection.execute("SELECT COUNT(*) AS n FROM item_folders").fetchone()["n"] == 0
    assert connection.execute("SELECT COUNT(*) AS n FROM reminders").fetchone()["n"] == 1
    assert connection.execute("SELECT COUNT(*) AS n FROM item_tags").fetchone()["n"] == 1
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    connection.execute(
        """
        INSERT INTO items (user_id, body, kind, created_at, updated_at)
        VALUES (1, 'idea game', 'idea', '2026-09-29T00:00:00Z', '2026-09-29T00:00:00Z')
        """
    )
    connection.execute("DELETE FROM items WHERE id = 1")
    assert connection.execute("SELECT COUNT(*) AS n FROM reminders").fetchone()["n"] == 0
    connection.close()


ONE_FOLDER_ITEMS = """
CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE folders (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE items (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'idea', 'task', 'link', 'reminder')),
    task_done INTEGER NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    split_from INTEGER REFERENCES items(id) ON DELETE SET NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    CHECK (kind = 'task' OR task_done = 0));
CREATE INDEX idx_items_folder ON items (user_id, folder_id);
INSERT INTO users VALUES (1, 'a@example.com', 'hash', '2026-09-26T00:00:00Z');
INSERT INTO folders VALUES (1, 1, NULL, 'Table Tennis', '2026-10-01T00:00:00Z');
INSERT INTO items VALUES (1, 1, 1, 'https://youtu.be/serve', 'link', 0, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', NULL, 0);
INSERT INTO items VALUES (2, 1, NULL, 'loose thought', 'note', 0, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', NULL, 0);
"""


def test_a_one_folder_database_moves_each_folder_into_item_folders(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    connection.executescript(ONE_FOLDER_ITEMS)
    init_db(connection)
    init_db(connection)

    columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
    assert "folder_id" not in columns
    memberships = connection.execute("SELECT item_id, folder_id FROM item_folders").fetchall()
    assert [tuple(row) for row in memberships] == [(1, 1)]
    assert [row["body"] for row in connection.execute("SELECT body FROM items ORDER BY id")] == [
        "https://youtu.be/serve",
        "loose thought",
    ]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
