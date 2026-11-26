"""The reminder rules: when a reminder is due, snoozed, repeated, or done.

Moved here from the inbox service. Every row carries the owner's user_id, so
ownership is checked in this database without asking inbox (ADR-6). Every
function takes `now`, so the rules are tested with a fixed clock.
"""

from datetime import date, datetime, timedelta, timezone

from app.db import Connection, Row

SNOOZE_FOR = timedelta(hours=1)
COLUMNS = "item_id, user_id, remind_at, status, snoozed_until, repeat_daily, repeat_until, snoozed_count"


class ReminderExists(Exception):
    """schedule(keep_existing=True) found a time that must not be moved."""


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
    connection: Connection,
    user_id: int,
    item_id: int,
    remind_at: datetime,
    now: datetime,
    repeat_daily: bool = False,
    repeat_until: date | None = None,
    keep_existing: bool = False,
) -> dict:
    """Gives an item its time, or moves it. Returns the stored reminder.

    keep_existing is for automatic sorting: a time the user already set is
    never replaced by one read from the text.
    """
    if remind_at.tzinfo is None:
        raise ValueError("the time needs a time zone")
    if repeat_until is not None and not repeat_daily:
        raise ValueError("an end date needs a daily reminder")
    if repeat_until is not None and repeat_until < remind_at.astimezone(timezone.utc).date():
        raise ValueError("the end date has to be on or after the start")
    existing = connection.execute("SELECT user_id FROM reminders WHERE item_id = ?", (item_id,)).fetchone()
    if existing is not None and int(existing["user_id"]) != user_id:
        raise LookupError("reminder not found")
    if existing is not None and keep_existing:
        raise ReminderExists()
    connection.execute(
        """
        MERGE reminders WITH (HOLDLOCK) AS r
        USING (SELECT ? AS item_id, ? AS user_id, ? AS remind_at, ? AS status,
                      ? AS repeat_daily, ? AS repeat_until) AS s
        ON r.item_id = s.item_id
        WHEN MATCHED THEN UPDATE SET
            remind_at = s.remind_at,
            status = s.status,
            snoozed_until = NULL,
            repeat_daily = s.repeat_daily,
            repeat_until = s.repeat_until
        WHEN NOT MATCHED THEN
            INSERT (item_id, user_id, remind_at, status, repeat_daily, repeat_until)
            VALUES (s.item_id, s.user_id, s.remind_at, s.status, s.repeat_daily, s.repeat_until);
        """,
        (
            item_id,
            user_id,
            _iso(remind_at),
            initial_status(remind_at, now),
            1 if repeat_daily else 0,
            repeat_until.isoformat() if repeat_until else None,
        ),
    )
    connection.commit()
    return _owned(connection, user_id, item_id)


def remove(connection: Connection, user_id: int, item_id: int) -> bool:
    cursor = connection.execute("DELETE FROM reminders WHERE item_id = ? AND user_id = ?", (item_id, user_id))
    connection.commit()
    return cursor.rowcount == 1


def refresh(connection: Connection, user_id: int, now: datetime) -> None:
    stamp = _iso(now)
    connection.execute(
        """
        UPDATE reminders SET status = 'due', snoozed_until = NULL
        WHERE user_id = ? AND status = 'snoozed' AND snoozed_until <= ?
        """,
        (user_id, stamp),
    )
    connection.execute(
        "UPDATE reminders SET status = 'due' WHERE user_id = ? AND status = 'scheduled' AND remind_at <= ?",
        (user_id, stamp),
    )
    connection.commit()


def list_for(connection: Connection, user_id: int, now: datetime, item_ids: list[int] | None = None) -> list[dict]:
    """The user's reminders, after the ones whose time has come became due."""
    refresh(connection, user_id, now)
    # COLUMNS is a constant and only "?" placeholders are added; values are parameters.
    sql = f"SELECT {COLUMNS} FROM reminders WHERE user_id = ?"  # noqa: S608
    parameters: list[object] = [user_id]
    if item_ids:
        sql += " AND item_id IN (" + ",".join("?" for _ in item_ids) + ")"
        parameters += item_ids
    rows = connection.execute(sql + " ORDER BY remind_at", parameters).fetchall()
    return [_as_dict(row) for row in rows]


def complete(connection: Connection, user_id: int, item_id: int, now: datetime) -> dict:
    row = _owned_row(connection, user_id, item_id)
    if not row["repeat_daily"]:
        return _set_status(connection, user_id, item_id, "done", None, row["remind_at"])
    until = date.fromisoformat(row["repeat_until"]) if row["repeat_until"] else None
    # Done counts for every day that has already passed: ticked three days
    # late, it moves to the next time still ahead, not to a day that is
    # already over and would be due again at once.
    upcoming = next_occurrence(_parse(row["remind_at"]), until)
    while upcoming is not None and upcoming <= now:
        upcoming = next_occurrence(upcoming, until)
    if upcoming is None:
        return _set_status(connection, user_id, item_id, "done", None, row["remind_at"])
    return _set_status(connection, user_id, item_id, initial_status(upcoming, now), None, _iso(upcoming))


def snooze(connection: Connection, user_id: int, item_id: int, now: datetime) -> dict:
    # Only a due reminder can be put off. One that is not due yet, or is still
    # snoozed, has nothing to put off; refresh first, so one whose time has just
    # come counts as due even if nobody listed it since.
    refresh(connection, user_id, now)
    row = _owned_row(connection, user_id, item_id)
    if row["status"] == "done":
        raise ValueError("a finished reminder cannot be snoozed")
    if row["status"] != "due":
        raise ValueError("only a due reminder can be snoozed")
    # Each snooze is counted (migration 002), so the UI can show "snoozed 2x".
    connection.execute(
        "UPDATE reminders SET status = 'snoozed', snoozed_until = ?, snoozed_count = snoozed_count + 1 "
        "WHERE item_id = ? AND user_id = ?",
        (_iso(now + SNOOZE_FOR), item_id, user_id),
    )
    connection.commit()
    return _owned(connection, user_id, item_id)


def _owned(connection: Connection, user_id: int, item_id: int) -> dict:
    return _as_dict(_owned_row(connection, user_id, item_id))


def _owned_row(connection: Connection, user_id: int, item_id: int) -> Row:
    row = connection.execute(
        f"SELECT {COLUMNS} FROM reminders WHERE item_id = ? AND user_id = ?",  # noqa: S608 (COLUMNS is a constant)
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("reminder not found")
    return row


def _set_status(
    connection: Connection,
    user_id: int,
    item_id: int,
    status: str,
    snoozed_until: str | None,
    remind_at: str,
) -> dict:
    connection.execute(
        "UPDATE reminders SET status = ?, snoozed_until = ?, remind_at = ? WHERE item_id = ? AND user_id = ?",
        (status, snoozed_until, remind_at, item_id, user_id),
    )
    connection.commit()
    return _owned(connection, user_id, item_id)


def _as_dict(row: Row) -> dict:
    return {
        "item_id": int(row["item_id"]),
        "remind_at": row["remind_at"],
        "status": row["status"],
        "snoozed_until": row["snoozed_until"],
        "repeat_daily": bool(row["repeat_daily"]),
        "repeat_until": row["repeat_until"],
        "snoozed_count": int(row["snoozed_count"]),
    }


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
