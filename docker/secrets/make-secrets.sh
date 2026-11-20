#!/bin/sh
# Run once by the one-off "secrets" service in docker-compose.yml, before
# anything else starts. It makes the four local secrets, so a clean clone runs
# with just `docker compose up --build`, and writes one small env file per
# container, so each container sees only the secrets it needs.
#
# A value set in .env always wins (and replaces a saved one). Otherwise a value
# saved by an earlier run is kept, because the database volume was created with
# it. Otherwise a new random one is made. Nothing is printed.
set -eu
cd /secrets

# 24 random lowercase letters and digits, plus "Ab1": SQL Server wants upper
# case, lower case, and digits, and the passwords go inside a URL, so no
# symbols.
random() { printf '%sAb1' "$(tr -dc 'a-z0-9' < /dev/urandom | head -c 24)"; }

# keep <file> <value from .env>
keep() {
    if [ -n "$2" ]; then
        printf '%s' "$2" > "$1"
    elif [ ! -s "$1" ]; then
        random > "$1"
    fi
}

keep sa_password "$SA_PASSWORD"
keep inbox_db_password "$INBOX_DB_PASSWORD"
keep reminders_db_password "$REMINDERS_DB_PASSWORD"
keep session_secret "$SESSION_SECRET"

sa=$(cat sa_password)
inbox=$(cat inbox_db_password)
reminders=$(cat reminders_db_password)
session=$(cat session_secret)

# with-secrets.sh loads <container>.env before the container's own command.
printf "MSSQL_SA_PASSWORD='%s'\nSQLCMDPASSWORD='%s'\n" "$sa" "$sa" > sqlserver.env
printf "SQLCMDPASSWORD='%s'\nINBOX_DB_PASSWORD='%s'\nREMINDERS_DB_PASSWORD='%s'\n" "$sa" "$inbox" "$reminders" > db-init.env
# service_env <login> <password>: each service logs in as its own user, which
# can open only noteezer_<login> (db/init.sql).
service_env() {
    printf "DATABASE_URL='mssql://%s:%s@sqlserver:1433/noteezer_%s'\nSESSION_SECRET='%s'\n" "$1" "$2" "$1" "$session"
}
service_env reminders "$reminders" > reminders.env
service_env inbox "$inbox" > inbox.env

# The services run as their own non-root users and must be able to read their file.
chmod 644 ./*.env
echo "Local secrets ready."
