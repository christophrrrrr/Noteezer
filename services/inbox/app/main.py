import asyncio
import contextlib
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import connect
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

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")), name="static")
    register_routes(app)
    return app
