import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.db import connect
from app.passwords import hash_password, verify_password
from app.users import AccountError, authenticate, normalize_email, register_user

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


@pytest.mark.parametrize(
    "email",
    ["a@example.com", "First.Last+notes@mail.example.co.uk", "x@b.io"],
)
def test_ordinary_addresses_are_accepted(email: str) -> None:
    assert normalize_email(email) == email.lower()


@pytest.mark.parametrize(
    "email",
    ["a@example", "a@@example.com", "a@example..com", "a b@example.com", "@example.com", "a@.com"],
)
def test_malformed_addresses_are_refused(email: str) -> None:
    with pytest.raises(AccountError):
        normalize_email(email)


def test_a_crafted_address_is_refused_quickly() -> None:
    # CodeQL's py/polynomial-redos example: the old pattern needed about 16 s
    # for 64 KB of this, and 0.6 s for 16 KB.
    crafted = "!@!." + "!." * 8000 + " "
    started = time.perf_counter()
    with pytest.raises(AccountError):
        normalize_email(crafted)
    with pytest.raises(AccountError):
        normalize_email(crafted[:300])
    assert time.perf_counter() - started < 0.1
