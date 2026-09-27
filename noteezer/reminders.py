import sqlite3
from datetime import datetime, timezone


def initial_status(remind_at: datetime, now: datetime) -> str:
    if remind_at <= now:
        return "due"
    return "scheduled"


def schedule(
    connection: sqlite3.Connection,
    item_id: int,
    remind_at: datetime,
    now: datetime,
) -> str:
    row = connection.execute("SELECT kind FROM items WHERE id = ?", (item_id,)).fetchone()
    if row is None or row["kind"] != "reminder":
        raise ValueError("only a reminder item can be scheduled")
    status = initial_status(remind_at, now)
    connection.execute(
        "INSERT INTO reminders (item_id, remind_at, status) VALUES (?, ?, ?)",
        (item_id, remind_at.astimezone(timezone.utc).isoformat(), status),
    )
    return status
