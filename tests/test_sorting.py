from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noteezer.db import connect, init_db
from noteezer.folders import create_folder, delete_folder
from noteezer.inbox import list_items, move_item, save_item
from noteezer.sorting import (
    MAX_ATTEMPTS,
    FolderOption,
    RulesSorter,
    Suggestion,
    accept_suggestion,
    requeue_unsorted,
    results_for,
    sort_pending,
    sorter_from_env,
)
from noteezer.users import register_user

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)

GAME = FolderOption(1, "Game", "Projects / Game")
MAPS = FolderOption(2, "Maps", "Projects / Game / Maps")
PROJECTS = FolderOption(3, "Projects", "Projects")
WATCH = FolderOption(4, "Watch later", "Watch later")
FOLDERS = [PROJECTS, GAME, MAPS, WATCH]


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection, email: str = "a@example.com") -> int:
    return register_user(connection, email, "password1", NOW)


class FixedSorter:
    name = "fixed"

    def __init__(self, folder_id: int | None, confidence: float) -> None:
        self.folder_id = folder_id
        self.confidence = confidence

    def suggest(self, body, tags, folders):
        return Suggestion(self.folder_id, self.confidence)


class BrokenSorter:
    name = "broken"

    def suggest(self, body, tags, folders):
        raise RuntimeError("quota exceeded")


def test_rules_match_a_tag_or_a_mentioned_name() -> None:
    rules = RulesSorter()
    assert rules.suggest("king of the hill #game", ("game",), FOLDERS) == Suggestion(GAME.id, 0.9)
    assert rules.suggest("Dune part 3, WATCH  later", (), FOLDERS).folder_id == WATCH.id
    assert rules.suggest("Dune part 3 #watchlater", ("watchlater",), FOLDERS).folder_id == WATCH.id
    assert rules.suggest("I am a gamer", (), FOLDERS) == Suggestion(None, 0.0)


def test_rules_prefer_the_deepest_folder_on_one_branch() -> None:
    rules = RulesSorter()
    assert rules.suggest("Game: more Maps for crown trouble", (), FOLDERS) == Suggestion(MAPS.id, 0.9)
    unclear = rules.suggest("game trailer to watch later", (), FOLDERS)
    assert unclear.confidence < 0.8


def test_confident_lines_are_filed_and_unclear_ones_wait(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    watch = create_folder(connection, owner, "Watch later", NOW)
    filed = save_item(connection, owner, "idea king of the hill #game", None, NOW)
    unclear = save_item(connection, owner, "game trailer to watch later", None, NOW)
    nothing = save_item(connection, owner, "quick sort and bubble sort", None, NOW)
    chosen = save_item(connection, owner, "already filed by hand", None, NOW, folder_id=watch)

    assert sort_pending(connection, RulesSorter(), NOW) == 3
    assert sort_pending(connection, RulesSorter(), NOW) == 0

    results = results_for(connection, owner)
    assert results[filed]["status"] == "filed"
    assert results[unclear]["status"] == "review"
    assert results[unclear]["folder_id"] in (game, watch)
    assert results[nothing] == {"status": "review", "folder_id": None, "confidence": 0.0, "engine": "rules"}
    assert chosen not in results
    assert [item["id"] for item in list_items(connection, owner, folder_id=game)] == [filed]
    connection.close()


def test_nothing_is_sorted_until_the_user_has_a_folder(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    item_id = save_item(connection, owner, "Dune part 3 #movies", None, NOW)
    assert sort_pending(connection, RulesSorter(), NOW) == 0

    movies = create_folder(connection, owner, "Movies", NOW)
    assert sort_pending(connection, RulesSorter(), NOW) == 1
    assert list_items(connection, owner, folder_id=movies)[0]["id"] == item_id
    connection.close()


def test_accepting_a_suggestion_files_the_item(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    item_id = save_item(connection, owner, "more maps?", None, NOW)
    sort_pending(connection, FixedSorter(game, 0.5), NOW)
    assert list_items(connection, owner, unsorted=True)[0]["id"] == item_id

    assert accept_suggestion(connection, owner, item_id) == game
    assert results_for(connection, owner)[item_id]["status"] == "accepted"
    assert list_items(connection, owner, folder_id=game)[0]["id"] == item_id
    with pytest.raises(LookupError):
        accept_suggestion(connection, owner, item_id)
    connection.close()


def test_a_deleted_suggestion_cannot_be_accepted(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    item_id = save_item(connection, owner, "more maps?", None, NOW)
    sort_pending(connection, FixedSorter(game, 0.5), NOW)
    delete_folder(connection, owner, game)
    assert results_for(connection, owner)[item_id]["folder_id"] is None
    with pytest.raises(LookupError):
        accept_suggestion(connection, owner, item_id)
    connection.close()


def test_an_engine_cannot_file_into_someone_elses_folder(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    other = _user(connection, "b@example.com")
    create_folder(connection, owner, "Mine", NOW)
    theirs = create_folder(connection, other, "Theirs", NOW)
    item_id = save_item(connection, owner, "hello", None, NOW)
    sort_pending(connection, FixedSorter(theirs, 0.99), NOW)
    assert results_for(connection, owner)[item_id]["folder_id"] is None
    assert list_items(connection, owner, unsorted=True)[0]["id"] == item_id
    connection.close()


def test_a_move_by_the_user_wins_over_the_sorter(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    watch = create_folder(connection, owner, "Watch later", NOW)
    item_id = save_item(connection, owner, "trailer", None, NOW)

    class MovedMeanwhile:
        name = "slow"

        def suggest(self, body, tags, folders):
            move_item(connection, owner, item_id, watch)
            return Suggestion(game, 0.95)

    sort_pending(connection, MovedMeanwhile(), NOW)
    assert list_items(connection, owner, folder_id=watch)[0]["id"] == item_id
    assert results_for(connection, owner)[item_id]["status"] == "review"
    connection.close()


def test_failures_are_retried_with_backoff_then_given_up(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    item_id = save_item(connection, owner, "more maps", None, NOW)

    assert sort_pending(connection, BrokenSorter(), NOW) == 1
    assert results_for(connection, owner)[item_id]["status"] == "retry"
    assert sort_pending(connection, BrokenSorter(), NOW + timedelta(seconds=59)) == 0
    assert sort_pending(connection, BrokenSorter(), NOW + timedelta(minutes=1)) == 1
    assert sort_pending(connection, BrokenSorter(), NOW + timedelta(minutes=2)) == 0

    later = NOW
    for _ in range(MAX_ATTEMPTS - 2):
        later += timedelta(hours=1)
        assert sort_pending(connection, BrokenSorter(), later) == 1
    assert results_for(connection, owner)[item_id]["status"] == "failed"
    assert sort_pending(connection, BrokenSorter(), later) == 0

    requeue_unsorted(connection, owner)
    sort_pending(connection, FixedSorter(game, 0.9), later)
    assert results_for(connection, owner)[item_id]["status"] == "filed"
    connection.close()


def test_sort_again_only_resets_items_still_in_unsorted(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    create_folder(connection, owner, "Game", NOW)
    save_item(connection, owner, "#game fix hitbox", None, NOW)
    save_item(connection, owner, "movie night", None, NOW)
    sort_pending(connection, RulesSorter(), NOW)

    assert requeue_unsorted(connection, owner) == 1
    create_folder(connection, owner, "Movie night", NOW)
    assert sort_pending(connection, RulesSorter(), NOW) == 1
    assert list_items(connection, owner, unsorted=True) == []
    connection.close()


def test_a_new_folder_only_retries_lines_that_matched_nothing(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    game = create_folder(connection, owner, "Game", NOW)
    climbing_note = save_item(connection, owner, "Climbing project cool", None, NOW)
    guessed = save_item(connection, owner, "more maps?", None, NOW)

    class GuessesMaps:
        name = "guess"

        def suggest(self, body, tags, folders):
            return Suggestion(game, 0.5) if "maps" in body else Suggestion(None, 0.0)

    sort_pending(connection, GuessesMaps(), NOW)
    assert results_for(connection, owner)[climbing_note]["folder_id"] is None
    assert results_for(connection, owner)[guessed]["folder_id"] == game

    climbing = create_folder(connection, owner, "Climbing", NOW)
    assert requeue_unsorted(connection, owner, only_unmatched=True) == 1
    assert sort_pending(connection, RulesSorter(), NOW) == 1
    assert list_items(connection, owner, folder_id=climbing)[0]["id"] == climbing_note
    assert results_for(connection, owner)[guessed]["folder_id"] == game
    connection.close()


def test_unknown_engine_falls_back_to_the_rules(monkeypatch) -> None:
    monkeypatch.setenv("SORTER", "magic")
    assert sorter_from_env().name == "rules"
    monkeypatch.delenv("SORTER")
    assert sorter_from_env().name == "rules"
