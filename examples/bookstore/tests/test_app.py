import importlib
import sys
from decimal import Decimal

from fastapi.testclient import TestClient

from app.api import discover_routers
from app.main import create_app
from app.models import Book


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_books_table_starts_empty(session):
    assert session.query(Book).count() == 0


def test_book_defaults(session):
    book = Book(title="Dune", author="Frank Herbert", price=Decimal("9.99"))
    session.add(book)
    session.commit()
    session.refresh(book)

    assert book.id is not None
    assert book.status == "active"
    assert book.created_at is not None


def test_new_router_file_is_discovered(tmp_path, monkeypatch):
    package = tmp_path / "sample_api"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "ping.py").write_text(
        "from fastapi import APIRouter\nrouter = APIRouter()\n"
        "@router.get('/ping')\ndef ping():\n    return 'pong'\n"
    )
    (package / "helpers.py").write_text("VALUE = 1\n")  # no router: ignored
    monkeypatch.syspath_prepend(str(tmp_path))

    try:
        routers = discover_routers(importlib.import_module("sample_api"))
        assert [route.path for router in routers for route in router.routes] == ["/ping"]
    finally:
        for name in ("sample_api.ping", "sample_api.helpers", "sample_api"):
            sys.modules.pop(name, None)


def test_no_database_file_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with TestClient(create_app("sqlite://")) as client:
        assert client.get("/health").status_code == 200

    assert list(tmp_path.iterdir()) == []
