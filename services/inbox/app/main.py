import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import middleware
from app.api import register_routes
from app.db import DatabaseError, connect
from app.migrate import check_read_write, migrate
from app.sorting import sorter_from_env
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
    app = FastAPI(title="Noteezer inbox", lifespan=lifespan)
    app.state.sort_worker = SortWorker(sorter_from_env())

    middleware.install(app, "inbox")

    middleware.health_route(app, connect, check_read_write, (DatabaseError,))

    register_routes(app)
    return app
