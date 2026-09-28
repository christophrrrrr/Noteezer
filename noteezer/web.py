from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from noteezer.classify import ClassificationError
from noteezer.db import connect
from noteezer.inbox import delete_item, list_items, save_item, set_task_done, update_item
from noteezer.reminders import complete, snooze
from noteezer.sessions import COOKIE_NAME, read_user_id, sign_user_id
from noteezer.users import AccountError, authenticate, register_user

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))


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
    def register(request: Request, email: str = Form(""), password: str = Form("")):
        connection = connect()
        try:
            user_id = register_user(connection, email, password, _now())
        except AccountError as error:
            return _account_page(request, "Create account", "/login", "Log in instead", email, str(error))
        finally:
            connection.close()
        return _logged_in(user_id)

    @app.get("/login")
    def login_form(request: Request):
        return _account_page(request, "Log in", "/register", "Create an account", "", None)

    @app.post("/login")
    def login(request: Request, email: str = Form(""), password: str = Form("")):
        connection = connect()
        try:
            user_id = authenticate(connection, email, password)
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
    def inbox_form(request: Request, q: str = ""):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        return _render_inbox(request, user_id, q, "", "", "", False, "", None)

    @app.post("/inbox")
    def inbox_save(
        request: Request,
        body: str = Form(""),
        kind: str = Form(""),
        remind_at: str = Form(""),
        repeat_daily: str = Form(""),
        repeat_until: str = Form(""),
        q: str = Form(""),
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        daily = repeat_daily == "1"
        connection = connect()
        try:
            save_item(
                connection,
                user_id,
                body,
                _parse_remind_at(remind_at),
                _now(),
                forced_kind=kind or None,
                repeat_daily=daily,
                repeat_until=_parse_date(repeat_until),
            )
        except (ClassificationError, ValueError) as error:
            return _render_inbox(
                request, user_id, q, body, kind, remind_at, daily, repeat_until, str(error), status_code=400
            )
        finally:
            connection.close()
        return _inbox_redirect(q)

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
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            update_item(
                connection,
                user_id,
                item_id,
                body,
                _parse_remind_at(remind_at),
                _now(),
                forced_kind=kind or None,
                repeat_daily=repeat_daily == "1",
                repeat_until=_parse_date(repeat_until),
            )
        except LookupError:
            return RedirectResponse("/inbox", status_code=303)
        except (ClassificationError, ValueError) as error:
            return _render_inbox(request, user_id, q, body, kind, remind_at, False, "", str(error), status_code=400)
        finally:
            connection.close()
        return _inbox_redirect(q)

    @app.post("/items/{item_id}/delete")
    def remove_item(request: Request, item_id: int, q: str = Form("")):
        user_id = _require_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            delete_item(connection, user_id, item_id)
        except LookupError:
            pass
        finally:
            connection.close()
        return _inbox_redirect(q)

    @app.post("/items/{item_id}/done")
    def mark_done(request: Request, item_id: int, q: str = Form("")):
        user_id = _require_user(request)
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
        return _inbox_redirect(q)

    @app.post("/items/{item_id}/snooze")
    def snooze_item(request: Request, item_id: int, q: str = Form("")):
        user_id = _require_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            snooze(connection, user_id, item_id, _now())
        except (LookupError, ValueError):
            pass
        finally:
            connection.close()
        return _inbox_redirect(q)


def _render_inbox(
    request: Request,
    user_id: int,
    query: str,
    body: str,
    kind: str,
    remind_at: str,
    repeat_daily: bool,
    repeat_until: str,
    error: str | None,
    status_code: int = 200,
):
    connection = connect()
    try:
        items = list_items(connection, user_id, query, _now())
    finally:
        connection.close()
    return TEMPLATES.TemplateResponse(
        request,
        "inbox.html",
        {
            "title": "Inbox",
            "items": items,
            "body": body,
            "kind": kind,
            "remind_at": remind_at,
            "repeat_daily": repeat_daily,
            "repeat_until": repeat_until,
            "query": query,
            "error": error,
        },
        status_code=status_code,
    )


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


def _inbox_redirect(query: str) -> RedirectResponse:
    target = "/inbox"
    if query.strip():
        target = "/inbox?q=" + quote(query.strip())
    return RedirectResponse(target, status_code=303)


def _current_user(request: Request) -> int | None:
    return read_user_id(request.cookies.get(COOKIE_NAME))


def _require_user(request: Request) -> int | None:
    return _current_user(request)


def _parse_remind_at(value: str) -> datetime | None:
    if not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _parse_date(value: str) -> date | None:
    if not value.strip():
        return None
    return date.fromisoformat(value.strip())


def _now() -> datetime:
    return datetime.now(timezone.utc)
