"""Tests run against a real SQL Server, the same engine as Azure SQL.

Start one locally with the docker run command in the README. The DB_SERVER,
DB_PORT, DB_USER, and DB_PASSWORD variables point at it (defaults match that
command). A fresh database named TEST_DB_NAME is created for each test run,
migrated once, and emptied before every test, so ids start at 1 each time.
"""

import os

import pymssql
import pytest

os.environ.setdefault("DB_PASSWORD", "Noteezer_dev1")
os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "noteezer_inbox_test")

from app.db import connect  # noqa: E402  (needs the variables above)
from app.migrate import migrate  # noqa: E402

TEST_DB = os.environ["DB_NAME"]

# Children before parents, so no foreign key blocks a delete. folders and items
# point at themselves, which is fine: one DELETE removes the whole table.
TABLES = (
    "item_folders",
    "folder_suggestions",
    "folder_checks",
    "item_tags",
    "tags",
    "reminders",
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
            f"Cannot reach SQL Server at {os.environ.get('DB_SERVER', 'localhost')}. "
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


@pytest.fixture(autouse=True)
def _empty_tables(_test_database):
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
