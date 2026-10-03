from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  (registers tables)
from app.api import health
from app.db import Base, database_url, make_engine


def create_app(url: str | None = None) -> FastAPI:
    """Build the app with its own engine; tests pass an in-memory URL."""
    engine = make_engine(url or database_url())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    application = FastAPI(title="Bookstore", lifespan=lifespan)
    application.state.engine = engine
    application.state.sessionmaker = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False
    )

    application.include_router(health.router)
    return application


app = create_app()
