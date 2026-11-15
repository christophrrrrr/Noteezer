from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import create_app
from app.passwords import hash_password, verify_password
from app.tokens import issue
from app.users import AccountError, authenticate, register_user

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def test_password_hash_verifies_and_rejects_the_wrong_password() -> None:
    stored = hash_password("password1")
    assert verify_password("password1", stored)
    assert not verify_password("password2", stored)


def test_register_and_login_use_the_email(tmp_path: Path) -> None:
    connection = connect()
    user_id = register_user(connection, "A@Example.com", "password1", NOW)
    assert authenticate(connection, "a@example.com", "password1") == user_id
    assert authenticate(connection, "a@example.com", "nope-nope") is None
    with pytest.raises(AccountError):
        register_user(connection, "a@example.com", "password1", NOW)
    connection.close()


def test_register_rejects_a_bad_email_and_a_short_password(tmp_path: Path) -> None:
    connection = connect()
    with pytest.raises(AccountError):
        register_user(connection, "not-an-email", "password1", NOW)
    with pytest.raises(AccountError):
        register_user(connection, "a@example.com", "short", NOW)
    assert authenticate(connection, "not-an-email", "password1") is None
    connection.close()


def test_only_a_valid_user_token_opens_the_inbox() -> None:
    connection = connect()
    user_id = register_user(connection, "a@example.com", "password1", NOW)
    connection.close()
    with TestClient(create_app()) as client:
        for token in (issue(user_id) + "x", "nope", issue(user_id, "inbox")):
            client.cookies.set("session", token)
            assert client.get("/inbox", follow_redirects=False).headers["location"] == "/login"
        client.cookies.clear()
        bearer = client.get("/inbox", headers={"Authorization": f"Bearer {issue(user_id)}"}, follow_redirects=False)
        assert bearer.status_code == 200
