# Jot

Jot is a phone-friendly capture app for the notes, tasks, links, and reminders that would otherwise be messages sent to yourself.

You type one line and save it. Optional buttons can force the kind. A reminder also needs a date and time from a picker. The app stores everything in SQLite and shows what is due when you open it.

Feature code waits on approval of this use case. This repository currently records the scope and the framework decision.

## Who it is for

One person who currently pastes notes into a chat with themselves and later searches that history. More than one person can register. Each account sees only its own items.

## Feature domains

**Inbox.** Saves the line, assigns a kind, keeps tags, and supports search, edit, delete, and marking a task done.

**Reminders.** Stores when an item is due and whether it is scheduled, due, snoozed, or done. It does not store the item text. Inbox saves the item, then calls `schedule(item_id, when)` when a time was picked.

## How a line is sorted

- The Reminder button, or a line that starts with `remind`, requires a picked date and time and becomes a reminder.
- A URL becomes a link.
- A line that starts with `todo` or `buy` becomes a task.
- Anything else becomes a note.
- `#words` become tags on any kind.
- Note, Task, Link, and Reminder buttons override that guess.

Times are chosen with a picker. The app does not parse phrases like "friday 3pm".

## In this assignment

- One process, Python, Flask, server-rendered pages, SQLite.
- Due reminders appear at the top when you open the app. Snooze and done are supported.
- Minimal accounts: register, log in, log out. Password hash and a signed session cookie. Not a third feature domain.

## Left for later

Lock-screen notifications need a second always-on process. This assignment forbids that, so delivery while the phone is locked waits until the app is split and deployed. Also out of scope: chat import, offline mode, and sharing items with other people.

## Run

Not runnable yet. Setup steps will be added with the first working version.
