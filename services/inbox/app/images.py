import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

MAX_BYTES = 10 * 1024 * 1024
PLACEHOLDER = "Screenshot"


class ImageError(Exception):
    pass


@dataclass(frozen=True)
class Image:
    data: bytes
    mime: str


def check_image(data: bytes) -> str:
    """Returns the image type, judged from the bytes, not from the browser.

    Only formats a browser shows safely are accepted. SVG is refused because
    it can carry a script.
    """
    if not data:
        raise ImageError("The image is empty.")
    if len(data) > MAX_BYTES:
        raise ImageError("Images can be up to 10 MB.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ImageError("Use a PNG, JPEG, WebP, or GIF image.")


def attach_image(
    connection: sqlite3.Connection,
    item_id: int,
    data: bytes,
    ai_allowed: bool,
) -> None:
    mime = check_image(data)
    connection.execute(
        "INSERT INTO images (item_id, mime, data, ai_allowed) VALUES (?, ?, ?, ?)",
        (item_id, mime, data, 1 if ai_allowed else 0),
    )
    connection.commit()


def image_for(connection: sqlite3.Connection, user_id: int, item_id: int) -> Image | None:
    row = connection.execute(
        """
        SELECT g.data, g.mime FROM images g
        JOIN items i ON i.id = g.item_id
        WHERE g.item_id = ? AND i.user_id = ?
        """,
        (item_id, user_id),
    ).fetchone()
    return None if row is None else Image(bytes(row["data"]), row["mime"])


def readable_image(connection: sqlite3.Connection, item_id: int) -> Image | None:
    # The image the sorter may send to an AI engine: only when the user left
    # "Let AI read this image" ticked.
    row = connection.execute(
        "SELECT data, mime FROM images WHERE item_id = ? AND ai_allowed = 1",
        (item_id,),
    ).fetchone()
    return None if row is None else Image(bytes(row["data"]), row["mime"])


def images_for(connection: sqlite3.Connection, user_id: int) -> dict[int, dict]:
    rows = connection.execute(
        """
        SELECT g.item_id, g.ai_allowed, g.title, g.text, g.read_at
        FROM images g
        JOIN items i ON i.id = g.item_id
        WHERE i.user_id = ?
        """,
        (user_id,),
    ).fetchall()
    return {
        int(row["item_id"]): {
            "ai_allowed": bool(row["ai_allowed"]),
            "title": row["title"],
            "text": row["text"],
            "read": row["read_at"] is not None,
        }
        for row in rows
    }


def record_reading(
    connection: sqlite3.Connection,
    user_id: int,
    item_id: int,
    title: str | None,
    text: str | None,
    now: datetime,
) -> None:
    """Stores what an engine read in the image.

    A line saved with only an image still says "Screenshot"; it takes the title
    as its text. Text the user typed is never replaced.
    """
    connection.execute(
        """
        UPDATE images SET title = ?, text = ?, read_at = ?
        WHERE item_id = ? AND item_id IN (SELECT id FROM items WHERE user_id = ?)
        """,
        (title, text, now.astimezone(timezone.utc).isoformat(), item_id, user_id),
    )
    if title:
        connection.execute(
            "UPDATE items SET body = ? WHERE id = ? AND user_id = ? AND body = ?",
            (title, item_id, user_id, PLACEHOLDER),
        )
    connection.commit()
