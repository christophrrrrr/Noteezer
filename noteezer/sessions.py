import hashlib
import hmac
import os

COOKIE_NAME = "session"


def session_secret() -> str:
    return os.environ.get("SESSION_SECRET", "dev-only-change-me")


def sign_user_id(user_id: int, secret: str | None = None) -> str:
    payload = str(user_id)
    signature = hmac.new(
        (secret or session_secret()).encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def read_user_id(cookie: str | None, secret: str | None = None) -> int | None:
    if not cookie or "." not in cookie:
        return None
    payload, _signature = cookie.split(".", 1)
    if not payload.isdigit():
        return None
    expected = sign_user_id(int(payload), secret)
    if not hmac.compare_digest(cookie, expected):
        return None
    return int(payload)
