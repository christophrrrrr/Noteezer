-- 002: count how often each reminder was snoozed, shown as "snoozed 2x".
--
-- This changes a table that already holds data. Every existing row is kept:
-- the column is added with a default, then backfilled. Never edit this file
-- once it is applied; a correction is a new file (ADR-7).

ALTER TABLE reminders ADD snoozed_count INT NOT NULL
    CONSTRAINT df_reminders_snoozed_count DEFAULT 0
    CONSTRAINT ck_reminders_snoozed_count CHECK (snoozed_count >= 0);
GO

-- Backfill: a reminder that is snoozed right now was snoozed at least once.
-- Older snoozes were not recorded, so the count starts from what is known.
UPDATE reminders SET snoozed_count = 1 WHERE status = 'snoozed';
