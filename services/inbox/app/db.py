"""Connections to SQL Server (Azure SQL in production, a container locally).

The rest of the code was written against sqlite3, so this module keeps that
shape: connection.execute(sql, params) returns a cursor, rows can be read by
column name, and placeholders are written as ?. Only the SQL itself had to
change to the SQL Server dialect.
"""

import logging
import os
import re
import time
from collections.abc import Iterable, Iterator, Sequence

import pymssql

log = logging.getLogger(__name__)

IntegrityError = pymssql.IntegrityError
DatabaseError = pymssql.DatabaseError

# Azure SQL serverless pauses when idle. The first connection after a pause
# gets error 40613 ("database is not currently available") for up to about a
# minute while it resumes, so connecting is retried with a growing wait.
RETRY_ERRORS = ("40613",)
CONNECT_ATTEMPTS = 6
FIRST_WAIT_SECONDS = 2.0


def settings(database: str | None = None) -> dict[str, object]:
    return {
        "server": os.environ.get("DB_SERVER", "localhost"),
        "port": os.environ.get("DB_PORT", "1433"),
        "user": os.environ.get("DB_USER", "sa"),
        "password": os.environ.get("DB_PASSWORD", ""),
        "database": database or os.environ.get("DB_NAME", "noteezer_inbox"),
    }


def connect(
    database: str | None = None,
    autocommit: bool = False,
    attempts: int = CONNECT_ATTEMPTS,
    wait: float = FIRST_WAIT_SECONDS,
) -> "Connection":
    for attempt in range(1, attempts + 1):
        try:
            raw = pymssql.connect(**settings(database), login_timeout=10, autocommit=autocommit)
            return Connection(raw)
        except pymssql.OperationalError as error:
            if attempt == attempts or not any(code in str(error) for code in RETRY_ERRORS):
                raise
            log.warning("Database not available yet (attempt %s of %s), retrying in %.0f s", attempt, attempts, wait)
            time.sleep(wait)
            wait *= 2
    raise AssertionError("unreachable")


class Row(tuple):
    """A result row that can be read by position or by column name."""

    _index: dict[str, int]

    def __new__(cls, values: Sequence[object], index: dict[str, int]) -> "Row":
        row = super().__new__(cls, values)
        row._index = index
        return row

    def __getitem__(self, key):  # type: ignore[override]
        if isinstance(key, str):
            return tuple.__getitem__(self, self._index[key])
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._index)


class Cursor:
    def __init__(self, cursor: "pymssql.Cursor") -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> Row | None:
        values = self._cursor.fetchone()
        return None if values is None else Row(values, self._columns())

    def fetchall(self) -> list[Row]:
        index = self._columns()
        return [Row(values, index) for values in self._cursor.fetchall()]

    def __iter__(self) -> Iterator[Row]:
        return iter(self.fetchall())

    def _columns(self) -> dict[str, int]:
        return {column[0]: position for position, column in enumerate(self._cursor.description or ())}


class Connection:
    def __init__(self, raw: "pymssql.Connection") -> None:
        self._raw = raw

    def execute(self, sql: str, parameters: Iterable[object] = ()) -> Cursor:
        cursor = self._raw.cursor()
        values = tuple(parameters)
        if values:
            cursor.execute(_placeholders(sql), values)
        else:
            cursor.execute(sql)
        return Cursor(cursor)

    def executemany(self, sql: str, rows: Iterable[Iterable[object]]) -> int:
        return sum(self.execute(sql, row).rowcount for row in rows)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


_QUESTION_MARK = re.compile(r"\?")


def _placeholders(sql: str) -> str:
    # pymssql uses %s placeholders, so a literal % has to be doubled first.
    return _QUESTION_MARK.sub("%s", sql.replace("%", "%%"))
