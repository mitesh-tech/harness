# Bookstore (sample app)

A small FastAPI + SQLite app used as a **target project** for the harness. The harness classifies tasks such as "Add a new book" and builds the endpoints into this app.

It starts with only a `Book` model and a health check. Book endpoints are added by the harness in later steps of the example plan. The app itself knows nothing about the harness: it runs, tests and deploys without it.

## Layout

```
app/
├── main.py      create_app(): builds the app with its own database engine,
│                creates tables at startup, includes every router in app/api/
├── db.py        declarative base, engine creation, one session per request
├── models/      SQLAlchemy models (Book)
└── api/         one file per feature; any module with a `router` is loaded automatically
tests/
├── conftest.py  each test gets its own app on a fresh in-memory database
└── test_app.py
```

### Why endpoints are discovered automatically

A new endpoint is a new file in `app/api/`. Nothing else needs to change, so generated code is added without editing existing files and can be checked and regenerated on its own.

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

The database file defaults to `./bookstore.db`; set `BOOKSTORE_DATABASE_URL` to use another one. Tests always use an in-memory database and never create a file.
