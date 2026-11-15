"""Signed bearer tokens, checked by both services with the same SESSION_SECRET.

A token is five parts joined by dots:

    v1.<user id>.<expires, unix seconds>.<scope>.<signature>

The signature is HMAC-SHA256 of the first four parts with SESSION_SECRET, so
nobody can change the user id, the expiry, or the scope without the secret.
The scope says who is calling:

    user   the person, from the login cookie or the UI (lasts 90 days)
    inbox  the inbox service calling reminders for that person (lasts 60 s)

Reminders only lets the inbox scope create or delete reminders, because only
inbox knows which items exist and belong to whom (ADR-7).

This file is identical in services/inbox and services/reminders; a test in
each service fails if the two copies differ.
"""

import hashlib
import hmac
import os
import time
from dataclasses import dataclass

VERSION = "v1"
SCOPES = ("user", "inbox")
USER_LIFETIME_SECONDS = 90 * 24 * 60 * 60
INBOX_LIFETIME_SECONDS = 60


@dataclass(frozen=True)
class Claims:
    user_id: int
    scope: str


def session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET", "")
    if not secret:
        raise RuntimeError("SESSION_SECRET is not set. Both services need the same long random value.")
    return secret


def issue(user_id: int, scope: str = "user", lifetime: int | None = None, now: float | None = None) -> str:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if lifetime is None:
        lifetime = USER_LIFETIME_SECONDS if scope == "user" else INBOX_LIFETIME_SECONDS
    expires = int((time.time() if now is None else now) + lifetime)
    payload = f"{VERSION}.{int(user_id)}.{expires}.{scope}"
    return f"{payload}.{_sign(payload)}"


def verify(token: str | None, now: float | None = None) -> Claims | None:
    """The claims of a valid, unexpired token, or None for anything else."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 5:
        return None
    version, user_id, expires, scope, signature = parts
    if version != VERSION or not user_id.isdigit() or not expires.isdigit() or scope not in SCOPES:
        return None
    if not hmac.compare_digest(signature, _sign(f"{version}.{user_id}.{expires}.{scope}")):
        return None
    if int(expires) <= (time.time() if now is None else now):
        return None
    return Claims(int(user_id), scope)


def from_header(authorization: str | None) -> str | None:
    """The token in an `Authorization: Bearer <token>` header."""
    if not authorization:
        return None
    kind, _, token = authorization.partition(" ")
    if kind.lower() != "bearer":
        return None
    return token.strip() or None


def _sign(payload: str) -> str:
    return hmac.new(session_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
