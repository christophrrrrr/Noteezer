import pytest

from app.tokens import Claims, from_header, issue, verify

NOW = 1_800_000_000.0


def test_a_token_names_its_user_and_scope_until_it_expires() -> None:
    token = issue(42, "inbox", lifetime=60, now=NOW)
    assert verify(token, now=NOW + 59) == Claims(42, "inbox")
    assert verify(token, now=NOW + 60) is None
    assert verify(issue(42, now=NOW), now=NOW + 89 * 24 * 3600) == Claims(42, "user")


def test_a_changed_or_foreign_token_is_refused(monkeypatch) -> None:
    token = issue(42, now=NOW)
    version, user, expires, scope, signature = token.split(".")
    for forged in (
        f"{version}.43.{expires}.{scope}.{signature}",
        f"{version}.{user}.{int(expires) + 1}.{scope}.{signature}",
        f"{version}.{user}.{expires}.inbox.{signature}",
        f"v2.{user}.{expires}.{scope}.{signature}",
        f"{version}.{user}.{expires}.admin.{signature}",
        "not-a-token",
        "",
        None,
    ):
        assert verify(forged, now=NOW) is None
    monkeypatch.setenv("SESSION_SECRET", "another-secret")
    assert verify(token, now=NOW) is None


def test_a_missing_secret_stops_signing(monkeypatch) -> None:
    monkeypatch.delenv("SESSION_SECRET")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        issue(1)
    with pytest.raises(ValueError):
        monkeypatch.setenv("SESSION_SECRET", "x")
        issue(1, "admin")


def test_the_bearer_header_is_read() -> None:
    assert from_header("Bearer abc") == "abc"
    assert from_header("bearer  abc ") == "abc"
    assert from_header("Basic abc") is None
    assert from_header("Bearer") is None
    assert from_header(None) is None
