# Diagrams

Both diagrams are drawn from the code as it is on 2026-10-04. GitHub renders the Mermaid blocks.

## Architecture

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

## Database

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
