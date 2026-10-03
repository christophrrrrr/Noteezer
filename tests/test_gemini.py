import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

import pytest

from noteezer.db import connect, init_db
from noteezer.folders import create_folder, list_folders
from noteezer.gemini import GeminiSorter, build_prompt, parse_answer
from noteezer.inbox import list_items, save_item
from noteezer.links import LinkPreview
from noteezer.sorting import (
    FolderOption,
    NewFolder,
    Note,
    Part,
    accept_proposal,
    requeue_unsorted,
    results_for,
    sort_pending,
    sorter_from_env,
)
from noteezer.users import register_user

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
PROJECTS = FolderOption(1, "Projects", "Projects")
GAME = FolderOption(2, "Game", "Projects / Game")
MOVIES = FolderOption(3, "Movies", "Movies")
FOLDERS = [PROJECTS, GAME, MOVIES]


def _answer(folder_id=None, confidence=0.0, new_folder=None, kind="note", remind_at=None, parts=()) -> str:
    return json.dumps(
        {
            "folder_id": folder_id,
            "confidence": confidence,
            "new_folder": new_folder,
            "kind": kind,
            "remind_at": remind_at,
            "parts": list(parts),
        }
    )


class FakeModel:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.images: list = []

    def __call__(self, system: str, prompt: str, image=None, schema=None) -> str:
        self.prompts.append(prompt)
        self.images.append(image)
        return self.answers.pop(0)


def test_a_clear_tag_decides_the_folder_but_the_model_still_reads_the_rest() -> None:
    model = FakeModel(_answer(MOVIES.id, 0.6, kind="task", remind_at="2026-10-03T15:00"))
    zone = ZoneInfo("Europe/Madrid")
    note = Note("fix hitbox friday 3pm #game", ("game",), now=datetime(2026, 10, 1, 10, 0, tzinfo=zone))
    suggestion = GeminiSorter(model, min_interval=0).suggest(note, FOLDERS)
    assert (suggestion.folder_id, suggestion.confidence) == (GAME.id, 0.9)
    assert suggestion.kind == "task"
    assert suggestion.remind_at == datetime(2026, 10, 3, 13, 0, tzinfo=timezone.utc)
    assert "Now: Thursday 2026-10-01 10:00 (Europe/Madrid)" in model.prompts[0]
    assert "Saved as: note" in model.prompts[0]


def test_the_model_sees_folder_ids_and_link_titles() -> None:
    note = Note(
        "https://youtu.be/abc",
        links=(LinkPreview("https://youtu.be/abc", "Dune trailer", "YouTube video by WB", "youtube.com"),),
    )
    prompt = build_prompt(note, FOLDERS)
    assert "2: Projects / Game" in prompt
    assert "Link (youtube.com): Dune trailer - YouTube video by WB" in prompt

    model = FakeModel(_answer(MOVIES.id, 0.93))
    suggestion = GeminiSorter(model, min_interval=0).suggest(note, FOLDERS)
    assert (suggestion.folder_id, suggestion.confidence, suggestion.engine) == (MOVIES.id, 0.93, "gemini")


def test_answers_are_checked_before_they_are_trusted() -> None:
    assert parse_answer(_answer(99, 0.9), FOLDERS).folder_id is None
    assert parse_answer(_answer(GAME.id, 7), FOLDERS).confidence == 1.0
    assert parse_answer(_answer(GAME.id, "high"), FOLDERS).confidence == 0.0
    assert parse_answer(_answer(None, 0.2, {"name": "a/b", "parent_id": None}), FOLDERS).new_folder is None
    with pytest.raises(ValueError):
        parse_answer("[]", FOLDERS)
    with pytest.raises(ValueError):
        parse_answer("not json", FOLDERS)


def test_a_new_folder_is_proposed_unless_it_already_exists() -> None:
    fresh = parse_answer(_answer(None, 0.3, {"name": " Climbing  Gym ", "parent_id": PROJECTS.id}), FOLDERS)
    assert fresh.new_folder == NewFolder("Climbing Gym", PROJECTS.id)
    assert fresh.folder_id is None

    unknown_parent = parse_answer(_answer(None, 0.3, {"name": "Hackathons", "parent_id": 42}), FOLDERS)
    assert unknown_parent.new_folder == NewFolder("Hackathons", None)

    existing = parse_answer(_answer(None, 0.85, {"name": "game", "parent_id": PROJECTS.id}), FOLDERS)
    assert existing.folder_id == GAME.id
    assert existing.new_folder is None


def test_calls_are_spaced_out_for_the_free_tier() -> None:
    now = [100.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    model = FakeModel(_answer(), _answer(), _answer())
    sorter = GeminiSorter(model, min_interval=4.0, clock=lambda: now[0], sleep=sleep)
    sorter.suggest(Note("a"), FOLDERS)
    now[0] += 1.5
    sorter.suggest(Note("b"), FOLDERS)
    now[0] += 10
    sorter.suggest(Note("c"), FOLDERS)
    assert slept == [2.5]


def test_a_proposed_folder_is_created_when_accepted(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    owner = register_user(connection, "a@example.com", "password1", NOW)
    projects = create_folder(connection, owner, "Projects", NOW)
    first = save_item(connection, owner, "Climbing project cool", None, NOW)
    second = save_item(connection, owner, "machine learning for climbing holds", None, NOW)
    proposal = {"name": "Climbing", "parent_id": projects}
    model = FakeModel(_answer(None, 0.4, proposal), _answer(None, 0.4, proposal))

    sort_pending(connection, GeminiSorter(model, min_interval=0), NOW)
    assert results_for(connection, owner)[first]["proposed_name"] == "Climbing"
    assert results_for(connection, owner)[first]["proposed_parent_id"] == projects
    assert requeue_unsorted(connection, owner, only_unmatched=True) == 0

    climbing = accept_proposal(connection, owner, first, NOW)
    assert [entry["path"] for entry in list_folders(connection, owner)] == ["Projects", "Projects / Climbing"]
    assert accept_proposal(connection, owner, second, NOW) == climbing
    assert {item["id"] for item in list_items(connection, owner, folder_id=climbing)} == {first, second}
    with pytest.raises(LookupError):
        accept_proposal(connection, owner, first, NOW)
    connection.close()


def test_env_picks_gemini_only_with_a_key(monkeypatch) -> None:
    monkeypatch.setenv("SORTER", "auto")
    assert sorter_from_env().name == "rules"
    monkeypatch.setenv("SORTER", "gemini")
    assert sorter_from_env().name == "rules"
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    assert sorter_from_env().name == "gemini"
    monkeypatch.setenv("SORTER", "rules")
    assert sorter_from_env().name == "rules"


def test_parts_and_times_are_checked_before_they_are_trusted() -> None:
    parts = [
        {"text": "Cannon Circle: bigger cannon balls", "kind": "task", "folder_id": GAME.id, "confidence": 0.9},
        {"text": "  ", "kind": "task", "folder_id": GAME.id, "confidence": 0.9},
        {"text": "Noteezer: share target", "kind": "maybe", "folder_id": 99, "confidence": 3},
    ]
    answer = parse_answer(_answer(parts=parts, remind_at="next friday"), FOLDERS)
    assert answer.parts == (
        Part("Cannon Circle: bigger cannon balls", "task", GAME.id, 0.9),
        Part("Noteezer: share target", None, None, 1.0),
    )
    assert answer.remind_at is None
    assert answer.kind == "note"
    assert parse_answer(_answer(parts=parts[:1]), FOLDERS).parts == ()
    assert parse_answer(_answer(remind_at="2026-10-03T15:00"), FOLDERS).remind_at == datetime(
        2026, 10, 3, 15, 0, tzinfo=timezone.utc
    )
