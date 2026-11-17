"""The reminders service: one table, a small JSON API, no pages.

Every call needs `Authorization: Bearer <token>` (see tokens.py). The UI uses
the person's own token to list, snooze, and finish reminders. Creating, moving,
and deleting a reminder needs the inbox scope, because only inbox knows which
items exist and who owns them (ADR-7).
"""

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app import middleware
from app.db import Connection, DatabaseError, connect
from app.migrate import check_read_write, migrate
from app.reminders import ReminderExists, complete, list_for, remove, schedule, snooze
from app.tokens import Claims, from_header, verify


class Reminder(BaseModel):
    item_id: int
    remind_at: str = Field(description="ISO 8601 time in UTC.")
    status: Literal["scheduled", "due", "snoozed", "done"]
    snoozed_until: str | None = Field(description="ISO 8601 time in UTC while snoozed.")
    repeat_daily: bool
    repeat_until: str | None = Field(description="Last day of a daily reminder, YYYY-MM-DD.")


class Problem(BaseModel):
    detail: str


def problems(*codes: int) -> dict:
    return {code: {"model": Problem} for code in codes}


class ReminderTime(BaseModel):
    remind_at: datetime
    repeat_daily: bool = False
    repeat_until: date | None = None
    keep_existing: bool = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    yield


def create_app(run_migrations: bool = True) -> FastAPI:
    app = FastAPI(title="Noteezer reminders", lifespan=lifespan if run_migrations else None)
    # The clock is read through app.state so tests can fix the time.
    app.state.clock = lambda: datetime.now(timezone.utc)
    middleware.install(app, "reminders")

    def now(request: Request) -> datetime:
        return request.app.state.clock()

    def database() -> Iterator[Connection]:
        connection = connect()
        try:
            yield connection
        finally:
            connection.close()

    def caller(authorization: str | None = Header(None)) -> Claims:
        claims = verify(from_header(authorization))
        if claims is None:
            raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
        return claims

    def inbox_only(claims: Claims = Depends(caller)) -> Claims:
        if claims.scope != "inbox":
            raise HTTPException(403, "only the inbox service may change which items have reminders")
        return claims

    middleware.health_route(app, connect, check_read_write, (DatabaseError,))

    @app.get("/reminders", responses=problems(401))
    def list_reminders(
        item_id: list[int] = Query(default=[]),
        claims: Claims = Depends(caller),
        connection: Connection = Depends(database),
        moment: datetime = Depends(now),
    ) -> list[Reminder]:
        return list_for(connection, claims.user_id, moment, item_id or None)

    @app.put("/reminders/{item_id}", responses=problems(400, 401, 403, 404, 409))
    def set_time(
        item_id: int,
        body: ReminderTime,
        claims: Claims = Depends(inbox_only),
        connection: Connection = Depends(database),
        moment: datetime = Depends(now),
    ) -> Reminder:
        try:
            return schedule(
                connection,
                claims.user_id,
                item_id,
                body.remind_at,
                moment,
                body.repeat_daily,
                body.repeat_until,
                body.keep_existing,
            )
        except ReminderExists:
            raise HTTPException(409, "this reminder already has a time") from None
        except LookupError:
            raise HTTPException(404, "reminder not found") from None
        except ValueError as error:
            raise HTTPException(400, str(error)) from None

    @app.delete("/reminders/{item_id}", status_code=204, responses=problems(401, 403))
    def delete_reminder(
        item_id: int,
        claims: Claims = Depends(inbox_only),
        connection: Connection = Depends(database),
    ) -> Response:
        # Deleting what is not there is fine: the caller only wants it gone.
        remove(connection, claims.user_id, item_id)
        return Response(status_code=204)

    @app.post("/reminders/{item_id}/done", responses=problems(401, 404))
    def mark_done(
        item_id: int,
        claims: Claims = Depends(caller),
        connection: Connection = Depends(database),
        moment: datetime = Depends(now),
    ) -> Reminder:
        try:
            return complete(connection, claims.user_id, item_id, moment)
        except LookupError:
            raise HTTPException(404, "reminder not found") from None

    @app.post("/reminders/{item_id}/snooze", responses=problems(401, 404, 409))
    def snooze_reminder(
        item_id: int,
        claims: Claims = Depends(caller),
        connection: Connection = Depends(database),
        moment: datetime = Depends(now),
    ) -> Reminder:
        try:
            return snooze(connection, claims.user_id, item_id, moment)
        except LookupError:
            raise HTTPException(404, "reminder not found") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    return app
