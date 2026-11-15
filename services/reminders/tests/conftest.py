"""Tests run against a real SQL Server, like the inbox service's tests.

DB_SERVER, DB_PORT, DB_USER, and DB_PASSWORD come from the environment or
services/reminders/.env (no password is written in code). A fresh database
named TEST_DB_NAME is created for each run and emptied before every test.
"""

import os
from pathlib import Path

import pymssql
import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
if not os.environ.get("DB_PASSWORD"):
    pytest.exit(
        "Set DB_PASSWORD (in the environment or services/reminders/.env) to the SQL Server password.", returncode=2
    )
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "noteezer_reminders_unit")
os.environ["SESSION_SECRET"] = "test-secret"

from app.db import connect  # noqa: E402  (needs the variables above)
from app.migrate import migrate  # noqa: E402

TEST_DB = os.environ["DB_NAME"]


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
