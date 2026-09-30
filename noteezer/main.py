import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI

from noteezer.db import connect, init_db
from noteezer.sorting import sorter_from_env
from noteezer.web import register_routes
from noteezer.worker import SortWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    connection = connect()
    try:
        init_db(connection)
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

    register_routes(app)
    return app
