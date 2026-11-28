"""Inbox's side of the seam: calls to the reminders service over HTTP.

Every call has a 2 second deadline (REMINDERS_TIMEOUT) for the whole call,
name lookup included. httpx's own timeout applies to each step on its own
(connecting, sending, reading) and not to the lookup, so one call to a
reminders service that was down took about 4 s; the call now runs in a worker
thread, and inbox stops waiting for it when the deadline passes (ADR-6). A
write that ran out of time may still reach reminders, as before, which is why
writes are not retried. When reminders does not answer in time,
cannot be reached, or fails, RemindersUnavailable is raised and inbox carries
on without times: a line still saves, the list shows "reminders
unavailable", and the sorter tries again later.

Each call carries a token with the inbox scope for the user it is about, made
here with the shared SESSION_SECRET, and the current request id so one id
finds the request in both services' logs.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as DeadlinePassed
from datetime import date, datetime

import httpx

from app.middleware import request_id
from app.tokens import issue

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8001"
DEFAULT_TIMEOUT_SECONDS = 2.0

# Tests can swap this for httpx.MockTransport.
_transport: httpx.BaseTransport | None = None

# Calls run here so inbox can stop waiting at the deadline. A call it gave up on
# finishes or fails on its own within httpx's per-step timeouts.
_calls = ThreadPoolExecutor(max_workers=8, thread_name_prefix="reminders-call")


class RemindersUnavailable(Exception):
    pass


class RemindersClient:
    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def for_user(self, user_id: int) -> dict[int, dict]:
        """The user's reminders by item id, after due ones were marked due."""
        response = self._call("GET", "/reminders", user_id)
        self._expect(response, 200)
        return {int(entry["item_id"]): entry for entry in response.json()}

    def schedule(
        self,
        user_id: int,
        item_id: int,
        remind_at: datetime,
        repeat_daily: bool = False,
        repeat_until: date | None = None,
        keep_existing: bool = False,
    ) -> bool:
        """True when the time was stored, False when keep_existing found one."""
        body = {
            "remind_at": remind_at.isoformat(),
            "repeat_daily": repeat_daily,
            "repeat_until": repeat_until.isoformat() if repeat_until else None,
            "keep_existing": keep_existing,
        }
        response = self._call("PUT", f"/reminders/{item_id}", user_id, json=body)
        if response.status_code == 409:
            return False
        if response.status_code == 400:
            raise ValueError(response.json().get("detail", "bad reminder time"))
        self._expect(response, 200)
        return True

    def remove(self, user_id: int, item_id: int) -> None:
        self._expect(self._call("DELETE", f"/reminders/{item_id}", user_id), 204)

    def complete(self, user_id: int, item_id: int) -> str:
        return self._action(user_id, item_id, "done")["status"]

    def snooze(self, user_id: int, item_id: int) -> datetime:
        return datetime.fromisoformat(self._action(user_id, item_id, "snooze")["snoozed_until"])

    def _action(self, user_id: int, item_id: int, action: str) -> dict:
        response = self._call("POST", f"/reminders/{item_id}/{action}", user_id)
        if response.status_code == 404:
            raise LookupError("reminder not found")
        if response.status_code == 409:
            raise ValueError(response.json().get("detail", "not allowed"))
        self._expect(response, 200)
        return response.json()

    def _call(self, method: str, path: str, user_id: int, json: dict | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {issue(user_id, 'inbox')}"}
        rid = request_id.get()
        if rid:
            headers["X-Request-ID"] = rid
        # The client is built here, before the deadline starts: building it
        # loads the TLS settings, which is not reminders' time.
        client = httpx.Client(base_url=self.base_url, timeout=self.timeout, transport=_transport)
        call = _calls.submit(self._send, client, method, path, headers, json)
        try:
            return call.result(timeout=self.timeout)
        except DeadlinePassed as error:
            log.warning("Reminders did not answer %s %s within %ss", method, path, self.timeout)
            raise RemindersUnavailable(f"no answer within {self.timeout}s") from error
        except httpx.HTTPError as error:
            log.warning("Reminders did not answer %s %s: %s", method, path, error)
            raise RemindersUnavailable(str(error)) from error

    @staticmethod
    def _send(client: httpx.Client, method: str, path: str, headers: dict, json: dict | None) -> httpx.Response:
        with client:
            return client.request(method, path, headers=headers, json=json)

    @staticmethod
    def _expect(response: httpx.Response, status: int) -> None:
        # Anything else (a 5xx, or a 401 from a SESSION_SECRET that differs
        # between the services) is treated like reminders being down.
        if response.status_code != status:
            log.warning("Reminders answered %s to %s", response.status_code, response.request.url.path)
            raise RemindersUnavailable(f"reminders answered {response.status_code}")


def default_client() -> RemindersClient:
    return RemindersClient(
        os.environ.get("REMINDERS_URL", DEFAULT_URL),
        float(os.environ.get("REMINDERS_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)),
    )
