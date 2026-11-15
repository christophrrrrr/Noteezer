import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import middleware
from app.db import DatabaseError, connect
from app.migrate import migrate
from app.sorting import sorter_from_env
from app.web import register_routes
from app.worker import SortWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = connect()
    try:
        migrate(connection)
    finally:
        connection.close()
    task = asyncio.create_task(app.state.sort_worker.run())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.state.sort_worker = SortWorker(sorter_from_env())

    middleware.install(app, "inbox")

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            connection = connect(attempts=1)
            try:
                connection.execute("SELECT 1").fetchone()
            finally:
                connection.close()
        except (DatabaseError, RuntimeError):
            return JSONResponse({"ok": False, "database": "down"}, status_code=503)
        return JSONResponse({"ok": True, "database": "up"})

    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
    register_routes(app)
    return app
