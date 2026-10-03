import sqlite3
from datetime import date, datetime, timezone, tzinfo

from noteezer.classify import URL_PATTERN, ClassificationError, classify
from noteezer.folders import require_folder
from noteezer.reminders import refresh, schedule


def save_item(
    connection: sqlite3.Connection,
    user_id: int,
    body: str,
    remind_at: datetime | None,
    now: datetime,
    forced_kind: str | None = None,
    repeat_daily: bool = False,
    repeat_until: date | None = None,
    folder_id: int | None = None,
) -> int:
    classified = classify(body, forced_kind)
    if folder_id is not None:
        require_folder(connection, user_id, folder_id)
    _check_reminder(classified.kind, remind_at, repeat_daily, repeat_until)
    if classified.kind != "reminder":
        repeat_daily = False
        repeat_until = None
    stamp = now.astimezone(timezone.utc).isoformat()
    cursor = connection.execute(
        """
        INSERT INTO items (user_id, folder_id, body, kind, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, folder_id, classified.body, classified.kind, stamp, stamp),
    )
    item_id = int(cursor.lastrowid)
    _attach_tags(connection, user_id, item_id, classified.tags)
    if classified.kind == "reminder" and remind_at is not None:
        schedule(connection, item_id, remind_at, now, repeat_daily, repeat_until)
    connection.commit()
    return item_id


def update_item(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    body: str,
    remind_at: datetime | None,
    now: datetime,
    forced_kind: str | None = None,
    repeat_daily: bool = False,
    repeat_until: date | None = None,
) -> None:
    existing = _owned_item(connection, user_id, item_id)
    classified = classify(body, forced_kind)
    _check_reminder(classified.kind, remind_at, repeat_daily, repeat_until)
    if classified.kind != "reminder":
        repeat_daily = False
        repeat_until = None
    stamp = now.astimezone(timezone.utc).isoformat()
    connection.execute(
        """
        UPDATE items
        SET body = ?, kind = ?,
            task_done = CASE WHEN ? = 'task' THEN task_done ELSE 0 END,
            updated_at = ?
        WHERE id = ? AND user_id = ?
        """,
        (classified.body, classified.kind, classified.kind, stamp, item_id, user_id),
    )
    connection.execute("DELETE FROM item_tags WHERE item_id = ?", (item_id,))
    _attach_tags(connection, user_id, item_id, classified.tags)
    if classified.kind == "reminder" and remind_at is not None:
        schedule(connection, item_id, remind_at, now, repeat_daily, repeat_until)
    elif existing["kind"] == "reminder":
        connection.execute("DELETE FROM reminders WHERE item_id = ?", (item_id,))
    connection.commit()


def delete_item(connection: sqlite3.Connection, user_id: int, item_id: int) -> None:
    cursor = connection.execute(
        "DELETE FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    )
    if cursor.rowcount == 0:
        raise LookupError("item not found")
    connection.commit()


def move_item(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    folder_id: int | None,
) -> None:
    _owned_item(connection, user_id, item_id)
    if folder_id is not None:
        require_folder(connection, user_id, folder_id)
    connection.execute(
        "UPDATE items SET folder_id = ? WHERE id = ? AND user_id = ?",
        (folder_id, item_id, user_id),
    )
    connection.commit()


def file_unsorted_item(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    folder_id: int,
) -> bool:
    # Used by automatic sorting. It only files an item that is still in
    # Unsorted, so it never undoes a move the user made in the meantime.
    require_folder(connection, user_id, folder_id)
    cursor = connection.execute(
        "UPDATE items SET folder_id = ? WHERE id = ? AND user_id = ? AND folder_id IS NULL",
        (folder_id, item_id, user_id),
    )
    connection.commit()
    return cursor.rowcount == 1


def change_plain_note_kind(connection: sqlite3.Connection, user_id: int, item_id: int, kind: str) -> bool:
    # Used by automatic sorting. Only a plain note becomes an idea or a task;
    # links, reminders, and lines that already have a kind are left alone.
    if kind not in ("idea", "task"):
        raise ValueError("a note can only become an idea or a task")
    cursor = connection.execute(
        "UPDATE items SET kind = ? WHERE id = ? AND user_id = ? AND kind = 'note'",
        (kind, item_id, user_id),
    )
    connection.commit()
    return cursor.rowcount == 1


def schedule_unset_reminder(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    remind_at: datetime,
    now: datetime,
) -> bool:
    # Used by automatic sorting for "remind ..." lines saved without a time.
    # A reminder that already has a time is never moved.
    row = connection.execute(
        """
        SELECT i.id FROM items i
        LEFT JOIN reminders r ON r.item_id = i.id
        WHERE i.id = ? AND i.user_id = ? AND i.kind = 'reminder' AND r.item_id IS NULL
        """,
        (item_id, user_id),
    ).fetchone()
    if row is None:
        return False
    schedule(connection, item_id, remind_at, now)
    connection.commit()
    return True


def make_reminder(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    remind_at: datetime,
    now: datetime,
) -> None:
    row = connection.execute(
        "SELECT body FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("item not found")
    update_item(connection, user_id, item_id, row["body"], remind_at, now, forced_kind="reminder")


def split_item(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    parts: list[tuple[str, str | None]],
    now: datetime,
) -> list[int]:
    """Turns one saved line into several. The original is hidden, not deleted.

    Each part is (text, kind). Every part points back with split_from, so
    undo_split can delete the parts and show the original again.
    """
    row = connection.execute(
        "SELECT folder_id, split_from, hidden FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("item not found")
    if row["split_from"] is not None or row["hidden"]:
        raise ValueError("this line is already part of a split")
    cleaned = [(body.strip(), kind) for body, kind in parts if body.strip()]
    if len(cleaned) < 2:
        raise ValueError("a split needs at least two parts")
    created = []
    for body, kind in cleaned:
        forced = kind if kind in ("note", "idea", "task") and not URL_PATTERN.search(body) else None
        part_id = save_item(connection, user_id, body, None, now, forced_kind=forced, folder_id=row["folder_id"])
        connection.execute("UPDATE items SET split_from = ? WHERE id = ?", (item_id, part_id))
        created.append(part_id)
    connection.execute("UPDATE items SET hidden = 1 WHERE id = ?", (item_id,))
    connection.commit()
    return created


def undo_split(connection: sqlite3.Connection, user_id: int, item_id: int) -> int:
    row = connection.execute(
        "SELECT id, split_from, hidden FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("item not found")
    original = row["split_from"] if row["split_from"] is not None else (row["id"] if row["hidden"] else None)
    if original is None:
        raise LookupError("this line was not split")
    connection.execute("DELETE FROM items WHERE split_from = ? AND user_id = ?", (original, user_id))
    connection.execute("UPDATE items SET hidden = 0 WHERE id = ? AND user_id = ?", (original, user_id))
    connection.commit()
    return int(original)


def count_by_folder(connection: sqlite3.Connection, user_id: int) -> dict[int | None, int]:
    rows = connection.execute(
        "SELECT folder_id, COUNT(*) AS total FROM items WHERE user_id = ? AND hidden = 0 GROUP BY folder_id",
        (user_id,),
    ).fetchall()
    return {row["folder_id"]: int(row["total"]) for row in rows}


def item_kind(connection: sqlite3.Connection, user_id: int, item_id: int) -> str:
    return _owned_item(connection, user_id, item_id)["kind"]


def toggle_task(connection: sqlite3.Connection, user_id: int, item_id: int) -> bool:
    row = connection.execute(
        "SELECT kind, task_done FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("item not found")
    done = not bool(row["task_done"])
    set_task_done(connection, user_id, item_id, done)
    return done


def set_task_done(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    done: bool,
) -> None:
    row = _owned_item(connection, user_id, item_id)
    if row["kind"] != "task":
        raise ValueError("only a task can be marked done")
    connection.execute(
        "UPDATE items SET task_done = ? WHERE id = ? AND user_id = ?",
        (1 if done else 0, item_id, user_id),
    )
    connection.commit()


def list_items(
    connection: sqlite3.Connection,
    user_id: int,
    query: str = "",
    now: datetime | None = None,
    folder_id: int | None = None,
    unsorted: bool = False,
    zone: tzinfo | None = None,
) -> list[dict]:
    if now is not None:
        refresh(connection, user_id, now)
    sql = """
        SELECT i.id, i.folder_id, i.body, i.kind, i.task_done, i.created_at, i.split_from,
               r.remind_at, r.status, r.repeat_daily, r.repeat_until
        FROM items i
        LEFT JOIN reminders r ON r.item_id = i.id
        WHERE i.user_id = ? AND i.hidden = 0
    """
    parameters: list[object] = [user_id]
    if unsorted:
        sql += " AND i.folder_id IS NULL"
    elif folder_id is not None:
        sql += " AND i.folder_id = ?"
        parameters.append(folder_id)
    cleaned = query.strip()
    if cleaned:
        sql += """
            AND (i.body LIKE ? ESCAPE '\\' OR EXISTS (
                SELECT 1 FROM images g
                WHERE g.item_id = i.id AND (g.title LIKE ? ESCAPE '\\' OR g.text LIKE ? ESCAPE '\\')
            ))
        """
        parameters += [_like(cleaned)] * 3
    sql += " ORDER BY CASE WHEN r.status = 'due' THEN 0 ELSE 1 END, i.created_at DESC"
    rows = connection.execute(sql, parameters).fetchall()
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
            "folder_id": row["folder_id"],
            "body": row["body"],
            "kind": row["kind"],
            "task_done": bool(row["task_done"]),
            "created_at": row["created_at"],
            "remind_at": row["remind_at"],
            "remind_local": _local_input(row["remind_at"], zone),
            "remind_label": _label(row["remind_at"], zone),
            "split_from": row["split_from"],
            "status": row["status"],
            "repeat_daily": bool(row["repeat_daily"]),
            "repeat_until": row["repeat_until"],
            "tags": tags_by_item[int(row["id"])],
        }
        for row in rows
    ]


def _check_reminder(
    kind: str,
    remind_at: datetime | None,
    repeat_daily: bool,
    repeat_until: date | None,
) -> None:
    # A reminder may be saved without a time: automatic sorting can read one
    # from the text, and until then the page asks for it.
    if kind != "reminder":
        return
    if repeat_daily and remind_at is None:
        raise ClassificationError("Pick a start time for a daily reminder.")
    if repeat_until is not None and not repeat_daily:
        raise ClassificationError("An end date is only for a daily reminder.")
    if (
        repeat_daily
        and repeat_until is not None
        and remind_at is not None
        and repeat_until < remind_at.astimezone(timezone.utc).date()
    ):
        raise ClassificationError("The end date has to be on or after the start.")


def _owned_item(connection: sqlite3.Connection, user_id: int, item_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT id, kind FROM items WHERE id = ? AND user_id = ?",
        (item_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("item not found")
    return row


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


def _local_input(value: str | None, zone: tzinfo | None) -> str:
    local = _local(value, zone)
    return local.strftime("%Y-%m-%dT%H:%M") if local else ""


def _label(value: str | None, zone: tzinfo | None) -> str:
    local = _local(value, zone)
    return f"{local:%a} {local.day} {local:%b}, {local:%H:%M}" if local else ""


def _local(value: str | None, zone: tzinfo | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(zone or timezone.utc)


def _like(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
