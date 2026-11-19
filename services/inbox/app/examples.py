"""Example lines for a new account: the inbox service's seed data.

A new account starts with a folder called Examples holding three lines that
show how a line gets its kind. Seeding is idempotent: it adds nothing when
the account already has a folder of that name, so running it twice, or after
a retried request, never doubles the lines. Deleting the folder deletes only
the folder; the lines move to Unsorted like any other (ADR-7).

The reminders service has no seed data: a reminder only exists for a line
someone saved.
"""

from datetime import datetime

from app.db import Connection
from app.folders import create_folder
from app.inbox import save_item

FOLDER = "Examples"
LINES = (
    "todo tick this task off once you have tried it",
    "idea a line that starts with idea becomes an idea",
    "Anything else is a note. #words become tags, and Gemini files lines into your folders",
)


def seed_examples(connection: Connection, user_id: int, now: datetime) -> bool:
    """Adds the example folder and lines once. Returns whether it added them."""
    exists = connection.execute(
        "SELECT 1 FROM folders WHERE user_id = ? AND parent_id IS NULL AND name = ?",
        (user_id, FOLDER),
    ).fetchone()
    if exists is not None:
        return False
    folder_id = create_folder(connection, user_id, FOLDER, now)
    for body in LINES:
        save_item(connection, user_id, body, None, now, folder_id=folder_id)
    return True
