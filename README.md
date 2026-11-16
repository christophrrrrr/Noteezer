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
- A static UI in `ui/`: plain HTML, CSS, and JavaScript with no build step, calling both services with a bearer token (ADR-8).
- Due reminders appear at the top when you open the app. Snooze and done are supported.
- A dark page in the style of Notion. Folders are in a sidebar, which becomes a drawer on a phone. Notes run from oldest to newest like a chat, with the newest just above the capture bar and due reminders pinned on top.
- Minimal accounts: register, log in, and log out with email and password. Not a third feature domain.

## Left for later

**Running without the laptop is the biggest gap, and the Azure deployment is what closes it.** Until it is live, Noteezer runs only where `docker compose up` runs. Both services are configured only through the environment variables below, so the move to Azure needs no code changes.

Push notifications to the phone are not built (ADR-5). The in-process background task could send them, but a reminder would only arrive while the computer running Noteezer is on, so they also wait for the always-on deployment. Until then, due reminders are pinned at the top when the app is opened. Also out of scope: chat import, offline mode, sharing items with other people, and video files (a video link is understood from its title).

## Run

Noteezer is a static UI and two services (ADR-6, ADR-7, ADR-8). **inbox** (`services/inbox`, port 8000) is a JSON API under `/api` for accounts, lines, folders, sorting, images, and link previews. **reminders** (`services/reminders`, port 8001) stores reminder times and decides when one is due. Each has its own database. The **UI** (`ui/`, port 8080) is plain files that call both: inbox for everything, and reminders directly for done and snooze. Inbox calls reminders over HTTP with a 2 second timeout, and keeps working without times when reminders is down.

### Everything at once, with Docker

Docker Desktop has to be running. Copy `.env.example` to `.env` (git ignores it) and fill in `DB_PASSWORD` (8+ characters with upper case, lower case, digits, and a symbol; SQL Server refuses weaker ones) and `SESSION_SECRET` (`python -c "import secrets; print(secrets.token_hex(32))"` makes one). Then:

```
docker compose up --build
```

Open `http://localhost:8080`. Compose starts SQL Server, waits until it answers, runs `db/init.sql` once to create the two databases, starts reminders and inbox (each applies its own migrations), and serves `ui/` with nginx. nginx writes `config.js` from `INBOX_URL` and `REMINDERS_URL` and sends a Content-Security-Policy that only lets the page call those two addresses. Only ports 8080, 8000, and 8001 are published, all on `127.0.0.1`; the database is reachable only from the other containers. `docker compose down` stops everything and keeps the data; `docker compose down -v` deletes it too.

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
$env:DB_PASSWORD = "<your password>"
docker run -d --name noteezer-sql -e ACCEPT_EULA=Y -e MSSQL_SA_PASSWORD=$env:DB_PASSWORD -p 127.0.0.1:1433:1433 mcr.microsoft.com/mssql/server:2022-latest
docker exec noteezer-sql /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P $env:DB_PASSWORD -Q "CREATE DATABASE noteezer_inbox; ALTER DATABASE noteezer_inbox SET READ_COMMITTED_SNAPSHOT ON; CREATE DATABASE noteezer_reminders; ALTER DATABASE noteezer_reminders SET READ_COMMITTED_SNAPSHOT ON"
```

The container only listens on this computer (`127.0.0.1`). Each service reads a git-ignored `.env` file in its folder. Both need the same `SESSION_SECRET`, a long random value (`python -c "import secrets; print(secrets.token_hex(32))"` makes one):

```
# services/inbox/.env
DB_PASSWORD=<your password>
DB_NAME=noteezer_inbox
SESSION_SECRET=<the same long random value>

# services/reminders/.env
DB_PASSWORD=<your password>
DB_NAME=noteezer_reminders
SESSION_SECRET=<the same long random value>
```

Add `CORS_ORIGINS=http://localhost:8080` to both files so the UI may call them. Then start each service in its own terminal, and serve the UI from a third (its `config.js` already points at ports 8000 and 8001):

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

Each process listens on `0.0.0.0`. On start it applies the numbered SQL files in its `migrations` folder that its database has not seen yet, each once and in its own transaction, and records them in `schema_migrations`. `python -m app.migrate` does the same by hand. A file that was applied is never edited; a change is a new, higher number. Both services log one line per request with a request id; inbox passes the id on to reminders in `X-Request-ID`, so one id finds a request in both logs. `GET /health` on either answers `{"ok": true, "database": "up"}`, or 503 when its database cannot be reached. Everything else is configured with environment variables:

| Variable | Default | What it does |
|---|---|---|
| `PORT` | `8000` inbox, `8001` reminders | Port to listen on. |
| `DB_SERVER` | `localhost` | SQL Server host. |
| `DB_PORT` | `1433` | SQL Server port. |
| `DB_USER` | `sa` | SQL login. |
| `DB_PASSWORD` | empty | Password for that login. Required. |
| `DB_NAME` | none | Required. The service's own database. It must exist; the migrations create the tables in it. |
| `SESSION_SECRET` | none | Required, the same in both services. Signs and checks the login token (ADR-7). |
| `CORS_ORIGINS` | empty | Both. Comma-separated sites allowed to call the service from a browser: the UI's address, `http://localhost:8080` locally. Empty allows none. |
| `REMINDERS_URL` | `http://127.0.0.1:8001` | Inbox only. Where the reminders service is. |
| `REMINDERS_TIMEOUT` | `2` | Inbox only. Seconds to wait for reminders before carrying on without times. |
| `GEMINI_API_KEY` | not set | Inbox only. Turns on Gemini sorting. Without it the rules sort and the app works the same otherwise. |
| `SORTER` | `auto` | `auto` uses Gemini when a key is set and the rules otherwise, `rules` never calls Gemini, `gemini` asks for it. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model for text lines. |
| `GEMINI_IMAGE_MODEL` | `gemini-3.8-flash` | Model for lines with an image. |

Variables already set in the environment win over the `.env` file. `GEMINI_API_KEY=...` can go in `services/inbox/.env` too. A free key comes from Google AI Studio. On the free tier Google may use the notes it sorts to improve its models.

The architecture and database diagrams are in [docs/diagrams.md](docs/diagrams.md).

Open `http://localhost:8080`, create an account, and save a line. A line that starts with `todo` or `buy` becomes a task, a URL becomes a link, and `remind` or the Reminder choice becomes a reminder that needs the date picker. Check Every day to repeat it, and set the date field only if it should stop. A reminder time that is already past is marked due and listed first. `#words` become tags.

## Use it on your phone

Noteezer installs as an app on Android (Chrome) and iPhone (Safari, Add to Home Screen). Installing and sharing need HTTPS. In Assignment 1 that came from `tailscale serve` on the laptop; with a separate UI and two services that is three addresses, so the phone uses the Azure address once Noteezer is deployed there. Open it, log in, and in Chrome choose Install app (or Add to Home screen). On Android, Noteezer then appears in the share sheet: sharing a YouTube video, a page, or a screenshot saves it to Unsorted, where it is sorted like any other line.

The app is a manifest (`ui/manifest.webmanifest`) and a service worker (`ui/sw.js`). The worker caches only the page, styles, script, and icons, and shows an offline page when nothing can be reached. It never touches calls to the services, so notes and images are never cached on the phone. The share sheet posts to `/share` on the UI's address; the worker keeps that post and opens the app, which sends it to inbox with the token. The token lasts 90 days, so the installed app stays signed in. Logging out makes the browser forget it (ADR-8).

## Tests

Each service has its own tests. They never call Gemini or fetch real pages; they use fakes. They do run against a real SQL Server, the container from the section above: each run creates fresh test databases, migrates them, empties them before every test, and drops them at the end. `DB_SERVER`, `DB_PORT`, `DB_USER`, and `DB_PASSWORD` point at the server, read from the environment or the service's `.env`; without `DB_PASSWORD` the run stops with a message. The tests set their own `SESSION_SECRET`.

The inbox tests start the real reminders service as a second process, on a free port with its own test database, so every reminder an inbox test saves crosses the seam over real HTTP. `tests/test_seam.py` also points inbox at a reminders that is down, slow, or answering with errors:

```
cd services\inbox
pytest --cov=app.api --cov=app.classify --cov=app.inbox --cov=app.users --cov=app.passwords --cov=app.folders --cov=app.sorting --cov=app.links --cov=app.gemini --cov=app.images --cov=app.db --cov=app.migrate --cov=app.tokens --cov=app.reminders_client --cov=app.middleware --cov-report=term-missing

cd services\reminders
pytest --cov=app --cov-report=term-missing
```

Inbox is 128 tests at 97% of those modules. `tests/test_api.py` drives the JSON API the UI uses, including that one person never reaches another's lines, and `tests/test_ui_files.py` checks the UI's files without a browser: the manifest and icons, that every file the page loads exists, that the page has no inline script, and that note text never becomes HTML. Reminders is 23 tests at 88% of the whole service, with the rules (`reminders.py`) and tokens at 100%.
