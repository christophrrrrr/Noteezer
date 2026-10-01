# Noteezer

Noteezer is a phone-friendly capture app for the notes, tasks, links, and reminders that would otherwise be messages sent to yourself.

You type one line and save it. Optional buttons can force the kind. A reminder takes its time from a picker or, with Gemini, from the text itself. The app stores everything in SQLite and shows what is due when you open it.

The use case is approved. You can register, save a line, search it, edit it, delete it, and mark a task done. A reminder can be a single time or every day from a start time until an optional end date. Snooze hides it for one hour. Marking a daily reminder done moves it to the next day.

## Who it is for

One person who currently pastes notes into a chat with themselves and later searches that history. More than one person can register. Each account sees only its own items.

## Feature domains

**Inbox.** Saves the line, assigns a kind, keeps tags, and supports search, edit, delete, and marking a task done.

**Folders.** Nested folders like Notion pages (Projects / Game / Cannon Circle). Create, rename, move, and delete them. An item without a folder is in Unsorted. Deleting a folder moves its items to Unsorted, never deletes them. Inbox only calls `require_folder(user_id, folder_id)` before it puts an item in a folder.

**Sorting.** Files new lines from Unsorted into a folder in the background. First the title of a line's link is fetched: YouTube through oEmbed, other pages from their `<head>`. Links that point into a private network are refused. The rules come next: a line that names a folder or has its name as a `#tag`, including in a link title, goes there, and the deepest matching folder wins. If the rules are not sure and `GEMINI_API_KEY` is set, Gemini picks a folder with a confidence, or proposes a new folder. A `#tag` or folder name the rules find still decides the folder, because that is the user's own choice, but Gemini is asked for every line so it can also read times and kinds. Calls are spaced 4 seconds apart to stay inside the free tier. A confident answer is filed and marked "filed by rules" or "filed by gemini". An unclear line stays in Unsorted with a suggestion (Accept) or a proposed folder (Create folder). Failed calls are retried with a growing wait. Gemini also turns a plain note into a task or an idea; links, reminders, and lines with a `todo`, `buy`, or `idea` prefix keep their kind. A note that holds several separate points, like a list of bugs under a heading, is split into one item per point, and each part is sorted on its own. The original is hidden, not deleted, and Undo split on any part brings it back. Creating or renaming a folder retries the lines that matched nothing, and Sort again retries everything in Unsorted.

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

Without Gemini, times come only from the picker. With Gemini, a line that starts with `remind` can be saved without the picker: "remind me friday 3pm call mom" is scheduled once the sorter has read it, and until then it shows "no time yet". Other lines with a time, like "dentist tuesday 10:00", get a "Remind me Tue 6 Oct, 10:00" button instead of a reminder. Every form sends the browser's timezone, which is stored on the account, so the picker and phrases like "friday" are read in the user's time. The server's own clock is UTC once deployed.

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

The process listens on `0.0.0.0` and port `8000`, or `PORT` if that variable is set. The SQLite file is `data/noteezer.db`, or `$DATA_DIR/noteezer.db` if `DATA_DIR` is set. The session cookie is signed with `SESSION_SECRET`, which defaults to `dev-only-change-me` so the app still starts with no setup. `SORTER` picks the sorting engine: `auto` (the default) uses Gemini when `GEMINI_API_KEY` is set and the rules otherwise, `rules` never calls Gemini, and `gemini` asks for it. `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`. `python app.py` also reads an optional `.env` file next to it, for example with `GEMINI_API_KEY=...`, and variables already set in the environment win. A free key comes from Google AI Studio. On the free tier Google may use the notes it sorts to improve its models.

Open `http://127.0.0.1:8000/register`, create an account, and save a line. A line that starts with `todo` or `buy` becomes a task, a URL becomes a link, and `remind` or the Reminder choice becomes a reminder that needs the date picker. Check Every day to repeat it, and set the date field only if it should stop. A reminder time that is already past is marked due and listed first. `#words` become tags.

Core logic tests, with coverage for the inbox, folder, sorting, link preview, Gemini, reminder, account, and kind rules modules. The tests never call Gemini or fetch real pages; they use fakes:

```
pytest --cov=noteezer.classify --cov=noteezer.inbox --cov=noteezer.reminders --cov=noteezer.users --cov=noteezer.passwords --cov=noteezer.folders --cov=noteezer.sorting --cov=noteezer.links --cov=noteezer.gemini --cov-report=term-missing
```

That run is 82 tests and 96% coverage on those modules.
