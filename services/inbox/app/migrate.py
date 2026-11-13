"""Applies the numbered SQL files in migrations/ that the database has not seen.

Each file runs once, in number order, inside its own transaction, and is then
recorded in schema_migrations. A file that fails is rolled back completely and
stops the run, so the database is never left half migrated. Files that were
applied are never edited: a change is always a new, higher number.

Run by hand with `python -m app.migrate` from services/inbox. The app also
runs it when it starts.
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from app.db import Connection, connect

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
FILE_NAME = re.compile(r"^(\d+)_[\w-]+\.sql$")
# A line with only GO ends a batch, like in sqlcmd. SQL Server needs it before
# statements such as CREATE VIEW that must start their own batch.
BATCH_SEPARATOR = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)


class MigrationError(Exception):
    pass


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = [path for path in directory.iterdir() if FILE_NAME.match(path.name)]
    files.sort(key=lambda path: int(FILE_NAME.match(path.name).group(1)))
    numbers = [int(FILE_NAME.match(path.name).group(1)) for path in files]
    if len(numbers) != len(set(numbers)):
        raise MigrationError(f"Two migration files in {directory} share a number.")
    return files


def applied_versions(connection: Connection) -> set[str]:
    _ensure_table(connection)
    return {row["version"] for row in connection.execute("SELECT version FROM schema_migrations")}


def migrate(connection: Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Applies what is missing and returns the names of the files it applied."""
    _ensure_table(connection)
    applied = []
    for path in migration_files(directory):
        version = path.stem
        try:
            # Only one process migrates at a time; the lock ends with the transaction.
            connection.execute(
                "EXEC sp_getapplock @Resource = 'schema_migrations', @LockMode = 'Exclusive', "
                "@LockOwner = 'Transaction', @LockTimeout = 60000"
            )
            done = connection.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone()
            if done is not None:
                connection.commit()
                continue
            for batch in split_batches(path.read_text(encoding="utf-8")):
                connection.execute(batch)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            connection.commit()
        except Exception as error:
            connection.rollback()
            raise MigrationError(f"{path.name} failed and was rolled back: {error}") from error
        applied.append(path.name)
    return applied


def split_batches(script: str) -> list[str]:
    return [batch.strip() for batch in BATCH_SEPARATOR.split(script) if batch.strip()]


def _ensure_table(connection: Connection) -> None:
    connection.execute(
        """
        IF OBJECT_ID('schema_migrations', 'U') IS NULL
            CREATE TABLE schema_migrations (
                version NVARCHAR(200) NOT NULL PRIMARY KEY,
                applied_at NVARCHAR(40) NOT NULL
            )
        """
    )
    connection.commit()


def main() -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    connection = connect()
    try:
        applied = migrate(connection)
    finally:
        connection.close()
    print("Applied: " + ", ".join(applied) if applied else "Nothing to apply, the database is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
