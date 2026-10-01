from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from noteezer.classify import ClassificationError
from noteezer.db import connect
from noteezer.folders import (
    FolderError,
    create_folder,
    delete_folder,
    list_folders,
    move_folder,
    rename_folder,
)
from noteezer.inbox import (
    count_by_folder,
    delete_item,
    list_items,
    move_item,
    save_item,
    set_task_done,
    update_item,
)
from noteezer.reminders import complete, snooze
from noteezer.sessions import COOKIE_NAME, read_user_id, sign_user_id
from noteezer.images import MAX_BYTES, PLACEHOLDER, ImageError, attach_image, check_image, image_for, images_for
from noteezer.links import previews_for
from noteezer.sorting import (
    accept_proposal,
    accept_reminder,
    accept_suggestion,
    requeue_unsorted,
    resort_if_waiting,
    results_for,
    undo_auto_split,
)
from noteezer.users import AccountError, authenticate, register_user, set_timezone, user_timezone

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
UNSORTED = "unsorted"


def register_routes(app: FastAPI) -> None:
    @app.get("/")
    def root(request: Request) -> RedirectResponse:
        if _current_user(request) is None:
            return RedirectResponse("/login", status_code=303)
        return RedirectResponse("/inbox", status_code=303)

    @app.get("/register")
    def register_form(request: Request):
        return _account_page(request, "Create account", "/login", "Log in instead", "", None)

    @app.post("/register")
    def register(request: Request, email: str = Form(""), password: str = Form(""), tz: str = Form("")):
        connection = connect()
        try:
            user_id = register_user(connection, email, password, _now())
            set_timezone(connection, user_id, tz)
        except AccountError as error:
            return _account_page(request, "Create account", "/login", "Log in instead", email, str(error))
        finally:
            connection.close()
        return _logged_in(user_id)

    @app.get("/login")
    def login_form(request: Request):
        return _account_page(request, "Log in", "/register", "Create an account", "", None)

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form(""), tz: str = Form("")):
        connection = connect()
        try:
            user_id = authenticate(connection, email, password)
            if user_id is not None:
                set_timezone(connection, user_id, tz)
        finally:
            connection.close()
        if user_id is None:
            return _account_page(
                request, "Log in", "/register", "Create an account", email, "Email or password is wrong."
            )
        return _logged_in(user_id)

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE_NAME)
        return response

    @app.get("/inbox")
    def inbox_form(request: Request, q: str = "", folder: str = ""):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        return _render_inbox(request, user_id, q, folder)

    @app.post("/inbox")
    def inbox_save(
        request: Request,
        body: str = Form(""),
        kind: str = Form(""),
        remind_at: str = Form(""),
        repeat_daily: str = Form(""),
        repeat_until: str = Form(""),
        q: str = Form(""),
        folder: str = Form(""),
        tz: str = Form(""),
        image: UploadFile | None = File(None),
        ai_image: str = Form(""),
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        daily = repeat_daily == "1"
        picture = _uploaded(image)
        connection = connect()
        try:
            if picture is not None:
                check_image(picture)
                if not body.strip():
                    body = PLACEHOLDER
            zone = _zone(connection, user_id, tz)
            item_id = save_item(
                connection,
                user_id,
                body,
                _parse_remind_at(remind_at, zone),
                _now(),
                forced_kind=kind or None,
                repeat_daily=daily,
                repeat_until=_parse_date(repeat_until),
                folder_id=_folder_id(folder),
            )
            if picture is not None:
                attach_image(connection, item_id, picture, ai_allowed=ai_image == "1")
        except (ClassificationError, ValueError, LookupError, ImageError) as error:
            if body == PLACEHOLDER:
                body = ""
            draft = _draft(body, kind, remind_at, daily, repeat_until)
            return _render_inbox(request, user_id, q, folder, draft, _message(error), status_code=400)
        finally:
            connection.close()
        _wake_sorter(request)
        return _inbox_redirect(q, folder)

    @app.get("/items/{item_id}/image")
    def item_image(request: Request, item_id: int):
        user_id = _current_user(request)
        if user_id is None:
            return Response(status_code=404)
        connection = connect()
        try:
            picture = image_for(connection, user_id, item_id)
        finally:
            connection.close()
        if picture is None:
            return Response(status_code=404)
        # The type comes from check_image, never from the upload, and nosniff
        # stops the browser from guessing a different one.
        return Response(
            picture.data,
            media_type=picture.mime,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
        )

    @app.post("/items/{item_id}/edit")
    def edit_item(
        request: Request,
        item_id: int,
        body: str = Form(""),
        kind: str = Form(""),
        remind_at: str = Form(""),
        repeat_daily: str = Form(""),
        repeat_until: str = Form(""),
        q: str = Form(""),
        folder: str = Form(""),
        tz: str = Form(""),
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            zone = _zone(connection, user_id, tz)
            update_item(
                connection,
                user_id,
                item_id,
                body,
                _parse_remind_at(remind_at, zone),
                _now(),
                forced_kind=kind or None,
                repeat_daily=repeat_daily == "1",
                repeat_until=_parse_date(repeat_until),
            )
            resort_if_waiting(connection, user_id, item_id)
        except LookupError:
            return _inbox_redirect(q, folder)
        except (ClassificationError, ValueError) as error:
            return _render_inbox(request, user_id, q, folder, None, str(error), status_code=400)
        finally:
            connection.close()
        _wake_sorter(request)
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/move")
    def move_item_route(
        request: Request,
        item_id: int,
        target: str = Form(""),
        q: str = Form(""),
        folder: str = Form(""),
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            move_item(connection, user_id, item_id, _folder_id(target))
        except (LookupError, ValueError):
            pass
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/delete")
    def remove_item(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            delete_item(connection, user_id, item_id)
        except LookupError:
            pass
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/done")
    def mark_done(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            row = connection.execute(
                "SELECT kind, task_done FROM items WHERE id = ? AND user_id = ?",
                (item_id, user_id),
            ).fetchone()
            if row is None:
                pass
            elif row["kind"] == "task":
                set_task_done(connection, user_id, item_id, not bool(row["task_done"]))
            elif row["kind"] == "reminder":
                complete(connection, user_id, item_id, _now())
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/snooze")
    def snooze_item(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            snooze(connection, user_id, item_id, _now())
        except (LookupError, ValueError):
            pass
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/accept")
    def accept_item(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            accept_suggestion(connection, user_id, item_id)
        except LookupError:
            pass
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/create-folder")
    def create_proposed_folder(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            accept_proposal(connection, user_id, item_id, _now())
            requeue_unsorted(connection, user_id, only_unmatched=True)
        except (LookupError, FolderError):
            pass
        finally:
            connection.close()
        _wake_sorter(request)
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/remind")
    def set_suggested_reminder(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            accept_reminder(connection, user_id, item_id, _now())
        except (LookupError, ValueError, ClassificationError):
            pass
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/items/{item_id}/unsplit")
    def undo_split_route(request: Request, item_id: int, q: str = Form(""), folder: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            undo_auto_split(connection, user_id, item_id)
        except LookupError:
            pass
        finally:
            connection.close()
        return _inbox_redirect(q, folder)

    @app.post("/sorting/retry")
    def sort_again(request: Request):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            requeue_unsorted(connection, user_id)
        finally:
            connection.close()
        _wake_sorter(request)
        return _inbox_redirect("", UNSORTED)

    @app.post("/folders")
    def folder_create(
        request: Request,
        name: str = Form(""),
        parent: str = Form(""),
        folder: str = Form(""),
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            created = create_folder(connection, user_id, name, _now(), parent_id=_folder_id(parent))
            requeue_unsorted(connection, user_id, only_unmatched=True)
        except (FolderError, LookupError, ValueError) as error:
            return _render_inbox(request, user_id, "", folder, None, _message(error), status_code=400)
        finally:
            connection.close()
        _wake_sorter(request)
        return _inbox_redirect("", str(created))

    @app.post("/folders/{folder_id}/rename")
    def folder_rename(request: Request, folder_id: int, name: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            rename_folder(connection, user_id, folder_id, name)
            requeue_unsorted(connection, user_id, only_unmatched=True)
        except FolderError as error:
            return _render_inbox(request, user_id, "", str(folder_id), None, str(error), status_code=400)
        except LookupError:
            return _inbox_redirect("", "")
        finally:
            connection.close()
        _wake_sorter(request)
        return _inbox_redirect("", str(folder_id))

    @app.post("/folders/{folder_id}/move")
    def folder_move(request: Request, folder_id: int, parent: str = Form("")):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            move_folder(connection, user_id, folder_id, _folder_id(parent))
        except FolderError as error:
            return _render_inbox(request, user_id, "", str(folder_id), None, str(error), status_code=400)
        except (LookupError, ValueError):
            return _inbox_redirect("", "")
        finally:
            connection.close()
        return _inbox_redirect("", str(folder_id))

    @app.post("/folders/{folder_id}/delete")
    def folder_delete(request: Request, folder_id: int):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            delete_folder(connection, user_id, folder_id)
        except LookupError:
            pass
        finally:
            connection.close()
        return _inbox_redirect("", "")


def _render_inbox(
    request: Request,
    user_id: int,
    query: str,
    folder: str,
    draft: dict | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    unsorted = folder == UNSORTED
    try:
        folder_id = None if unsorted else _folder_id(folder)
    except ValueError:
        folder_id = None
    connection = connect()
    try:
        folders = list_folders(connection, user_id)
        counts = count_by_folder(connection, user_id)
        current = next((entry for entry in folders if entry["id"] == folder_id), None)
        if folder_id is not None and current is None:
            folder_id = None
        zone = user_timezone(connection, user_id)
        items = list_items(
            connection, user_id, query, _now(), folder_id=folder_id, unsorted=unsorted, zone=zone
        )
        results = results_for(connection, user_id)
        previews = previews_for(connection, user_id)
        pictures = images_for(connection, user_id)
    finally:
        connection.close()
    for entry in folders:
        entry["count"] = counts.get(entry["id"], 0)
    names = {entry["id"]: entry["path"] for entry in folders}
    for item in items:
        item["folder_path"] = names.get(item["folder_id"], "")
        item["preview"] = previews.get(item["id"])
        item["image"] = pictures.get(item["id"])
        item["sort"] = results.get(item["id"])
        if item["sort"] is not None:
            item["sort"]["folder_path"] = names.get(item["sort"]["folder_id"], "")
            item["sort"]["remind_label"] = _label(item["sort"]["suggested_remind_at"], zone)
            if item["sort"]["proposed_name"]:
                parent = names.get(item["sort"]["proposed_parent_id"])
                name = item["sort"]["proposed_name"]
                item["sort"]["proposed_path"] = f"{parent} / {name}" if parent else name
    if current is not None:
        view = current["path"]
        view_key = str(current["id"])
    elif unsorted:
        view = "Unsorted"
        view_key = UNSORTED
    else:
        view = "Everything"
        view_key = ""
    return TEMPLATES.TemplateResponse(
        request,
        "inbox.html",
        {
            "title": view,
            "items": items,
            "folders": folders,
            "current": current,
            "subfolders": [entry for entry in folders if current and entry["parent_id"] == current["id"]],
            "view": view,
            "folder": view_key,
            "unsorted_count": counts.get(None, 0),
            "has_folders": bool(folders),
            "total_count": sum(counts.values()),
            "draft": draft or _draft("", "", "", False, ""),
            "query": query,
            "error": error,
        },
        status_code=status_code,
    )


def _draft(body: str, kind: str, remind_at: str, repeat_daily: bool, repeat_until: str) -> dict:
    return {
        "body": body,
        "kind": kind,
        "remind_at": remind_at,
        "repeat_daily": repeat_daily,
        "repeat_until": repeat_until,
    }


def _account_page(request: Request, title: str, other_href: str, other_label: str, email: str, error: str | None):
    autocomplete = "new-password" if title == "Create account" else "current-password"
    return TEMPLATES.TemplateResponse(
        request,
        "login.html",
        {
            "title": title,
            "other_href": other_href,
            "other_label": other_label,
            "email": email,
            "error": error,
            "autocomplete": autocomplete,
        },
        status_code=400 if error else 200,
    )


def _logged_in(user_id: int) -> RedirectResponse:
    response = RedirectResponse("/inbox", status_code=303)
    response.set_cookie(COOKIE_NAME, sign_user_id(user_id), httponly=True, samesite="lax")
    return response


def _inbox_redirect(query: str, folder: str) -> RedirectResponse:
    parameters = {}
    if folder.strip():
        parameters["folder"] = folder.strip()
    if query.strip():
        parameters["q"] = query.strip()
    target = "/inbox"
    if parameters:
        target += "?" + urlencode(parameters)
    return RedirectResponse(target, status_code=303)


def _message(error: Exception) -> str:
    if isinstance(error, LookupError):
        return "That folder no longer exists."
    return str(error)


def _uploaded(upload: UploadFile | None) -> bytes | None:
    # An empty file field still arrives as an upload with no name and no bytes.
    if upload is None or not upload.filename:
        return None
    data = upload.file.read(MAX_BYTES + 1)
    return data or None


def _wake_sorter(request: Request) -> None:
    request.app.state.sort_worker.wake()


def _current_user(request: Request) -> int | None:
    return read_user_id(request.cookies.get(COOKIE_NAME))


def _folder_id(value: str) -> int | None:
    # "" and "unsorted" both mean no folder. Anything else must be a folder id.
    cleaned = value.strip()
    if not cleaned or cleaned == UNSORTED:
        return None
    return int(cleaned)


def _zone(connection, user_id: int, browser_zone: str) -> tzinfo:
    # The page sends the browser's timezone with every form, so the account
    # follows the user when they travel. A missing or unknown value keeps the
    # one already stored.
    set_timezone(connection, user_id, browser_zone)
    return user_timezone(connection, user_id)


def _parse_remind_at(value: str, zone: tzinfo) -> datetime | None:
    # The date picker sends local wall time without an offset. It is read in
    # the user's timezone, not the server's, which is UTC once deployed.
    if not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.astimezone(timezone.utc)


def _label(value: str | None, zone: tzinfo) -> str:
    if not value:
        return ""
    local = datetime.fromisoformat(value).astimezone(zone)
    return f"{local:%a} {local.day} {local:%b}, {local:%H:%M}"


def _parse_date(value: str) -> date | None:
    if not value.strip():
        return None
    return date.fromisoformat(value.strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)
