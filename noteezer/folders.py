import sqlite3
from datetime import datetime, timezone

MAX_NAME_LENGTH = 60


class FolderError(Exception):
    pass


def create_folder(
    connection: sqlite3.Connection,
    user_id: int,
    name: str,
    now: datetime,
    parent_id: int | None = None,
) -> int:
    cleaned = _clean_name(name)
    if parent_id is not None:
        require_folder(connection, user_id, parent_id)
    try:
        cursor = connection.execute(
            "INSERT INTO folders (user_id, parent_id, name, created_at) VALUES (?, ?, ?, ?)",
            (user_id, parent_id, cleaned, now.astimezone(timezone.utc).isoformat()),
        )
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise FolderError(f"There is already a folder called {cleaned} here.") from error
    connection.commit()
    return int(cursor.lastrowid)


def rename_folder(connection: sqlite3.Connection, user_id: int, folder_id: int, name: str) -> None:
    cleaned = _clean_name(name)
    require_folder(connection, user_id, folder_id)
    try:
        connection.execute(
            "UPDATE folders SET name = ? WHERE id = ? AND user_id = ?",
            (cleaned, folder_id, user_id),
        )
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise FolderError(f"There is already a folder called {cleaned} here.") from error
    connection.commit()


def move_folder(
    connection: sqlite3.Connection,
    user_id: int,
    folder_id: int,
    parent_id: int | None,
) -> None:
    require_folder(connection, user_id, folder_id)
    if parent_id is not None:
        require_folder(connection, user_id, parent_id)
        if parent_id in descendant_ids(connection, user_id, folder_id):
            raise FolderError("A folder cannot go inside itself.")
    try:
        connection.execute(
            "UPDATE folders SET parent_id = ? WHERE id = ? AND user_id = ?",
            (parent_id, folder_id, user_id),
        )
    except sqlite3.IntegrityError as error:
        connection.rollback()
        raise FolderError("A folder with that name is already there.") from error
    connection.commit()


def delete_folder(connection: sqlite3.Connection, user_id: int, folder_id: int) -> None:
    # Subfolders go with it (ON DELETE CASCADE). Items are never deleted with a
    # folder: items.folder_id is ON DELETE SET NULL, so they land in Unsorted.
    cursor = connection.execute(
        "DELETE FROM folders WHERE id = ? AND user_id = ?",
        (folder_id, user_id),
    )
    if cursor.rowcount == 0:
        raise LookupError("folder not found")
    connection.commit()


def require_folder(connection: sqlite3.Connection, user_id: int, folder_id: int) -> None:
    row = connection.execute(
        "SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
        (folder_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("folder not found")


def descendant_ids(connection: sqlite3.Connection, user_id: int, folder_id: int) -> set[int]:
    rows = connection.execute(
        """
        WITH RECURSIVE subtree(id) AS (
            SELECT id FROM folders WHERE id = ? AND user_id = ?
            UNION ALL
            SELECT f.id FROM folders f JOIN subtree s ON f.parent_id = s.id
        )
        SELECT id FROM subtree
        """,
        (folder_id, user_id),
    ).fetchall()
    return {int(row["id"]) for row in rows}


def list_folders(connection: sqlite3.Connection, user_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT id, parent_id, name FROM folders WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    children: dict[int | None, list[sqlite3.Row]] = {}
    for row in rows:
        children.setdefault(row["parent_id"], []).append(row)
    for siblings in children.values():
        siblings.sort(key=lambda row: row["name"].casefold())

    ordered: list[dict] = []

    def walk(parent_id: int | None, depth: int, trail: list[str]) -> None:
        for row in children.get(parent_id, []):
            path = [*trail, row["name"]]
            ordered.append(
                {
                    "id": int(row["id"]),
                    "parent_id": row["parent_id"],
                    "name": row["name"],
                    "depth": depth,
                    "path": " / ".join(path),
                }
            )
            walk(int(row["id"]), depth + 1, path)

    walk(None, 0, [])
    return ordered


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise FolderError("Give the folder a name.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise FolderError(f"Keep folder names under {MAX_NAME_LENGTH} characters.")
    if "/" in cleaned:
        raise FolderError("A folder name cannot contain /.")
    return cleaned
