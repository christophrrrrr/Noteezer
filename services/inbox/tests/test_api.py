"""The inbox JSON API that the static UI calls (ADR-8)."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import FolderList, ItemPage, TaskState
from app.db import connect
from app.links import LinkPreview, save_preview
from app.main import create_app
from app.reminders_client import default_client
from app.sorting import NewFolder, Part, Suggestion, sort_pending
from app.tokens import issue
from tests.conftest import sign_in

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
LATER = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)


class Answer:
    name = "fake"

    def __init__(self, suggestion: Suggestion) -> None:
        self.suggestion = suggestion

    def suggest(self, note, folders):
        return self.suggestion


@pytest.fixture
def client(monkeypatch):
    with TestClient(create_app()) as test_client:
        # Saves would wake the background sorter, which races the tests that
        # sort with a fake answer. Tests run the sorter themselves instead.
        monkeypatch.setattr(test_client.app.state.sort_worker, "wake", lambda: None)
        yield test_client


def _listed(client: TestClient, folder: str = "", q: str = "") -> dict[int, dict]:
    answer = client.get("/api/items", params={"folder": folder, "q": q})
    assert answer.status_code == 200, answer.text
    return {item["id"]: item for item in answer.json()["items"]}


def _sort(answer: Suggestion) -> None:
    connection = connect()
    try:
        sort_pending(connection, Answer(answer), datetime.now(timezone.utc))
    finally:
        connection.close()


def test_register_login_and_tokens(client: TestClient) -> None:
    assert client.get("/api/items").status_code == 401
    sign_in(client)
    assert client.post("/api/register", json={"email": "a@example.com", "password": "password1"}).status_code == 400
    assert client.post("/api/login", json={"email": "a@example.com", "password": "wrong-one"}).status_code == 401
    logged_in = client.post("/api/login", json={"email": "A@example.com", "password": "password1"})
    assert logged_in.status_code == 200

    for token in (logged_in.json()["token"] + "x", "nope", issue(1, "inbox")):
        answer = client.get("/api/items", headers={"Authorization": f"Bearer {token}"})
        assert answer.status_code == 401
        assert answer.json() == {"detail": "Log in again."}
    assert client.get("/api/items", headers={"Authorization": f"Bearer {logged_in.json()['token']}"}).status_code == 200


def test_a_saved_line_comes_back_with_its_kind_and_tags(client: TestClient) -> None:
    sign_in(client)
    created = client.post("/api/items", data={"body": "buy milk #home"})
    assert created.status_code == 201
    item = _listed(client)[created.json()["id"]]
    assert (item["body"], item["kind"], item["tags"]) == ("buy milk #home", "task", ["home"])
    assert client.post("/api/items", data={"body": "   "}).status_code == 400
    assert client.post("/api/items", data={"body": "x", "kind": "poem"}).status_code == 400
    assert client.post("/api/items", data={"body": "x", "remind_at": "soon"}).status_code == 400
    assert client.post("/api/items", data={"body": "x", "repeat_until": "May"}).status_code == 400
    assert client.post("/api/items", data={"body": "x", "folder_id": "99"}).json() == {
        "detail": "That folder no longer exists."
    }


def test_folders_hold_lines_and_unsorted_holds_the_rest(client: TestClient) -> None:
    sign_in(client)
    movies = client.post("/api/folders", json={"name": "Movies"}).json()["id"]
    client.post("/api/items", data={"body": "Dune part 3", "folder_id": str(movies)})
    client.post("/api/items", data={"body": "loose thought"})

    assert [item["body"] for item in _listed(client, str(movies)).values()] == ["Dune part 3"]
    assert [item["body"] for item in _listed(client, "unsorted").values()] == ["loose thought"]
    assert [item["body"] for item in _listed(client, q="dune").values()] == ["Dune part 3"]
    assert client.get("/api/items", params={"folder": "99"}).status_code == 404
    assert client.get("/api/items", params={"folder": "abc"}).status_code == 400

    folders = client.get("/api/folders").json()
    assert folders["total_count"] == 2 and folders["unsorted_count"] == 1
    assert folders["folders"][0] | {"count": 1} == folders["folders"][0]

    duplicate = client.post("/api/folders", json={"name": "movies"})
    assert duplicate.status_code == 400
    assert "already a folder" in duplicate.json()["detail"]


def test_folders_can_be_renamed_moved_and_deleted(client: TestClient) -> None:
    sign_in(client)
    uni = client.post("/api/folders", json={"name": "Uni"}).json()["id"]
    notes = client.post("/api/folders", json={"name": "Notes"}).json()["id"]
    assert client.put(f"/api/folders/{notes}/name", json={"name": "Lectures"}).status_code == 204
    assert client.put(f"/api/folders/{notes}/parent", json={"parent_id": uni}).status_code == 204
    assert [entry["path"] for entry in client.get("/api/folders").json()["folders"]] == ["Uni", "Uni / Lectures"]
    assert client.put(f"/api/folders/{uni}/parent", json={"parent_id": notes}).status_code == 400  # a loop
    assert client.put(f"/api/folders/{notes}/name", json={"name": " "}).status_code == 400
    assert client.delete(f"/api/folders/{uni}").status_code == 204
    assert client.get("/api/folders").json()["folders"] == []
    assert client.delete(f"/api/folders/{uni}").status_code == 404


def test_a_line_is_edited_ticked_off_and_deleted(client: TestClient) -> None:
    sign_in(client)
    task = client.post("/api/items", data={"body": "buy milk"}).json()["id"]
    edited = client.put(f"/api/items/{task}", json={"body": "buy oat milk", "kind": "task"})
    assert edited.status_code == 204
    assert client.post(f"/api/items/{task}/toggle").json() == {"task_done": True}
    assert _listed(client)[task]["body"] == "buy oat milk"
    assert client.put(f"/api/items/{task}", json={"body": "x", "kind": "poem"}).status_code == 400

    note = client.post("/api/items", data={"body": "a thought", "kind": "note"}).json()["id"]
    assert client.post(f"/api/items/{note}/toggle").status_code == 400
    assert client.delete(f"/api/items/{task}").status_code == 204
    assert client.delete(f"/api/items/{task}").status_code == 404
    assert client.put(f"/api/items/{task}", json={"body": "back?"}).status_code == 404


def test_nobody_reaches_another_persons_lines(client: TestClient) -> None:
    sign_in(client)
    item = client.post("/api/items", data={"body": "mine"}).json()["id"]
    folder = client.post("/api/folders", json={"name": "Mine"}).json()["id"]
    sign_in(client, "b@example.com")
    assert _listed(client) == {}
    for method, path in [
        ("PUT", f"/api/items/{item}"),
        ("DELETE", f"/api/items/{item}"),
        ("POST", f"/api/items/{item}/toggle"),
        ("PUT", f"/api/items/{item}/folders"),
        ("POST", f"/api/items/{item}/accept"),
        ("POST", f"/api/items/{item}/create-folder"),
        ("POST", f"/api/items/{item}/remind"),
        ("POST", f"/api/items/{item}/unsplit"),
        ("POST", f"/api/items/{item}/also/{folder}"),
        ("PUT", f"/api/folders/{folder}/name"),
        ("DELETE", f"/api/folders/{folder}"),
        ("GET", f"/api/items/{item}/image"),
    ]:
        body = {"body": "x", "folder_ids": [], "name": "x"}
        assert client.request(method, path, json=body).status_code == 404, path


def test_the_sorter_files_lines_and_the_rest_wait_for_review(client: TestClient) -> None:
    sign_in(client)
    client.post("/api/folders", json={"name": "Movies"})
    filed = client.post("/api/items", data={"body": "Dune part 3 #movies"}).json()["id"]
    unclear = client.post("/api/items", data={"body": "quick sort notes"}).json()["id"]
    client.app.state.sort_worker.run_once()

    listed = _listed(client)
    assert (listed[filed]["sort"]["status"], listed[filed]["sort"]["engine"]) == ("filed", "rules")
    assert listed[filed]["folders"] == [{"id": 1, "path": "Movies"}]
    assert listed[unclear]["sort"]["status"] == "review"
    assert client.post("/api/sorting/retry").status_code == 204
    assert _listed(client)[unclear]["sort"] is None  # waits to be sorted again


def test_suggestions_are_accepted_or_turned_into_a_folder(client: TestClient) -> None:
    sign_in(client)
    uni = client.post("/api/folders", json={"name": "Uni"}).json()["id"]
    exam = client.post("/api/items", data={"body": "exam on friday"}).json()["id"]
    _sort(Suggestion(uni, 0.2))
    assert _listed(client)[exam]["sort"]["folder_path"] == "Uni"
    assert client.post(f"/api/items/{exam}/accept").status_code == 204
    assert _listed(client)[exam]["folder_ids"] == [uni]

    pasta = client.post("/api/items", data={"body": "pasta recipe"}).json()["id"]
    _sort(Suggestion(None, 0.0, new_folder=NewFolder("Cooking", uni)))
    assert _listed(client)[pasta]["sort"]["proposed_path"] == "Uni / Cooking"
    assert client.post(f"/api/items/{pasta}/create-folder").status_code == 204
    assert "Uni / Cooking" in [entry["path"] for entry in client.get("/api/folders").json()["folders"]]


def test_a_split_note_can_be_put_back(client: TestClient) -> None:
    sign_in(client)
    client.post("/api/folders", json={"name": "Home"})  # with a folder, new lines wait to be sorted
    client.post("/api/items", data={"body": "buy milk and call mom"})
    _sort(Suggestion(None, 0.0, parts=(Part("buy milk", "task", None, 0.0), Part("call mom", "task", None, 0.0))))
    parts = [item for item in _listed(client).values() if item["split_from"]]
    assert len(parts) == 2
    assert client.post(f"/api/items/{parts[0]['id']}/unsplit").status_code == 204
    assert [item["body"] for item in _listed(client).values()] == ["buy milk and call mom"]


def test_folders_are_ticked_and_also_suggested(client: TestClient) -> None:
    sign_in(client)
    tennis = client.post("/api/folders", json={"name": "Table Tennis"}).json()["id"]
    item = client.post("/api/items", data={"body": "https://youtu.be/serve", "folder_id": str(tennis)}).json()["id"]
    videos = client.post("/api/folders", json={"name": "Cool videos"}).json()["id"]
    music = client.post("/api/folders", json={"name": "Music"}).json()["id"]

    connection = connect()
    for folder in (videos, music):
        connection.execute("INSERT INTO folder_suggestions (item_id, folder_id) VALUES (?, ?)", (item, folder))
    connection.commit()
    connection.close()
    assert [extra["path"] for extra in _listed(client)[item]["also"]] == ["Cool videos", "Music"]
    assert client.post(f"/api/items/{item}/also/{videos}").status_code == 204
    assert client.delete(f"/api/items/{item}/also/{music}").status_code == 204
    listed = _listed(client)[item]
    assert listed["also"] == [] and sorted(listed["folder_ids"]) == [tennis, videos]

    assert client.put(f"/api/items/{item}/folders", json={"folder_ids": [videos]}).status_code == 204
    assert item not in _listed(client, str(tennis))
    assert client.put(f"/api/items/{item}/folders", json={"folder_ids": []}).status_code == 204
    assert item in _listed(client, "unsorted")


def test_the_date_picker_is_read_in_the_persons_timezone(client: TestClient) -> None:
    sign_in(client, timezone="Europe/Madrid")
    item = client.post("/api/items", data={"body": "remind call mom", "remind_at": "2026-12-05T15:00"}).json()["id"]
    listed = client.get("/api/items").json()
    assert listed["timezone"] == "Europe/Madrid"
    assert listed["items"][0]["remind_label"] == "Sat 5 Dec, 15:00"
    assert default_client().for_user(1)[item]["remind_at"].startswith("2026-12-05T14:00:00")

    assert client.put("/api/me/timezone", json={"timezone": "America/New_York"}).status_code == 204
    assert client.get("/api/items").json()["items"][0]["remind_label"] == "Sat 5 Dec, 09:00"
    assert client.put("/api/me/timezone", json={"timezone": "Mars/Base"}).status_code == 204  # kept as it was
    assert client.get("/api/items").json()["timezone"] == "America/New_York"


def test_a_suggested_time_waits_while_reminders_is_down(client: TestClient, monkeypatch) -> None:
    sign_in(client)
    client.post("/api/folders", json={"name": "Health"})
    item = client.post("/api/items", data={"body": "dentist on saturday", "kind": "note"}).json()["id"]
    _sort(Suggestion(None, 0.0, remind_at=LATER))
    assert _listed(client)[item]["sort"]["remind_label"]
    real = default_client().base_url
    monkeypatch.setenv("REMINDERS_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("REMINDERS_TIMEOUT", "0.3")
    assert client.post(f"/api/items/{item}/remind").status_code == 503
    monkeypatch.setenv("REMINDERS_URL", real)
    assert client.post(f"/api/items/{item}/remind").status_code == 204
    assert _listed(client)[item]["kind"] == "reminder"


def test_the_ui_finishes_reminders_in_the_reminders_service_with_the_same_token(client: TestClient) -> None:
    # The UI calls reminders itself for done and snooze (ADR-6), with the token
    # inbox gave it at login.
    token = sign_in(client)
    item = client.post("/api/items", data={"body": "remind stretch", "remind_at": "2020-01-01T09:00"}).json()["id"]
    reminders = httpx.Client(base_url=default_client().base_url, headers={"Authorization": f"Bearer {token}"})
    assert reminders.post(f"/reminders/{item}/snooze").status_code == 200
    assert _listed(client)[item]["status"] == "snoozed"
    assert reminders.post(f"/reminders/{item}/done").json()["status"] == "done"
    assert reminders.post(f"/reminders/{item}/snooze").status_code == 409
    assert reminders.put(f"/reminders/{item}", json={"remind_at": LATER.isoformat()}).status_code == 403


def test_images_are_uploaded_and_fetched_with_the_token(client: TestClient) -> None:
    sign_in(client)
    image = {"image": ("shot.png", PNG, "image/png")}
    saved = client.post("/api/items", data={"body": "", "ai_image": "false"}, files=image)
    assert saved.status_code == 201
    item = _listed(client)[saved.json()["id"]]
    assert item["body"] == "Screenshot"
    assert item["image"]["ai_allowed"] is False

    picture = client.get(f"/api/items/{item['id']}/image")
    assert picture.content == PNG
    assert picture.headers["content-type"] == "image/png"
    assert picture.headers["x-content-type-options"] == "nosniff"
    assert client.get(f"/api/items/{item['id']}/image", headers={"Authorization": ""}).status_code == 401

    refused = client.post("/api/items", data={"body": ""}, files={"image": ("x.svg", b"<svg/>", "image/png")})
    assert refused.json() == {"detail": "Use a PNG, JPEG, WebP, or GIF image."}
    # What a browser sends when no file was chosen: a file part with an empty name.
    boundary = "----NoFileChosen"
    no_file = "\r\n".join(
        [
            f"--{boundary}",
            'Content-Disposition: form-data; name="body"',
            "",
            "",
            f"--{boundary}",
            'Content-Disposition: form-data; name="image"; filename=""',
            "Content-Type: application/octet-stream",
            "",
            "",
            f"--{boundary}--",
            "",
        ]
    ).encode()
    empty = client.post(
        "/api/items", content=no_file, headers={"content-type": f"multipart/form-data; boundary={boundary}"}
    )
    assert empty.json() == {"detail": "Write something before saving."}


def test_sharing_saves_each_piece_once_and_screenshots_too(client: TestClient) -> None:
    assert client.post("/api/share", data={"text": "hello"}).status_code == 401
    sign_in(client)
    assert client.post("/api/share", data={"text": "https://youtu.be/mG-xk4SLI80"}).status_code == 201
    client.post("/api/share", data={"title": "Bloque 21", "text": "Bloque 21", "url": "https://bloque21.example/comp"})
    assert client.post("/api/share", data={"title": "", "text": "", "url": ""}).status_code == 204
    shot = client.post("/api/share", files={"image": ("shot.png", PNG, "image/png")})
    assert shot.status_code == 201
    assert client.post("/api/share", files={"image": ("x.png", b"<svg/>", "image/png")}).status_code == 400
    assert [item["body"] for item in _listed(client, "unsorted").values()][::-1] == [
        "https://youtu.be/mG-xk4SLI80",
        "Bloque 21\nhttps://bloque21.example/comp",
        "Screenshot",
    ]
    assert _listed(client)[shot.json()["id"]]["image"]["ai_allowed"] is True


def test_the_ui_address_may_call_the_api_from_the_browser(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:8080")
    with TestClient(create_app()) as client:
        preflight = client.options(
            "/api/items/1",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == "http://localhost:8080"
        other = client.options(
            "/api/items/1", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "PUT"}
        )
        assert "access-control-allow-origin" not in other.headers


def test_a_new_account_starts_with_the_examples_unless_it_asks_not_to(client) -> None:
    answer = client.post("/api/register", json={"email": "new@example.com", "password": "password1"})
    headers = {"Authorization": f"Bearer {answer.json()['token']}"}
    folders = client.get("/api/folders", headers=headers).json()
    assert [folder["name"] for folder in folders["folders"]] == ["Examples"]
    assert client.get("/api/folders", headers=headers).json()["total_count"] == 3

    answer = client.post("/api/register", json={"email": "old@example.com", "password": "password1", "examples": False})
    headers = {"Authorization": f"Bearer {answer.json()['token']}"}
    assert client.get("/api/folders", headers=headers).json()["total_count"] == 0


def test_every_answer_matches_its_documented_schema(client: TestClient) -> None:
    # openapi.yaml documents these answers with the models in api.py. Every
    # optional part is filled in here, and the models forbid extra fields, so a
    # field added, renamed, or dropped in the code fails this test.
    sign_in(client)
    projects = client.post("/api/folders", json={"name": "Projects"}).json()["id"]
    game = client.post("/api/folders", json={"name": "Game", "parent_id": projects}).json()["id"]
    task = client.post("/api/items", data={"body": "todo fix hitbox #game", "kind": "task"}).json()["id"]
    client.post("/api/items", data={"body": "remind dentist", "remind_at": f"{LATER:%Y-%m-%dT%H:%M}"})
    client.post("/api/items", data={"body": "a screenshot"}, files={"image": ("shot.png", PNG, "image/png")})
    client.post("/api/items", data={"body": "dentist on saturday"})
    link = client.post("/api/items", data={"body": "https://example.com/serve", "folder_id": str(game)}).json()["id"]
    connection = connect()
    try:
        preview = LinkPreview("https://example.com/serve", "Serve", "A serve drill", "example.com")
        save_preview(connection, link, "https://example.com/serve", preview, datetime.now(timezone.utc))
        connection.execute("INSERT INTO folder_suggestions (item_id, folder_id) VALUES (?, ?)", (link, projects))
        connection.commit()
    finally:
        connection.close()
    _sort(Suggestion(None, 0.0, new_folder=NewFolder("Health", projects), remind_at=LATER))

    assert TaskState.model_validate(client.post(f"/api/items/{task}/toggle").json()).task_done is True
    folders = FolderList.model_validate(client.get("/api/folders").json())
    assert {(entry.path, entry.depth) for entry in folders.folders} == {("Projects", 0), ("Projects / Game", 1)}
    page = ItemPage.model_validate(client.get("/api/items").json())
    items = page.items
    assert any(item.preview for item in items) and any(item.image for item in items)
    assert any(item.sort and item.sort.proposed_path == "Projects / Health" for item in items)
    assert any(item.also for item in items) and any(item.tags for item in items)
    assert any(item.remind_at for item in items)
