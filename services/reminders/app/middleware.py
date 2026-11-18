"""Request logs and CORS, the same in both services.

Every request is logged as one line: request id, method, path, status, and
time taken. The request id comes from an incoming X-Request-ID header or is
made up here, is sent back in the response, and is kept in `request_id` so a
call inbox makes to reminders carries the same id. One id then finds the
request in both services' logs.

This file is identical in services/inbox and services/reminders; a test in
each service fails if the two copies differ.
"""

import contextvars
import logging
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def install(app: FastAPI, service: str) -> None:
    log = logging.getLogger(f"{service}.requests")

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id.set(rid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("%s %s %s failed", rid, request.method, request.url.path)
            raise
        finally:
            request_id.reset(token)
        took = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = rid
        log.info("%s %s %s %s %.0fms", rid, request.method, request.url.path, response.status_code, took)
        return response

    # Added last so it runs first: a browser's preflight request is answered
    # before anything else looks at it.
    origins = cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            expose_headers=["X-Request-ID"],
        )


def health_route(app: FastAPI, connect, check_read_write, database_errors: tuple[type[Exception], ...]) -> None:
    """GET /health: 200 when this service can read and write its own database.

    It never asks the other service: a service is healthy when it can do its
    own work. "version" is the git commit the image was built from.
    """

    @app.get("/health", response_model=Health, responses={503: {"model": Health}})
    def health() -> JSONResponse:
        version = os.environ.get("GIT_SHA", "dev")
        try:
            connection = connect(attempts=1)
            try:
                check_read_write(connection)
            finally:
                connection.close()
        except (*database_errors, RuntimeError):
            return JSONResponse({"status": "error", "db": "down", "version": version}, status_code=503)
        return JSONResponse({"status": "ok", "db": "ok", "version": version})


class Health(BaseModel):
    status: str
    db: str
    version: str


def cors_origins() -> list[str]:
    # CORS_ORIGINS is a comma-separated list, like the UI's address in Azure.
    # Empty means no other site may call this service from a browser.
    return [origin.strip().rstrip("/") for origin in os.environ.get("CORS_ORIGINS", "").split(",") if origin.strip()]


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
