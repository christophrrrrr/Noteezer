# Diagrams

GitHub renders the Mermaid blocks. The first part is the system as deployed for Assignment 2, drawn from the code and the Azure setup on 2026-11-20. The second part is Assignment 1 as it was on 2026-10-04, kept unchanged as the "before".

## After: two services on Azure (Assignment 2)

### Deployment

```mermaid
flowchart LR
    user["Phone or laptop browser"]

    subgraph gh["GitHub"]
        main["main branch"]
        ci["ci.yml<br/>lint, tests on SQL Server,<br/>contracts, image build"]
        cd["cd.yml<br/>push images, deploy,<br/>/health gate"]
        ghcr["GHCR, public<br/>noteezer-inbox, noteezer-reminders<br/>tagged with the commit SHA"]
        main -- "push to main" --> cd
        cd -- "calls first" --> ci
        cd --> ghcr
    end

    subgraph az["Azure for Students: rg-crintz-ieu2024, Germany West Central"]
        web["Storage static website<br/>crintzieu2024ui, $web container<br/>index.html, app.js, config.js"]
        mi["Managed identity<br/>crintz-ieu2024-github<br/>Contributor on the group"]
        subgraph env["Container Apps environment crintz-ieu2024-env, Consumption"]
            inbox["crintz-ieu2024-inbox<br/>FastAPI on 8000, 0 to 1 replicas<br/>JSON API + sort worker"]
            rem["crintz-ieu2024-reminders<br/>FastAPI on 8001, 0 to 1 replicas"]
        end
        subgraph sql["Azure SQL server crintz-ieu2024-sql, serverless free offer"]
            idb[("crintz-ieu2024-inbox-db<br/>user: inbox")]
            rdb[("crintz-ieu2024-reminders-db<br/>user: reminders")]
        end
    end

    user -- "HTTPS: the page" --> web
    user -- "HTTPS + bearer token<br/>/api/*" --> inbox
    user -- "HTTPS + bearer token<br/>snooze, done" --> rem
    inbox -- "HTTPS + inbox token<br/>2 s timeout" --> rem
    inbox --> idb
    rem --> rdb
    inbox -- "HTTPS, only with GEMINI_API_KEY" --> gemini["Google Gemini API"]
    cd -- "OIDC, no stored password" --> mi
    mi -- "new image, secrets, env" --> env
    mi -- "upload the UI" --> web
    env -- "pull by SHA" --> ghcr
```

The browser talks to three addresses: the static website for the page, and each Container App for its API. Both services accept browser calls only from the website's origin (`CORS_ORIGINS`). Inbox is the only service that calls the other one; reminders never calls inbox. Each service can open only its own database, with its own contained user. Nothing runs while the app is idle: both apps scale to zero and both databases pause after 60 minutes (ADR-8), which is also why reminders are not pushed to the phone (ADR-10).

### Saving a reminder, and what happens when reminders is down

```mermaid
sequenceDiagram
    participant B as Browser (static UI)
    participant I as inbox
    participant IDB as inbox database
    participant R as reminders
    participant RDB as reminders database

    B->>I: POST /api/items "call mum at 6pm" (user token)
    I->>IDB: INSERT the line, COMMIT
    I->>R: PUT /reminders/{id} (inbox token, X-Request-ID, 2 s timeout)
    alt reminders answers
        R->>RDB: INSERT the reminder with a copy of user_id
        R-->>I: 200 with the time
        I-->>B: the line with its time
    else no answer, too slow, 5xx, or token refused
        R--xI: RemindersUnavailable
        I->>IDB: needs_time = 1
        I-->>B: the line, saved, with "no time yet"
    end
    B->>I: GET /api/items
    I->>R: GET /reminders?item_id=... (one call per page)
    opt reminders is down
        I-->>B: the lines with reminder_unavailable, and the banner "Reminders are unavailable right now"
    end
```

The line is committed before reminders is called, so a reminders failure never loses it (ADR-6). Snooze and done go from the browser straight to reminders, so while it is down they fail with a message, and everything in inbox keeps working.

### Databases

Each service owns its database and runs its own numbered migrations at startup (ADR-7), recorded in its own `schema_migrations` table. There is no foreign key across the seam: `reminders.item_id` points at `items.id` in the other database, and `reminders.user_id` is a copy, so reminders checks ownership without asking inbox.

```mermaid
erDiagram
    users ||--o{ items : owns
    users ||--o{ folders : owns
    users ||--o{ tags : owns
    folders |o--o{ folders : parent_id
    items |o--o{ items : split_from
    items ||--o{ item_folders : "in (CASCADE)"
    folders ||--o{ item_folders : "holds (CASCADE)"
    items ||--o{ item_tags : "tagged (CASCADE)"
    tags ||--o{ item_tags : "used by (CASCADE)"
    items ||--o| sort_results : "sorted as (CASCADE)"
    folders |o--o{ sort_results : "folder_id (SET NULL), proposed_parent_id"
    items ||--o{ folder_suggestions : "also add (CASCADE)"
    folders ||--o{ folder_suggestions : "suggested (CASCADE)"
    folders ||--o| folder_checks : "check queued (CASCADE)"
    items ||--o| images : "has (CASCADE)"
    items ||--o| link_previews : "has (CASCADE)"
    items ||..o| reminders : "item_id across the seam, no FK"

    users {
        INT id PK
        NVARCHAR email UK
        NVARCHAR password_hash
        NVARCHAR created_at
        NVARCHAR timezone
    }
    items {
        INT id PK
        INT user_id FK
        NVARCHAR body
        NVARCHAR kind "note|idea|task|link|reminder"
        INT task_done "only tasks"
        NVARCHAR created_at
        NVARCHAR updated_at
        INT split_from FK
        INT hidden
        INT needs_time "reminder still waits for a time"
    }
    item_folders {
        INT item_id PK,FK
        INT folder_id PK,FK
    }
    tags {
        INT id PK
        INT user_id FK
        NVARCHAR name "unique per user"
    }
    item_tags {
        INT item_id PK,FK
        INT tag_id PK,FK
    }
    folders {
        INT id PK
        INT user_id FK
        INT parent_id FK
        NVARCHAR name
        NVARCHAR created_at
        INT parent_key "computed, for unique names"
    }
    sort_results {
        INT item_id PK,FK
        NVARCHAR status "filed|review|accepted|retry|failed"
        INT folder_id FK
        FLOAT confidence
        NVARCHAR engine
        INT attempts
        NVARCHAR retry_at
        NVARCHAR error
        NVARCHAR sorted_at
        NVARCHAR proposed_name
        INT proposed_parent_id FK
        NVARCHAR suggested_remind_at
    }
    folder_suggestions {
        INT item_id PK,FK
        INT folder_id PK,FK
    }
    folder_checks {
        INT folder_id PK,FK
        NVARCHAR requested_at
        NVARCHAR checked_at
        INT attempts
    }
    images {
        INT item_id PK,FK
        NVARCHAR mime
        VARBINARY data
        INT ai_allowed
        NVARCHAR title
        NVARCHAR text
        NVARCHAR read_at
    }
    link_previews {
        INT item_id PK,FK
        NVARCHAR url
        NVARCHAR title
        NVARCHAR description
        NVARCHAR site
        NVARCHAR fetched_at
    }
    reminders {
        INT item_id PK "items.id in the inbox database"
        INT user_id "copy of items.user_id"
        NVARCHAR remind_at
        NVARCHAR status "scheduled|due|snoozed|done"
        NVARCHAR snoozed_until
        INT repeat_daily
        NVARCHAR repeat_until
        INT snoozed_count "added by migration 002"
    }
```

| Database | Owner | Tables | Migrations |
|---|---|---|---|
| `crintz-ieu2024-inbox-db` | inbox service, user `inbox` | `users`, `items`, `item_folders`, `tags`, `item_tags`, `folders`, `sort_results`, `folder_suggestions`, `folder_checks`, `images`, `link_previews` | `001_init.sql` |
| `crintz-ieu2024-reminders-db` | reminders service, user `reminders` | `reminders` | `001_init.sql`, `002_snoozed_count.sql` |

The dotted line is the one relation that used to be a foreign key with `ON DELETE CASCADE` (ADR-3). Now inbox calls `DELETE /reminders/{id}` after it deletes a line. If reminders is down, the reminder row stays behind; that is harmless, because inbox only shows times for lines that exist and are reminders, and an item id is never reused. Timestamps are ISO 8601 text with a binary collation, so they compare character by character, as in SQLite.

## Before: one process on the laptop (Assignment 1)

Drawn from the code as it was on 2026-10-04.

### Architecture (Assignment 1)

One process: `python app.py` starts Uvicorn with the FastAPI app from `noteezer/main.py`. The sort worker is an asyncio task inside that process, started in `lifespan`, not a second process.

```mermaid
flowchart LR
    phone["Phone (installed PWA)<br/>or laptop browser"]
    ts["Tailscale serve<br/>(HTTPS, own devices only)"]
    phone -- HTTPS --> ts

    subgraph proc["One process: python app.py (Uvicorn, 0.0.0.0:$PORT)"]
        direction TB
        web["web.py<br/>routes, Jinja templates,<br/>/share, /sw.js"]
        sessions["sessions.py<br/>signed cookie"]

        subgraph accounts["Accounts"]
            users["users.py<br/>passwords.py"]
        end
        subgraph inboxd["Inbox domain"]
            inbox["inbox.py<br/>classify.py"]
        end
        subgraph remd["Reminders domain"]
            reminders["reminders.py"]
        end
        subgraph folderd["Folders"]
            folders["folders.py"]
        end
        subgraph sortd["Sorting"]
            worker["worker.py<br/>SortWorker (asyncio task)"]
            sorting["sorting.py<br/>RulesSorter"]
            gemini["gemini.py<br/>GeminiSorter"]
            links["links.py<br/>link previews"]
            images["images.py<br/>screenshots"]
        end

        web --> sessions
        web --> users
        web --> inbox
        web --> folders
        web --> reminders
        web --> images
        web -- "accept / dismiss suggestions" --> sorting
        web -- "wake()" --> worker
        inbox -- "schedule(item_id, when)" --> reminders
        inbox -- "require_folder(user_id, folder_id)" --> folders
        worker --> sorting
        worker --> links
        sorting --> gemini
        sorting -- "file_unsorted_item, add_to_folder,<br/>split_item, change_plain_note_kind" --> inbox
        sorting --> folders
        sorting --> images
    end

    ts --> web
    db[("SQLite<br/>$DATA_DIR/noteezer.db<br/>(default data/noteezer.db)")]
    proc --> db
    gemini -- "HTTPS, only if GEMINI_API_KEY" --> gapi["Google Gemini API"]
    links -- "HTTPS, public addresses only" --> pages["Web pages,<br/>YouTube oEmbed"]
```

The two graded domains are **Inbox** (what was saved) and **Reminders** (when it is due). Folders and Sorting are the extra areas added later (ADR-2 updates). Accounts are minimal login, not a domain.

Where the seams are today:

- **Writes across a seam go through a function.** Inbox only calls `schedule` in reminders and `require_folder` in folders. Sorting changes a line only through guarded inbox functions (`file_unsorted_item`, `add_to_folder`, `split_item`, `change_plain_note_kind`, `schedule_unset_reminder`). One exception: `record_reading` in `images.py` replaces the body "Screenshot" with Gemini's title directly in `items`.
- **Reads across a seam still use SQL joins.** `sort_pending` and `check_new_folders` in `sorting.py` read `items`, `item_folders`, `reminders`, and `images` to find work, and `list_items` in `inbox.py` searches the text in `images`. `reminders.py`, `images.py`, and `links.py` join `items` to check that a row belongs to the user. In Assignment 2 these reads become API calls or copies of the `user_id` on the owning table.

### Database (Assignment 1)

Twelve tables in one SQLite file, created by `noteezer/schema.sql` in `init_db`. Each table is grouped by the module that writes it.

```mermaid
erDiagram
    users ||--o{ items : owns
    users ||--o{ folders : owns
    users ||--o{ tags : owns
    folders |o--o{ folders : "parent_id (CASCADE)"
    items |o--o{ items : "split_from (SET NULL)"
    items ||--o{ item_folders : "in (CASCADE)"
    folders ||--o{ item_folders : "holds (CASCADE)"
    items ||--o{ item_tags : "tagged (CASCADE)"
    tags ||--o{ item_tags : "used by (CASCADE)"
    items ||--o| reminders : "scheduled by (CASCADE)"
    items ||--o| sort_results : "sorted as (CASCADE)"
    folders |o--o{ sort_results : "folder_id, proposed_parent_id (SET NULL)"
    items ||--o{ folder_suggestions : "also add (CASCADE)"
    folders ||--o{ folder_suggestions : "suggested (CASCADE)"
    folders ||--o| folder_checks : "check queued (CASCADE)"
    items ||--o| images : "has (CASCADE)"
    items ||--o| link_previews : "has (CASCADE)"

    users {
        INTEGER id PK
        TEXT email UK
        TEXT password_hash
        TEXT created_at
        TEXT timezone
    }
    items {
        INTEGER id PK
        INTEGER user_id FK
        TEXT body
        TEXT kind "note|idea|task|link|reminder"
        INTEGER task_done
        TEXT created_at
        TEXT updated_at
        INTEGER split_from FK
        INTEGER hidden
    }
    item_folders {
        INTEGER item_id PK,FK
        INTEGER folder_id PK,FK
    }
    tags {
        INTEGER id PK
        INTEGER user_id FK
        TEXT name "unique per user"
    }
    item_tags {
        INTEGER item_id PK,FK
        INTEGER tag_id PK,FK
    }
    folders {
        INTEGER id PK
        INTEGER user_id FK
        INTEGER parent_id FK
        TEXT name "unique among siblings, any case"
        TEXT created_at
    }
    reminders {
        INTEGER item_id PK,FK
        TEXT remind_at
        TEXT status "scheduled|due|snoozed|done"
        TEXT snoozed_until
        INTEGER repeat_daily
        TEXT repeat_until
    }
    sort_results {
        INTEGER item_id PK,FK
        TEXT status "filed|review|accepted|retry|failed"
        INTEGER folder_id FK
        REAL confidence
        TEXT engine
        INTEGER attempts
        TEXT retry_at
        TEXT error
        TEXT sorted_at
        TEXT proposed_name
        INTEGER proposed_parent_id FK
        TEXT suggested_remind_at
    }
    folder_suggestions {
        INTEGER item_id PK,FK
        INTEGER folder_id PK,FK
    }
    folder_checks {
        INTEGER folder_id PK,FK
        TEXT requested_at
        TEXT checked_at
        INTEGER attempts
    }
    images {
        INTEGER item_id PK,FK
        TEXT mime
        BLOB data
        INTEGER ai_allowed
        TEXT title
        TEXT text
        TEXT read_at
    }
    link_previews {
        INTEGER item_id PK,FK
        TEXT url
        TEXT title
        TEXT description
        TEXT site
        TEXT fetched_at
    }
```

| Module | Tables it writes |
|---|---|
| `users.py` | `users` |
| `inbox.py` | `items`, `item_folders`, `tags`, `item_tags` |
| `reminders.py` | `reminders` |
| `folders.py` | `folders` |
| `sorting.py` | `sort_results`, `folder_suggestions`, `folder_checks` |
| `images.py` | `images` (and `items.body` for a "Screenshot" line, see above) |
| `links.py` | `link_previews` |

`reminders`, `sort_results`, `images`, and `link_previews` use `item_id` as their primary key, so a line has at most one of each, and each is deleted with its line. Deleting a folder never deletes a line: only its `item_folders` rows go, and a line with none left is in Unsorted.
