# Noteezer

Noteezer is a phone-friendly capture app for the notes, tasks, links, and reminders that would otherwise be messages sent to yourself.

You type one line and save it. Optional buttons can force the kind. A reminder takes its time from a picker or, with Gemini, from the text itself. The app stores everything in SQLite and shows what is due when you open it.

The use case is approved. You can register, save a line, search it, edit it, delete it, and mark a task done. A reminder can be a single time or every day from a start time until an optional end date. Snooze hides it for one hour. Marking a daily reminder done moves it to the next day.

## Who it is for

One person who currently pastes notes into a chat with themselves and later searches that history. More than one person can register. Each account sees only its own items.

## Feature domains

**Inbox.** Saves the line, assigns a kind, keeps tags, and supports search, edit, delete, and marking a task done.

**Folders.** Nested folders like Notion pages (Projects / Game / Cannon Circle). Create, rename, move, and delete them. A line can be in several folders at once, for example Table Tennis and Cool videos; tick them in the line's menu. A line in no folder is in Unsorted. Deleting a folder never deletes lines: a line that was in no other folder goes back to Unsorted. Inbox only calls `require_folder(user_id, folder_id)` before it puts a line in a folder.

**Sorting.** Files new lines from Unsorted into a folder in the background. First the title of a line's link is fetched: YouTube through oEmbed, other pages from their `<head>`. Links that point into a private network are refused. The rules come next: a line that names a folder or has its name as a `#tag`, including in a link title, goes there, and the deepest matching folder wins. If the rules are not sure and `GEMINI_API_KEY` is set, Gemini picks a folder with a confidence, or proposes a new folder. A `#tag` or folder name the rules find still decides the folder, because that is the user's own choice, but Gemini is asked for every line so it can also read times and kinds. Calls are spaced 4 seconds apart to stay inside the free tier. A confident answer is filed, into every folder the engine is sure about (a line with `#tabletennis #coolvideos` goes into both), and marked "filed by rules" or "filed by gemini". An unclear line stays in Unsorted with a suggestion (Accept) or a proposed folder (Create folder). Failed calls are retried with a growing wait. Gemini also turns a plain note into a task or an idea; links, reminders, and lines with a `todo`, `buy`, or `idea` prefix keep their kind. A note that holds several separate points, like a list of bugs under a heading, is split into one item per point, and each part is sorted on its own. The original is hidden, not deleted, and Undo split on any part brings it back. Creating or renaming a folder retries the lines that matched nothing, and Sort again retries everything in Unsorted. A new folder is also checked against lines that are already filed: one call looks through the 60 most recent ones and puts an "Also add to Cool videos" button on those that fit. Nothing is moved without a tap.

**Images.** A line can carry one screenshot or photo: pick it with the image field or paste it into the text box. Only PNG, JPEG, WebP, and GIF up to 10 MB are accepted, judged from the file's bytes. Images are stored in SQLite and served only to their owner. With "Let AI read this image" ticked (the default), Gemini reads the image while sorting. A line saved with only an image starts as "Screenshot" and takes Gemini's title as its text, and the text Gemini read is shown below the image and found by search. Unticked, the image never leaves the server.

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

Without Gemini, times come only from the picker. With Gemini, a line that starts with `remind` can be saved without the picker: "remind me friday 3pm call mom" is scheduled once the sorter has read it, and until then it shows "no time yet". Other lines with a time, like "dentist tuesday 10:00", get a "Remind me Tue 6 Oct, 10:00" button instead of a reminder. Every form sends the browser's timezone, which is stored on the account, so the picker and phrases like "friday" are read in the user's time. The server's own clock is UTC once deployed.

## In this assignment

- One process, Python, FastAPI, server-rendered pages, SQLite.
- Due reminders appear at the top when you open the app. Snooze and done are supported.
- A dark page in the style of Notion. Folders are in a sidebar, which becomes a drawer on a phone. Notes run from oldest to newest like a chat, with the newest just above the capture bar and due reminders pinned on top. Styles and a small script are in `noteezer/static`. Every button is a normal form, so the app also works without the script.
- Minimal accounts: register, log in, and log out with email and password. Not a third feature domain.

## Left for later

**Running without the laptop is the biggest gap.** Noteezer works only while `python app.py` runs on the computer that hosts it, and the phone reaches it through Tailscale. When that computer is off or asleep, the phone app shows its offline page, nothing can be saved, and nothing is sorted. This assignment does not allow a real public deployment, so always-on hosting comes with Assignment 2, when the app is put in a container and deployed to Azure. Because the app is configured only through `PORT`, `DATA_DIR`, and the other variables above, that move needs no code changes.

Push notifications to the phone are not built (ADR-5). The in-process background task could send them, but a reminder would only arrive while the computer running Noteezer is on, so they also wait for the always-on deployment. Until then, due reminders are pinned at the top when the app is opened. Also out of scope: chat import, offline mode, sharing items with other people, and video files (a video link is understood from its title).

## Run

From the repository root:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

The process listens on `0.0.0.0` and needs no setup step: the database and its tables are created on the first start. Everything is configured with environment variables, and none is required:

| Variable | Default | What it does |
|---|---|---|
| `PORT` | `8000` | Port to listen on. |
| `DATA_DIR` | `data` | Folder for the SQLite file, which is `$DATA_DIR/noteezer.db`. The folder is created if missing. |
| `SESSION_SECRET` | `dev-only-change-me` | Signs the login cookie. Set it to a long random value anywhere but your own laptop. |
| `GEMINI_API_KEY` | not set | Turns on Gemini sorting. Without it the rules sort and the app works the same otherwise. |
| `SORTER` | `auto` | `auto` uses Gemini when a key is set and the rules otherwise, `rules` never calls Gemini, `gemini` asks for it. |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Model for text lines. |
| `GEMINI_IMAGE_MODEL` | `gemini-3.8-flash` | Model for lines with an image. |

`python app.py` also reads an optional `.env` file next to it, for example with `GEMINI_API_KEY=...`, and variables already set in the environment win. A free key comes from Google AI Studio. On the free tier Google may use the notes it sorts to improve its models.

The architecture and database diagrams are in [docs/diagrams.md](docs/diagrams.md).

Open `http://127.0.0.1:8000/register`, create an account, and save a line. A line that starts with `todo` or `buy` becomes a task, a URL becomes a link, and `remind` or the Reminder choice becomes a reminder that needs the date picker. Check Every day to repeat it, and set the date field only if it should stop. A reminder time that is already past is marked due and listed first. `#words` become tags.

## Use it on your phone

Noteezer installs as an app on Android (Chrome) and iPhone (Safari, Add to Home Screen). Installing and sharing need HTTPS, and the easiest private way to get it is [Tailscale](https://tailscale.com), which is free for personal use:

1. Install Tailscale on the computer running Noteezer and on the phone, and sign in to both with the same account. In the Tailscale admin console, under DNS, turn on MagicDNS and HTTPS Certificates.
2. Start Noteezer with `python app.py`, then in another terminal run:

   ```
   tailscale serve --bg 8000
   ```

   It prints an address like `https://your-computer.your-tailnet.ts.net`. Only your own devices can open it. `tailscale serve reset` stops it.
3. Open that address on the phone and log in. In Chrome, open the menu and choose Install app (or Add to Home screen).
4. On Android, Noteezer now appears in the share sheet. Sharing a YouTube video, a page, or a screenshot saves it to Unsorted, where it is sorted like any other line.

The app is a manifest (`noteezer/static/manifest.webmanifest`) and a service worker (`noteezer/static/sw.js`). The worker caches only the styles, script, and icons, and shows an offline page when the server cannot be reached. Notes and images are never cached on the phone. The login cookie lasts 90 days, so the installed app stays signed in.

Core logic tests, with coverage for the inbox, folder, sorting, link preview, Gemini, image, reminder, account, and kind rules modules. The tests never call Gemini or fetch real pages; they use fakes:

```
pytest --cov=noteezer.classify --cov=noteezer.inbox --cov=noteezer.reminders --cov=noteezer.users --cov=noteezer.passwords --cov=noteezer.folders --cov=noteezer.sorting --cov=noteezer.links --cov=noteezer.gemini --cov=noteezer.images --cov-report=term-missing
```

That run is 103 tests and 96% coverage on those modules.
