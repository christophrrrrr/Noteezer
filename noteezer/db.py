import os
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def database_path() -> Path:
    data_dir = Path(os.environ.get("DATA_DIR", "data"))
    return data_dir / "noteezer.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = database_path() if path is None else path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    _add_folders_to_items(connection)
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Columns added after a table first shipped. CREATE TABLE IF NOT EXISTS
    # does not change a table that already exists, so older files get them here.
    _add_column(connection, "reminders", "repeat_daily", "INTEGER NOT NULL DEFAULT 0")
    _add_column(connection, "reminders", "repeat_until", "TEXT")
    _add_column(connection, "sort_results", "proposed_name", "TEXT")
    _add_column(
        connection, "sort_results", "proposed_parent_id", "INTEGER REFERENCES folders(id) ON DELETE SET NULL"
    )
    _add_column(connection, "sort_results", "suggested_remind_at", "TEXT")
    _add_column(connection, "users", "timezone", "TEXT")
    _add_column(connection, "items", "split_from", "INTEGER REFERENCES items(id) ON DELETE SET NULL")
    _add_column(connection, "items", "hidden", "INTEGER NOT NULL DEFAULT 0")
    connection.commit()


def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _add_folders_to_items(connection: sqlite3.Connection) -> None:
    # Databases from before folders have no items.folder_id and a kind CHECK
    # without 'idea'. SQLite cannot change a CHECK in place, so the table is
    # rebuilt. Foreign keys are off during the swap so dropping the old table
    # does not cascade into reminders and item_tags.
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(items)")}
    if not columns or "folder_id" in columns:
        return
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.executescript(
            """
            BEGIN;
            CREATE TABLE items_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
                body TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('note', 'idea', 'task', 'link', 'reminder')),
                task_done INTEGER NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (kind = 'task' OR task_done = 0)
            );
            INSERT INTO items_new (id, user_id, body, kind, task_done, created_at, updated_at)
            SELECT id, user_id, body, kind, task_done, created_at, updated_at FROM items;
            DROP TABLE items;
            ALTER TABLE items_new RENAME TO items;
            COMMIT;
            """
        )
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
