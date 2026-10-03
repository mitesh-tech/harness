from decimal import Decimal

from fastapi.testclient import TestClient

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


def test_no_database_file_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with TestClient(create_app("sqlite://")) as client:
        assert client.get("/health").status_code == 200

    assert list(tmp_path.iterdir()) == []
