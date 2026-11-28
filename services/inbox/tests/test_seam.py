"""The seam between inbox and reminders (ADR-6, ADR-7).

The first tests use the real reminders service that conftest starts. The rest
point inbox at a reminders that is down, slow, or misconfigured and check that
inbox keeps working without times.
"""

import socket
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import reminders_client
from app.db import connect
from app.folders import create_folder
from app.inbox import delete_item, list_items, save_item, update_item
from app.main import create_app
from app.middleware import request_id
from app.reminders_client import RemindersClient, RemindersUnavailable, default_client
from app.sorting import Suggestion, accept_reminder, results_for, sort_pending
from app.tokens import verify
from app.users import register_user
from tests.conftest import REMINDERS_DB, sign_in

# The reminders service uses the real clock, so these times are relative to it.
NOW = datetime.now(timezone.utc).replace(microsecond=0)
LATER = NOW + timedelta(days=2)
DOWN = "http://127.0.0.1:1"


class Answer:
    name = "fake"

    def __init__(self, suggestion: Suggestion) -> None:
        self.suggestion = suggestion

    def suggest(self, note, folders):
        return self.suggestion


def _down(monkeypatch) -> None:
    # Nothing listens on port 1. Windows retries a refused connection for about
    # 2 s, so the timeout is shortened to keep these tests quick.
    monkeypatch.setenv("REMINDERS_URL", DOWN)
    monkeypatch.setenv("REMINDERS_TIMEOUT", "0.3")


def _user(connection, email: str = "a@example.com") -> int:
    return register_user(connection, email, "password1", NOW)


def _reminder_rows() -> list[tuple]:
    connection = connect(REMINDERS_DB)
    try:
        rows = connection.execute("SELECT item_id, user_id, remind_at, status FROM reminders ORDER BY item_id")
        return [tuple(row) for row in rows]
    finally:
        connection.close()


def _needs_time(connection, item_id: int) -> int:
    return connection.execute("SELECT needs_time FROM items WHERE id = ?", (item_id,)).fetchone()["needs_time"]


def test_a_time_is_stored_in_reminders_with_its_owner_and_goes_with_the_item() -> None:
    connection = connect()
    owner = _user(connection)
    _user(connection, "b@example.com")
    call = save_item(connection, owner, "remind call the bank", LATER, NOW)
    water = save_item(connection, owner, "remind drink water", NOW - timedelta(minutes=1), NOW)
    assert _reminder_rows() == [
        (call, owner, LATER.isoformat(), "scheduled"),
        (water, owner, (NOW - timedelta(minutes=1)).isoformat(), "due"),
    ]
    assert _needs_time(connection, call) == 0
    assert [item["id"] for item in list_items(connection, owner)][0] == water  # due is pinned first

    update_item(connection, owner, water, "drink water", None, NOW)  # no longer a reminder
    delete_item(connection, owner, call)
    assert _reminder_rows() == []
    connection.close()


def test_the_list_says_reminders_are_unavailable_and_still_answers(monkeypatch) -> None:
    with TestClient(create_app()) as client:
        sign_in(client)
        client.post("/api/items", data={"body": "remind stretch", "remind_at": "2030-01-01T09:00"})
        assert client.get("/api/items").json()["reminders_down"] is False
        _down(monkeypatch)
        listed = client.get("/api/items").json()
        assert listed["reminders_down"] is True
        assert listed["items"][0]["body"] == "remind stretch"
        assert client.post("/api/items", data={"body": "remind call mom"}).status_code == 201


def test_a_line_still_saves_when_reminders_is_down(monkeypatch) -> None:
    _down(monkeypatch)
    connection = connect()
    owner = _user(connection)
    item_id = save_item(connection, owner, "remind call the bank", LATER, NOW)
    save_item(connection, owner, "a plain note", None, NOW)

    listed = {item["id"]: item for item in list_items(connection, owner)}
    assert (listed[item_id]["kind"], listed[item_id]["status"]) == ("reminder", None)
    assert listed[item_id]["reminder_unavailable"] is True
    assert not any(item["reminder_unavailable"] for item in listed.values() if item["id"] != item_id)
    assert _needs_time(connection, item_id) == 1  # waits as "no time yet"
    delete_item(connection, owner, item_id)  # the leftover reminder, if any, is harmless
    connection.close()


def test_the_sorter_retries_a_time_it_could_not_store(monkeypatch) -> None:
    connection = connect()
    owner = _user(connection)
    item_id = save_item(connection, owner, "remind me saturday 3pm call mom", None, NOW)
    real = default_client().base_url
    _down(monkeypatch)
    assert sort_pending(connection, Answer(Suggestion(None, 0.0, remind_at=LATER)), NOW) == 1
    assert results_for(connection, owner)[item_id]["status"] == "retry"
    assert _needs_time(connection, item_id) == 1

    monkeypatch.setenv("REMINDERS_URL", real)
    monkeypatch.delenv("REMINDERS_TIMEOUT")
    sort_pending(connection, Answer(Suggestion(None, 0.0, remind_at=LATER)), NOW + timedelta(hours=1))
    assert _reminder_rows() == [(item_id, owner, LATER.isoformat(), "scheduled")]
    assert _needs_time(connection, item_id) == 0
    connection.close()


def test_accepting_a_suggested_time_keeps_the_suggestion_while_reminders_is_down(monkeypatch) -> None:
    connection = connect()
    owner = _user(connection)
    create_folder(connection, owner, "Uni", NOW)
    dentist = save_item(connection, owner, "dentist on saturday", None, NOW)
    sort_pending(connection, Answer(Suggestion(None, 0.0, remind_at=LATER)), NOW)
    real = default_client().base_url
    _down(monkeypatch)
    with pytest.raises(RemindersUnavailable):
        accept_reminder(connection, owner, dentist, NOW)
    assert results_for(connection, owner)[dentist]["suggested_remind_at"] == LATER.isoformat()
    monkeypatch.setenv("REMINDERS_URL", real)
    monkeypatch.delenv("REMINDERS_TIMEOUT")
    assert accept_reminder(connection, owner, dentist, NOW) == LATER
    connection.close()


def test_a_slow_reminders_is_given_up_on_after_the_timeout() -> None:
    # A socket that accepts the connection but never answers.
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        client = RemindersClient(f"http://127.0.0.1:{silent.getsockname()[1]}", timeout=0.5)
        started = time.monotonic()
        with pytest.raises(RemindersUnavailable):
            client.for_user(1)
        assert time.monotonic() - started < 2


def test_the_timeout_bounds_the_whole_call(monkeypatch) -> None:
    # httpx's timeout covers each step on its own and not the name lookup, so a
    # slow lookup followed by a slow answer took twice the timeout (ADR-6). A
    # transport that stalls stands in for both: no step of it ever times out.
    def stall(request: httpx.Request) -> httpx.Response:
        time.sleep(1.5)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(reminders_client, "_transport", httpx.MockTransport(stall))
    client = RemindersClient("http://reminders", timeout=0.3)
    started = time.monotonic()
    with pytest.raises(RemindersUnavailable, match="within"):
        client.for_user(1)
    assert time.monotonic() - started < 1


@pytest.mark.parametrize("status", [500, 503, 401])
def test_an_error_answer_counts_as_unavailable(monkeypatch, status) -> None:
    monkeypatch.setattr(reminders_client, "_transport", httpx.MockTransport(lambda request: httpx.Response(status)))
    with pytest.raises(RemindersUnavailable):
        RemindersClient("http://reminders").for_user(1)


def test_each_call_carries_an_inbox_token_for_the_user_and_the_request_id(monkeypatch) -> None:
    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "PUT":
            return httpx.Response(409, json={"detail": "has a time"})
        if request.url.path.endswith("/snooze"):
            return httpx.Response(200, json={"snoozed_until": LATER.isoformat()})
        return httpx.Response(404)

    monkeypatch.setattr(reminders_client, "_transport", httpx.MockTransport(answer))
    client = RemindersClient("http://reminders")
    token = request_id.set("req-42")
    try:
        assert client.schedule(7, 3, LATER, keep_existing=True) is False
        assert client.snooze(7, 3) == LATER
        with pytest.raises(LookupError):
            client.complete(7, 3)
    finally:
        request_id.reset(token)
    claims = verify(seen[0].headers["authorization"].removeprefix("Bearer "))
    assert (claims.user_id, claims.scope) == (7, "inbox")
    assert {request.headers["x-request-id"] for request in seen} == {"req-42"}


def test_health_reports_the_database_and_responses_carry_a_request_id(monkeypatch) -> None:
    with TestClient(create_app()) as client:
        up = client.get("/health")
        assert up.json() == {"status": "ok", "db": "ok", "version": "dev"}
        assert len(up.headers["X-Request-ID"]) == 12
        # Health is about inbox's own database only: reminders being down
        # does not make inbox unhealthy.
        monkeypatch.setenv("REMINDERS_URL", "http://127.0.0.1:1")
        assert client.get("/health").status_code == 200
        monkeypatch.setenv("DATABASE_URL", "mssql://nobody:wrong@127.0.0.1:1/nowhere")
        down = client.get("/health")
        assert (down.status_code, down.json()["db"]) == (503, "down")


def test_shared_files_are_the_same_in_both_services() -> None:
    from pathlib import Path

    here = Path(__file__).resolve().parents[1] / "app"
    reminders = Path(__file__).resolve().parents[2] / "reminders" / "app"
    for name in ("db.py", "migrate.py", "tokens.py", "middleware.py"):
        assert (here / name).read_bytes() == (reminders / name).read_bytes(), f"{name} differs between the services"
