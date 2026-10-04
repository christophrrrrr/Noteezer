# Architecture Decision Record

## [1]. Backend language and framework

Date: 2026-09-25
Status: Decided
Context: The app is one process with phone pages, SQLite, and two domains that Assignment 2 will split into services. The framework should be the one you would pick for that service, not a larger platform and not a second UI toolchain.
Decision: Python with FastAPI and Jinja templates, served as one process, with the standard-library `sqlite3` module behind repository functions. Inbox and reminder rules live in plain modules that do not import FastAPI. Routes validate input and call those modules.
Alternatives considered: Django was rejected because its admin, ORM, and auth stack are more than register/login and two domains need. Flask was rejected because the known next step is to split two HTTP services, and Flask does not give typed request boundaries for that split. A React frontend was rejected because one text box does not need a second toolchain. SQLAlchemy was rejected because the schema is a few tables and the tested logic should stay in our functions, not in an ORM.
Consequences: Dependencies stay at FastAPI, Uvicorn, Jinja2, and the test tools. Passwords are hashed with the standard-library `hashlib`, and the session is a signed cookie implemented in our code. Assignment 2 can move each domain module behind its own FastAPI app without rewriting the rules.

Updated: 2026-09-30. Three runtime packages were added for AI sorting: `google-genai` for Gemini, `httpx` to read link titles, and `python-dotenv` so `python app.py` can read an optional `.env` file. Gemini's free tier was chosen over Claude, which costs money per note. Ollama was rejected because it would run on a laptop without a GPU, so reading screenshots would be slow and the laptop would have to stay on. Jev was rejected because it only reads text and cannot propose folders. The cost is that Google may use free-tier notes to improve its models. The app still starts and sorts with the rules when no `GEMINI_API_KEY` is set.

Updated: 2026-10-01. `tzdata` was added because Windows has no timezone database of its own, and the app now reads times in each user's timezone with `zoneinfo`.

Updated: 2026-10-01. Lines with an image go to `gemini-3.8-flash` instead of `gemini-3.5-flash-lite`. In testing, Flash-Lite missed the date on a poster in one of two tries and Flash read it in three of three. Text lines stay on Flash-Lite, which is faster and has more free quota.

## [2]. Inbox and reminders are separate domains

Date: 2026-09-25
Status: Decided
Context: The product is one text box that becomes a note, task, link, or reminder. If the due time lived on the same row as the text, the app would be a single notes table and would be too thin for two feature domains.
Decision: Inbox owns the text, kind, tags, search, edit, delete, and task completion. Reminders own `remind_at` and status (scheduled, due, snoozed, done) in their own table, keyed by item id. The only coupling is `schedule(item_id, when)` after inbox saves an item that has a picked time. Deleting an item also removes its reminder through that same boundary.
Alternatives considered: One `notes` table with an optional `remind_at` column was rejected because reminders would not have their own responsibility or data. Making tags the second domain was rejected because the behaviour the user needs next is due, snooze, and done, which is a schedule, not a label.
Consequences: Assignment 2 can run reminders as its own service that stores item ids and due times, while inbox keeps the text. Lock-screen delivery can sit on the reminder side later without changing how lines are captured.

Updated: 2026-09-29. Folders are a third area in `noteezer/folders.py` that owns the folder tree: create, rename, move, delete, and the rule that a folder cannot move inside itself. Inbox stores which folder an item is in, but the only call it makes into folders is `require_folder(user_id, folder_id)` before it saves or moves an item. This is where automatic sorting will live, so it can later become its own service next to reminders.

Updated: 2026-09-30. Sorting lives in `noteezer/sorting.py` with its own `sort_results` table. Inbox does not call sorting at all: a line waits to be sorted when it is in Unsorted and has no sort result yet. Sorting reads those lines and files them only through `file_unsorted_item`, which does nothing if the user already moved the line. The engine sits behind a `Sorter` interface picked by the `SORTER` variable, so a Gemini engine can be added without touching inbox. A background task inside the same process runs the sorting, which the single-process rule allows. A message queue was rejected because it needs a second process.

Updated: 2026-10-01. Sorting can now change more than the folder, but only through guarded inbox functions that do nothing if the item changed meanwhile. `change_plain_note_kind` only turns a plain note into an idea or a task. `schedule_unset_reminder` only times a reminder that has no time yet. `split_item` creates the parts and hides the original. Inbox is still the only code that calls `schedule` in reminders.

Updated: 2026-10-03. The route that marks a line done read the `items` table itself, which crossed the seam. It now asks inbox with `item_kind` and `toggle_task`, and calls `complete` in reminders for a reminder. No route runs SQL anymore, so each table is read only by the module that owns it.

Updated: 2026-10-03. Which folders a line is in is still written only by inbox (`set_folders`, `add_to_folder`, `file_unsorted_item`). Sorting owns `folder_suggestions` and `folder_checks`: when a folder is created, the worker runs `check_new_folders` and stores suggestions, and `accept_also` asks inbox to add the line.

Updated: 2026-10-04. The 2026-10-03 note above went too far: no route runs SQL, but not every table is read only by its owner. Writes across a seam go through functions, with one exception: `record_reading` in `images.py` replaces the body "Screenshot" in `items` itself. Reads still cross with SQL joins: `sort_pending` and `check_new_folders` read `items`, `item_folders`, `reminders`, and `images` to find work, and `reminders`, `images`, and `links` join `items` to check the owner. These joins are what Assignment 2 has to replace, with calls between services or a `user_id` copied onto each owned table. `docs/diagrams.md` marks each crossing.

## [3]. How inbox rows relate to reminder rows

Date: 2026-09-26
Status: Decided
Context: The two domains need a schema that matches the split. A due time on `items` would collapse them. Tags and accounts also need a place that does not become a third domain.
Decision: `items` stores body and kind for one user. `reminders` uses `item_id` as its primary key, so an item has at most one schedule, and the schedule does not copy the text. A reminder row is only created when `kind` is `reminder`. Tags are one row per user and name in `tags`, linked through `item_tags`. Accounts are `users.email` plus a password hash.
Alternatives considered: An optional `remind_at` column on `items` was rejected because inbox would own the schedule. Allowing any kind to have a due time was rejected so a task stays a task, and only a reminder item gets a schedule. Storing tags as text on the item was rejected because the same `#groceries` tag must be one record the user can filter on.
Consequences: Deleting an item deletes its reminder and tag links. The database diagram is `users` to `items` and `tags`, `items` to `item_tags` to `tags`, and `items` to `reminders` one-to-one. `schedule(item_id, when)` is the only write from inbox into reminders.

Updated: 2026-09-28. A daily reminder stores `repeat_daily` and an optional `repeat_until` on the same reminder row. `remind_at` is the next occurrence, not a copy of the text. Marking it done moves `remind_at` forward one day until that end date, or until it is turned off when no end date is set.

Updated: 2026-09-29. `folders` has `parent_id` pointing at another folder, so folders nest like Notion pages. A unique index on user, parent, and name (ignoring case) stops two sibling folders with the same name. The same name in different parents is allowed. `items.folder_id` is optional, and no folder means Unsorted. Deleting a folder deletes its subfolders (`ON DELETE CASCADE`), but items are never deleted with a folder: `ON DELETE SET NULL` moves them to Unsorted. The kind of an item (note, idea, task, link, reminder) stays separate from its folder, because a Game folder holds both tasks and ideas. Older databases are rebuilt once on startup because SQLite cannot change the kind CHECK in place. Rejected: storing a folder path as text on the item, because renaming or moving a folder would then need to rewrite every item.

Updated: 2026-09-30. `sort_results` uses `item_id` as its primary key, like `reminders`, so an item has at most one sort result, and it is deleted with the item. It stores the status (filed, review, accepted, retry, failed), the suggested `folder_id`, the confidence, the engine name, and the retry count and time. If the suggested folder is deleted, `folder_id` becomes NULL and the suggestion disappears. `proposed_name` and `proposed_parent_id` hold a new folder the engine suggests. It is only created when the user taps Create folder. `link_previews` is also keyed by `item_id` and stores the title and description of a line's first link, so a link is fetched once and not again on every retry.

Updated: 2026-10-01. `users.timezone` stores the browser's IANA zone, such as Europe/Madrid. `items.split_from` points a part at the line it was split from, with `ON DELETE SET NULL`, and `items.hidden` hides that original while its parts exist. Undo split deletes the parts and clears `hidden`. Deleting the original was rejected because a bad split could not be undone. `sort_results.suggested_remind_at` holds a time Gemini read from a line that is not a reminder, until the user accepts it. A reminder item may now exist without a `reminders` row while it waits for a time.

Updated: 2026-10-01. `images` uses `item_id` as its primary key, so a line has at most one image, and it is deleted with the line. It stores the bytes as a BLOB, the type found by `check_image`, `ai_allowed` from the checkbox, and the `title` and `text` Gemini read. Files in a folder next to the database were rejected: the assignment wants one documented SQLite path, and a folder of files could keep images whose line is gone. The cost is a larger database file; images are capped at 10 MB.

Updated: 2026-10-03. A line can now be in several folders. `items.folder_id` was replaced by `item_folders (item_id, folder_id)`, with both keys `ON DELETE CASCADE`, so deleting a folder only removes its memberships, and a line with no membership is in Unsorted. This changed because a table tennis video was filed into Table Tennis, and later it also belonged in a new Cool videos folder; with one folder per line the user had to choose. Rejected: copying the line into each folder, because an edit or a done task would then have to be kept in sync. `folder_suggestions (item_id, folder_id)` holds "Also add to" suggestions from a new-folder check, and `folder_checks (folder_id)` queues those checks. Old databases are rebuilt once on startup, and every old `folder_id` becomes a row in `item_folders`.

Updated: 2026-10-04. The diagram described in Consequences only covers the first four tables. The full database diagram, with all twelve tables from `schema.sql` and the module that writes each one, is in `docs/diagrams.md`.

## [4]. Testing approach

Date: 2026-09-30
Status: Decided
Context: Grading needs at least 70% coverage of the core rules. The rules that could lose or misfile a note are the sorting decisions, the folder tree rules, and reminder status changes. The pages are thin code around those rules.
Decision: pytest calls the domain modules (`classify`, `inbox`, `folders`, `sorting`, `reminders`, `users`, `passwords`) directly against a real SQLite file in a temporary folder. Each test passes a fixed `now` and uses fake sorter engines. Coverage is measured on those modules only, with pytest-cov. A few `TestClient` tests check that the routes are wired up.
Alternatives considered: Mocking `sqlite3` was rejected because cascades, unique indexes, and the upgrade of old databases are rules that live in SQLite, and only a real database proves them. Browser tests were rejected because they are slow and check layout, not decisions.
Consequences: 54 tests cover 96% of those modules. The background worker loop, the routes, and the templates are tested less: a broken route shows up in the `TestClient` tests or by hand, not in the coverage number. An engine that calls an outside API will be tested with a fake, so the tests never need a network connection or a key.

Updated: 2026-10-03. The suite is now 94 tests at 96% of the domain modules, which now also include `links`, `gemini`, and `images`. Gemini, web pages, and the clock are always faked, so the tests never need a key, a network connection, or a particular day.

Updated: 2026-10-04. After lines could be in several folders, the suite is 103 tests at 96% of the same ten modules. `test_multi_folders.py` checks that deleting one folder keeps the line in its other folders and that a new folder only suggests filed lines, never moves them. `test_db.py` checks that an old database's `folder_id` values become rows in `item_folders`.

## [5]. Reminders are not pushed to the phone

Date: 2026-10-03
Status: Decided
Context: A reminder helps most when it reaches the phone at the right time, even while the app is closed. Noteezer now installs as a phone app, so push notifications look like the obvious next step.
Decision: Push notifications are not built in this assignment. A due reminder is shown at the top when the app is opened, and the background task only sorts lines; it never sends anything to the phone.
Alternatives considered: Web Push sent from the existing in-process background task fits the single-process rule, because it only calls the browser's push service. It was rejected for now because a reminder would only arrive while the computer running Noteezer is on, which is not true for a laptop at home. It also needs signing keys kept in environment variables, a table of phone subscriptions, and another dependency for encrypting the messages. A second always-on process just for notifications was rejected because the assignment allows only one.
Consequences: A reminder is missed if the app is not opened around its time. Push can be added in Assignment 2, once the app runs on an always-on server: the reminders side would store subscriptions and send due reminders, without changing how lines are captured.

