import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from noteezer.db import connect, init_db
from noteezer.folders import create_folder, delete_folder
from noteezer.gemini import GeminiSorter, parse_answer
from noteezer.inbox import add_to_folder, count_by_folder, count_items, list_items, save_item, set_folders
from noteezer.main import create_app
from noteezer.sorting import (
    Candidate,
    FolderOption,
    Note,
    RulesSorter,
    Suggestion,
    accept_also,
    also_suggestions_for,
    check_new_folders,
    dismiss_also,
    request_folder_check,
    sort_pending,
)
from noteezer.users import register_user

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _db(tmp_path: Path):
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    return connection


def _user(connection) -> int:
    return register_user(connection, "a@example.com", "password1", NOW)


class Engine:
    """A fake engine with a fixed answer for lines and for new-folder checks."""

    name = "fake"

    def __init__(self, suggestion: Suggestion | None = None, matches=None, fail: bool = False) -> None:
        self.suggestion = suggestion
        self.matches = matches or []
        self.fail = fail
        self.seen: list[list[Candidate]] = []

    def suggest(self, note, folders):
        return self.suggestion

    def match_folder(self, folder, candidates):
        self.seen.append(candidates)
        if self.fail:
            raise RuntimeError("quota exceeded")
        return self.matches


def test_a_line_can_be_in_several_folders(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    tennis = create_folder(connection, owner, "Table Tennis", NOW)
    videos = create_folder(connection, owner, "Cool videos", NOW)
    serve = save_item(connection, owner, "https://youtu.be/serve", None, NOW, folder_id=tennis)
    save_item(connection, owner, "loose thought", None, NOW)

    assert add_to_folder(connection, owner, serve, videos) is True
    assert add_to_folder(connection, owner, serve, videos) is False
    assert list_items(connection, owner, folder_id=tennis)[0]["folder_ids"] == [tennis, videos]
    assert [item["id"] for item in list_items(connection, owner, folder_id=videos)] == [serve]
    assert count_by_folder(connection, owner) == {tennis: 1, videos: 1, None: 1}
    assert count_items(connection, owner) == 2

    delete_folder(connection, owner, tennis)
    assert list_items(connection, owner, folder_id=videos)[0]["folder_ids"] == [videos]
    set_folders(connection, owner, serve, [])
    assert len(list_items(connection, owner, unsorted=True)) == 2
    with pytest.raises(LookupError):
        set_folders(connection, owner, serve, [999])
    connection.close()


def test_every_tagged_folder_gets_the_line(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    tennis = create_folder(connection, owner, "Table Tennis", NOW)
    videos = create_folder(connection, owner, "Cool videos", NOW)
    item = save_item(connection, owner, "serve drill #tabletennis #coolvideos", None, NOW)
    sort_pending(connection, RulesSorter(), NOW)
    assert sorted(list_items(connection, owner)[0]["folder_ids"]) == sorted([tennis, videos])
    assert list_items(connection, owner, folder_id=videos)[0]["id"] == item
    connection.close()


def test_extra_folders_are_only_filed_with_a_confident_answer(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    tennis = create_folder(connection, owner, "Table Tennis", NOW)
    videos = create_folder(connection, owner, "Cool videos", NOW)
    sure = save_item(connection, owner, "serve video", None, NOW)
    sort_pending(connection, Engine(Suggestion(tennis, 0.9, also=(videos, 999))), NOW)
    assert list_items(connection, owner)[0]["folder_ids"] == [tennis, videos]

    unsure = save_item(connection, owner, "maybe a video", None, NOW)
    sort_pending(connection, Engine(Suggestion(tennis, 0.5, also=(videos,))), NOW)
    folders = {item["id"]: item["folder_ids"] for item in list_items(connection, owner)}
    assert folders == {sure: [tennis, videos], unsure: []}
    connection.close()


def test_gemini_answers_extra_folders_and_keeps_a_tag(tmp_path: Path) -> None:
    folders = [FolderOption(1, "Table Tennis", "Table Tennis"), FolderOption(2, "Cool videos", "Cool videos")]
    answer = {
        "folder_id": 1,
        "confidence": 0.9,
        "also_folder_ids": [2, 1, 2, 99],
        "new_folder": None,
        "kind": "note",
        "remind_at": None,
        "parts": [],
    }
    assert parse_answer(json.dumps(answer), folders).also == (2,)
    assert parse_answer(json.dumps({**answer, "folder_id": None}), folders).also == ()

    def model(system, prompt, image=None, schema=None):
        return json.dumps({**answer, "folder_id": 2, "also_folder_ids": [2]})

    tagged = GeminiSorter(model, min_interval=0).suggest(Note("serve #tabletennis", ("tabletennis",)), folders)
    assert (tagged.folder_id, tagged.also) == (1, (2,))


def test_a_new_folder_suggests_filed_lines_that_belong_there_too(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    tennis = create_folder(connection, owner, "Table Tennis", NOW)
    serve = save_item(connection, owner, "https://youtu.be/serve", None, NOW, folder_id=tennis)
    grip = save_item(connection, owner, "grip notes", None, NOW, folder_id=tennis)
    loose = save_item(connection, owner, "a video in Unsorted", None, NOW)
    videos = create_folder(connection, owner, "Cool videos", NOW)
    already = save_item(connection, owner, "already in videos", None, NOW, folder_id=videos)

    request_folder_check(connection, videos, NOW)
    engine = Engine(matches=[serve, loose, already, 999])
    assert check_new_folders(connection, engine, NOW) == 1
    assert {candidate.id for candidate in engine.seen[0]} == {serve, grip}
    assert also_suggestions_for(connection, owner) == {serve: [videos]}
    assert check_new_folders(connection, engine, NOW) == 0

    accept_also(connection, owner, serve, videos)
    assert list_items(connection, owner, folder_id=videos)[0]["folder_ids"] in ([tennis, videos], [videos])
    assert {item["id"] for item in list_items(connection, owner, folder_id=videos)} == {serve, already}
    assert also_suggestions_for(connection, owner) == {}
    with pytest.raises(LookupError):
        dismiss_also(connection, owner, serve, videos)
    connection.close()


def test_a_failing_folder_check_is_retried_then_given_up(tmp_path: Path) -> None:
    connection = _db(tmp_path)
    owner = _user(connection)
    tennis = create_folder(connection, owner, "Table Tennis", NOW)
    serve = save_item(connection, owner, "serve video", None, NOW, folder_id=tennis)
    videos = create_folder(connection, owner, "Cool videos", NOW)
    request_folder_check(connection, videos, NOW)
    for _ in range(3):
        assert check_new_folders(connection, Engine(fail=True), NOW) == 1
    assert check_new_folders(connection, Engine(matches=[serve]), NOW) == 0

    request_folder_check(connection, videos, NOW)
    check_new_folders(connection, Engine(matches=[serve]), NOW)
    dismiss_also(connection, owner, serve, videos)
    assert also_suggestions_for(connection, owner) == {}
    connection.close()


def test_the_engines_match_a_new_folder() -> None:
    folder = FolderOption(7, "Cool videos", "Cool videos")
    candidates = [Candidate(1, "cool videos to watch later"), Candidate(2, "grip notes")]
    assert RulesSorter().match_folder(folder, candidates) == [1]

    prompts = []

    def model(system, prompt, image=None, schema=None):
        prompts.append((prompt, schema))
        return json.dumps({"item_ids": [2, 1, 99, "x"]})

    assert GeminiSorter(model, min_interval=0).match_folder(folder, candidates) == [2, 1]
    assert "New folder: Cool videos" in prompts[0][0]
    assert "2: grip notes" in prompts[0][0]
    assert prompts[0][1]["required"] == ["item_ids"]


def test_folders_are_ticked_and_suggested_through_the_pages(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as client:
        client.post("/register", data={"email": "a@example.com", "password": "password1"})
        client.post("/folders", data={"name": "Table Tennis", "parent": ""})
        client.post("/inbox", data={"body": "https://youtu.be/serve", "folder": "1"})
        client.post("/folders", data={"name": "Cool videos", "parent": ""})

        connection = connect()
        connection.execute("INSERT INTO folder_suggestions (item_id, folder_id) VALUES (1, 2)")
        connection.commit()
        connection.close()
        page = client.get("/inbox")
        assert "Also add to Cool videos" in page.text
        client.post("/items/1/also/2", data={"folder": ""})
        page = client.get("/inbox")
        assert "Also add to Cool videos" not in page.text
        assert 'href="/inbox?folder=1"' in page.text and 'href="/inbox?folder=2"' in page.text

        client.post("/items/1/folders", data={"folders": ["2"]})
        assert "youtu.be/serve" not in client.get("/inbox?folder=1").text
        client.post("/items/1/folders", data={})
        assert "youtu.be/serve" in client.get("/inbox?folder=unsorted").text
