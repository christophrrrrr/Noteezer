from datetime import datetime, timezone
from pathlib import Path

import pytest

from noteezer.classify import ClassificationError
from noteezer.db import connect, init_db
from noteezer.inbox import list_items, save_item
from noteezer.reminders import initial_status, schedule
from noteezer.users import register_user

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
PAST = datetime(2026, 9, 27, 11, 0, tzinfo=timezone.utc)
FUTURE = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection, email: str = "a@example.com") -> int:
    return register_user(connection, email, "password1", NOW)


def test_past_reminder_is_due_and_future_one_is_scheduled() -> None:
    assert initial_status(PAST, NOW) == "due"
    assert initial_status(NOW, NOW) == "due"
    assert initial_status(FUTURE, NOW) == "scheduled"


def test_save_sorts_tags_and_lists_only_that_user(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    other = _user(connection, "b@example.com")
    save_item(connection, owner, "buy milk #Groceries", None, NOW)
    save_item(connection, other, "secret note", None, NOW)

    listed = list_items(connection, owner)
    assert [item["body"] for item in listed] == ["buy milk #Groceries"]
    assert listed[0]["kind"] == "task"
    assert listed[0]["tags"] == ["groceries"]
    assert listed[0]["status"] is None
    connection.close()


def test_past_reminder_is_saved_as_due_and_listed_first(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    save_item(connection, owner, "a note", None, NOW)
    save_item(connection, owner, "remind call landlord", PAST, NOW)

    listed = list_items(connection, owner)
    assert listed[0]["kind"] == "reminder"
    assert listed[0]["status"] == "due"
    assert listed[1]["kind"] == "note"
    connection.close()


def test_reminder_without_a_time_is_rejected(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    with pytest.raises(ClassificationError):
        save_item(connection, owner, "remind call landlord", None, NOW)
    assert list_items(connection, owner) == []
    connection.close()


def test_schedule_rejects_a_note(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    item_id = save_item(connection, owner, "just a note", None, NOW)
    with pytest.raises(ValueError):
        schedule(connection, item_id, FUTURE, NOW)
    connection.close()
