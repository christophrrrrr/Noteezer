import sqlite3
from datetime import date, datetime, timedelta, timezone

SNOOZE_FOR = timedelta(hours=1)


def initial_status(remind_at: datetime, now: datetime) -> str:
    if remind_at <= now:
        return "due"
    return "scheduled"


def next_occurrence(remind_at: datetime, repeat_until: date | None) -> datetime | None:
    upcoming = remind_at + timedelta(days=1)
    if repeat_until is not None and upcoming.astimezone(timezone.utc).date() > repeat_until:
        return None
    return upcoming


def schedule(
    connection: sqlite3.Connection,
    item_id: int,
    remind_at: datetime,
    now: datetime,
    repeat_daily: bool = False,
    repeat_until: date | None = None,
) -> str:
    row = connection.execute("SELECT kind FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["kind"] != "reminder":
        raise ValueError("only a reminder item can be scheduled")
    if repeat_until is not None and not repeat_daily:
        raise ValueError("an end date needs a daily reminder")
    if (
        repeat_until is not None
        and repeat_until < remind_at.astimezone(timezone.utc).date()
    ):
        raise ValueError("the end date has to be on or after the start")
    status = initial_status(remind_at, now)
    until = repeat_until.isoformat() if repeat_until else None
    connection.execute(
        """
        INSERT INTO reminders (item_id, remind_at, status, repeat_daily, repeat_until)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            remind_at = excluded.remind_at,
            status = excluded.status,
            snoozed_until = NULL,
            repeat_daily = excluded.repeat_daily,
            repeat_until = excluded.repeat_until
        """,
        (
            item_id,
            remind_at.astimezone(timezone.utc).isoformat(),
            status,
            1 if repeat_daily else 0,
            until,
        ),
    )
    return status


def refresh(connection: sqlite3.Connection, user_id: int, now: datetime) -> None:
    stamp = now.astimezone(timezone.utc).isoformat()
    connection.execute(
        """
        UPDATE reminders
        SET status = 'due', snoozed_until = NULL
        WHERE status = 'snoozed' AND snoozed_until <= ?
          AND item_id IN (SELECT id FROM items WHERE user_id = ?)
        """,
        (stamp, user_id),
    )
    connection.execute(
        """
        UPDATE reminders
        SET status = 'due'
        WHERE status = 'scheduled' AND remind_at <= ?
          AND item_id IN (SELECT id FROM items WHERE user_id = ?)
        """,
        (stamp, user_id),
    )


def complete(connection: sqlite3.Connection, user_id: int, item_id: int, now: datetime) -> str:
    row = _owned(connection, user_id, item_id)
    if not row["repeat_daily"]:
        _set_status(connection, item_id, "done", None, row["remind_at"])
        return "done"
    current = _parse(row["remind_at"])
    until = date.fromisoformat(row["repeat_until"]) if row["repeat_until"] else None
    upcoming = next_occurrence(current, until)
    if upcoming is None:
        _set_status(connection, item_id, "done", None, row["remind_at"])
        return "done"
    status = initial_status(upcoming, now)
    _set_status(connection, item_id, status, None, upcoming.astimezone(timezone.utc).isoformat())
    return status


def snooze(connection: sqlite3.Connection, user_id: int, item_id: int, now: datetime) -> datetime:
    row = _owned(connection, user_id, item_id)
    if row["status"] == "done":
        raise ValueError("a finished reminder cannot be snoozed")
    until = now.astimezone(timezone.utc) + SNOOZE_FOR
    _set_status(connection, item_id, "snoozed", until.isoformat(), row["remind_at"])
    return until


def _owned(connection: sqlite3.Connection, user_id: int, item_id: int) -> sqlite3.Row:
    row = connection.execute(
        """
        SELECT r.remind_at, r.status, r.repeat_daily, r.repeat_until
        FROM reminders r
        JOIN items i ON i.id = r.item_id
        WHERE r.item_id = ? AND i.user_id = ?
        """,
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("reminder not found")
    return row


def _set_status(
    connection: sqlite3.Connection,
    item_id: int,
    status: str,
    snoozed_until: str | None,
    remind_at: str,
) -> None:
    connection.execute(
        """
        UPDATE reminders
        SET status = ?, snoozed_until = ?, remind_at = ?
        WHERE item_id = ?
        """,
        (status, snoozed_until, remind_at, item_id),
    )
    connection.commit()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
