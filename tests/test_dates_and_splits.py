from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from noteezer.db import connect, init_db
from noteezer.folders import create_folder
from noteezer.inbox import list_items, save_item, split_item
from noteezer.sorting import (
    Part,
    Suggestion,
    accept_reminder,
    requeue_unsorted,
    results_for,
    resort_if_waiting,
    sort_pending,
    undo_auto_split,
)
from noteezer.users import register_user, set_timezone, user_timezone

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
SATURDAY_3PM = datetime(2026, 10, 3, 13, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection) -> int:
    return register_user(connection, "a@example.com", "password1", NOW)


class Answer:
    """A fake engine that gives the same suggestion for every line."""

    name = "fake"

    def __init__(self, suggestion: Suggestion) -> None:
        self.suggestion = suggestion
        self.notes = []

    def suggest(self, note, folders):
        self.notes.append(note)
        return self.suggestion


def test_the_browser_timezone_is_kept_when_it_is_real(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    assert user_timezone(connection, owner) == ZoneInfo("UTC")
    assert set_timezone(connection, owner, "Europe/Madrid") is True
    assert set_timezone(connection, owner, "Mars/Olympus") is False
    assert set_timezone(connection, owner, "") is False
    assert user_timezone(connection, owner) == ZoneInfo("Europe/Madrid")
    connection.close()


def test_a_remind_line_without_a_time_gets_one_from_the_text(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    set_timezone(connection, owner, "Europe/Madrid")
    game = create_folder(connection, owner, "Game", NOW)
    unsorted = save_item(connection, owner, "remind me saturday 3pm call mom", None, NOW)
    filed = save_item(connection, owner, "remind me to test the map saturday 3pm", None, NOW, folder_id=game)
    engine = Answer(Suggestion(None, 0.0, remind_at=SATURDAY_3PM))

    assert sort_pending(connection, engine, NOW) == 2
    assert engine.notes[0].now == NOW.astimezone(ZoneInfo("Europe/Madrid"))
    listed = {item["id"]: item for item in list_items(connection, owner, zone=ZoneInfo("Europe/Madrid"))}
    assert listed[unsorted]["status"] == "scheduled"
    assert listed[unsorted]["remind_label"] == "Sat 3 Oct, 15:00"
    assert listed[filed]["status"] == "scheduled"
    assert listed[filed]["folder_ids"] == [game]
    assert sort_pending(connection, engine, NOW) == 0
    connection.close()


def test_other_lines_with_a_time_only_get_a_suggestion(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    create_folder(connection, owner, "Uni", NOW)
    dentist = save_item(connection, owner, "dentist saturday 15:00", None, NOW)
    old = save_item(connection, owner, "dentist was yesterday", None, NOW)
    sort_pending(connection, Answer(Suggestion(None, 0.0, remind_at=SATURDAY_3PM)), NOW, limit=1)
    sort_pending(connection, Answer(Suggestion(None, 0.0, remind_at=NOW - timedelta(days=1))), NOW)

    results = results_for(connection, owner)
    assert results[dentist]["suggested_remind_at"] == SATURDAY_3PM.isoformat()
    assert results[old]["suggested_remind_at"] is None
    assert list_items(connection, owner, query="saturday")[0]["kind"] == "note"

    assert accept_reminder(connection, owner, dentist, NOW) == SATURDAY_3PM
    item = list_items(connection, owner, query="saturday")[0]
    assert (item["kind"], item["status"]) == ("reminder", "scheduled")
    with pytest.raises(LookupError):
        accept_reminder(connection, owner, dentist, NOW)
    connection.close()


def test_only_a_plain_note_changes_kind(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    create_folder(connection, owner, "Game", NOW)
    note = save_item(connection, owner, "fix cannon hitbox", None, NOW)
    link = save_item(connection, owner, "https://example.com", None, NOW)
    idea = save_item(connection, owner, "idea spooky spikes", None, NOW)
    sort_pending(connection, Answer(Suggestion(None, 0.0, kind="task")), NOW)
    kinds = {item["id"]: item["kind"] for item in list_items(connection, owner)}
    assert kinds == {note: "task", link: "link", idea: "idea"}
    connection.close()


def test_a_list_is_split_into_parts_that_are_sorted_on_their_own(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    noteezer = create_folder(connection, owner, "Noteezer", NOW)
    original = save_item(connection, owner, "Cannon Circle:\nbigger balls\nfix hitbox\nshare target", None, NOW)
    split = Suggestion(
        None,
        0.0,
        parts=(
            Part("Cannon Circle: bigger balls", "idea", game, 0.9),
            Part("Cannon Circle: fix hitbox", "task", game, 0.5),
            Part("Noteezer share target", "idea", None, 0.0),
        ),
    )
    sort_pending(connection, Answer(split), NOW)

    listed = {item["body"]: item for item in list_items(connection, owner)}
    assert original not in {item["id"] for item in listed.values()}
    assert listed["Cannon Circle: bigger balls"]["folder_ids"] == [game]
    assert listed["Cannon Circle: bigger balls"]["kind"] == "idea"
    assert listed["Cannon Circle: fix hitbox"]["folder_ids"] == []
    assert listed["Cannon Circle: fix hitbox"]["kind"] == "task"
    results = results_for(connection, owner)
    assert results[listed["Cannon Circle: fix hitbox"]["id"]]["folder_id"] == game
    waiting = listed["Noteezer share target"]["id"]
    assert waiting not in results

    # The part with no folder is sorted on the next pass, and is never split again.
    again = Answer(Suggestion(noteezer, 0.9, parts=split.parts))
    assert sort_pending(connection, again, NOW) == 1
    assert list_items(connection, owner, folder_id=noteezer)[0]["id"] == waiting
    assert len(list_items(connection, owner)) == 3
    connection.close()


def test_undo_split_brings_the_original_back_for_good(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    create_folder(connection, owner, "Game", NOW)
    original = save_item(connection, owner, "a\nb", None, NOW)
    split = Answer(Suggestion(None, 0.0, parts=(Part("a", "task", None, 0), Part("b", "task", None, 0))))
    sort_pending(connection, split, NOW)
    part = list_items(connection, owner)[0]["id"]

    assert undo_auto_split(connection, owner, part) == original
    assert [item["id"] for item in list_items(connection, owner)] == [original]
    assert requeue_unsorted(connection, owner, only_unmatched=True) == 0
    assert sort_pending(connection, split, NOW) == 0
    with pytest.raises(LookupError):
        undo_auto_split(connection, owner, original)
    with pytest.raises(ValueError):
        split_item(connection, owner, original, [("only one", "task")], NOW)
    connection.close()


def test_editing_a_line_into_a_timeless_reminder_reads_it_again(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    create_folder(connection, owner, "Game", NOW)
    item_id = save_item(connection, owner, "call mom", None, NOW)
    sort_pending(connection, Answer(Suggestion(None, 0.0)), NOW)
    assert resort_if_waiting(connection, owner, item_id) is False

    from noteezer.inbox import update_item

    update_item(connection, owner, item_id, "remind call mom saturday 3pm", None, NOW)
    assert resort_if_waiting(connection, owner, item_id) is True
    sort_pending(connection, Answer(Suggestion(None, 0.0, remind_at=SATURDAY_3PM)), NOW)
    assert list_items(connection, owner)[0]["status"] == "scheduled"
    connection.close()
