from datetime import datetime, timezone

from app.db import Connection, IntegrityError, Row

MAX_NAME_LENGTH = 60


class FolderError(Exception):
    pass


def create_folder(
    connection: Connection,
    user_id: int,
    name: str,
    now: datetime,
    parent_id: int | None = None,
) -> int:
    cleaned = _clean_name(name)
    if parent_id is not None:
        require_folder(connection, user_id, parent_id)
    try:
        row = connection.execute(
            "INSERT INTO folders (user_id, parent_id, name, created_at) OUTPUT INSERTED.id VALUES (?, ?, ?, ?)",
            (user_id, parent_id, cleaned, now.astimezone(timezone.utc).isoformat()),
        ).fetchone()
    except IntegrityError as error:
        connection.rollback()
        raise FolderError(f"There is already a folder called {cleaned} here.") from error
    connection.commit()
    return int(row["id"])


def rename_folder(connection: Connection, user_id: int, folder_id: int, name: str) -> None:
    cleaned = _clean_name(name)
    require_folder(connection, user_id, folder_id)
    try:
        connection.execute(
            "UPDATE folders SET name = ? WHERE id = ? AND user_id = ?",
            (cleaned, folder_id, user_id),
        )
    except IntegrityError as error:
        connection.rollback()
        raise FolderError(f"There is already a folder called {cleaned} here.") from error
    connection.commit()


def move_folder(
    connection: Connection,
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
    except IntegrityError as error:
        connection.rollback()
        raise FolderError("A folder with that name is already there.") from error
    connection.commit()


def delete_folder(connection: Connection, user_id: int, folder_id: int) -> None:
    # Subfolders go with it. Items are never deleted with a folder: only their
    # item_folders rows go (ON DELETE CASCADE), so a line that was in no other
    # folder lands in Unsorted. SQL Server refuses a cascade from a table to
    # itself, so the subtree is collected here and deleted in one statement,
    # and new-folder proposals that pointed into it are cleared first.
    subtree = descendant_ids(connection, user_id, folder_id)
    if not subtree:
        raise LookupError("folder not found")
    placeholders = ",".join("?" for _ in subtree)
    ids = tuple(subtree)
    connection.execute(
        f"UPDATE sort_results SET proposed_parent_id = NULL WHERE proposed_parent_id IN ({placeholders})",
        ids,
    )
    connection.execute(f"DELETE FROM folders WHERE id IN ({placeholders})", ids)
    connection.commit()


def require_folder(connection: Connection, user_id: int, folder_id: int) -> None:
    row = connection.execute(
        "SELECT 1 FROM folders WHERE id = ? AND user_id = ?",
        (folder_id, user_id),
    ).fetchone()
    if row is None:
        raise LookupError("folder not found")


def find_folder(
    connection: Connection,
    user_id: int,
    name: str,
    parent_id: int | None,
) -> int | None:
    row = connection.execute(
        """
        SELECT id FROM folders
        WHERE user_id = ? AND parent_key = ISNULL(?, 0) AND name = ?
        """,
        (user_id, parent_id, " ".join(name.split())),
    ).fetchone()
    return None if row is None else int(row["id"])


def descendant_ids(connection: Connection, user_id: int, folder_id: int) -> set[int]:
    rows = connection.execute(
        """
        WITH subtree(id) AS (
            SELECT id FROM folders WHERE id = ? AND user_id = ?
            UNION ALL
            SELECT f.id FROM folders f JOIN subtree s ON f.parent_id = s.id
        )
        SELECT id FROM subtree
        """,
        (folder_id, user_id),
    ).fetchall()
    return {int(row["id"]) for row in rows}


def list_folders(connection: Connection, user_id: int) -> list[dict]:
    rows = connection.execute(
        "SELECT id, parent_id, name FROM folders WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    children: dict[int | None, list[Row]] = {}
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
