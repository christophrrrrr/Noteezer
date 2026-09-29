import pytest

from noteezer.classify import ClassificationError, classify


def test_plain_line_is_a_note() -> None:
    result = classify("  call the idea notebook  ")
    assert result.kind == "note"
    assert result.body == "call the idea notebook"
    assert result.tags == ()


def test_todo_and_buy_prefixes_are_tasks() -> None:
    assert classify("TODO oat milk").kind == "task"
    assert classify("buy oat milk").kind == "task"


def test_url_is_a_link() -> None:
    assert classify("pasta https://example.com/pasta").kind == "link"


def test_remind_prefix_is_a_reminder() -> None:
    assert classify("Remind Friday call landlord").kind == "reminder"


def test_button_overrides_the_guess() -> None:
    assert classify("todo oat milk", forced_kind="note").kind == "note"
    assert classify("hello", forced_kind="reminder").kind == "reminder"


def test_task_prefix_wins_over_a_url() -> None:
    assert classify("todo read https://example.com").kind == "task"


def test_hashtags_are_lowercase_and_unique() -> None:
    result = classify("buy milk #Groceries #groceries #home")
    assert result.tags == ("groceries", "home")


def test_empty_line_is_rejected() -> None:
    with pytest.raises(ClassificationError):
        classify("   ")


def test_unknown_kind_is_rejected() -> None:
    with pytest.raises(ClassificationError):
        classify("hello", forced_kind="folder")


def test_idea_prefix_is_an_idea() -> None:
    assert classify("Idea: king of the hill map").kind == "idea"
    assert classify("todo write the idea down").kind == "task"
    assert classify("ideas are cheap").kind == "note"
