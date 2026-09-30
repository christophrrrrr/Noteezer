import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from noteezer.classify import classify
from noteezer.folders import list_folders
from noteezer.inbox import file_unsorted_item, move_item

FILE_AT = 0.8
MAX_ATTEMPTS = 5
RETRY_AFTER = timedelta(minutes=1)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderOption:
    id: int
    name: str
    path: str


@dataclass(frozen=True)
class Suggestion:
    folder_id: int | None
    confidence: float


class Sorter(Protocol):
    name: str

    def suggest(self, body: str, tags: tuple[str, ...], folders: list[FolderOption]) -> Suggestion: ...


class RulesSorter:
    """Files a line into a folder whose name it mentions or tags.

    When all matches sit on one branch (Game and Game / Maps), the deepest one
    wins with high confidence. Matches on different branches are a guess, so
    the item waits in Unsorted with a suggestion.
    """

    name = "rules"

    def suggest(self, body: str, tags: tuple[str, ...], folders: list[FolderOption]) -> Suggestion:
        tag_keys = {_key(tag) for tag in tags}
        matches = [
            folder for folder in folders if _key(folder.name) in tag_keys or _mentions(body, folder.name)
        ]
        if not matches:
            return Suggestion(None, 0.0)
        deepest = max(matches, key=lambda folder: folder.path.count(" / "))
        one_branch = all(
            deepest.path == folder.path or deepest.path.startswith(folder.path + " / ")
            for folder in matches
        )
        return Suggestion(deepest.id, 0.9 if one_branch else 0.5)


def sorter_from_env() -> Sorter:
    choice = os.environ.get("SORTER", "rules").strip().lower()
    if choice != "rules":
        log.warning("Unknown SORTER %r, sorting with the rules instead", choice)
    return RulesSorter()


def sort_pending(
    connection: sqlite3.Connection,
    sorter: Sorter,
    now: datetime,
    limit: int = 20,
) -> int:
    # Pending means: still in Unsorted, never sorted (or due for a retry), and
    # the owner has at least one folder to sort into.
    rows = connection.execute(
        """
        SELECT i.id, i.user_id, i.body, r.attempts
        FROM items i
        LEFT JOIN sort_results r ON r.item_id = i.id
        WHERE i.folder_id IS NULL
          AND (r.item_id IS NULL OR (r.status = 'retry' AND r.retry_at <= ?))
          AND EXISTS (SELECT 1 FROM folders f WHERE f.user_id = i.user_id)
        ORDER BY i.id
        LIMIT ?
        """,
        (_iso(now), limit),
    ).fetchall()
    options_by_user: dict[int, list[FolderOption]] = {}
    for row in rows:
        user_id = int(row["user_id"])
        if user_id not in options_by_user:
            options_by_user[user_id] = [
                FolderOption(entry["id"], entry["name"], entry["path"])
                for entry in list_folders(connection, user_id)
            ]
        options = options_by_user[user_id]
        item_id = int(row["id"])
        attempts = int(row["attempts"] or 0) + 1
        try:
            suggestion = sorter.suggest(row["body"], classify(row["body"]).tags, options)
        except Exception as error:  # an engine can fail on network or quota; the item is retried later
            log.warning("Sorting item %s with %s failed: %s", item_id, sorter.name, error)
            _record_failure(connection, item_id, sorter.name, attempts, str(error), now)
            continue
        _apply(connection, user_id, item_id, suggestion, options, sorter.name, attempts, now)
    return len(rows)


def accept_suggestion(connection: sqlite3.Connection, user_id: int, item_id: int) -> int:
    row = connection.execute(
        """
        SELECT r.folder_id
        FROM sort_results r
        JOIN items i ON i.id = r.item_id
        WHERE r.item_id = ? AND i.user_id = ? AND r.status = 'review' AND r.folder_id IS NOT NULL
        """,
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("no suggestion to accept")
    folder_id = int(row["folder_id"])
    move_item(connection, user_id, item_id, folder_id)
    connection.execute("UPDATE sort_results SET status = 'accepted' WHERE item_id = ?", (item_id,))
    connection.commit()
    return folder_id


def requeue_unsorted(connection: sqlite3.Connection, user_id: int, only_unmatched: bool = False) -> int:
    # only_unmatched is used when a folder is created or renamed: lines that
    # matched nothing may match the new name. Lines that already have a
    # suggestion are left alone so an AI engine is not asked again for all of them.
    sql = """
        DELETE FROM sort_results
        WHERE item_id IN (SELECT id FROM items WHERE user_id = ? AND folder_id IS NULL)
    """
    if only_unmatched:
        sql += " AND status = 'review' AND folder_id IS NULL"
    cursor = connection.execute(sql, (user_id,))
    connection.commit()
    return cursor.rowcount


def results_for(connection: sqlite3.Connection, user_id: int) -> dict[int, dict]:
    rows = connection.execute(
        """
        SELECT r.item_id, r.status, r.folder_id, r.confidence, r.engine
        FROM sort_results r
        JOIN items i ON i.id = r.item_id
        WHERE i.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    return {
        int(row["item_id"]): {
            "status": row["status"],
            "folder_id": row["folder_id"],
            "confidence": row["confidence"],
            "engine": row["engine"],
        }
        for row in rows
    }


def _apply(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    suggestion: Suggestion,
    options: list[FolderOption],
    engine: str,
    attempts: int,
    now: datetime,
) -> None:
    # An engine may only point at one of the user's own folders.
    folder_id = suggestion.folder_id if suggestion.folder_id in {option.id for option in options} else None
    status = "review"
    if folder_id is not None and suggestion.confidence >= FILE_AT:
        try:
            if file_unsorted_item(connection, user_id, item_id, folder_id):
                status = "filed"
        except LookupError:
            folder_id = None
    _record(connection, item_id, status, folder_id, suggestion.confidence, engine, attempts, None, None, now)


def _record_failure(
    connection: sqlite3.Connection,
    item_id: int,
    engine: str,
    attempts: int,
    error: str,
    now: datetime,
) -> None:
    if attempts >= MAX_ATTEMPTS:
        _record(connection, item_id, "failed", None, None, engine, attempts, None, error, now)
        return
    retry_at = now + RETRY_AFTER * 2 ** (attempts - 1)
    _record(connection, item_id, "retry", None, None, engine, attempts, _iso(retry_at), error, now)


def _record(
    connection: sqlite3.Connection,
    item_id: int,
    status: str,
    folder_id: int | None,
    confidence: float | None,
    engine: str,
    attempts: int,
    retry_at: str | None,
    error: str | None,
    now: datetime,
) -> None:
    # Only a row waiting for a retry may be overwritten, so two runs that
    # picked up the same item cannot replace each other's result.
    connection.execute(
        """
        INSERT INTO sort_results
            (item_id, status, folder_id, confidence, engine, attempts, retry_at, error, sorted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            status = excluded.status,
            folder_id = excluded.folder_id,
            confidence = excluded.confidence,
            engine = excluded.engine,
            attempts = excluded.attempts,
            retry_at = excluded.retry_at,
            error = excluded.error,
            sorted_at = excluded.sorted_at
        WHERE sort_results.status = 'retry'
        """,
        (item_id, status, folder_id, confidence, engine, attempts, retry_at, error, _iso(now)),
    )
    connection.commit()


def _mentions(body: str, name: str) -> bool:
    words = r"\s+".join(re.escape(word) for word in name.split())
    return re.search(rf"(?<!\w){words}(?!\w)", body, re.IGNORECASE) is not None


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()
