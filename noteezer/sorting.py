import logging
import os
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Protocol

from noteezer.classify import classify
from noteezer.folders import FolderError, create_folder, find_folder, list_folders
from noteezer.images import Image, readable_image, record_reading
from noteezer.inbox import (
    change_plain_note_kind,
    file_unsorted_item,
    make_reminder,
    move_item,
    schedule_unset_reminder,
    split_item,
    undo_split,
)
from noteezer.links import LinkPreview, first_url, save_preview, stored_preview
from noteezer.users import user_timezone

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
class Note:
    body: str
    tags: tuple[str, ...] = ()
    links: tuple[LinkPreview, ...] = ()
    kind: str = "note"
    now: datetime | None = None  # in the user's timezone, so "friday 3pm" can be read
    image: Image | None = None  # only when the user let AI read it


@dataclass(frozen=True)
class NewFolder:
    name: str
    parent_id: int | None


@dataclass(frozen=True)
class Part:
    text: str
    kind: str | None
    folder_id: int | None
    confidence: float


@dataclass(frozen=True)
class Suggestion:
    folder_id: int | None
    confidence: float
    new_folder: NewFolder | None = None
    engine: str | None = None
    kind: str | None = None
    remind_at: datetime | None = None
    parts: tuple[Part, ...] = ()
    image_title: str | None = None
    image_text: str | None = None


class Sorter(Protocol):
    name: str

    def suggest(self, note: Note, folders: list[FolderOption]) -> Suggestion: ...


class RulesSorter:
    """Files a line into a folder whose name it mentions or tags.

    Link titles count as text, so a video called "Climbing tips" matches a
    Climbing folder. When all matches sit on one branch (Game and Game / Maps),
    the deepest one wins with high confidence. Matches on different branches
    are a guess, so the item waits in Unsorted with a suggestion.
    """

    name = "rules"

    def suggest(self, note: Note, folders: list[FolderOption]) -> Suggestion:
        text = " ".join([note.body, *(link.title for link in note.links)])
        tag_keys = {_key(tag) for tag in note.tags}
        matches = [
            folder for folder in folders if _key(folder.name) in tag_keys or _mentions(text, folder.name)
        ]
        if not matches:
            return Suggestion(None, 0.0, engine=self.name)
        deepest = max(matches, key=lambda folder: folder.path.count(" / "))
        one_branch = all(
            deepest.path == folder.path or deepest.path.startswith(folder.path + " / ")
            for folder in matches
        )
        return Suggestion(deepest.id, 0.9 if one_branch else 0.5, engine=self.name)


def sorter_from_env() -> Sorter:
    # "auto" (the default) uses Gemini when GEMINI_API_KEY is set and the
    # rules otherwise, so the app always starts without a key.
    choice = os.environ.get("SORTER", "auto").strip().lower()
    has_key = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if choice in ("auto", "gemini") and has_key:
        from noteezer.gemini import GeminiSorter

        return GeminiSorter.from_env()
    if choice == "gemini":
        log.warning("SORTER=gemini but GEMINI_API_KEY is not set, sorting with the rules instead")
    elif choice not in ("auto", "rules"):
        log.warning("Unknown SORTER %r, sorting with the rules instead", choice)
    return RulesSorter()


def sort_pending(
    connection: sqlite3.Connection,
    sorter: Sorter,
    now: datetime,
    limit: int = 20,
    fetch_preview: Callable[[str], LinkPreview | None] | None = None,
) -> int:
    # Pending means never sorted (or due for a retry) and either still in
    # Unsorted while the owner has a folder to sort into, a reminder saved
    # without a time, or an image AI may read, wherever it is. Split originals
    # are hidden and skipped.
    # fetch_preview is passed by the worker; without it no link is fetched.
    rows = connection.execute(
        """
        SELECT i.id, i.user_id, i.body, i.kind, i.folder_id, i.split_from, r.attempts
        FROM items i
        LEFT JOIN sort_results r ON r.item_id = i.id
        WHERE i.hidden = 0
          AND (r.item_id IS NULL OR (r.status = 'retry' AND r.retry_at <= ?))
          AND (
            (i.folder_id IS NULL AND EXISTS (SELECT 1 FROM folders f WHERE f.user_id = i.user_id))
            OR (i.kind = 'reminder' AND NOT EXISTS (SELECT 1 FROM reminders m WHERE m.item_id = i.id))
            OR EXISTS (SELECT 1 FROM images g WHERE g.item_id = i.id AND g.ai_allowed = 1 AND g.read_at IS NULL)
          )
        ORDER BY i.id
        LIMIT ?
        """,
        (_iso(now), limit),
    ).fetchall()
    options_by_user: dict[int, list[FolderOption]] = {}
    zones: dict[int, tzinfo] = {}
    for row in rows:
        user_id = int(row["user_id"])
        if user_id not in options_by_user:
            options_by_user[user_id] = [
                FolderOption(entry["id"], entry["name"], entry["path"])
                for entry in list_folders(connection, user_id)
            ]
            zones[user_id] = user_timezone(connection, user_id)
        options = options_by_user[user_id]
        item_id = int(row["id"])
        attempts = int(row["attempts"] or 0) + 1
        note = Note(
            body=row["body"],
            tags=classify(row["body"]).tags,
            links=_links(connection, item_id, row["body"], fetch_preview, now),
            kind=row["kind"],
            now=now.astimezone(zones[user_id]),
            image=readable_image(connection, item_id),
        )
        try:
            suggestion = sorter.suggest(note, options)
        except Exception as error:  # an engine can fail on network or quota; the item is retried later
            log.warning("Sorting item %s with %s failed: %s", item_id, sorter.name, error)
            _record_failure(connection, item_id, sorter.name, attempts, str(error), now)
            continue
        if note.image is not None:
            # What the engine read is kept with the image. A screenshot is one
            # thing, so it is never split into parts.
            if suggestion.image_title or suggestion.image_text:
                record_reading(connection, user_id, item_id, suggestion.image_title, suggestion.image_text, now)
            suggestion = replace(suggestion, parts=())
        _apply(connection, user_id, row, suggestion, options, sorter.name, attempts, now)
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


def accept_proposal(connection: sqlite3.Connection, user_id: int, item_id: int, now: datetime) -> int:
    row = connection.execute(
        """
        SELECT r.proposed_name, r.proposed_parent_id
        FROM sort_results r
        JOIN items i ON i.id = r.item_id
        WHERE r.item_id = ? AND i.user_id = ? AND r.status = 'review' AND r.proposed_name IS NOT NULL
        """,
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("no new folder to create")
    name = row["proposed_name"]
    parent_id = row["proposed_parent_id"]
    try:
        folder_id = create_folder(connection, user_id, name, now, parent_id=parent_id)
    except FolderError:
        # Another line may have proposed the same folder, and it was created first.
        existing = find_folder(connection, user_id, name, parent_id)
        if existing is None:
            raise
        folder_id = existing
    move_item(connection, user_id, item_id, folder_id)
    connection.execute("UPDATE sort_results SET status = 'accepted' WHERE item_id = ?", (item_id,))
    connection.commit()
    return folder_id


def accept_reminder(connection: sqlite3.Connection, user_id: int, item_id: int, now: datetime) -> datetime:
    row = connection.execute(
        """
        SELECT r.suggested_remind_at
        FROM sort_results r
        JOIN items i ON i.id = r.item_id
        WHERE r.item_id = ? AND i.user_id = ? AND r.suggested_remind_at IS NOT NULL
        """,
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("no reminder time to accept")
    remind_at = datetime.fromisoformat(row["suggested_remind_at"])
    make_reminder(connection, user_id, item_id, remind_at, now)
    connection.execute("UPDATE sort_results SET suggested_remind_at = NULL WHERE item_id = ?", (item_id,))
    connection.commit()
    return remind_at


def undo_auto_split(connection: sqlite3.Connection, user_id: int, item_id: int) -> int:
    # The original comes back as one line and is marked as decided, so a new
    # folder does not send it to the sorter again and split it a second time.
    original = undo_split(connection, user_id, item_id)
    connection.execute("UPDATE sort_results SET status = 'accepted' WHERE item_id = ?", (original,))
    connection.commit()
    return original


def resort_if_waiting(connection: sqlite3.Connection, user_id: int, item_id: int) -> bool:
    # After an edit turns a line into a reminder without a time, its old sort
    # result is dropped so the sorter reads the new text for a time.
    cursor = connection.execute(
        """
        DELETE FROM sort_results
        WHERE item_id IN (
            SELECT i.id FROM items i
            WHERE i.id = ? AND i.user_id = ? AND i.kind = 'reminder'
              AND NOT EXISTS (SELECT 1 FROM reminders m WHERE m.item_id = i.id)
        )
        """,
        (item_id, user_id),
    )
    connection.commit()
    return cursor.rowcount == 1


def requeue_unsorted(connection: sqlite3.Connection, user_id: int, only_unmatched: bool = False) -> int:
    # only_unmatched is used when a folder is created or renamed: lines that
    # matched nothing may match the new name. Lines that already have a
    # suggestion are left alone so an AI engine is not asked again for all of them.
    sql = """
        DELETE FROM sort_results
        WHERE item_id IN (SELECT id FROM items WHERE user_id = ? AND folder_id IS NULL)
    """
    if only_unmatched:
        sql += " AND status = 'review' AND folder_id IS NULL AND proposed_name IS NULL"
    cursor = connection.execute(sql, (user_id,))
    connection.commit()
    return cursor.rowcount


def results_for(connection: sqlite3.Connection, user_id: int) -> dict[int, dict]:
    rows = connection.execute(
        """
        SELECT r.item_id, r.status, r.folder_id, r.confidence, r.engine,
               r.proposed_name, r.proposed_parent_id, r.suggested_remind_at
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
            "proposed_name": row["proposed_name"],
            "proposed_parent_id": row["proposed_parent_id"],
            "suggested_remind_at": row["suggested_remind_at"],
        }
        for row in rows
    }


def _links(
    connection: sqlite3.Connection,
    item_id: int,
    body: str,
    fetch_preview: Callable[[str], LinkPreview | None] | None,
    now: datetime,
) -> tuple[LinkPreview, ...]:
    url = first_url(body)
    if url is None:
        return ()
    stored = stored_preview(connection, item_id)
    if stored is not None and stored[0] == url:
        preview = stored[1]
    elif fetch_preview is not None:
        preview = fetch_preview(url)
        save_preview(connection, item_id, url, preview, now)
    else:
        preview = None
    return (preview,) if preview else ()


def _apply(
    connection: sqlite3.Connection,
    user_id: int,
    row: sqlite3.Row,
    suggestion: Suggestion,
    options: list[FolderOption],
    engine: str,
    attempts: int,
    now: datetime,
) -> None:
    item_id = int(row["id"])
    engine = suggestion.engine or engine
    known = {option.id for option in options}
    unsorted = row["folder_id"] is None

    parts = [part for part in suggestion.parts if part.text.strip()]
    if unsorted and row["split_from"] is None and len(parts) >= 2:
        _split(connection, user_id, item_id, parts, known, engine, attempts, now)
        return

    # An engine may only point at one of the user's own folders, and only an
    # item that is still in Unsorted is filed.
    folder_id = suggestion.folder_id if unsorted and suggestion.folder_id in known else None
    proposal = suggestion.new_folder if unsorted and folder_id is None else None
    if proposal is not None and proposal.parent_id not in known:
        proposal = NewFolder(proposal.name, None)
    status = "review"
    if folder_id is not None and suggestion.confidence >= FILE_AT:
        try:
            if file_unsorted_item(connection, user_id, item_id, folder_id):
                status = "filed"
        except LookupError:
            folder_id = None

    if suggestion.kind in ("idea", "task"):
        change_plain_note_kind(connection, user_id, item_id, suggestion.kind)

    # A "remind ..." line saved without a time gets the time straight away.
    # Any other line with a time only gets a suggestion to accept.
    suggested_at = None
    if suggestion.remind_at is not None and suggestion.remind_at > now:
        if not schedule_unset_reminder(connection, user_id, item_id, suggestion.remind_at, now):
            if row["kind"] != "reminder":
                suggested_at = _iso(suggestion.remind_at)

    _record(
        connection,
        item_id,
        status,
        folder_id,
        suggestion.confidence,
        engine,
        attempts,
        None,
        None,
        now,
        proposal,
        suggested_at,
    )


def _split(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    parts: list[Part],
    known: set[int],
    engine: str,
    attempts: int,
    now: datetime,
) -> None:
    # Each part with a confident folder is filed at once. A part with a
    # weaker guess gets it as a suggestion. A part with no folder gets no
    # result, so the worker sorts it on its own next.
    part_ids = split_item(connection, user_id, item_id, [(part.text, part.kind) for part in parts], now)
    for part_id, part in zip(part_ids, parts):
        folder_id = part.folder_id if part.folder_id in known else None
        if folder_id is None:
            continue
        status = "review"
        if part.confidence >= FILE_AT and file_unsorted_item(connection, user_id, part_id, folder_id):
            status = "filed"
        _record(connection, part_id, status, folder_id, part.confidence, engine, 1, None, None, now)
    _record(connection, item_id, "review", None, None, engine, attempts, None, None, now)


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
    proposal: NewFolder | None = None,
    suggested_remind_at: str | None = None,
) -> None:
    # Only a row waiting for a retry may be overwritten, so two runs that
    # picked up the same item cannot replace each other's result.
    connection.execute(
        """
        INSERT INTO sort_results
            (item_id, status, folder_id, confidence, engine, attempts, retry_at, error, sorted_at,
             proposed_name, proposed_parent_id, suggested_remind_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            status = excluded.status,
            folder_id = excluded.folder_id,
            confidence = excluded.confidence,
            engine = excluded.engine,
            attempts = excluded.attempts,
            retry_at = excluded.retry_at,
            error = excluded.error,
            sorted_at = excluded.sorted_at,
            proposed_name = excluded.proposed_name,
            proposed_parent_id = excluded.proposed_parent_id,
            suggested_remind_at = excluded.suggested_remind_at
        WHERE sort_results.status = 'retry'
        """,
        (
            item_id,
            status,
            folder_id,
            confidence,
            engine,
            attempts,
            retry_at,
            error,
            _iso(now),
            proposal.name if proposal else None,
            proposal.parent_id if proposal else None,
            suggested_remind_at,
        ),
    )
    connection.commit()


def _mentions(body: str, name: str) -> bool:
    words = r"\s+".join(re.escape(word) for word in name.split())
    return re.search(rf"(?<!\w){words}(?!\w)", body, re.IGNORECASE) is not None


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()
