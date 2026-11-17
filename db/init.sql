-- Creates the two empty databases, one per service (ADR-6). The services
-- create their own tables with their migrations when they start. Safe to run
-- again: a database that exists is left as it is.
IF DB_ID('noteezer_inbox') IS NULL
    CREATE DATABASE noteezer_inbox;
GO
IF DB_ID('noteezer_reminders') IS NULL
    CREATE DATABASE noteezer_reminders;
GO
-- Azure SQL has this on by default: readers see the last committed rows
-- instead of waiting for a writer to finish.
ALTER DATABASE noteezer_inbox SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
ALTER DATABASE noteezer_reminders SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
GO
