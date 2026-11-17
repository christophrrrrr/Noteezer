"""The inbox JSON API that the static UI in ui/ calls (ADR-8).

Every route but register, login, and health needs `Authorization: Bearer
<token>` with a user token (tokens.py). Errors are `{"detail": "..."}` with
400 for input the person can fix, 401 for a missing or expired token, and 404
for an item or folder that does not exist or belongs to someone else.

Reminder done and snooze are not here: the UI calls the reminders service
for those itself (ADR-7). Inbox only toggles tasks.
"""

from datetime import date, datetime, timezone, tzinfo

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel

from app.classify import ClassificationError
from app.db import connect
from app.folders import (
    FolderError,
    create_folder,
    delete_folder,
    list_folders,
    move_folder,
    rename_folder,
)
from app.images import (
    MAX_BYTES,
    PLACEHOLDER,
    ImageError,
    attach_image,
    check_image,
    image_for,
    images_for,
)
from app.inbox import (
    count_by_folder,
    count_items,
    delete_item,
    item_kind,
    list_items,
    save_item,
    set_folders,
    toggle_task,
    update_item,
)
from app.links import previews_for
from app.reminders_client import RemindersUnavailable
from app.sorting import (
    accept_also,
    accept_proposal,
    accept_reminder,
    accept_suggestion,
    also_suggestions_for,
    dismiss_also,
    request_folder_check,
    requeue_unsorted,
    resort_if_waiting,
    results_for,
    undo_auto_split,
)
from app.tokens import from_header, issue, verify
from app.users import (
    AccountError,
    authenticate,
    register_user,
    set_timezone,
    user_timezone,
)

UNSORTED = "unsorted"
KINDS = ("note", "idea", "task", "link", "reminder")


class Credentials(BaseModel):
    email: str
    password: str
    timezone: str = ""


class Token(BaseModel):
    token: str


class Zone(BaseModel):
    timezone: str


class ItemEdit(BaseModel):
    body: str
    kind: str | None = None
    remind_at: str = ""
    repeat_daily: bool = False
    repeat_until: str = ""


class FolderIds(BaseModel):
    folder_ids: list[int]


class NewFolder(BaseModel):
    name: str
    parent_id: int | None = None


class FolderName(BaseModel):
    name: str


class FolderParent(BaseModel):
    parent_id: int | None = None


class Created(BaseModel):
    id: int


def current_user(request: Request) -> int:
    claims = verify(from_header(request.headers.get("authorization")))
    if claims is None or claims.scope != "user":
        raise HTTPException(401, "Log in again.", headers={"WWW-Authenticate": "Bearer"})
    return claims.user_id


def register_routes(app: FastAPI) -> None:
    @app.post("/api/register", status_code=201, tags=["account"])
    def register(body: Credentials) -> Token:
        connection = connect()
        try:
            user_id = register_user(connection, body.email, body.password, _now())
            set_timezone(connection, user_id, body.timezone)
        except AccountError as error:
            raise HTTPException(400, str(error)) from None
        finally:
            connection.close()
        return Token(token=issue(user_id, "user"))

    @app.post("/api/login", tags=["account"])
    def login(body: Credentials) -> Token:
        connection = connect()
        try:
            user_id = authenticate(connection, body.email, body.password)
            if user_id is not None:
                set_timezone(connection, user_id, body.timezone)
        finally:
            connection.close()
        if user_id is None:
            raise HTTPException(401, "Email or password is wrong.")
        return Token(token=issue(user_id, "user"))

    @app.put("/api/me/timezone", status_code=204, tags=["account"])
    def change_timezone(body: Zone, user_id: int = Depends(current_user)) -> Response:
        # The UI sends the browser's zone when it opens, so the account follows
        # the person when they travel. An unknown name keeps the stored one.
        connection = connect()
        try:
            set_timezone(connection, user_id, body.timezone)
        finally:
            connection.close()
        return Response(status_code=204)

    @app.get("/api/folders", tags=["folders"])
    def folders(user_id: int = Depends(current_user)) -> dict:
        connection = connect()
        try:
            entries = list_folders(connection, user_id)
            counts = count_by_folder(connection, user_id)
            total = count_items(connection, user_id)
        finally:
            connection.close()
        for entry in entries:
            entry["count"] = counts.get(entry["id"], 0)
        return {"folders": entries, "unsorted_count": counts.get(None, 0), "total_count": total}

    @app.get("/api/items", tags=["items"])
    def items(q: str = "", folder: str = "", user_id: int = Depends(current_user)) -> dict:
        # folder is "" for everything, "unsorted", or a folder id.
        unsorted = folder == UNSORTED
        folder_id = None if unsorted else _folder_id(folder)
        connection = connect()
        try:
            entries = list_folders(connection, user_id)
            names = {entry["id"]: entry["path"] for entry in entries}
            if folder_id is not None and folder_id not in names:
                raise HTTPException(404, "That folder no longer exists.")
            zone = user_timezone(connection, user_id)
            listed = list_items(connection, user_id, q, folder_id=folder_id, unsorted=unsorted, zone=zone)
            also = also_suggestions_for(connection, user_id)
            results = results_for(connection, user_id)
            previews = previews_for(connection, user_id)
            pictures = images_for(connection, user_id)
        finally:
            connection.close()
        for item in listed:
            item["folders"] = [{"id": fid, "path": names.get(fid, "")} for fid in item["folder_ids"]]
            item["also"] = [{"id": fid, "path": names[fid]} for fid in also.get(item["id"], []) if fid in names]
            item["preview"] = previews.get(item["id"])
            item["image"] = pictures.get(item["id"])
            item["sort"] = _sort_result(results.get(item["id"]), names, zone)
        return {
            "items": listed,
            "timezone": str(zone),
            "reminders_down": any(item["reminder_unavailable"] for item in listed),
        }

    @app.post("/api/items", status_code=201, tags=["items"])
    def create_item(
        request: Request,
        body: str = Form(""),
        kind: str = Form(""),
        remind_at: str = Form(""),
        repeat_daily: bool = Form(False),
        repeat_until: str = Form(""),
        folder_id: int | None = Form(None),
        image: UploadFile | None = File(None),
        ai_image: bool = Form(True),
        user_id: int = Depends(current_user),
    ) -> Created:
        picture = _uploaded(image)
        connection = connect()
        try:
            if picture is not None:
                check_image(picture)
                body = body if body.strip() else PLACEHOLDER
            item_id = save_item(
                connection,
                user_id,
                body,
                _parse_remind_at(remind_at, user_timezone(connection, user_id)),
                _now(),
                forced_kind=_kind(kind),
                repeat_daily=repeat_daily,
                repeat_until=_parse_date(repeat_until),
                folder_id=folder_id,
            )
            if picture is not None:
                attach_image(connection, item_id, picture, ai_allowed=ai_image)
        except LookupError:
            raise HTTPException(400, "That folder no longer exists.") from None
        except (ClassificationError, ValueError, ImageError) as error:
            raise HTTPException(400, str(error)) from None
        finally:
            connection.close()
        _wake_sorter(request)
        return Created(id=item_id)

    @app.post("/api/share", tags=["items"], responses={204: {"description": "Nothing was shared."}})
    def share(
        request: Request,
        title: str = Form(""),
        text: str = Form(""),
        url: str = Form(""),
        image: UploadFile | None = File(None),
        user_id: int = Depends(current_user),
    ):
        # The UI's service worker forwards Android's share sheet here. The
        # line goes to Unsorted and is sorted like any other.
        body = _shared_line(title, text, url)
        picture = _uploaded(image)
        if picture is None and not body:
            return Response(status_code=204)
        connection = connect()
        try:
            if picture is not None:
                check_image(picture)
                body = body or PLACEHOLDER
            item_id = save_item(connection, user_id, body, None, _now())
            if picture is not None:
                attach_image(connection, item_id, picture, ai_allowed=True)
        except (ClassificationError, ImageError, ValueError) as error:
            raise HTTPException(400, str(error)) from None
        finally:
            connection.close()
        _wake_sorter(request)
        return Response(status_code=201, content=Created(id=item_id).model_dump_json(), media_type="application/json")

    @app.get("/api/items/{item_id}/image", tags=["items"], response_class=Response)
    def item_image(item_id: int, user_id: int = Depends(current_user)) -> Response:
        connection = connect()
        try:
            picture = image_for(connection, user_id, item_id)
        finally:
            connection.close()
        if picture is None:
            raise HTTPException(404, "No image.")
        # The type comes from check_image, never from the upload, and nosniff
        # stops the browser from guessing a different one.
        return Response(
            picture.data,
            media_type=picture.mime,
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"},
        )

    @app.put("/api/items/{item_id}", status_code=204, tags=["items"])
    def edit_item(item_id: int, body: ItemEdit, request: Request, user_id: int = Depends(current_user)) -> Response:
        connection = connect()
        try:
            update_item(
                connection,
                user_id,
                item_id,
                body.body,
                _parse_remind_at(body.remind_at, user_timezone(connection, user_id)),
                _now(),
                forced_kind=_kind(body.kind or ""),
                repeat_daily=body.repeat_daily,
                repeat_until=_parse_date(body.repeat_until),
            )
            resort_if_waiting(connection, user_id, item_id)
        except LookupError:
            raise HTTPException(404, "Not found.") from None
        except (ClassificationError, ValueError) as error:
            raise HTTPException(400, str(error)) from None
        finally:
            connection.close()
        _wake_sorter(request)
        return Response(status_code=204)

    @app.delete("/api/items/{item_id}", status_code=204, tags=["items"])
    def remove_item(item_id: int, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: delete_item(connection, user_id, item_id))
        return Response(status_code=204)

    @app.put("/api/items/{item_id}/folders", status_code=204, tags=["items"])
    def item_folders(item_id: int, body: FolderIds, user_id: int = Depends(current_user)) -> Response:
        # The line ends up in exactly these folders, or in Unsorted when empty.
        _run(lambda connection: set_folders(connection, user_id, item_id, body.folder_ids))
        return Response(status_code=204)

    @app.post("/api/items/{item_id}/toggle", tags=["items"])
    def toggle(item_id: int, user_id: int = Depends(current_user)) -> dict:
        def flip(connection):
            if item_kind(connection, user_id, item_id) != "task":
                raise HTTPException(400, "Only a task can be ticked off. Reminders are finished in reminders.")
            return toggle_task(connection, user_id, item_id)

        return {"task_done": _run(flip)}

    @app.post("/api/items/{item_id}/also/{folder_id}", status_code=204, tags=["sorting"])
    def add_suggested_folder(item_id: int, folder_id: int, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: accept_also(connection, user_id, item_id, folder_id))
        return Response(status_code=204)

    @app.delete("/api/items/{item_id}/also/{folder_id}", status_code=204, tags=["sorting"])
    def dismiss_suggested_folder(item_id: int, folder_id: int, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: dismiss_also(connection, user_id, item_id, folder_id))
        return Response(status_code=204)

    @app.post("/api/items/{item_id}/accept", status_code=204, tags=["sorting"])
    def accept_item(item_id: int, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: accept_suggestion(connection, user_id, item_id))
        return Response(status_code=204)

    @app.post("/api/items/{item_id}/create-folder", status_code=204, tags=["sorting"])
    def create_proposed_folder(item_id: int, request: Request, user_id: int = Depends(current_user)) -> Response:
        def create(connection):
            accept_proposal(connection, user_id, item_id, _now())
            requeue_unsorted(connection, user_id, only_unmatched=True)

        _run(create)
        _wake_sorter(request)
        return Response(status_code=204)

    @app.post("/api/items/{item_id}/remind", status_code=204, tags=["sorting"])
    def set_suggested_reminder(item_id: int, user_id: int = Depends(current_user)) -> Response:
        try:
            _run(lambda connection: accept_reminder(connection, user_id, item_id, _now()))
        except RemindersUnavailable:
            raise HTTPException(503, "Reminders are unavailable right now. Try again in a moment.") from None
        return Response(status_code=204)

    @app.post("/api/items/{item_id}/unsplit", status_code=204, tags=["sorting"])
    def undo_split_route(item_id: int, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: undo_auto_split(connection, user_id, item_id))
        return Response(status_code=204)

    @app.post("/api/sorting/retry", status_code=204, tags=["sorting"])
    def sort_again(request: Request, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: requeue_unsorted(connection, user_id))
        _wake_sorter(request)
        return Response(status_code=204)

    @app.post("/api/folders", status_code=201, tags=["folders"])
    def folder_create(body: NewFolder, request: Request, user_id: int = Depends(current_user)) -> Created:
        def create(connection):
            created = create_folder(connection, user_id, body.name, _now(), parent_id=body.parent_id)
            requeue_unsorted(connection, user_id, only_unmatched=True)
            request_folder_check(connection, created, _now())
            return created

        created = _run(create)
        _wake_sorter(request)
        return Created(id=created)

    @app.put("/api/folders/{folder_id}/name", status_code=204, tags=["folders"])
    def folder_rename(folder_id: int, body: FolderName, request: Request, user_id: int = Depends(current_user)):
        def rename(connection):
            rename_folder(connection, user_id, folder_id, body.name)
            requeue_unsorted(connection, user_id, only_unmatched=True)

        _run(rename)
        _wake_sorter(request)
        return Response(status_code=204)

    @app.put("/api/folders/{folder_id}/parent", status_code=204, tags=["folders"])
    def folder_move(folder_id: int, body: FolderParent, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: move_folder(connection, user_id, folder_id, body.parent_id))
        return Response(status_code=204)

    @app.delete("/api/folders/{folder_id}", status_code=204, tags=["folders"])
    def folder_delete(folder_id: int, user_id: int = Depends(current_user)) -> Response:
        _run(lambda connection: delete_folder(connection, user_id, folder_id))
        return Response(status_code=204)


def _run(action):
    """Runs one change on a fresh connection and maps domain errors to HTTP."""
    connection = connect()
    try:
        return action(connection)
    except LookupError:
        raise HTTPException(404, "Not found.") from None
    except (FolderError, ClassificationError, ValueError) as error:
        raise HTTPException(400, str(error)) from None
    finally:
        connection.close()


def _sort_result(result: dict | None, names: dict[int, str], zone: tzinfo) -> dict | None:
    if result is None:
        return None
    result["folder_path"] = names.get(result["folder_id"], "")
    result["remind_label"] = _label(result["suggested_remind_at"], zone)
    result["proposed_path"] = ""
    if result["proposed_name"]:
        parent = names.get(result["proposed_parent_id"])
        result["proposed_path"] = f"{parent} / {result['proposed_name']}" if parent else result["proposed_name"]
    return result


def _kind(value: str) -> str | None:
    # "" lets the rules pick. Anything else must be a known kind.
    cleaned = value.strip()
    if not cleaned:
        return None
    if cleaned not in KINDS:
        raise HTTPException(400, f"Kind must be one of: {', '.join(KINDS)}.")
    return cleaned


def _shared_line(title: str, text: str, url: str) -> str:
    # Apps fill these differently: YouTube puts the link in text, Chrome
    # sends the page title and url. Each piece is kept once.
    parts: list[str] = []
    for piece in (title, text, url):
        cleaned = piece.strip()
        if cleaned and not any(cleaned in existing for existing in parts):
            parts.append(cleaned)
    return "\n".join(parts)


def _uploaded(upload: UploadFile | None) -> bytes | None:
    # An empty file field still arrives as an upload with no name and no bytes.
    if upload is None or not upload.filename:
        return None
    data = upload.file.read(MAX_BYTES + 1)
    return data or None


def _wake_sorter(request: Request) -> None:
    request.app.state.sort_worker.wake()


def _folder_id(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        raise HTTPException(400, "folder must be empty, 'unsorted', or a folder id.") from None


def _parse_remind_at(value: str, zone: tzinfo) -> datetime | None:
    # The date picker sends local wall time without an offset. It is read in
    # the user's timezone, not the server's, which is UTC once deployed.
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, "remind_at must be a date and time like 2026-11-20T09:00.") from None
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
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(400, "repeat_until must be a date like 2026-11-30.") from None


def _now() -> datetime:
    return datetime.now(timezone.utc)
