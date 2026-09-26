from contextlib import asynccontextmanager

from fastapi import FastAPI

from noteezer.db import connect, init_db


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

    return app
