"""Imports the Assignment 1 notes (one SQLite file) into the deployed system.

Assignment 1 kept everything in data/noteezer.db. This copies its account,
folders, lines, task ticks, and reminders into the two services through their
public APIs, the same way the UI does, so every rule and the seam are used:
inbox stores the lines and folders, and reminders gets the times from inbox.

    NOTEEZER_PASSWORD=... python scripts/import_a1.py --inbox https://... --reminders https://...

The account keeps its Assignment 1 email and timezone. Its password cannot be
copied (only a hash is stored), so the new one comes from NOTEEZER_PASSWORD,
never from the command line. The original dates are not kept: each line is
created now, in the original order. Link previews and sorting results are not
copied; inbox makes them again. The import refuses to run twice into an
account that already has lines.

It needs httpx, which is in services/inbox/requirements.txt.
"""

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parent.parent


def read_a1(path: Path) -> dict:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    users = connection.execute("SELECT id, email, timezone FROM users").fetchall()
    if len(users) != 1:
        raise SystemExit(f"Expected one account in {path}, found {len(users)}.")
    user = users[0]
    data = {
        "email": user["email"],
        "timezone": user["timezone"] or "UTC",
        # Parents before children, so a parent always exists when its child is created.
        "folders": connection.execute(
            "SELECT id, parent_id, name FROM folders WHERE user_id = ? ORDER BY parent_id IS NOT NULL, id",
            (user["id"],),
        ).fetchall(),
        "items": connection.execute(
            "SELECT id, body, kind, task_done FROM items WHERE user_id = ? AND hidden = 0 ORDER BY created_at, id",
            (user["id"],),
        ).fetchall(),
        "item_folders": connection.execute("SELECT item_id, folder_id FROM item_folders").fetchall(),
        "reminders": {row["item_id"]: row for row in connection.execute("SELECT * FROM reminders")},
    }
    connection.close()
    return data


def local_wall_time(utc_text: str, zone: str) -> str:
    # The API reads a picked time as wall time in the account's timezone.
    moment = datetime.fromisoformat(utc_text).astimezone(ZoneInfo(zone))
    return moment.strftime("%Y-%m-%dT%H:%M")


def run(sqlite_path: Path, inbox: str, reminders: str, password: str) -> None:
    data = read_a1(sqlite_path)
    client = httpx.Client(timeout=120)  # a cold start can take a minute

    def send(method: str, url: str, token: str | None = None, **kwargs) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # A service that scaled to zero answers 503 "App port is not ready"
        # (no JSON detail) while it starts; wait and ask again.
        for wait in (5, 10, 20, 30, 30, 0):
            response = client.request(method, url, headers=headers, **kwargs)
            if response.status_code not in (502, 503, 504) or "detail" in response.text or not wait:
                break
            print(f"  {url.split('/')[2].split('.')[0]} is starting, waiting {wait} s")
            time.sleep(wait)
        return response

    def call(method: str, url: str, token: str | None = None, **kwargs) -> httpx.Response:
        response = send(method, url, token, **kwargs)
        if response.status_code >= 400:
            raise SystemExit(f"{method} {url.split('?')[0]} answered {response.status_code}: {response.text[:200]}")
        return response

    # No example lines: the account gets its own notes instead.
    account = {"email": data["email"], "password": password, "timezone": data["timezone"], "examples": False}
    response = send("POST", f"{inbox}/api/register", json=account)
    if response.status_code == 201:
        token = response.json()["token"]
        print("Account created.")
    else:
        token = call("POST", f"{inbox}/api/login", json=account).json()["token"]
        if call("GET", f"{inbox}/api/folders", token).json()["total_count"]:
            raise SystemExit("This account already has lines; the import runs only once.")
        print("Account exists and is empty.")

    folder_ids: dict[int, int] = {}
    for folder in data["folders"]:
        parent = folder_ids.get(folder["parent_id"]) if folder["parent_id"] else None
        created = call("POST", f"{inbox}/api/folders", token, json={"name": folder["name"], "parent_id": parent})
        folder_ids[folder["id"]] = created.json()["id"]
    print(f"{len(folder_ids)} folders.")

    folders_of: dict[int, list[int]] = {}
    for row in data["item_folders"]:
        folders_of.setdefault(row["item_id"], []).append(folder_ids[row["folder_id"]])

    done_reminders = 0
    for item in data["items"]:
        form = {"body": item["body"], "kind": item["kind"]}
        reminder = data["reminders"].get(item["id"])
        if reminder is not None:
            form["remind_at"] = local_wall_time(reminder["remind_at"], data["timezone"])
            form["repeat_daily"] = "true" if reminder["repeat_daily"] else "false"
            form["repeat_until"] = reminder["repeat_until"] or ""
        targets = folders_of.get(item["id"], [])
        if targets:
            form["folder_id"] = str(targets[0])
        new_id = call("POST", f"{inbox}/api/items", token, data=form).json()["id"]
        if len(targets) > 1:
            call("PUT", f"{inbox}/api/items/{new_id}/folders", token, json={"folder_ids": targets})
        if item["task_done"]:
            call("POST", f"{inbox}/api/items/{new_id}/toggle", token)
        if reminder is not None and reminder["status"] == "done" and not reminder["repeat_daily"]:
            call("POST", f"{reminders}/reminders/{new_id}/done", token)
            done_reminders += 1
    print(f"{len(data['items'])} lines, {len(data['reminders'])} reminders ({done_reminders} done).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sqlite", type=Path, default=ROOT / "data" / "noteezer.db")
    parser.add_argument("--inbox", required=True, help="Base URL of the inbox service")
    parser.add_argument("--reminders", required=True, help="Base URL of the reminders service")
    args = parser.parse_args()
    password = os.environ.get("NOTEEZER_PASSWORD", "")
    if len(password) < 8:
        raise SystemExit("Set NOTEEZER_PASSWORD to the new account's password (8 characters or more).")
    run(args.sqlite, args.inbox.rstrip("/"), args.reminders.rstrip("/"), password)
    return 0


if __name__ == "__main__":
    sys.exit(main())
