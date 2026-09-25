# Architecture Decision Record

## [1]. Backend language and framework

Date: 2026-09-25
Status: Decided
Context: The app is one process with server-rendered phone pages, SQLite, and two small domains. The written check requires explaining the code with no notes, and the brief treats a heavier framework than the app needs as poor judgment.
Decision: Python with Flask, Jinja templates, and the standard-library `sqlite3` module. Business rules live in plain modules. Routes stay thin.
Alternatives considered: Django was rejected because its admin, ORM, and auth stack are more than register/login and two domains need. FastAPI was rejected because this version serves HTML pages rather than a separate API, and Flask is the smaller surface to explain. A React frontend was rejected because it adds a second toolchain and more dependencies without a second process to justify them.
Consequences: The dependency list stays small (Flask plus the test tools). Account sessions use Flask's signed cookie and Werkzeug's password hash, which already come with Flask. A later service split can lift the two domain modules out without rewriting them around a web framework.

## [2]. Inbox and reminders are separate domains

Date: 2026-09-25
Status: Decided
Context: The product is one text box that becomes a note, task, link, or reminder. If the due time lived on the same row as the text, the app would be a single notes table and would be too thin for two feature domains.
Decision: Inbox owns the text, kind, tags, search, edit, delete, and task completion. Reminders own `remind_at` and status (scheduled, due, snoozed, done) in their own table, keyed by item id. The only coupling is `schedule(item_id, when)` after inbox saves an item that has a picked time. Deleting an item also removes its reminder through that same boundary.
Alternatives considered: One `notes` table with an optional `remind_at` column was rejected because reminders would not have their own responsibility or data. Making tags the second domain was rejected because the behaviour the user needs next is due, snooze, and done, which is a schedule, not a label.
Consequences: Assignment 2 can run reminders as its own service that stores item ids and due times, while inbox keeps the text. Lock-screen delivery can sit on the reminder side later without changing how lines are captured.
