from pathlib import Path

from fastapi.testclient import TestClient

from noteezer.main import create_app


def test_register_then_save_shows_the_sorted_line(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as client:
        created = client.post(
            "/register",
            data={"email": "a@example.com", "password": "password1"},
            follow_redirects=False,
        )
        assert created.status_code == 303
        page = client.post(
            "/inbox",
            data={"body": "buy milk #home", "kind": "", "remind_at": ""},
            follow_redirects=True,
        )
        assert page.status_code == 200
        assert "buy milk #home" in page.text
        assert "task" in page.text
        assert "#home" in page.text


def test_folder_page_saves_into_that_folder(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as client:
        client.post("/register", data={"email": "a@example.com", "password": "password1"})
        created = client.post("/folders", data={"name": "Movies", "parent": ""}, follow_redirects=False)
        assert created.status_code == 303
        folder_url = created.headers["location"]
        assert folder_url.startswith("/inbox?folder=")
        folder_id = folder_url.split("=")[1]

        client.post("/inbox", data={"body": "Dune part 3", "folder": folder_id})
        client.post("/inbox", data={"body": "loose thought", "folder": ""})
        inside = client.get(folder_url)
        assert "Dune part 3" in inside.text
        assert "loose thought" not in inside.text
        unsorted = client.get("/inbox?folder=unsorted")
        assert "loose thought" in unsorted.text
        assert "Dune part 3" not in unsorted.text

        duplicate = client.post("/folders", data={"name": "movies", "parent": ""})
        assert duplicate.status_code == 400
        assert "already a folder" in duplicate.text


def test_a_saved_line_is_sorted_in_the_background(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as client:
        client.post("/register", data={"email": "a@example.com", "password": "password1"})
        client.post("/folders", data={"name": "Movies", "parent": ""})
        client.post("/inbox", data={"body": "Dune part 3 #movies", "folder": ""})
        client.post("/inbox", data={"body": "quick sort notes", "folder": ""})
        client.app.state.sort_worker.run_once()

        everything = client.get("/inbox")
        assert "filed by rules" in everything.text
        unsorted = client.get("/inbox?folder=unsorted")
        assert "quick sort notes" in unsorted.text
        assert "No folder matched." in unsorted.text
        assert "Dune part 3" not in unsorted.text


def test_the_date_picker_is_read_in_the_browser_timezone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    with TestClient(create_app()) as client:
        client.post(
            "/register",
            data={"email": "a@example.com", "password": "password1", "tz": "Europe/Madrid"},
        )
        client.post(
            "/inbox",
            data={"body": "remind call mom", "remind_at": "2026-12-05T15:00", "tz": "Europe/Madrid"},
        )
        page = client.get("/inbox")
        assert "Sat 5 Dec, 15:00" in page.text

        from noteezer.db import connect

        connection = connect()
        stored = connection.execute("SELECT remind_at FROM reminders").fetchone()["remind_at"]
        connection.close()
        assert stored.startswith("2026-12-05T14:00:00")
