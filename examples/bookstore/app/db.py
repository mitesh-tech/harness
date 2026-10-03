"""Database setup: the declarative base, engine creation and per-request sessions."""

import os
from collections.abc import Iterator
from typing import Any

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool

DEFAULT_DATABASE_URL = "sqlite:///./bookstore.db"
IN_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:"}


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("BOOKSTORE_DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str) -> Engine:
    options: dict[str, Any] = {}
    if url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False}
        if url in IN_MEMORY_URLS:
            # One shared connection, so every session sees the same in-memory database.
            options["poolclass"] = StaticPool
    return create_engine(url, **options)


def get_session(request: Request) -> Iterator[Session]:
    """FastAPI dependency: one session per request, from the app's own engine."""
    with request.app.state.sessionmaker() as session:
        yield session
