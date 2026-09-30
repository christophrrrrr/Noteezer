# Noteezer

Noteezer is a phone-friendly capture app for the notes, tasks, links, and reminders that would otherwise be messages sent to yourself.

You type one line and save it. Optional buttons can force the kind. A reminder also needs a date and time from a picker. The app stores everything in SQLite and shows what is due when you open it.

The use case is approved. You can register, save a line, search it, edit it, delete it, and mark a task done. A reminder can be a single time or every day from a start time until an optional end date. Snooze hides it for one hour. Marking a daily reminder done moves it to the next day.

## Who it is for

One person who currently pastes notes into a chat with themselves and later searches that history. More than one person can register. Each account sees only its own items.

## Feature domains

**Inbox.** Saves the line, assigns a kind, keeps tags, and supports search, edit, delete, and marking a task done.

**Folders.** Nested folders like Notion pages (Projects / Game / Cannon Circle). Create, rename, move, and delete them. An item without a folder is in Unsorted. Deleting a folder moves its items to Unsorted, never deletes them. Inbox only calls `require_folder(user_id, folder_id)` before it puts an item in a folder.

**Sorting.** Files new lines from Unsorted into a folder in the background. The engine is chosen with `SORTER`. Only `rules` exists so far: a line that names a folder or has its name as a `#tag` goes there, and the deepest matching folder wins. A confident match is filed and marked "filed by rules". An unclear line stays in Unsorted with a suggestion you can accept. Creating or renaming a folder retries the lines that matched nothing, and Sort again retries everything in Unsorted.

**Reminders.** Stores when an item is due and whether it is scheduled, due, snoozed, or done. It does not store the item text. Inbox saves the item, then calls `schedule(item_id, when)` when a time was picked.

## How a line gets its kind

- The Reminder button, or a line that starts with `remind`, requires a picked date and time and becomes a reminder.
- A URL becomes a link.
- A line that starts with `todo` or `buy` becomes a task.
- A line that starts with `idea` becomes an idea.
- Anything else becomes a note.
- `#words` become tags on any kind.
- Note, Idea, Task, Link, and Reminder buttons override that guess.
- The kind is separate from the folder. Saving while a folder is open puts the line in that folder. Otherwise it goes to Unsorted.

Times are chosen with a picker. The app does not parse phrases like "friday 3pm".

## In this assignment

- One process, Python, FastAPI, server-rendered pages, SQLite.
- Due reminders appear at the top when you open the app. Snooze and done are supported.
- Minimal accounts: register, log in, and log out with email and password. Not a third feature domain.

## Left for later

Lock-screen notifications need a second always-on process. This assignment forbids that, so delivery while the phone is locked waits until the app is split and deployed. Also out of scope: chat import, offline mode, and sharing items with other people.

## Run

From the repository root:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The process listens on `0.0.0.0` and port `8000`, or `PORT` if that variable is set. The SQLite file is `data/noteezer.db`, or `$DATA_DIR/noteezer.db` if `DATA_DIR` is set. The session cookie is signed with `SESSION_SECRET`, which defaults to `dev-only-change-me` so the app still starts with no setup. `SORTER` picks the sorting engine and defaults to `rules`.

Open `http://127.0.0.1:8000/register`, create an account, and save a line. A line that starts with `todo` or `buy` becomes a task, a URL becomes a link, and `remind` or the Reminder choice becomes a reminder that needs the date picker. Check Every day to repeat it, and set the date field only if it should stop. A reminder time that is already past is marked due and listed first. `#words` become tags.

Core logic tests, with coverage for the inbox, folder, sorting, reminder, account, and kind rules modules:

```
pytest --cov=noteezer.classify --cov=noteezer.inbox --cov=noteezer.reminders --cov=noteezer.users --cov=noteezer.passwords --cov=noteezer.folders --cov=noteezer.sorting --cov-report=term-missing
```

That run is 54 tests and 96% coverage on those modules.
