-- Creates the two databases and one login per service (ADR-6, ADR-7). Each
-- login can reach only its own database: it has a user there and nowhere
-- else. The services create their tables with their own migrations when they
-- start, so the login may change the schema (db_ddladmin) and read and write
-- rows. Safe to run again: what exists is kept, and the passwords are set
-- again so they always match .env.
--
-- sqlcmd fills in $(INBOX_DB_PASSWORD) and $(REMINDERS_DB_PASSWORD) from the
-- environment variables of the same name, so no password is written here.
-- They must not contain a single quote.
IF DB_ID('noteezer_inbox') IS NULL
    CREATE DATABASE noteezer_inbox;
GO
IF DB_ID('noteezer_reminders') IS NULL
    CREATE DATABASE noteezer_reminders;
GO
-- After a restart SQL Server answers before databases that already exist have
-- finished recovering, so wait until both are online (at most a minute).
DECLARE @tries INT = 0;
WHILE (DATABASEPROPERTYEX('noteezer_inbox', 'Status') <> 'ONLINE'
       OR DATABASEPROPERTYEX('noteezer_reminders', 'Status') <> 'ONLINE') AND @tries < 60
BEGIN
    WAITFOR DELAY '00:00:01';
    SET @tries += 1;
END
GO
-- Azure SQL has this on by default: readers see the last committed rows
-- instead of waiting for a writer to finish. Only set once, because it has
-- to close every other connection to the database.
IF (SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = 'noteezer_inbox') = 0
    ALTER DATABASE noteezer_inbox SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
IF (SELECT is_read_committed_snapshot_on FROM sys.databases WHERE name = 'noteezer_reminders') = 0
    ALTER DATABASE noteezer_reminders SET READ_COMMITTED_SNAPSHOT ON WITH ROLLBACK IMMEDIATE;
GO

IF SUSER_ID('inbox') IS NULL
    CREATE LOGIN inbox WITH PASSWORD = '$(INBOX_DB_PASSWORD)', DEFAULT_DATABASE = noteezer_inbox;
ELSE
    ALTER LOGIN inbox WITH PASSWORD = '$(INBOX_DB_PASSWORD)';
GO
IF SUSER_ID('reminders') IS NULL
    CREATE LOGIN reminders WITH PASSWORD = '$(REMINDERS_DB_PASSWORD)', DEFAULT_DATABASE = noteezer_reminders;
ELSE
    ALTER LOGIN reminders WITH PASSWORD = '$(REMINDERS_DB_PASSWORD)';
GO

USE noteezer_inbox;
IF USER_ID('inbox') IS NULL
    CREATE USER inbox FOR LOGIN inbox;
ALTER ROLE db_ddladmin ADD MEMBER inbox;
ALTER ROLE db_datareader ADD MEMBER inbox;
ALTER ROLE db_datawriter ADD MEMBER inbox;
GO

USE noteezer_reminders;
IF USER_ID('reminders') IS NULL
    CREATE USER reminders FOR LOGIN reminders;
ALTER ROLE db_ddladmin ADD MEMBER reminders;
ALTER ROLE db_datareader ADD MEMBER reminders;
ALTER ROLE db_datawriter ADD MEMBER reminders;
GO
