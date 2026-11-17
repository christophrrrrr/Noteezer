"""Tests run against a real SQL Server, the same engine as Azure SQL.

Start one locally with the docker run command in the README. DATABASE_URL
points at it with a login that may create databases (sa locally and in CI),
from the environment or from services/inbox/.env (never from the repo: no
password is written in code). Its database name is ignored: a fresh database
named TEST_DB_NAME is created for each test run, migrated once, and emptied
before every test, so ids start at 1 each time.

The real reminders service runs next to the tests: the session starts it with
`python -m app` from services/reminders, on a free port and with its own test
database, so every reminder an inbox test saves goes over real HTTP (ADR-7).
"""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pymssql
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not os.environ.get("DATABASE_URL"):
    pytest.exit("Set DATABASE_URL (in the environment or services/inbox/.env); see the README.", returncode=2)
SERVER_URL = os.environ["DATABASE_URL"]


def database_url(name: str) -> str:
    """DATABASE_URL with the same login and server, pointing at another database."""
    return urlunsplit(urlsplit(SERVER_URL)._replace(path="/" + name))


TEST_DB = os.environ.get("TEST_DB_NAME", "noteezer_inbox_test")
os.environ["DATABASE_URL"] = database_url(TEST_DB)
os.environ["SESSION_SECRET"] = "test-secret"
REMINDERS_DB = os.environ.get("TEST_REMINDERS_DB_NAME", "noteezer_reminders_test")
REMINDERS_DIR = Path(__file__).resolve().parents[2] / "reminders"

from app.db import connect  # noqa: E402  (needs the variables above)
from app.migrate import migrate  # noqa: E402

# Children before parents, so no foreign key blocks a delete. folders and items
# point at themselves, which is fine: one DELETE removes the whole table.
TABLES = (
    "item_folders",
    "folder_suggestions",
    "folder_checks",
    "item_tags",
    "tags",
    "sort_results",
    "images",
    "link_previews",
    "items",
    "folders",
    "users",
)
IDENTITY_TABLES = ("users", "folders", "items", "tags")


def create_database(name: str) -> None:
    try:
        server = connect("master", autocommit=True, attempts=1)
    except pymssql.OperationalError as error:
        pytest.exit(
            f"Cannot reach SQL Server at {urlsplit(SERVER_URL).hostname}. "
            f"Start the container from the README first. ({error})",
            returncode=2,
        )
    try:
        drop_database(server, name)
        server.execute(f"CREATE DATABASE [{name}]")
        # Azure SQL has this on by default: readers see the last committed
        # rows instead of waiting for a writer to finish.
        server.execute(f"ALTER DATABASE [{name}] SET READ_COMMITTED_SNAPSHOT ON")
    finally:
        server.close()


def drop_database(server, name: str) -> None:
    server.execute(
        f"""
        IF DB_ID('{name}') IS NOT NULL
        BEGIN
            ALTER DATABASE [{name}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
            DROP DATABASE [{name}];
        END
        """
    )


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    create_database(TEST_DB)
    connection = connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    yield
    server = connect("master", autocommit=True)
    try:
        drop_database(server, TEST_DB)
    finally:
        server.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="session", autouse=True)
def reminders_service(tmp_path_factory):
    """The reminders service as its own process. Yields its base URL."""
    create_database(REMINDERS_DB)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    log_file = (tmp_path_factory.mktemp("reminders") / "service.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "app"],
        cwd=REMINDERS_DIR,
        env={**os.environ, "DATABASE_URL": database_url(REMINDERS_DB), "PORT": str(port)},
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.exit(f"The reminders service did not start; see {log_file.name}", returncode=2)
            time.sleep(0.3)
        os.environ["REMINDERS_URL"] = url
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)
        log_file.close()
        server = connect("master", autocommit=True)
        try:
            drop_database(server, REMINDERS_DB)
        finally:
            server.close()


@pytest.fixture(autouse=True)
def _empty_tables(_test_database, reminders_service, monkeypatch):
    monkeypatch.setenv("REMINDERS_URL", reminders_service)
    reminders = connect(REMINDERS_DB)
    try:
        reminders.execute("DELETE FROM reminders")
        reminders.commit()
    finally:
        reminders.close()
    connection = connect()
    try:
        for table in TABLES:
            connection.execute(f"DELETE FROM {table}")
        for table in IDENTITY_TABLES:
            # A table that never had a row starts at 1 by itself; reseeding it
            # to 0 would make its first id 0.
            connection.execute(
                f"""
                IF (SELECT last_value FROM sys.identity_columns WHERE object_id = OBJECT_ID('{table}')) IS NOT NULL
                    DBCC CHECKIDENT ('{table}', RESEED, 0) WITH NO_INFOMSGS
                """
            )
        connection.commit()
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def _rules_only(monkeypatch):
    # Tests never talk to Gemini, even when a key is set in the shell.
    monkeypatch.setenv("SORTER", "rules")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def sign_in(client, email: str = "a@example.com", timezone: str = "") -> str:
    """Registers through the API and sends the token on every later request."""
    answer = client.post("/api/register", json={"email": email, "password": "password1", "timezone": timezone})
    assert answer.status_code == 201, answer.text
    token = answer.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    return token
