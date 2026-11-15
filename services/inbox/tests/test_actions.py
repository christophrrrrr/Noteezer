from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import connect
from app.inbox import delete_item, item_kind, list_items, save_item, set_task_done, toggle_task, update_item
from app.users import register_user

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    connection = connect()
    return connection


def _user(connection) -> int:
    return register_user(connection, "a@example.com", "password1", NOW)


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
