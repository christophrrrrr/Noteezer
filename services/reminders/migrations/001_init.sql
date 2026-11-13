-- Reminders service schema, first version on SQL Server (Azure SQL in production).
--
-- This service owns one table (ADR-6). item_id points at an item in the inbox
-- service's database, so it cannot be a foreign key; user_id is copied onto
-- each row so ownership is checked here without asking inbox.
-- Timestamps are ISO 8601 text with a binary collation, so comparing two of
-- them works character by character, the same as in the inbox service.

CREATE TABLE reminders (
    item_id INT PRIMARY KEY,
    user_id INT NOT NULL,
    remind_at NVARCHAR(40) COLLATE Latin1_General_BIN2 NOT NULL,
    status NVARCHAR(10) NOT NULL CHECK (status IN ('scheduled', 'due', 'snoozed', 'done')),
    snoozed_until NVARCHAR(40) COLLATE Latin1_General_BIN2 NULL,
    repeat_daily INT NOT NULL DEFAULT 0 CHECK (repeat_daily IN (0, 1)),
    repeat_until NVARCHAR(10) COLLATE Latin1_General_BIN2 NULL
);

CREATE INDEX idx_reminders_user_due ON reminders (user_id, status, remind_at);
