import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import Connection, IntegrityError
from app.passwords import hash_password, verify_password

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    pass


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if not _EMAIL_PATTERN.fullmatch(cleaned):
        raise AccountError("Enter a valid email address.")
    return cleaned


def register_user(connection: Connection, email: str, password: str, now: datetime) -> int:
    cleaned = normalize_email(email)
    if len(password) < 8:
        raise AccountError("Use at least 8 characters for the password.")
    try:
        row = connection.execute(
            "INSERT INTO users (email, password_hash, created_at) OUTPUT INSERTED.id VALUES (?, ?, ?)",
            (cleaned, hash_password(password), _iso(now)),
        ).fetchone()
    except IntegrityError as error:
        connection.rollback()
        raise AccountError("An account with that email already exists.") from error
    connection.commit()
    return int(row["id"])


def authenticate(connection: Connection, email: str, password: str) -> int | None:
    try:
        cleaned = normalize_email(email)
    except AccountError:
        return None
    row = connection.execute(
        "SELECT id, password_hash FROM users WHERE email = ?",
        (cleaned,),
    ).fetchone()
    if row is None or not verify_password(password, row["password_hash"]):
        return None
    return int(row["id"])


def set_timezone(connection: Connection, user_id: int, name: str) -> bool:
    # The browser sends its IANA zone name, like Europe/Madrid. Anything that is
    # not a known zone is ignored, so a bad value cannot break reminders.
    cleaned = name.strip()
    try:
        ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    connection.execute(
        "UPDATE users SET timezone = ? WHERE id = ? AND ISNULL(timezone, '') != ?",
        (cleaned, user_id, cleaned),
    )
    connection.commit()
    return True


def user_timezone(connection: Connection, user_id: int) -> ZoneInfo:
    row = connection.execute("SELECT timezone FROM users WHERE id = ?", (user_id,)).fetchone()
    try:
        return ZoneInfo(row["timezone"]) if row is not None and row["timezone"] else ZoneInfo("UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()
