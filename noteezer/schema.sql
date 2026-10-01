PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    timezone TEXT
);

CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    parent_id INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    body TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('note', 'idea', 'task', 'link', 'reminder')),
    task_done INTEGER NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    split_from INTEGER REFERENCES items(id) ON DELETE SET NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS sort_results (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('filed', 'review', 'accepted', 'retry', 'failed')),
    folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    confidence REAL,
    engine TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT,
    error TEXT,
    sorted_at TEXT NOT NULL,
    proposed_name TEXT,
    proposed_parent_id INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    suggested_remind_at TEXT
);

CREATE TABLE IF NOT EXISTS images (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    mime TEXT NOT NULL,
    data BLOB NOT NULL,
    ai_allowed INTEGER NOT NULL DEFAULT 1,
    title TEXT,
    text TEXT,
    read_at TEXT
);

CREATE TABLE IF NOT EXISTS link_previews (
    item_id INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    site TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_user_created ON items (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_items_folder ON items (user_id, folder_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_folders_sibling_name
    ON folders (user_id, IFNULL(parent_id, 0), name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, remind_at);
