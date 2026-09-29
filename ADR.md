# Architecture Decision Record

## [1]. Backend language and framework

Date: 2026-09-25
Status: Decided
Context: The app is one process with phone pages, SQLite, and two domains that Assignment 2 will split into services. The framework should be the one you would pick for that service, not a larger platform and not a second UI toolchain.
Decision: Python with FastAPI and Jinja templates, served as one process, with the standard-library `sqlite3` module behind repository functions. Inbox and reminder rules live in plain modules that do not import FastAPI. Routes validate input and call those modules.
Alternatives considered: Django was rejected because its admin, ORM, and auth stack are more than register/login and two domains need. Flask was rejected because the known next step is to split two HTTP services, and Flask does not give typed request boundaries for that split. A React frontend was rejected because one text box does not need a second toolchain. SQLAlchemy was rejected because the schema is a few tables and the tested logic should stay in our functions, not in an ORM.
Consequences: Dependencies stay at FastAPI, Uvicorn, Jinja2, and the test tools. Passwords are hashed with the standard-library `hashlib`, and the session is a signed cookie implemented in our code. Assignment 2 can move each domain module behind its own FastAPI app without rewriting the rules.

## [2]. Inbox and reminders are separate domains

Date: 2026-09-25
Status: Decided
Context: The product is one text box that becomes a note, task, link, or reminder. If the due time lived on the same row as the text, the app would be a single notes table and would be too thin for two feature domains.
Decision: Inbox owns the text, kind, tags, search, edit, delete, and task completion. Reminders own `remind_at` and status (scheduled, due, snoozed, done) in their own table, keyed by item id. The only coupling is `schedule(item_id, when)` after inbox saves an item that has a picked time. Deleting an item also removes its reminder through that same boundary.
Alternatives considered: One `notes` table with an optional `remind_at` column was rejected because reminders would not have their own responsibility or data. Making tags the second domain was rejected because the behaviour the user needs next is due, snooze, and done, which is a schedule, not a label.
Consequences: Assignment 2 can run reminders as its own service that stores item ids and due times, while inbox keeps the text. Lock-screen delivery can sit on the reminder side later without changing how lines are captured.

Updated: 2026-09-29. Folders are a third area in `noteezer/folders.py` that owns the folder tree: create, rename, move, delete, and the rule that a folder cannot move inside itself. Inbox stores which folder an item is in, but the only call it makes into folders is `require_folder(user_id, folder_id)` before it saves or moves an item. This is where automatic sorting will live, so it can later become its own service next to reminders.

## [3]. How inbox rows relate to reminder rows

Date: 2026-09-26
Status: Decided
Context: The two domains need a schema that matches the split. A due time on `items` would collapse them. Tags and accounts also need a place that does not become a third domain.
Decision: `items` stores body and kind for one user. `reminders` uses `item_id` as its primary key, so an item has at most one schedule, and the schedule does not copy the text. A reminder row is only created when `kind` is `reminder`. Tags are one row per user and name in `tags`, linked through `item_tags`. Accounts are `users.email` plus a password hash.
Alternatives considered: An optional `remind_at` column on `items` was rejected because inbox would own the schedule. Allowing any kind to have a due time was rejected so a task stays a task, and only a reminder item gets a schedule. Storing tags as text on the item was rejected because the same `#groceries` tag must be one record the user can filter on.
Consequences: Deleting an item deletes its reminder and tag links. The database diagram is `users` to `items` and `tags`, `items` to `item_tags` to `tags`, and `items` to `reminders` one-to-one. `schedule(item_id, when)` is the only write from inbox into reminders.

Updated: 2026-09-28. A daily reminder stores `repeat_daily` and an optional `repeat_until` on the same reminder row. `remind_at` is the next occurrence, not a copy of the text. Marking it done moves `remind_at` forward one day until that end date, or until it is turned off when no end date is set.

Updated: 2026-09-29. `folders` has `parent_id` pointing at another folder, so folders nest like Notion pages. A unique index on user, parent, and name (ignoring case) stops two sibling folders with the same name. The same name in different parents is allowed. `items.folder_id` is optional, and no folder means Unsorted. Deleting a folder deletes its subfolders (`ON DELETE CASCADE`), but items are never deleted with a folder: `ON DELETE SET NULL` moves them to Unsorted. The kind of an item (note, idea, task, link, reminder) stays separate from its folder, because a Game folder holds both tasks and ideas. Older databases are rebuilt once on startup because SQLite cannot change the kind CHECK in place. Rejected: storing a folder path as text on the item, because renaming or moving a folder would then need to rewrite every item.
