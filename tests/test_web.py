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
