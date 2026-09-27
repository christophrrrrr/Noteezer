from contextlib import asynccontextmanager

from fastapi import FastAPI

from noteezer.db import connect, init_db
from noteezer.web import register_routes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    connection = connect()
    try:
        init_db(connection)
    finally:
        connection.close()
    yield


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    register_routes(app)
    return app
