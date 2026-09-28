PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'task', 'link', 'reminder')),
    task_done INTEGER NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (kind = 'task' OR task_done = 0)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);

CREATE TABLE IF NOT EXISTS reminders (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    remind_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'due', 'snoozed', 'done')),
    snoozed_until TEXT,
    repeat_daily INTEGER NOT NULL DEFAULT 0 CHECK (repeat_daily IN (0, 1)),
    repeat_until TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_user_created ON items (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, remind_at);
