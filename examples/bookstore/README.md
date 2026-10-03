# Bookstore (sample app)

A small, ordinary FastAPI + SQLite app, used as a sample **target project** for the harness.

It starts with only a `Book` model and a health check.

## Layout

```
app/
├── main.py      create_app(): builds the app with its own database engine,
│                creates tables at startup and includes the routers
├── db.py        declarative base, engine creation, one session per request
├── models/      SQLAlchemy models (Book)
└── api/         route modules (health)
tests/
├── conftest.py  each test gets its own app on a fresh in-memory database
└── test_app.py
```

## The `Book` model

| Field | Type | Notes |
|---|---|---|
| `id` | integer | primary key |
| `title` | text (≤ 200) | required |
| `author` | text (≤ 200) | required |
| `price` | decimal (10, 2) | required |
| `status` | text | `active` (default) or `archived` |
| `created_at` | timestamp (UTC) | set automatically |

## Run

From this folder:

```bash
uv sync
uv run pytest
uv run uvicorn app.main:app --reload    # then open http://127.0.0.1:8000/docs
```

The database file defaults to `./bookstore.db`; set `BOOKSTORE_DATABASE_URL` to use another one. Tests use an in-memory database and never create a file.
