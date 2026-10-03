"""Test setup: every test gets its own app on a fresh in-memory database."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import create_app


@pytest.fixture
def app() -> FastAPI:
    return create_app("sqlite://")


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:  # runs startup, which creates the tables
        yield test_client


@pytest.fixture
def session(app: FastAPI, client: TestClient) -> Iterator[Session]:
    with app.state.sessionmaker() as db:
        yield db
