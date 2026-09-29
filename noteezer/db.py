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
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(reminders)")}
    if "repeat_daily" not in columns:
        connection.execute(
            "ALTER TABLE reminders ADD COLUMN repeat_daily INTEGER NOT NULL DEFAULT 0"
        )
    if "repeat_until" not in columns:
        connection.execute("ALTER TABLE reminders ADD COLUMN repeat_until TEXT")


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
