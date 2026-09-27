from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from noteezer.classify import ClassificationError
from noteezer.db import connect
from noteezer.inbox import list_items, save_item
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
    def inbox_form(request: Request):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            items = list_items(connection, user_id)
        finally:
            connection.close()
        return TEMPLATES.TemplateResponse(
            request,
            "inbox.html",
            _inbox_context(items, "", "", "", None),
        )

    @app.post("/inbox")
    def inbox_save(
        request: Request,
        body: str = Form(""),
        kind: str = Form(""),
        remind_at: str = Form(""),
    ):
        user_id = _current_user(request)
        if user_id is None:
            return RedirectResponse("/login", status_code=303)
        connection = connect()
        try:
            save_item(
                connection,
                user_id,
                body,
                _parse_remind_at(remind_at),
                _now(),
                forced_kind=kind or None,
            )
            items = list_items(connection, user_id)
        except ClassificationError as error:
            items = list_items(connection, user_id)
            return TEMPLATES.TemplateResponse(
                request,
                "inbox.html",
                _inbox_context(items, body, kind, remind_at, str(error)),
                status_code=400,
            )
        finally:
            connection.close()
        return RedirectResponse("/inbox", status_code=303)


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


def _inbox_context(items: list[dict], body: str, kind: str, remind_at: str, error: str | None) -> dict:
    return {
        "title": "Inbox",
        "items": items,
        "body": body,
        "kind": kind,
        "remind_at": remind_at,
        "error": error,
    }


def _logged_in(user_id: int) -> RedirectResponse:
    response = RedirectResponse("/inbox", status_code=303)
    response.set_cookie(COOKIE_NAME, sign_user_id(user_id), httponly=True, samesite="lax")
    return response


def _current_user(request: Request) -> int | None:
    return read_user_id(request.cookies.get(COOKIE_NAME))


def _parse_remind_at(value: str) -> datetime | None:
    if not value.strip():
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)
