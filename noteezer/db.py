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
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _rebuild_items(connection)
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
    connection.commit()


def _add_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


ITEM_COLUMNS = ("id", "user_id", "body", "kind", "task_done", "created_at", "updated_at", "split_from", "hidden")


def _rebuild_items(connection: sqlite3.Connection) -> None:
    """Brings an older items table to the current shape.

    Before folders, items had a kind CHECK without 'idea'. Between 2026-09-29
    and 2026-10-03 an item had one folder in items.folder_id; now an item can
    be in many folders through item_folders. SQLite cannot change a CHECK or
    drop a column with a foreign key in place, so the table is rebuilt, and
    every old folder_id becomes a row in item_folders. Foreign keys are off
    during the swap so dropping the old table does not cascade into reminders,
    tags, and images.
    """
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'items'").fetchone()
    columns = [info["name"] for info in connection.execute("PRAGMA table_info(items)")]
    if "folder_id" not in columns and "'idea'" in row["sql"]:
        return
    kept = ", ".join(column for column in ITEM_COLUMNS if column in columns)
    memberships = (
        "INSERT OR IGNORE INTO item_folders (item_id, folder_id) "
        "SELECT id, folder_id FROM items WHERE folder_id IS NOT NULL;"
        if "folder_id" in columns
        else ""
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    try:
        connection.executescript(
            f"""
            BEGIN;
            CREATE TABLE items_new (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                body TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('note', 'idea', 'task', 'link', 'reminder')),
                task_done INTEGER NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                split_from INTEGER REFERENCES items(id) ON DELETE SET NULL,
                hidden INTEGER NOT NULL DEFAULT 0,
                CHECK (kind = 'task' OR task_done = 0)
            );
            {memberships}
            INSERT INTO items_new ({kept}) SELECT {kept} FROM items;
            DROP TABLE items;
            ALTER TABLE items_new RENAME TO items;
            CREATE INDEX IF NOT EXISTS idx_items_user_created ON items (user_id, created_at);
            COMMIT;
            """
        )
    except sqlite3.Error:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys = ON")
