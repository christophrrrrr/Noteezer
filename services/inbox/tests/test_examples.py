"""The example lines are the inbox service's seed data, and seeding is idempotent."""

from datetime import datetime, timezone

from app.db import connect
from app.examples import LINES, seed_examples
from app.users import register_user

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


def _counts(connection, user_id: int) -> tuple[int, int]:
    folders = connection.execute("SELECT COUNT(*) AS n FROM folders WHERE user_id = ?", (user_id,)).fetchone()["n"]
    items = connection.execute("SELECT COUNT(*) AS n FROM items WHERE user_id = ?", (user_id,)).fetchone()["n"]
    return folders, items


def test_seeding_twice_adds_the_examples_once() -> None:
    connection = connect()
    try:
        user_id = register_user(connection, "seed@example.com", "password1", NOW)
        assert seed_examples(connection, user_id, NOW) is True
        assert _counts(connection, user_id) == (1, len(LINES))
        assert seed_examples(connection, user_id, NOW) is False
        assert _counts(connection, user_id) == (1, len(LINES))
        rows = connection.execute("SELECT kind FROM items WHERE user_id = ? ORDER BY id", (user_id,))
        kinds = [row["kind"] for row in rows]
        assert kinds == ["task", "idea", "note"]
    finally:
        connection.close()
