from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.tokens import issue

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
START = (NOW + timedelta(hours=3)).isoformat()


@pytest.fixture
def client():
    app = create_app(run_migrations=False)
    app.state.clock = lambda: NOW
    with TestClient(app) as test_client:
        yield test_client


def _as(user_id: int, scope: str = "user") -> dict:
    return {"Authorization": f"Bearer {issue(user_id, scope)}"}


def test_every_call_needs_a_valid_token(client) -> None:
    assert client.get("/reminders").status_code == 401
    assert client.get("/reminders", headers={"Authorization": "Bearer nonsense"}).status_code == 401
    assert client.get("/reminders", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.get("/reminders", headers=_as(1)).json() == []


def test_only_inbox_may_create_or_delete_a_reminder(client) -> None:
    body = {"remind_at": START}
    assert client.put("/reminders/10", json=body, headers=_as(1)).status_code == 403
    assert client.delete("/reminders/10", headers=_as(1)).status_code == 403
    created = client.put("/reminders/10", json=body, headers=_as(1, "inbox"))
    assert created.status_code == 200
    assert created.json()["status"] == "scheduled"


def test_the_ui_lists_snoozes_and_finishes_with_the_persons_own_token(client) -> None:
    client.put("/reminders/10", json={"remind_at": NOW.isoformat()}, headers=_as(1, "inbox"))
    assert [entry["status"] for entry in client.get("/reminders", headers=_as(1)).json()] == ["due"]
    snoozed = client.post("/reminders/10/snooze", headers=_as(1))
    assert snoozed.json()["status"] == "snoozed"
    assert client.post("/reminders/10/done", headers=_as(1)).json()["status"] == "done"
    assert client.post("/reminders/10/snooze", headers=_as(1)).status_code == 409
    assert client.get("/reminders", params={"item_id": [10, 99]}, headers=_as(1)).json()[0]["item_id"] == 10


def test_errors_have_their_own_codes(client) -> None:
    assert client.post("/reminders/77/done", headers=_as(1)).status_code == 404
    assert client.post("/reminders/77/snooze", headers=_as(1)).status_code == 404
    client.put("/reminders/11", json={"remind_at": START}, headers=_as(1, "inbox"))
    early = client.post("/reminders/11/snooze", headers=_as(1))
    assert (early.status_code, early.json()["detail"]) == (409, "only a due reminder can be snoozed")
    bad = client.put(
        "/reminders/10", json={"remind_at": START, "repeat_until": "2026-10-01"}, headers=_as(1, "inbox")
    )
    assert bad.status_code == 400
    assert client.put("/reminders/10", json={"remind_at": "soon"}, headers=_as(1, "inbox")).status_code == 422
    client.put("/reminders/10", json={"remind_at": START}, headers=_as(1, "inbox"))
    kept = client.put("/reminders/10", json={"remind_at": START, "keep_existing": True}, headers=_as(1, "inbox"))
    assert kept.status_code == 409
    assert client.put("/reminders/10", json={"remind_at": START}, headers=_as(2, "inbox")).status_code == 404
    assert client.delete("/reminders/10", headers=_as(1, "inbox")).status_code == 204
    assert client.delete("/reminders/10", headers=_as(1, "inbox")).status_code == 204


def test_health_reports_the_database_and_the_version(client, monkeypatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc1234")
    assert client.get("/health").json() == {"status": "ok", "db": "ok", "version": "abc1234"}
    monkeypatch.setenv("DATABASE_URL", "mssql://nobody:wrong@127.0.0.1:1/nowhere")
    down = client.get("/health")
    assert (down.status_code, down.json()) == (503, {"status": "error", "db": "down", "version": "abc1234"})


def test_every_response_carries_a_request_id(client) -> None:
    assert len(client.get("/health").headers["X-Request-ID"]) == 12
    assert client.get("/health", headers={"X-Request-ID": "abc123"}).headers["X-Request-ID"] == "abc123"


def test_cors_lets_only_the_configured_ui_call_from_a_browser(monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://ui.example.net/, http://localhost:8080")
    with TestClient(create_app(run_migrations=False)) as test_client:
        preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
        allowed = test_client.options("/reminders/1/done", headers={"Origin": "https://ui.example.net", **preflight})
        assert allowed.headers["access-control-allow-origin"] == "https://ui.example.net"
        refused = test_client.options("/reminders/1/done", headers={"Origin": "https://evil.example", **preflight})
        assert "access-control-allow-origin" not in refused.headers


def test_shared_files_are_the_same_in_both_services() -> None:
    here = Path(__file__).resolve().parents[1] / "app"
    inbox = Path(__file__).resolve().parents[2] / "inbox" / "app"
    for name in ("db.py", "migrate.py", "tokens.py", "middleware.py"):
        assert (here / name).read_bytes() == (inbox / name).read_bytes(), f"{name} differs between the services"
