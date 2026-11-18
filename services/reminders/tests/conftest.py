"""Tests run against a real SQL Server, like the inbox service's tests.

DATABASE_URL comes from the environment or services/reminders/.env (no
password is written in code), with a login that may create databases. Its
database name is ignored: a fresh database named TEST_DB_NAME is created for
each run and emptied before every test.
"""

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pymssql
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not os.environ.get("DATABASE_URL"):
    pytest.exit("Set DATABASE_URL (in the environment or services/reminders/.env); see the README.", returncode=2)
TEST_DB = os.environ.get("TEST_DB_NAME", "noteezer_reminders_unit")
os.environ["DATABASE_URL"] = urlunsplit(urlsplit(os.environ["DATABASE_URL"])._replace(path="/" + TEST_DB))
os.environ["SESSION_SECRET"] = "test-secret"

from app.db import connect  # noqa: E402  (needs the variables above)
from app.migrate import migrate  # noqa: E402


def _server():
    try:
        return connect("master", autocommit=True, attempts=1)
    except pymssql.OperationalError as error:
        pytest.exit(f"Cannot reach SQL Server. Start the container from the README first. ({error})", returncode=2)


def _drop(server, name: str) -> None:
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
    server = _server()
    try:
        _drop(server, TEST_DB)
        server.execute(f"CREATE DATABASE [{TEST_DB}]")
        server.execute(f"ALTER DATABASE [{TEST_DB}] SET READ_COMMITTED_SNAPSHOT ON")
    finally:
        server.close()
    connection = connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    yield
    server = _server()
    try:
        _drop(server, TEST_DB)
    finally:
        server.close()


@pytest.fixture(autouse=True)
def _empty_table(_test_database):
    connection = connect()
    try:
        connection.execute("DELETE FROM reminders")
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def db():
    connection = connect()
    yield connection
    connection.close()
