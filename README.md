# Noteezer

Noteezer is a phone-friendly capture app for the notes, tasks, links, and reminders that would otherwise be messages sent to yourself.

You type one line and save it. Optional buttons can force the kind. A reminder takes its time from a picker or, with Gemini, from the text itself. The app stores everything in SQL Server (Azure SQL when deployed) and shows what is due when you open it.

The use case is approved. You can register, save a line, search it, edit it, delete it, and mark a task done. A reminder can be a single time or every day from a start time until an optional end date. Snooze hides it for one hour. Marking a daily reminder done moves it to the next day.

## Who it is for

One person who currently pastes notes into a chat with themselves and later searches that history. More than one person can register. Each account sees only its own items.

## Feature domains

**Inbox.** Saves the line, assigns a kind, keeps tags, and supports search, edit, delete, and marking a task done.

**Folders.** Nested folders like Notion pages (Projects / Game / Cannon Circle). Create, rename, move, and delete them. A line can be in several folders at once, for example Table Tennis and Cool videos; tick them in the line's menu. A line in no folder is in Unsorted. Deleting a folder never deletes lines: a line that was in no other folder goes back to Unsorted. Inbox only calls `require_folder(user_id, folder_id)` before it puts a line in a folder.

**Sorting.** Files new lines from Unsorted into a folder in the background. First the title of a line's link is fetched: YouTube through oEmbed, other pages from their `<head>`. Links that point into a private network are refused. The rules come next: a line that names a folder or has its name as a `#tag`, including in a link title, goes there, and the deepest matching folder wins. If the rules are not sure and `GEMINI_API_KEY` is set, Gemini picks a folder with a confidence, or proposes a new folder. A `#tag` or folder name the rules find still decides the folder, because that is the user's own choice, but Gemini is asked for every line so it can also read times and kinds. Calls are spaced 4 seconds apart to stay inside the free tier. A confident answer is filed, into every folder the engine is sure about (a line with `#tabletennis #coolvideos` goes into both), and marked "filed by rules" or "filed by gemini". An unclear line stays in Unsorted with a suggestion (Accept) or a proposed folder (Create folder). Failed calls are retried with a growing wait. Gemini also turns a plain note into a task or an idea; links, reminders, and lines with a `todo`, `buy`, or `idea` prefix keep their kind. A note that holds several separate points, like a list of bugs under a heading, is split into one item per point, and each part is sorted on its own. The original is hidden, not deleted, and Undo split on any part brings it back. Creating or renaming a folder retries the lines that matched nothing, and Sort again retries everything in Unsorted. A new folder is also checked against lines that are already filed: one call looks through the 60 most recent ones and puts an "Also add to Cool videos" button on those that fit. Nothing is moved without a tap.

**Images.** A line can carry one screenshot or photo: pick it with the image field or paste it into the text box. Only PNG, JPEG, WebP, and GIF up to 10 MB are accepted, judged from the file's bytes. Images are stored in the database and served only to their owner. With "Let AI read this image" ticked (the default), Gemini reads the image while sorting. A line saved with only an image starts as "Screenshot" and takes Gemini's title as its text, and the text Gemini read is shown below the image and found by search. Unticked, the image never leaves the server.

**Reminders.** Stores when an item is due and whether it is scheduled, due, snoozed, or done. It does not store the item text. Inbox saves the item, then calls `schedule(item_id, when)` when a time was picked.

## How a line gets its kind

- The Reminder button, or a line that starts with `remind`, requires a picked date and time and becomes a reminder.
- A URL becomes a link.
- A line that starts with `todo` or `buy` becomes a task.
- A line that starts with `idea` becomes an idea.
- Anything else becomes a note.
- `#words` become tags on any kind.
- Note, Idea, Task, Link, and Reminder buttons override that guess.
- The kind is separate from the folder. Saving while a folder is open puts the line in that folder. Otherwise it goes to Unsorted.

Without Gemini, times come only from the picker. With Gemini, a line that starts with `remind` can be saved without the picker: "remind me friday 3pm call mom" is scheduled once the sorter has read it, and until then it shows "no time yet". Other lines with a time, like "dentist tuesday 10:00", get a "Remind me Tue 6 Oct, 10:00" button instead of a reminder. The UI sends the browser's timezone when you log in and each time it opens, and it is stored on the account, so the picker and phrases like "friday" are read in the user's time. The server's own clock is UTC once deployed.

## In this assignment

- Two services in Python with FastAPI, inbox and reminders, each with its own SQL Server database (ADR-6, ADR-7). Azure SQL once deployed.
- A static UI in `ui/`: plain HTML, CSS, and JavaScript with no build step, calling both services with a bearer token (ADR-6).
- Due reminders appear at the top when you open the app. Snooze and done are supported.
- A dark page in the style of Notion. Folders are in a sidebar, which becomes a drawer on a phone. Notes run from oldest to newest like a chat, with the newest just above the capture bar and due reminders pinned on top.
- Minimal accounts: register, log in, and log out with email and password. Not a third feature domain.

## Left for later

Noteezer is deployed on Azure at **https://crintzieu2024ui.z1.web.core.windows.net**, so it no longer needs the laptop (ADR-8). Every push to `main` deploys it; the one-time setup is under [Azure](#azure).

Push notifications to the phone are still not built (ADR-5, ADR-10). The deployment scales to zero, so nothing is awake at a reminder's time to send one, and keeping something awake would use up the free Azure allowances within days. Due reminders are pinned at the top when the app is opened. Also out of scope: chat import, offline mode, sharing items with other people, and video files (a video link is understood from its title).

## Run

Noteezer is a static UI and two services (ADR-6). **inbox** (`services/inbox`, port 8000) is a JSON API under `/api` for accounts, lines, folders, sorting, images, and link previews. **reminders** (`services/reminders`, port 8001) stores reminder times and decides when one is due. Each has its own database. The **UI** (`ui/`, port 8080) is plain files that call both: inbox for everything, and reminders directly for done and snooze. Inbox calls reminders over HTTP with a 2 second timeout, and keeps working without times when reminders is down.

### Everything at once, with Docker

Docker Desktop has to be running. From a clean clone, with no other step:

```
docker compose up --build
```

The passwords are made on the first start. A one-off `secrets` container makes random ones for SQL Server's administrator, the two service logins, and `SESSION_SECRET`, and keeps them in the `secrets` volume. Each container then loads only its own into its environment through `docker/secrets/with-secrets.sh` before its normal command. To choose them yourself, copy `.env.example` to `.env` (git ignores it) and fill in any of them; a value in `.env` always wins. SQL Server keeps the administrator password from its first start, so changing `SA_PASSWORD` later needs `docker compose down -v`, which also deletes the data. `GEMINI_API_KEY` in `.env` turns on Gemini sorting.

Open `http://localhost:8080`. Compose makes or loads the local passwords, starts SQL Server, waits until it answers, runs `db/init.sql` to create the two databases and one login per service (`inbox` can open only `noteezer_inbox`, `reminders` only `noteezer_reminders`), starts reminders and inbox, each logged in as its own user and applying its own migrations (ADR-7), and serves `ui/` with nginx. nginx writes `config.js` from `INBOX_URL` and `REMINDERS_URL` and sends a Content-Security-Policy that only lets the page call those two addresses. Only ports 8080, 8000, and 8001 are published, all on `127.0.0.1`; the database is reachable only from the other containers. `docker compose down` stops everything and keeps the data and the passwords; `docker compose down -v` deletes both.

If an antivirus or a company proxy re-signs HTTPS on your computer (Norton does), `pip install` inside the image build fails with `CERTIFICATE_VERIFY_FAILED`. Export that root certificate as a PEM file outside the repo and add a git-ignored `docker-compose.override.yml`:

```
services:
  inbox:
    build:
      secrets: [extra_ca]
  reminders:
    build:
      secrets: [extra_ca]
secrets:
  extra_ca:
    file: C:/path/to/your-root-ca.pem
```

The Dockerfiles use it only for the `pip install` step, so it never lands in an image.

### One service at a time, for development and tests

The tests and a service started by hand need a SQL Server. Locally that is a container (Docker Desktop has to be running). Choose a password of your own (at least 8 characters with upper case, lower case, and digits; SQL Server refuses weaker ones) and never commit it. In PowerShell:

```
$env:SA_PASSWORD = "<your password>"
docker run -d --name noteezer-sql -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=$env:SA_PASSWORD -p 127.0.0.1:1433:1433 mcr.microsoft.com/mssql/server:2022-latest
docker exec noteezer-sql /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P $env:SA_PASSWORD -Q "CREATE DATABASE noteezer_inbox; ALTER DATABASE noteezer_inbox SET READ_COMMITTED_SNAPSHOT ON; CREATE DATABASE noteezer_reminders; ALTER DATABASE noteezer_reminders SET READ_COMMITTED_SNAPSHOT ON"
```

The container only listens on this computer (`127.0.0.1`). Each service reads a git-ignored `.env` file in its folder, with its connection string in `DATABASE_URL` (`mssql://user:password@host:port/database`; a password with `@`, `:` or `/` in it is written percent-encoded). By hand it is simplest to use `sa`; compose uses the per-service logins. Both need the same `SESSION_SECRET`, a long random value (`python -c "import secrets; print(secrets.token_hex(32))"` makes one):

```
# services/inbox/.env
DATABASE_URL=mssql://sa:<your password>@localhost:1433/noteezer_inbox
SESSION_SECRET=<the same long random value>

# services/reminders/.env
DATABASE_URL=mssql://sa:<your password>@localhost:1433/noteezer_reminders
SESSION_SECRET=<the same long random value>
```

Add `CORS_ORIGINS=http://localhost:8080` to both files so the UI may call them. The commands below are for Windows; on macOS or Linux use `python3 -m venv .venv`, `source .venv/bin/activate`, and `/` in the paths. Start each service in its own terminal, and serve the UI from a third (its `config.js` already points at ports 8000 and 8001):

```
cd services\reminders
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app

cd services\inbox
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m app

python -m http.server 8080 -d ui
```

Each process listens on `0.0.0.0`. On start it applies the numbered SQL files in its `migrations` folder that its database has not seen yet, each once and in its own transaction, and records them in `schema_migrations`. `python -m app.migrate` does the same by hand. Every file applied is logged (`Applied migration 001_init.sql`), and a start with nothing to do logs that the database is up to date. A file that was applied is never edited; a change is a new, higher number. Both services log one line per request with a request id; inbox passes the id on to reminders in `X-Request-ID`, so one id finds a request in both logs. `GET /health` on either reads from and writes to its database (the write is rolled back) and answers `{"status": "ok", "db": "ok", "version": "<commit>"}`, or 503 with `"status": "error", "db": "down"` when it cannot. The version is the commit the image was built from (`GIT_SHA`), `dev` outside an image. Everything else is configured with environment variables:

| Variable | Default | What it does |
|---|---|---|
| `PORT` | `8000` inbox, `8001` reminders | Port to listen on. |
| `DATABASE_URL` | none | Required. `mssql://user:password@host:port/database` for the service's own database (port 1433 when left out). The database must exist; the migrations create the tables in it. |
| `SESSION_SECRET` | none | Required, the same in both services. Signs and checks the login token (ADR-6). |
| `CORS_ORIGINS` | empty | Both. Comma-separated sites allowed to call the service from a browser: the UI's address, `http://localhost:8080` locally. Empty allows none. |
| `REMINDERS_URL` | `http://127.0.0.1:8001` | Inbox only. Where the reminders service is. |
| `REMINDERS_TIMEOUT` | `2` | Inbox only. Seconds to wait for reminders before carrying on without times. |
| `GEMINI_API_KEY` | not set | Inbox only. Turns on Gemini sorting. Without it the rules sort and the app works the same otherwise. |
| `SORTER` | `auto` | `auto` uses Gemini when a key is set and the rules otherwise, `rules` never calls Gemini, `gemini` asks for it. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model for text lines. |
| `GEMINI_IMAGE_MODEL` | `gemini-3.8-flash` | Model for lines with an image. |
| `GIT_SHA` | `dev` | Set when the image is built. Shown by `/health` as the version. |

Variables already set in the environment win over the `.env` file. `GEMINI_API_KEY=...` can go in `services/inbox/.env` too. A free key comes from Google AI Studio. On the free tier Google may use the notes it sorts to improve its models.

The architecture and database diagrams are in [docs/diagrams.md](docs/diagrams.md).

Open `http://localhost:8080`, create an account, and save a line. A line that starts with `todo` or `buy` becomes a task, a URL becomes a link, and `remind` or the Reminder choice becomes a reminder that needs the date picker. Check Every day to repeat it, and set the date field only if it should stop. A reminder time that is already past is marked due and listed first. `#words` become tags.

## Use it on your phone

Noteezer installs as an app on Android (Chrome) and iPhone (Safari, Add to Home Screen). Installing and sharing need HTTPS. In Assignment 1 that came from `tailscale serve` on the laptop; with a separate UI and two services that is three addresses, so the phone uses the Azure address once Noteezer is deployed there. Open it, log in, and in Chrome choose Install app (or Add to Home screen). On Android, Noteezer then appears in the share sheet: sharing a YouTube video, a page, or a screenshot saves it to Unsorted, where it is sorted like any other line.

The app is a manifest (`ui/manifest.webmanifest`) and a service worker (`ui/sw.js`). The worker caches only the page, styles, script, and icons, and shows an offline page when nothing can be reached. It never touches calls to the services, so notes and images are never cached on the phone. The share sheet posts to `/share` on the UI's address; the worker keeps that post and opens the app, which sends it to inbox with the token. The token lasts 90 days, so the installed app stays signed in. Logging out makes the browser forget it (ADR-6).

## Tests

Each service has its own tests. They never call Gemini or fetch real pages; they use fakes. They do run against a real SQL Server, the container from the section above: each run creates fresh test databases, migrates them, empties them before every test, and drops them at the end. `DATABASE_URL` points at the server with a login that may create databases (`sa` locally; the database part of the URL is ignored), read from the environment or the service's `.env`; without it the run stops with a message. The test tools are in `requirements-dev.txt` (`pip install -r requirements-dev.txt`), so the images install only `requirements.txt`. The tests set their own `SESSION_SECRET`.

The inbox tests start the real reminders service as a second process, on a free port with its own test database, so every reminder an inbox test saves crosses the seam over real HTTP. `tests/test_seam.py` also points inbox at a reminders that is down, slow, or answering with errors:

```
cd services\inbox
pytest --cov

cd services\reminders
pytest --cov
```

Coverage is measured on each service's whole `app` (settings in its `pyproject.toml`), and the run fails below 70%. Inbox is 132 tests at 97%. `tests/test_api.py` drives the JSON API the UI uses, including that one person never reaches another's lines, and `tests/test_ui_files.py` checks the UI's files without a browser: the manifest and icons, that every file the page loads exists, that the page has no inline script, and that note text never becomes HTML. Reminders is 23 tests at 91%, with the rules (`reminders.py`) and tokens at 100%.

`ruff check services scripts` lints both services with the rules in `ruff.toml`.

## CI

`.github/workflows/ci.yml` runs on every push and pull request, and a red job blocks the merge (ADR-9):

- **lint**: ruff, including its Bandit security rules (SAST). A finding that is not a real risk is silenced on its own line with the reason.
- **test**, once per service: starts a throwaway SQL Server container in the job, runs the tests with coverage (below 70% fails), and checks that the committed `openapi.yaml` still matches the code. `pip-audit` first checks the service's dependencies against the published advisories (SCA) and fails on any known vulnerability.
- **contracts**: validates `system.yaml` against `system.schema.json` and both `openapi.yaml` files against the OpenAPI specification, then runs `scripts/check_system.py`, which checks that every variable the code reads is listed in `system.yaml` and every endpoint inbox calls exists in reminders' `openapi.yaml`.
- **build**: both Docker images, with the commit SHA as their version. Trivy scans each image and fails on a HIGH or CRITICAL vulnerability that already has a fix.

`.github/workflows/codeql.yml` runs CodeQL on pull requests, on `main`, and weekly, for the Python services, the UI's JavaScript, and the workflows; its findings are under Security > Code scanning. Every action in the workflows is pinned to a full commit SHA. On GitHub, Dependabot alerts and security updates, and secret scanning with push protection, are turned on, next to GitGuardian.

CI never touches Azure SQL.

## Data on Azure

The notes from Assignment 1 were moved from `data/noteezer.db` into the deployed system once, through the services' APIs (ADR-7):

```
NOTEEZER_PASSWORD=<new password> python scripts/import_a1.py --inbox https://<inbox address> --reminders https://<reminders address>
```

It recreates the account, folders, lines, ticks, and reminders, and refuses to run into an account that already has lines. The password comes from the environment, never the command line. It needs `httpx` (in `services/inbox/requirements.txt`).

A new account starts with an Examples folder holding three lines that show how a line gets its kind. Seeding adds nothing to an account that already has that folder, so running it again never doubles the lines; `POST /api/register` with `"examples": false` skips it.

### Migration 002

`services/reminders/migrations/002_snoozed_count.sql` adds `snoozed_count` to `reminders`, a table that already held data on Azure. Existing rows keep every value; the column starts at 0, and a reminder that was snoozed at that moment is backfilled to 1. The evidence is in [docs/evidence](docs/evidence): the rows before (`migration-002-before.txt`), the startup log line, and the rows after (`migration-002-after.txt`).

## When reminders is down

On 2026-11-20 reminders was locked out of its own database on Azure on purpose (`DENY CONNECT`). Inbox stayed healthy, lines still saved within about 4.5 s, and a reminder saved meanwhile showed "no time yet, reminders is unavailable" under a red banner (screenshot below). A deploy merged during the outage failed at its health check, because reminders' new revision could not start. After `GRANT CONNECT` the same run was re-run and passed. The test also found two bugs, both fixed: a refused login was retried for a minute as if the database were paused, and the health check could take 27 minutes to give up. Every step, log line and run link is in [docs/evidence/partial-failure.md](docs/evidence/partial-failure.md).

<img src="docs/evidence/partial-failure-reminders-down.png" alt="The inbox page while reminders is down: a red banner says reminders are unavailable and lines still save, and the new reminder line says no time yet, reminders is unavailable" width="300">

## Secret scanning incidents

GitGuardian scans every pull request. No real credential has been pushed, but both alerts are recorded here, as the brief asks for any leak.

- **2026-11-13, PR #9: a local test password.** The password of the SQL Server container on my laptop was written as a default in `tests/conftest.py` and in this README. It only ever protected a container listening on `127.0.0.1`. The same day it was removed from both files (commit 7318560), the tests started reading it from the environment or a git-ignored `.env`, and the container was recreated with a new random password, so the old value works nowhere. History was not rewritten, because pushed commits keep their timestamps. GitGuardian's incident was resolved.
- **2026-11-18, PR #12: a false positive.** `tests/test_db.py` contained the example connection string `mssql://inbox:p%40ss%2Fw0rd@db.example.net:1444/...`, which checks that a percent-encoded password is decoded. It was never a password anywhere, and `example.net` is reserved for examples. On 2026-11-19 the test was changed to build its example from parts so scanners stop matching it, and the incident was marked as a false positive.

Real passwords live only in git-ignored `.env` files on the laptop and in GitHub Actions secrets.

## Azure

Live at **https://crintzieu2024ui.z1.web.core.windows.net**. The UI is a Storage static website, both services are Container Apps that scale to zero, and each has a serverless Azure SQL database on the free offer (ADR-8). After the first request following a quiet period, the app may show "starting up" for up to a minute while the databases resume.

### Deploying

`.github/workflows/cd.yml` runs on every push to `main` and nothing else changes Azure. It runs CI first, pushes both images to GHCR tagged with the commit SHA, signs in as the managed identity `crintz-ieu2024-github` through OpenID Connect (no Azure password is stored), points each Container App at its new image with its secrets, builds the UI with `scripts/build_ui.py` (which writes `config.js` and the Content-Security-Policy with the services' addresses) and uploads it to the `$web` container, then fails the run unless both `/health` answer 200 with the new commit within eight minutes (one deadline for both, since 2026-11-20). The guide expects the first deploy to fail because new GHCR packages are private. Here it passed on the first run (2026-11-18, both services migrated with `001_init.sql` on startup): the images are pushed with the workflow's `GITHUB_TOKEN` from a public repository, so GHCR links each package to the repository and makes it public too. Anyone can pull them without a password, which is what Container Apps needs.

GitHub holds these secrets (Settings > Secrets and variables > Actions): `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `INBOX_DB_PASSWORD`, `REMINDERS_DB_PASSWORD`, `SESSION_SECRET`, and `GEMINI_API_KEY`, and these variables: `PREFIX=crintz-ieu2024`, `RESOURCE_GROUP=rg-crintz-ieu2024`, `UI_STORAGE=crintzieu2024ui`. The workflow passes the passwords to Container Apps as secrets, never as plain variables.

### One-time setup

Done once on 2026-11-18 with the Azure CLI and `gh`, in Git Bash, signed in with `az login` on the Azure for Students subscription. No password is written here: they were generated into a git-ignored `.env.azure` (`SQL_ADMIN_PASSWORD`, `INBOX_DB_PASSWORD`, `REMINDERS_DB_PASSWORD`, `SESSION_SECRET`, each `secrets.token_hex(16)` plus `Ab1`, or `token_hex(32)` for the session secret) and read from there. In Git Bash, `MSYS_NO_PATHCONV=1` stops paths like `/subscriptions/...` from being rewritten as Windows paths.

```bash
set -a; . ./.env.azure; set +a
PREFIX=crintz-ieu2024; RG=rg-$PREFIX; LOCATION=germanywestcentral

# 1. Subscription and region: pick Azure for Students, then one allowed region.
az account set --subscription "Azure for Students"
az policy assignment list --query "[?displayName=='Allowed resource deployment regions'].parameters"
# austriaeast, denmarkeast, switzerlandnorth, germanywestcentral, polandcentral

# 2. Providers and resource group.
for p in Microsoft.App Microsoft.OperationalInsights Microsoft.Sql Microsoft.Storage Microsoft.ManagedIdentity; do
  az provider register --namespace $p --wait
done
az group create -n $RG -l $LOCATION

# 3. The UI: a Storage static website.
az storage account create -n crintzieu2024ui -g $RG -l $LOCATION --kind StorageV2 --sku Standard_LRS \
  --min-tls-version TLS1_2 --allow-blob-public-access false
az storage blob service-properties update --account-name crintzieu2024ui --auth-mode key \
  --static-website --index-document index.html --404-document index.html

# 4. SQL server and two serverless databases on the free offer, then one contained user each.
az sql server create -n $PREFIX-sql -g $RG -l $LOCATION --admin-user noteezeradmin \
  --admin-password "$SQL_ADMIN_PASSWORD" --minimal-tls-version 1.2
az sql server firewall-rule create -g $RG -s $PREFIX-sql -n AllowAzureServices \
  --start-ip-address 0.0.0.0 --end-ip-address 0.0.0.0
for svc in inbox reminders; do
  az sql db create -g $RG -s $PREFIX-sql -n $PREFIX-$svc-db --edition GeneralPurpose \
    --compute-model Serverless --family Gen5 --min-capacity 0.5 --capacity 1 \
    --backup-storage-redundancy Local --use-free-limit true --free-limit-exhaustion-behavior AutoPause
done
# Open the firewall to this computer only while creating the users. Use the
# address Azure reports in its error message if a proxy changes it.
az sql server firewall-rule create -g $RG -s $PREFIX-sql -n SetupFromLaptop \
  --start-ip-address <your IP> --end-ip-address <your IP>
for svc in inbox reminders; do
  pwvar="$(echo $svc | tr a-z A-Z)_DB_PASSWORD"
  # sqlcmd from the SQL Server container; it reads $(SVC) and $(USERPW) from the environment.
  MSYS_NO_PATHCONV=1 docker exec -e SQLCMDPASSWORD="$SQL_ADMIN_PASSWORD" -e USERPW="${!pwvar}" -e SVC=$svc \
    noteezer-sql /opt/mssql-tools18/bin/sqlcmd -S $PREFIX-sql.database.windows.net -d $PREFIX-$svc-db \
    -U noteezeradmin -N -b -Q "
      CREATE USER [\$(SVC)] WITH PASSWORD = '\$(USERPW)';
      ALTER ROLE db_ddladmin ADD MEMBER [\$(SVC)];
      ALTER ROLE db_datareader ADD MEMBER [\$(SVC)];
      ALTER ROLE db_datawriter ADD MEMBER [\$(SVC)];"
done
az sql server firewall-rule delete -g $RG -s $PREFIX-sql -n SetupFromLaptop

# 5. Container Apps: one environment (no Log Analytics workspace) and one app per service,
#    started with a placeholder image that the first deploy replaces.
az extension add --name containerapp --upgrade
az containerapp env create -n $PREFIX-env -g $RG -l $LOCATION --logs-destination none
for pair in inbox:8000 reminders:8001; do
  az containerapp create -n $PREFIX-${pair%%:*} -g $RG --environment $PREFIX-env \
    --image mcr.microsoft.com/k8se/quickstart:latest --ingress external --target-port ${pair##*:} \
    --min-replicas 0 --max-replicas 1 --cpu 0.25 --memory 0.5Gi
done

# 6. The pipeline's identity: Contributor on this resource group only, trusted for main only.
az identity create -n $PREFIX-github -g $RG -l $LOCATION
MSYS_NO_PATHCONV=1 az role assignment create --role Contributor --assignee-principal-type ServicePrincipal \
  --assignee-object-id "$(az identity show -n $PREFIX-github -g $RG --query principalId -o tsv)" \
  --scope "$(az group show -n $RG --query id -o tsv)"
owner_id=$(gh api repos/christophrrrrr/Noteezer --jq .owner.id); repo_id=$(gh api repos/christophrrrrr/Noteezer --jq .id)
for subject in "repo:christophrrrrr/Noteezer:ref:refs/heads/main" \
               "repo:christophrrrrr@$owner_id/Noteezer@$repo_id:ref:refs/heads/main"; do
  az identity federated-credential create --identity-name $PREFIX-github -g $RG \
    -n "github-main$([[ $subject == *@* ]] && echo -ids)" --subject "$subject" \
    --issuer https://token.actions.githubusercontent.com --audiences api://AzureADTokenExchange
done

# 7. GitHub secrets and variables, piped in so no value is printed.
az identity show -n $PREFIX-github -g $RG --query clientId -o tsv | gh secret set AZURE_CLIENT_ID
az account show --query tenantId -o tsv | gh secret set AZURE_TENANT_ID
az account show --query id -o tsv | gh secret set AZURE_SUBSCRIPTION_ID
printf '%s' "$INBOX_DB_PASSWORD" | gh secret set INBOX_DB_PASSWORD
printf '%s' "$REMINDERS_DB_PASSWORD" | gh secret set REMINDERS_DB_PASSWORD
printf '%s' "$SESSION_SECRET" | gh secret set SESSION_SECRET
gh secret set GEMINI_API_KEY   # pasted when asked
gh variable set PREFIX --body $PREFIX
gh variable set RESOURCE_GROUP --body $RG
gh variable set UI_STORAGE --body crintzieu2024ui

# 9. Read access for the professors.
for who in amartini@faculty.ie.edu bserra@faculty.ie.edu; do
  MSYS_NO_PATHCONV=1 az role assignment create --assignee $who --role Reader \
    --scope "$(az group show -n $RG --query id -o tsv)"
done
```

Step 8 is the CD workflow above. Since then only the pipeline changes anything. To read a service's logs: `az containerapp logs show -n crintz-ieu2024-inbox -g rg-crintz-ieu2024 --follow` (add `--type system` for start-up and probe events).

## System description

`system.yaml` describes the whole system: both services, their variables, what inbox calls on reminders and what happens when that fails, and the Azure names. `system.schema.json` is its schema. Each service's `openapi.yaml` is generated from the code, never edited by hand. After changing a route, regenerate it and commit it with the change:

```
cd services\inbox
python ..\..\scripts\openapi.py

cd services\reminders
python ..\..\scripts\openapi.py
```

From the repository root, `python scripts/check_system.py` runs the same check as CI.
