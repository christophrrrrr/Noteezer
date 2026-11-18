import pytest

from app.db import IntegrityError, Row, _placeholders, connect, settings

USER = "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)"
STAMP = "2026-09-26T00:00:00+00:00"


def _tables(connection) -> set[str]:
    return {row["name"] for row in connection.execute("SELECT name FROM sys.tables")}


def test_migration_creates_every_table() -> None:
    connection = connect()
    assert _tables(connection) == {
        "schema_migrations",
        "users",
        "folders",
        "items",
        "item_folders",
        "folder_suggestions",
        "folder_checks",
        "tags",
        "item_tags",
        "sort_results",
        "link_previews",
        "images",
    }
    versions = [row["version"] for row in connection.execute("SELECT version FROM schema_migrations")]
    assert versions == ["001_init"]
    connection.close()


def test_email_is_unique_in_any_case() -> None:
    connection = connect()
    connection.execute(USER, ("a@example.com", "hash", STAMP))
    with pytest.raises(IntegrityError):
        connection.execute(USER, ("A@example.com", "other", STAMP))
    connection.close()


def test_note_cannot_be_marked_done() -> None:
    connection = connect()
    connection.execute(USER, ("a@example.com", "hash", STAMP))
    with pytest.raises(IntegrityError):
        connection.execute(
            """
            INSERT INTO items (user_id, body, kind, task_done, created_at, updated_at)
            VALUES (1, 'hello', 'note', 1, ?, ?)
            """,
            (STAMP, STAMP),
        )
    connection.close()


def test_tag_name_is_unique_per_user_only() -> None:
    connection = connect()
    connection.execute(USER, ("a@example.com", "hash", STAMP))
    connection.execute(USER, ("b@example.com", "hash", STAMP))
    connection.execute("INSERT INTO tags (user_id, name) VALUES (1, 'groceries')")
    connection.execute("INSERT INTO tags (user_id, name) VALUES (2, 'groceries')")
    with pytest.raises(IntegrityError):
        connection.execute("INSERT INTO tags (user_id, name) VALUES (1, 'groceries')")
    connection.close()


def test_two_sibling_folders_cannot_share_a_name_in_any_case() -> None:
    connection = connect()
    folder = "INSERT INTO folders (user_id, parent_id, name, created_at) VALUES (1, ?, ?, ?)"
    connection.execute(USER, ("a@example.com", "hash", STAMP))
    connection.execute(folder, (None, "Sport", STAMP))
    with pytest.raises(IntegrityError):
        connection.execute(folder, (None, "sport", STAMP))
    connection.execute(folder, (1, "sport", STAMP))
    connection.close()


def test_timestamps_compare_character_by_character() -> None:
    # With a case-insensitive collation, '+' and '.' could sort differently
    # than in SQLite. The binary collation keeps the old order.
    connection = connect()
    connection.execute(USER, ("a@example.com", "hash", "2026-09-26T10:00:00+00:00"))
    later = connection.execute(
        "SELECT COUNT(*) AS n FROM users WHERE created_at < ?", ("2026-09-26T10:00:00.5+00:00",)
    ).fetchone()
    assert later["n"] == 1
    connection.close()


def test_rows_read_by_name_or_position() -> None:
    row = Row((7, "seven"), {"id": 0, "name": 1})
    assert (row["id"], row[1], tuple(row), row.keys()) == (7, "seven", (7, "seven"), ["id", "name"])


def test_placeholders_become_pymssql_style_and_keep_percent_signs() -> None:
    assert _placeholders("SELECT '100%' WHERE a = ? AND b = ?") == "SELECT '100%%' WHERE a = %s AND b = %s"


def test_database_url_is_read_with_an_encoded_password(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "mssql://inbox:p%40ss%2Fw0rd@db.example.net:1444/noteezer_inbox")
    assert settings() == {
        "server": "db.example.net",
        "port": "1444",
        "user": "inbox",
        "password": "p@ss/w0rd",
        "database": "noteezer_inbox",
    }
    assert settings("other")["database"] == "other"
    monkeypatch.setenv("DATABASE_URL", "mssql://inbox:x@db.example.net/noteezer_inbox")
    assert settings()["port"] == "1433"


@pytest.mark.parametrize(
    ("url", "problem"),
    [("", "not set"), ("postgres://u:p@h/d", "mssql://"), ("mssql://u:p@h:1433/", "names no database")],
)
def test_a_missing_or_wrong_database_url_is_refused(monkeypatch, url: str, problem: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    with pytest.raises(RuntimeError, match=problem):
        settings()
