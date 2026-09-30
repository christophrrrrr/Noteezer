import pytest


@pytest.fixture(autouse=True)
def _rules_only(monkeypatch):
    # Tests never talk to Gemini, even when a key is set in the shell.
    monkeypatch.setenv("SORTER", "rules")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
