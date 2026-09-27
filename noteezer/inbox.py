import sqlite3
from datetime import datetime, timezone

from noteezer.classify import ClassificationError, classify
from noteezer.reminders import schedule


def save_item(
    connection: sqlite3.Connection,
    user_id: int,
    body: str,
    remind_at: datetime | None,
    now: datetime,
    forced_kind: str | None = None,
) -> int:
    classified = classify(body, forced_kind)
    if classified.kind == "reminder" and remind_at is None:
        raise ClassificationError("Pick a date and time for a reminder.")
    stamp = now.astimezone(timezone.utc).isoformat()
    cursor = connection.execute(
        """
        INSERT INTO items (user_id, body, kind, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (user_id, classified.body, classified.kind, stamp, stamp),
    )
    item_id = int(cursor.lastrowid)
    _attach_tags(connection, user_id, item_id, classified.tags)
    if classified.kind == "reminder" and remind_at is not None:
        schedule(connection, item_id, remind_at, now)
    connection.commit()
    return item_id


def list_items(connection: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = connection.execute(
        """
        SELECT i.id, i.body, i.kind, i.created_at, r.remind_at, r.status
        FROM items i
        LEFT JOIN reminders r ON r.item_id = i.id
        WHERE i.user_id = ?
        ORDER BY CASE WHEN r.status = 'due' THEN 0 ELSE 1 END, i.created_at DESC
        """,
        (user_id,),
    ).fetchall()
    tags_by_item: dict[int, list[str]] = {int(row["id"]): [] for row in rows}
    if tags_by_item:
        placeholders = ",".join("?" for _ in tags_by_item)
        tag_rows = connection.execute(
            f"""
            SELECT it.item_id, t.name
            FROM item_tags it
            JOIN tags t ON t.id = it.tag_id
            WHERE it.item_id IN ({placeholders})
            ORDER BY t.name
            """,
            tuple(tags_by_item),
        ).fetchall()
        for tag_row in tag_rows:
            tags_by_item[int(tag_row["item_id"])].append(tag_row["name"])
    return [
        {
            "id": int(row["id"]),
            "body": row["body"],
            "kind": row["kind"],
            "created_at": row["created_at"],
            "remind_at": row["remind_at"],
            "status": row["status"],
            "tags": tags_by_item[int(row["id"])],
        }
        for row in rows
    ]


def _attach_tags(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    tags: tuple[str, ...],
) -> None:
    for name in tags:
        connection.execute(
            """
            INSERT INTO tags (user_id, name) VALUES (?, ?)
            ON CONFLICT(user_id, name) DO NOTHING
            """,
            (user_id, name),
        )
        tag_id = connection.execute(
            "SELECT id FROM tags WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO item_tags (item_id, tag_id) VALUES (?, ?)",
            (item_id, int(tag_id)),
        )
