from datetime import date, datetime, timedelta, timezone

import pytest

from app.reminders import (
    ReminderExists,
    complete,
    initial_status,
    list_for,
    next_occurrence,
    remove,
    schedule,
    snooze,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
OWNER = 1
OTHER = 2


def test_past_reminder_is_due_and_future_one_is_scheduled() -> None:
    assert initial_status(NOW - timedelta(hours=1), NOW) == "due"
    assert initial_status(NOW, NOW) == "due"
    assert initial_status(NOW + timedelta(hours=1), NOW) == "scheduled"


def test_daily_done_moves_to_the_next_day_until_the_end(db) -> None:
    schedule(db, OWNER, 10, START, NOW, repeat_daily=True, repeat_until=date(2026, 9, 29))
    assert complete(db, OWNER, 10, NOW)["status"] == "scheduled"
    assert list_for(db, OWNER, NOW)[0]["remind_at"].startswith("2026-09-29T15:00:00")
    assert complete(db, OWNER, 10, NOW)["status"] == "done"


def test_daily_without_an_end_keeps_moving(db) -> None:
    schedule(db, OWNER, 10, START, NOW, repeat_daily=True)
    assert next_occurrence(START, None) == START + timedelta(days=1)
    moved = complete(db, OWNER, 10, NOW)
    assert (moved["status"], moved["remind_at"]) == ("scheduled", (START + timedelta(days=1)).isoformat())


def test_one_shot_done_stays_done(db) -> None:
    schedule(db, OWNER, 10, START, NOW)
    assert complete(db, OWNER, 10, NOW)["status"] == "done"
    assert complete(db, OWNER, 10, NOW)["status"] == "done"
    with pytest.raises(ValueError):
        snooze(db, OWNER, 10, NOW)


def test_snooze_hides_a_due_reminder_for_one_hour(db) -> None:
    schedule(db, OWNER, 10, NOW - timedelta(minutes=5), NOW)
    assert list_for(db, OWNER, NOW)[0]["status"] == "due"
    snoozed = snooze(db, OWNER, 10, NOW)
    assert (snoozed["status"], snoozed["snoozed_until"]) == ("snoozed", (NOW + timedelta(hours=1)).isoformat())
    assert list_for(db, OWNER, NOW + timedelta(minutes=59))[0]["status"] == "snoozed"
    later = list_for(db, OWNER, NOW + timedelta(hours=1))[0]
    assert (later["status"], later["snoozed_until"]) == ("due", None)


def test_every_snooze_is_counted(db) -> None:
    schedule(db, OWNER, 10, NOW - timedelta(minutes=5), NOW)
    assert list_for(db, OWNER, NOW)[0]["snoozed_count"] == 0
    assert snooze(db, OWNER, 10, NOW)["snoozed_count"] == 1
    assert snooze(db, OWNER, 10, NOW + timedelta(hours=2))["snoozed_count"] == 2
    # Becoming due again does not reset it.
    assert list_for(db, OWNER, NOW + timedelta(hours=4))[0]["snoozed_count"] == 2


def test_a_scheduled_reminder_becomes_due_when_listed_after_its_time(db) -> None:
    schedule(db, OWNER, 10, START, NOW)
    assert list_for(db, OWNER, NOW)[0]["status"] == "scheduled"
    assert list_for(db, OWNER, START)[0]["status"] == "due"


def test_scheduling_again_moves_the_time_and_forgets_a_snooze(db) -> None:
    schedule(db, OWNER, 10, NOW - timedelta(minutes=5), NOW)
    snooze(db, OWNER, 10, NOW)
    moved = schedule(db, OWNER, 10, START, NOW)
    assert (moved["status"], moved["snoozed_until"], moved["remind_at"]) == ("scheduled", None, START.isoformat())


def test_keep_existing_never_moves_a_time_the_user_set(db) -> None:
    schedule(db, OWNER, 10, START, NOW)
    with pytest.raises(ReminderExists):
        schedule(db, OWNER, 10, START + timedelta(days=3), NOW, keep_existing=True)
    assert list_for(db, OWNER, NOW)[0]["remind_at"] == START.isoformat()
    assert schedule(db, OWNER, 11, START, NOW, keep_existing=True)["item_id"] == 11


def test_bad_times_are_refused(db) -> None:
    with pytest.raises(ValueError, match="time zone"):
        schedule(db, OWNER, 10, START.replace(tzinfo=None), NOW)
    with pytest.raises(ValueError, match="daily"):
        schedule(db, OWNER, 10, START, NOW, repeat_until=date(2026, 10, 1))
    with pytest.raises(ValueError, match="on or after"):
        schedule(db, OWNER, 10, START, NOW, repeat_daily=True, repeat_until=date(2026, 9, 27))
    assert list_for(db, OWNER, NOW) == []


def test_one_person_never_sees_or_changes_another_persons_reminder(db) -> None:
    schedule(db, OWNER, 10, START, NOW)
    assert list_for(db, OTHER, NOW) == []
    for action in (complete, snooze):
        with pytest.raises(LookupError):
            action(db, OTHER, 10, NOW)
    with pytest.raises(LookupError):
        schedule(db, OTHER, 10, START, NOW)
    assert remove(db, OTHER, 10) is False
    assert remove(db, OWNER, 10) is True
    assert list_for(db, OWNER, NOW) == []


def test_listing_can_ask_for_some_items_only(db) -> None:
    for item_id in (10, 11, 12):
        schedule(db, OWNER, item_id, START + timedelta(minutes=item_id), NOW)
    assert [entry["item_id"] for entry in list_for(db, OWNER, NOW, [12, 10])] == [10, 12]
