-- Inbox service schema, first version on SQL Server (Azure SQL in production).
--
-- Ported from the SQLite schema.sql of Assignment 1. What changed on the way:
-- * Timestamps stay ISO 8601 text, as before. They use a binary collation so
--   comparing two of them (remind_at <= now) works character by character,
--   exactly like SQLite did.
-- * SQL Server refuses a cascade that can reach the same table twice. Three
--   former cascades are now done in code instead:
--     folders.parent_id          (subfolders are deleted in folders.delete_folder)
--     items.split_from           (cleared in inbox.delete_item)
--     sort_results.proposed_parent_id (cleared in folders.delete_folder)
-- * "One name per parent folder, any case" used an expression index. SQL Server
--   cannot index an expression, so parent_key is a computed column, and the
--   default collation is already case-insensitive.
--
-- TEMPORARY: the reminders table is still here because inbox code reads and
-- writes it directly until the reminders HTTP API exists (roadmap, Sat 14).
-- The reminders service has its own copy in services/reminders/migrations.
-- This file can still change until the first deploy (Tue 17); after that,
-- every change is a new numbered file.

CREATE TABLE users (
    id INT IDENTITY(1, 1) PRIMARY KEY,
    email NVARCHAR(320) NOT NULL CONSTRAINT uq_users_email UNIQUE,
    password_hash NVARCHAR(200) NOT NULL,
    created_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    timezone NVARCHAR(64) NULL
);

CREATE TABLE folders (
    id INT IDENTITY(1, 1) PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    parent_id INT NULL REFERENCES folders(id),
    name NVARCHAR(100) NOT NULL,
    created_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    parent_key AS ISNULL(parent_id, 0) PERSISTED
);

CREATE TABLE items (
    id INT IDENTITY(1, 1) PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    body NVARCHAR(MAX) NOT NULL,
    kind NVARCHAR(10) NOT NULL CHECK (kind IN ('note', 'idea', 'task', 'link', 'reminder')),
    task_done INT NOT NULL DEFAULT 0 CHECK (task_done IN (0, 1)),
    created_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    updated_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    split_from INT NULL REFERENCES items(id),
    hidden INT NOT NULL DEFAULT 0,
    CONSTRAINT ck_items_only_tasks_done CHECK (kind = 'task' OR task_done = 0)
);

CREATE TABLE item_folders (
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    folder_id INT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, folder_id)
);

CREATE TABLE folder_suggestions (
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    folder_id INT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, folder_id)
);

CREATE TABLE folder_checks (
    folder_id INT PRIMARY KEY REFERENCES folders(id) ON DELETE CASCADE,
    requested_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    checked_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NULL,
    attempts INT NOT NULL DEFAULT 0
);

CREATE TABLE tags (
    id INT IDENTITY(1, 1) PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id),
    name NVARCHAR(100) NOT NULL,
    CONSTRAINT uq_tags_user_name UNIQUE (user_id, name)
);

CREATE TABLE item_tags (
    item_id INT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id INT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);

CREATE TABLE reminders (
    item_id INT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    remind_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    status NVARCHAR(10) NOT NULL CHECK (status IN ('scheduled', 'due', 'snoozed', 'done')),
    snoozed_until NVARCHAR(40) COLLATE Latin1_General_BIN2 NULL,
    repeat_daily INT NOT NULL DEFAULT 0 CHECK (repeat_daily IN (0, 1)),
    repeat_until NVARCHAR(10) COLLATE Latin1_General_BIN2 NULL
);

CREATE TABLE sort_results (
    item_id INT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    status NVARCHAR(10) NOT NULL CHECK (status IN ('filed', 'review', 'accepted', 'retry', 'failed')),
    folder_id INT NULL REFERENCES folders(id) ON DELETE SET NULL,
    confidence FLOAT NULL,
    engine NVARCHAR(40) NOT NULL,
    attempts INT NOT NULL DEFAULT 0,
    retry_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NULL,
    error NVARCHAR(MAX) NULL,
    sorted_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    proposed_name NVARCHAR(100) NULL,
    proposed_parent_id INT NULL REFERENCES folders(id),
    suggested_remind_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NULL
);

CREATE TABLE images (
    item_id INT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    mime NVARCHAR(40) NOT NULL,
    data VARBINARY(MAX) NOT NULL,
    ai_allowed INT NOT NULL DEFAULT 1,
    title NVARCHAR(MAX) NULL,
    text NVARCHAR(MAX) NULL,
    read_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NULL
);

CREATE TABLE link_previews (
    item_id INT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    url NVARCHAR(2048) NOT NULL,
    title NVARCHAR(MAX) NOT NULL,
    description NVARCHAR(MAX) NOT NULL,
    site NVARCHAR(255) NOT NULL,
    fetched_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL
);

CREATE INDEX idx_items_user_created ON items (user_id, created_at);
CREATE INDEX idx_items_split_from ON items (split_from);
CREATE INDEX idx_item_folders_folder ON item_folders (folder_id);
CREATE INDEX idx_folders_parent ON folders (parent_id);
CREATE UNIQUE INDEX idx_folders_sibling_name ON folders (user_id, parent_key, name);
CREATE INDEX idx_reminders_due ON reminders (status, remind_at);
