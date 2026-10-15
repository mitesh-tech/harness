import sqlite3
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from conftest import BOOKSTORE, SEMIF_URL, edit, factory_override
from semif_recording import ReplayTransport

from harness.classify.walk import classify
from harness.datasources import installed_source_types
from harness.knowledge.validate import validate_project
from harness.logic.compile import compile_knowledge
from harness.logic.engine import Engine
from harness.semif.http import SemIfHttp


def messages(report, severity="error") -> list[str]:
    return [p.message for p in report.problems if p.severity == severity]


def data_file(project: Path) -> Path:
    return project / ".harness/data.yaml"


# ── Source-type plugins ─────────────────────────────────────────────


def test_relational_plugin_is_installed():
    relational = installed_source_types()["relational"]
    assert {"insert_row", "insert_link", "filter", "paging"} <= set(
        relational.manifest.capabilities
    )
    assert relational.manifest.converters["money_cents"].types == ["money"]
    assert relational.introspect() is not None


# ── Bookstore's data.yaml ───────────────────────────────────────────


def test_bookstore_data_loads():
    report = validate_project(BOOKSTORE)
    assert report.ok, messages(report)
    data = report.data
    assert data.summary() == "1 source, 4 entities, 2 relationships"
    assert data.entities["Book"].table == "books"
    assert data.entities["Book"].column_of("status") == "status"
    assert data.entities["Review"].parent_key == "book_id"
    assert data.relationships["collection_books"].stored_as == "link_table"
    assert data.relationships["book_publisher"].stored_as == "reference"


def test_relative_sqlite_url_is_relative_to_the_project(monkeypatch):
    monkeypatch.delenv("BOOKSTORE_DATABASE_URL", raising=False)
    source = validate_project(BOOKSTORE).data.sources["bookstore_db"]
    assert source.url(BOOKSTORE) == f"sqlite:///{(BOOKSTORE / 'bookstore.db').resolve()}"
    monkeypatch.setenv("BOOKSTORE_DATABASE_URL", "postgresql://db.internal/bookstore")
    assert source.url(BOOKSTORE) == "postgresql://db.internal/bookstore"


def test_no_data_yaml_is_a_warning(project):
    data_file(project).unlink()
    report = validate_project(project)
    assert report.ok and report.data is None
    assert any(m.startswith("no data.yaml") for m in messages(report, "warning"))


# ── Checks: sources and connections ─────────────────────────────────


def test_unknown_source_type(project):
    edit(data_file(project), "type: relational", "type: graph")
    [error] = validate_project(project).errors
    assert error.message == "unknown source type 'graph' (installed: relational)"
    assert error.path == ("sources", "bookstore_db", "type")
    assert error.line is not None


def test_connection_is_checked_by_the_plugin(project):
    edit(data_file(project), "url_env: BOOKSTORE_DATABASE_URL", "url_env: bookstore_url")
    [error] = validate_project(project).errors
    assert error.path == ("sources", "bookstore_db", "connection", "url_env")
    assert "not a valid name" in error.message


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("postgresql://app:secret@localhost/db", "must not contain a password"),
        ("postgresql://db.internal/bookstore", "only for a local database"),
    ],
)
def test_no_secrets_in_default_url(project, url, message):
    edit(data_file(project), "sqlite:///./bookstore.db", url)
    [error] = validate_project(project).errors
    assert message in error.message


def test_unknown_source_in_an_entry(project):
    edit(
        data_file(project), "  Publisher:\n    source: bookstore_db", "  Publisher:\n    source: db"
    )
    [error] = validate_project(project).errors
    assert error.message == "unknown source 'db' (sources: bookstore_db)"
    assert error.path == ("entities", "Publisher", "source")


# ── Checks: entities ────────────────────────────────────────────────


def test_every_attribute_needs_a_column(project):
    edit(data_file(project), "      isbn: isbn\n", "")
    [error] = validate_project(project).errors
    assert error.message.startswith("'isbn' has no column")
    assert error.path == ("entities", "Book", "columns")


def test_attribute_not_stored(project):
    edit(data_file(project), "      isbn: isbn\n", "      isbn: { stored: false }\n")
    report = validate_project(project)
    assert report.ok, messages(report)
    assert report.data.entities["Book"].column_of("isbn") is None


def test_column_for_an_unknown_attribute(project):
    edit(data_file(project), "      isbn: isbn\n", "      isbn: isbn\n      subtitle: subtitle\n")
    [error] = validate_project(project).errors
    assert error.message == "'subtitle' is not an attribute of Book"


def test_converters(project):
    edit(
        data_file(project),
        "      price: price\n",
        "      price: { column: price_cents, convert: money_cents }\n",
    )
    assert validate_project(project).ok
    edit(data_file(project), "convert: money_cents", "convert: bool_int")
    [error] = validate_project(project).errors
    assert error.message == "converter 'bool_int' is for boolean, but price is money"
    edit(data_file(project), "convert: bool_int", "convert: cents")
    [error] = validate_project(project).errors
    assert error.message.startswith("unknown converter 'cents'")


def test_composition_child_needs_a_parent_key(project):
    edit(
        data_file(project),
        "    parent_key: book_id       # part of a Book: the column pointing at its Book\n",
        "",
    )
    [error] = validate_project(project).errors
    assert error.message.startswith("Review is part of a Book; add `parent_key`")


def test_parent_key_only_for_a_child(project):
    edit(
        data_file(project),
        "    table: publishers\n",
        "    table: publishers\n    parent_key: book_id\n",
    )
    [error] = validate_project(project).errors
    assert error.message == "Publisher is not part of another entity"


def test_unmapped_entity_is_a_warning(project):
    text = data_file(project).read_text()
    start = text.index("  Collection:\n")
    end = text.index("# ── Relationships")
    data_file(project).write_text(text[:start] + text[end:])
    report = validate_project(project)
    assert report.ok
    assert "Collection has no entry: tasks about it end as no_mapping" in messages(
        report, "warning"
    )


# ── Checks: relationships ───────────────────────────────────────────


def test_many_to_many_needs_a_link_table(project):
    edit(
        data_file(project),
        "    table: collection_books\n    keys:\n"
        "      Collection: collection_id\n      Book: book_id\n",
        "    column: books.collection_id\n",
    )
    [error] = validate_project(project).errors
    assert error.message == "'collection_books' is many-to-many: give a link table with keys"


def test_link_table_keys_are_for_both_entities(project):
    edit(
        data_file(project), "      Collection: collection_id\n", "      Publisher: collection_id\n"
    )
    [error] = validate_project(project).errors
    assert error.message == "keys are needed for Collection and Book"


def test_reference_column_is_on_the_childs_table(project):
    edit(data_file(project), "column: books.publisher_id", "column: publishers.book_id")
    [error] = validate_project(project).errors
    assert error.message == ("the reference column belongs on Book's table (books), not publishers")


def test_a_composition_is_not_a_relationship_entry(project):
    edit(
        data_file(project),
        "relationships:\n",
        "relationships:\n  book_reviews:\n    source: bookstore_db\n    column: reviews.book_id\n",
    )
    [error] = validate_project(project).errors
    assert error.message == "'book_reviews' is a composition: it is stored by Review's parent_key"


# ── Prolog: data facts and checks ───────────────────────────────────


@pytest.fixture(scope="module")
def bookstore():
    report = validate_project(BOOKSTORE, logic=False)
    assert report.ok
    return report


def test_data_facts(bookstore):
    program = compile_knowledge(bookstore.domain, bookstore.technical, bookstore.data)
    for fact in (
        "stored_in(book, bookstore_db).",
        "stored_table(review, reviews).",
        "stored_column(book, price, price).",
        "relationship_storage(collection_books, link_table).",
        "source_supports(relational, insert_link).",
    ):
        assert fact in program


def test_source_cannot_persist(project):
    factory_override(project).write_text(
        "kinds:\n"
        "  delete:\n"
        "    description: Remove a {target}\n"
        "    examples: [delete]\n"
        "    not: Not for taking a thing out of a group (use unlink)\n"
        "    effects: [It is gone]\n"
        "    method: DELETE\n"
        "    persistence: purge_row\n"
        "    status: 204\n"
        "    names_as: remove_{entity}\n"
    )
    [error] = validate_project(project).errors
    assert error.path == ("services", "remove_review")
    assert error.message == (
        "source_cannot_persist: its source bookstore_db cannot do purge_row, "
        "which this kind of API needs"
    )


def test_source_cannot_filter(bookstore):
    data = bookstore.data
    source = data.sources["bookstore_db"]
    manifest = source.type.manifest.model_copy(
        update={"capabilities": [c for c in source.type.manifest.capabilities if c != "filter"]}
    )
    data.sources["bookstore_db"] = replace(source, type=replace(source.type, manifest=manifest))
    try:
        problems = Engine(bookstore.domain, bookstore.technical, data=data).problems()
    finally:
        data.sources["bookstore_db"] = source
    assert [(p.name, p.code) for p in problems] == [("list_books", "source_cannot_filter")]


def test_relationship_used_but_not_stored(project):
    text = data_file(project).read_text()
    start = text.index("  collection_books:")
    data_file(project).write_text(text[:start] + text[text.index("  book_publisher:") :])
    report = validate_project(project)
    [error] = report.errors
    assert error.path == ("relationships", "collection_books")
    assert error.message == (
        "relationship_not_stored: collection_books has no entry under relationships in data.yaml"
    )


# ── Introspection against a real (temporary) database ───────────────


@pytest.fixture
def database(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "shop.db"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author TEXT, price NUMERIC,"
            " status TEXT, publisher_id INTEGER, created_at TEXT)"
        )
        db.execute("CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("CREATE TABLE reviews (id INTEGER PRIMARY KEY, rating INTEGER, comment TEXT)")
    monkeypatch.setenv("BOOKSTORE_DATABASE_URL", f"sqlite:///{path}")
    return path


def test_introspection_reports_differences(project, database):
    report = validate_project(project, introspect=True)
    assert report.ok
    assert report.introspection == "5 differences"
    warnings = messages(report, "warning")
    for expected in (
        "column 'isbn' not found in books",
        "books.created_at is not in the domain (ignored)",
        "column 'book_id' not found in reviews",
        "table 'collections' not found (it is created when Collection's first API is generated)",
        "table 'collection_books' not found",
    ):
        assert expected in warnings


def test_introspection_matches(project, database):
    with sqlite3.connect(database) as db:
        db.execute("ALTER TABLE books ADD COLUMN isbn TEXT")
        db.execute("ALTER TABLE books DROP COLUMN created_at")
        db.execute("ALTER TABLE reviews ADD COLUMN book_id INTEGER")
        db.execute("CREATE TABLE collections (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("CREATE TABLE collection_books (collection_id INTEGER, book_id INTEGER)")
    report = validate_project(project, introspect=True)
    assert report.introspection == "matches", messages(report, "warning")


def test_introspection_without_a_url(project, monkeypatch):
    monkeypatch.delenv("BOOKSTORE_DATABASE_URL", raising=False)
    edit(
        data_file(project),
        "      default_url: sqlite:///./bookstore.db   # local fallback for development\n",
        "",
    )
    report = validate_project(project, introspect=True)
    assert report.introspection == "1 source not read"
    assert "cannot read bookstore_db: set BOOKSTORE_DATABASE_URL" in messages(report, "warning")


# ── Classification: NO_MAPPING (replayed SemIf answers) ─────────────


def test_unmapped_target_ends_as_no_mapping(project):
    text = data_file(project).read_text()
    start = text.index("  Publisher:\n")
    data_file(project).write_text(text[:start] + text[text.index("  Review:\n") :])
    report = validate_project(project, logic=False)
    manifests = {n: f.manifest for n, f in report.factories.items()} | report.planned
    model = SemIfHttp(SEMIF_URL, client=httpx.Client(transport=ReplayTransport()))
    trace = classify(
        "Let staff add a new publisher through a new endpoint",
        report.domain,
        report.technical,
        model,
        0.8,
        manifests,
        report.data,
    )
    assert trace.outcome == "no_mapping"
    assert trace.path == ["factory", "target", "parent", "data"]
    assert trace.events[-1].reason == "Publisher (or its parent) has no entry in data.yaml"
