import pytest
from conftest import BOOKSTORE, edit, factory_override
from typer.testing import CliRunner

from harness.cli import app
from harness.knowledge.validate import validate_project
from harness.resolve import ResolveError, design_service

runner = CliRunner()


@pytest.fixture(scope="module")
def bookstore():
    report = validate_project(BOOKSTORE)
    assert report.ok
    return report


def design(report, service) -> dict:
    return design_service(report, service).to_dict()


def errors(report) -> list[str]:
    return [p.message for p in report.errors]


# ── Paths for every Bookstore service (I2–I7) ───────────────────────


@pytest.mark.parametrize(
    ("service", "method", "path"),
    [
        ("add_book", "POST", "/books"),
        ("list_books", "GET", "/books"),
        ("get_book", "GET", "/books/{book_id}"),
        ("change_book_price", "PATCH", "/books/{book_id}"),
        ("replace_book", "PUT", "/books/{book_id}"),
        ("archive_book", "PATCH", "/books/{book_id}/archive"),
        ("set_book_publisher", "PUT", "/books/{book_id}/publisher"),
        ("add_review", "POST", "/books/{book_id}/reviews"),
        ("list_reviews", "GET", "/books/{book_id}/reviews"),
        ("remove_review", "DELETE", "/books/{book_id}/reviews/{review_id}"),
        ("create_collection", "POST", "/collections"),
        ("add_book_to_collection", "PUT", "/collections/{collection_id}/books/{book_id}"),
        ("remove_book_from_collection", "DELETE", "/collections/{collection_id}/books/{book_id}"),
    ],
)
def test_paths(bookstore, service, method, path):
    api = design(bookstore, service)["api"]
    assert (api["method"], api["path"]) == (method, path)


def test_path_params_name_their_entity(bookstore):
    api = design(bookstore, "remove_review")["api"]
    assert api["path_params"] == {
        "book_id": {"type": "id", "refers_to": "Book"},
        "review_id": {"type": "id", "refers_to": "Review"},
    }


# ── Body, query and response (I8–I10) ───────────────────────────────


def test_create_body_has_every_editable_attribute(bookstore):
    fields = design(bookstore, "add_book")["api"]["request"]["fields"]
    assert list(fields) == ["isbn", "title", "author", "price"]  # not id, not status
    assert fields["price"] == {"type": "money", "required": True, "min": 0}


def test_change_body_has_only_the_changes(bookstore):
    fields = design(bookstore, "change_book_price")["api"]["request"]["fields"]
    assert fields == {"price": {"type": "money", "required": False, "min": 0}}


def test_reference_link_body(bookstore):
    fields = design(bookstore, "set_book_publisher")["api"]["request"]["fields"]
    assert fields == {"publisher_id": {"type": "id", "required": True, "refers_to": "Publisher"}}


@pytest.mark.parametrize("service", ["get_book", "remove_review", "archive_book"])
def test_no_body(bookstore, service):
    assert "request" not in design(bookstore, service)["api"]


def test_query_params_only_on_lists(bookstore):
    params = design(bookstore, "list_books")["api"]["query_params"]
    assert list(params) == ["author", "status", "page", "size"]
    assert params["author"] == {"filters": "author", "type": "text"}  # a filter is optional
    assert params["size"] == {"type": "integer", "default": 20, "min": 1, "max": 100}
    assert "query_params" not in design(bookstore, "get_book")["api"]


def test_item_shows_everything_and_a_list_shows_the_summary(bookstore):
    item = design(bookstore, "get_book")["api"]["response"]
    assert list(item["fields"]) == ["id", "isbn", "title", "author", "price", "status"]
    listed = design(bookstore, "list_books")["api"]["response"]
    assert listed["shape"] == "list"
    assert list(listed["fields"]) == ["id", "title", "author", "price", "status"]  # no isbn
    assert listed["includes"] == ["items", "paging", "applied_filters"]


def test_delete_has_no_content(bookstore):
    response = design(bookstore, "remove_review")["api"]["response"]
    assert response == {"status": 204, "shape": "none"}


def test_create_returns_its_location(bookstore):
    assert design(bookstore, "add_book")["api"]["response"]["headers"] == ["Location"]


# ── Errors (I9) ─────────────────────────────────────────────────────


def statuses(report, service):
    return [(e["status"], e["when"]) for e in design(report, service)["api"].get("errors", [])]


def test_errors(bookstore):
    assert statuses(bookstore, "add_book") == [
        (422, "invalid or missing fields"),
        (409, "another Book already has this isbn"),
    ]
    assert statuses(bookstore, "remove_review") == [
        (404, "no Book has this book_id"),
        (404, "no Review has this review_id"),
    ]
    assert (409, "Book can't move from its current stage to that one") in statuses(
        bookstore, "archive_book"
    )
    assert statuses(bookstore, "list_books") == []


# ── Dependents and delete (I11, I12) ────────────────────────────────


def test_delete_cascades_to_composition_children(project):
    edit(
        project / ".harness/domain.yaml",
        "  # community",
        "  remove_book:\n    description: Remove a book\n    examples: [Delete a book]\n"
        "    kind: delete\n    acts_on: Book\n\n  # community",
    )
    effects = design(validate_project(project), "remove_book")["delete_effects"]
    assert [(e["effect"], e["on"]) for e in effects] == [
        ("cascade", "Review"),
        ("remove_links", "collection_books"),
    ]
    assert effects[0]["means"] == "its Reviews are deleted with it"


def remove_publisher(project):
    edit(
        project / ".harness/domain.yaml",
        "  # community",
        "  remove_publisher:\n    description: Remove a publisher\n"
        "    kind: delete\n    acts_on: Publisher\n\n  # community",
    )


def test_delete_is_restricted_while_referenced(project):
    remove_publisher(project)
    result = design(validate_project(project), "remove_publisher")
    assert result["delete_effects"] == [
        {
            "effect": "restrict",
            "on": "Book",
            "means": "refused (409) while Books point at it",
            "rule": "reference-restrict",
        }
    ]
    assert {
        "status": 409,
        "when": "Books still point at this Publisher",
        "rule": "delete-restricted",
    } in (result["api"]["errors"])


def test_set_null_clears_references(project):
    remove_publisher(project)
    edit(project / ".harness/domain.yaml", "on_delete: restrict", "on_delete: set_null")
    result = design(validate_project(project), "remove_publisher")
    assert [e["effect"] for e in result["delete_effects"]] == ["set_null"]
    assert all(e["status"] != 409 for e in result["api"].get("errors", []))


def test_on_delete_only_for_one_to_many_aggregations(project):
    edit(
        project / ".harness/domain.yaml",
        "    child: Review\n    cardinality: one_to_many",
        "    child: Review\n    cardinality: one_to_many\n    on_delete: set_null",
    )
    assert any(
        "on_delete and required are for one-to-many" in m for m in errors(validate_project(project))
    )


def test_set_null_of_a_required_reference(project):
    edit(
        project / ".harness/domain.yaml",
        "on_delete: restrict",
        "on_delete: set_null\n    required: true",
    )
    assert any(
        "clears a reference that is required" in m for m in errors(validate_project(project))
    )


# ── Summary checks (I10) ────────────────────────────────────────────


def test_filter_must_be_in_the_summary(project):
    edit(
        project / ".harness/domain.yaml",
        "summary: [title, author, price]",
        "summary: [title, price]",
    )
    [error] = validate_project(project).errors
    assert error.path == ("services", "list_books")
    assert error.message.startswith("filter_not_in_summary: it filters by author")


def test_summary_names_real_attributes(project):
    edit(
        project / ".harness/domain.yaml",
        "summary: [title, author, price]",
        "summary: [title, colour]",
    )
    assert any(
        "summary names unknown attribute(s): colour" in m for m in errors(validate_project(project))
    )


# ── Overriding conventions and rules (I19–I22) ──────────────────────


def test_override_path_conventions(project):
    factory_override(project).write_text("api:\n  paths: { plural: false, nest_children: false }\n")
    report = validate_project(project)
    assert report.ok, errors(report)
    assert design(report, "get_book")["api"]["path"] == "/book/{book_id}"
    assert design(report, "add_review")["api"]["path"] == "/review"
    assert design(report, "get_book")["sources"]["api.path"] == "rule item-path"


def test_transition_as_a_field_in_the_body(project):
    factory_override(project).write_text("api:\n  paths: { transition: field_in_body }\n")
    report = validate_project(project)
    # archiving is now a PATCH of the book itself, like changing its price: one endpoint
    assert errors(report) == [
        "route_clash: PATCH /books/{book_id} is also the path of change_book_price"
    ]
    archive = design(report, "archive_book")["api"]
    assert archive["path"] == "/books/{book_id}"
    assert list(archive["request"]["fields"]) == ["status"]


def test_override_paging_names(project):
    factory_override(project).write_text("api:\n  paging: { page: offset, size: limit }\n")
    params = design(validate_project(project), "list_books")["api"]["query_params"]
    assert list(params) == ["author", "status", "offset", "limit"]


def test_bad_convention_points_at_the_override(project):
    path = factory_override(project)
    path.write_text("api:\n  paths: { case: camel }\n")
    [error] = validate_project(project).errors
    assert error.file == path
    assert error.path[:2] == ("api", "paths")


def test_remove_a_rule(project):
    factory_override(project).write_text("remove_rules: [lists-are-paged]\n")
    report = validate_project(project)
    assert report.ok, errors(report)
    result = design(report, "list_books")
    assert list(result["api"]["query_params"]) == ["author", "status"]  # no paging
    assert "pagination" not in [c["name"] for c in result["components"]]


def test_remove_an_unknown_rule(project):
    factory_override(project).write_text("remove_rules: [lists-are-pagd]\n")
    assert errors(validate_project(project)) == ["no rule 'lists-are-pagd' to remove"]


def test_service_override_path(project):
    edit(
        project / ".harness/domain.yaml",
        "    filters: [author, status]",
        "    filters: [author, status]\n    api: { path: /catalog/items }",
    )
    result = design(validate_project(project), "list_books")
    assert result["api"]["path"] == "/catalog/items"
    assert result["sources"]["api.path"] == "service override (domain.yaml)"


def test_route_clash(project):
    edit(
        project / ".harness/domain.yaml",
        "    filters: [author, status]",
        '    filters: [author, status]\n    api: { path: "/books/{book_id}" }',
    )
    report = validate_project(project)
    assert any(
        m.startswith("route_clash: GET /books/{book_id} is also the path of")
        for m in errors(report)
    )


def test_transition_needs_a_verb_name(project):
    (project / ".harness/evals/classify.yaml").unlink()  # its cases name archive_book
    edit(project / ".harness/domain.yaml", "  archive_book:", "  book_archiving:")
    [error] = validate_project(project).errors
    assert error.message.startswith("service_without_path")


# ── The harness version a project asks for (I25) ────────────────────


def test_version_outside_the_range_is_a_warning(project):
    edit(project / "harness.yaml", 'harness: ">=0.0.1"', 'harness: ">=99"')
    report = validate_project(project)
    assert report.ok
    assert any("works with harness >=99" in p.message for p in report.warnings)


def test_version_range_must_be_valid(project):
    edit(project / "harness.yaml", 'harness: ">=0.0.1"', 'harness: "newest"')
    assert any("is not a version range" in m for m in errors(validate_project(project)))


# ── Commands ────────────────────────────────────────────────────────


def test_cli_resolve(project):
    result = runner.invoke(app, ["resolve", "--service", "add_review", "-p", str(project)])
    assert result.exit_code == 0, result.output
    assert "path: /books/{book_id}/reviews" in result.output
    assert "api.path: rule collection-path" in result.output


def test_cli_resolve_unknown_service(project):
    result = runner.invoke(app, ["resolve", "--service", "fly_book", "-p", str(project)])
    assert result.exit_code == 1
    assert "unknown service 'fly_book'" in result.output


def test_unknown_service_raises(bookstore):
    with pytest.raises(ResolveError):
        design_service(bookstore, "fly_book")


def test_cli_override_starts_a_file(project):
    result = runner.invoke(app, ["override", "kinds.delete", "-p", str(project)])
    assert result.exit_code == 0, result.output
    text = factory_override(project).read_text()
    assert "kinds:\n  delete:\n    description: Remove a {target}" in text
    assert validate_project(project).ok
    again = runner.invoke(app, ["override", "rules.lists-are-paged", "-p", str(project)])
    assert "# Add this to .harness/factories/crud-api/technical.yaml" in again.output
    assert "- id: lists-are-paged" in again.output


def test_cli_override_unknown_entry(project):
    result = runner.invoke(app, ["override", "kinds.fly", "-p", str(project)])
    assert result.exit_code == 1
    assert "the crud-api factory has no kinds.fly" in result.output
