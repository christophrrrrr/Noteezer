from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from noteezer.db import connect, init_db
from noteezer.inbox import delete_item, item_kind, list_items, save_item, set_task_done, toggle_task, update_item
from noteezer.reminders import complete, next_occurrence, snooze
from noteezer.users import register_user

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection) -> int:
    return register_user(connection, "a@example.com", "password1", NOW)


def test_daily_done_moves_to_the_next_day_until_the_end(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    item_id = save_item(
        connection,
        owner,
        "remind stretch",
        START,
        NOW,
        repeat_daily=True,
        repeat_until=date(2026, 9, 29),
    )
    assert complete(connection, owner, item_id, NOW) == "scheduled"
    listed = list_items(connection, owner)
    assert listed[0]["remind_at"].startswith("2026-09-29T15:00:00")
    assert complete(connection, owner, item_id, NOW) == "done"
    connection.close()


def test_daily_without_an_end_keeps_moving(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    item_id = save_item(connection, owner, "remind water", START, NOW, repeat_daily=True)
    assert next_occurrence(START, None) == START + timedelta(days=1)
    assert complete(connection, owner, item_id, NOW) == "scheduled"
    connection.close()


def test_one_shot_done_stays_done(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    item_id = save_item(connection, owner, "remind once", START, NOW)
    assert complete(connection, owner, item_id, NOW) == "done"
    assert complete(connection, owner, item_id, NOW) == "done"
    connection.close()


def test_snooze_hides_a_due_reminder_for_one_hour(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    past = datetime(2026, 9, 28, 11, 0, tzinfo=timezone.utc)
    item_id = save_item(connection, owner, "remind call", past, NOW)
    until = snooze(connection, owner, item_id, NOW)
    listed = list_items(connection, owner, now=NOW)
    assert listed[0]["status"] == "snoozed"
    assert until == NOW + timedelta(hours=1)
    later = list_items(connection, owner, now=until)
    assert later[0]["status"] == "due"
    connection.close()


def test_search_edit_delete_and_task_done(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    milk = save_item(connection, owner, "buy milk #home", None, NOW)
    save_item(connection, owner, "read the chapter", None, NOW)
    found = list_items(connection, owner, query="milk")
    assert [item["id"] for item in found] == [milk]

    update_item(connection, owner, milk, "buy oats #home", None, NOW, forced_kind="task")
    edited = list_items(connection, owner, query="oats")[0]
    assert edited["kind"] == "task"
    assert edited["tags"] == ["home"]

    set_task_done(connection, owner, milk, True)
    assert list_items(connection, owner, query="oats")[0]["task_done"] is True

    delete_item(connection, owner, milk)
    assert list_items(connection, owner, query="oats") == []
    connection.close()


def test_done_is_decided_through_inbox_functions(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    task = save_item(connection, owner, "todo fix hitbox", None, NOW)
    note = save_item(connection, owner, "just a thought", None, NOW)
    assert item_kind(connection, owner, task) == "task"
    assert toggle_task(connection, owner, task) is True
    assert toggle_task(connection, owner, task) is False
    with pytest.raises(ValueError):
        toggle_task(connection, owner, note)
    with pytest.raises(LookupError):
        item_kind(connection, owner, 999)
    with pytest.raises(LookupError):
        toggle_task(connection, owner, 999)
    connection.close()
