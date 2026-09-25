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
