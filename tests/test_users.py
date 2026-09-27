from datetime import datetime, timezone
from pathlib import Path

import pytest

from noteezer.db import connect, init_db
from noteezer.passwords import hash_password, verify_password
from noteezer.sessions import read_user_id, sign_user_id
from noteezer.users import AccountError, authenticate, register_user

NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def test_password_hash_verifies_and_rejects_the_wrong_password() -> None:
    stored = hash_password("password1")
    assert verify_password("password1", stored)
    assert not verify_password("password2", stored)


def test_register_and_login_use_the_email(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    user_id = register_user(connection, "A@Example.com", "password1", NOW)
    assert authenticate(connection, "a@example.com", "password1") == user_id
    assert authenticate(connection, "a@example.com", "nope-nope") is None
    with pytest.raises(AccountError):
        register_user(connection, "a@example.com", "password1", NOW)
    connection.close()


def test_register_rejects_a_bad_email_and_a_short_password(tmp_path: Path) -> None:
    connection = connect(tmp_path / "noteezer.db")
    init_db(connection)
    with pytest.raises(AccountError):
        register_user(connection, "not-an-email", "password1", NOW)
    with pytest.raises(AccountError):
        register_user(connection, "a@example.com", "short", NOW)
    assert authenticate(connection, "not-an-email", "password1") is None
    connection.close()


def test_session_cookie_rejects_a_tampered_value() -> None:
    cookie = sign_user_id(4, secret="test-secret")
    assert read_user_id(cookie, secret="test-secret") == 4
    assert read_user_id(cookie + "x", secret="test-secret") is None
    assert read_user_id("nope", secret="test-secret") is None
