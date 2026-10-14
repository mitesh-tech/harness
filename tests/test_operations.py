import pytest
import yaml
from conftest import BOOKSTORE, edit, factory_override

from harness.knowledge.schema import Entity
from harness.knowledge.validate import validate_project
from harness.logic.compile import compile_knowledge
from harness.logic.engine import Engine


def messages(report) -> list[str]:
    return [p.message for p in report.errors]


@pytest.fixture(scope="module")
def bookstore():
    report = validate_project(BOOKSTORE, logic=False)
    assert report.ok
    return report


@pytest.fixture(scope="module")
def engine(bookstore) -> Engine:
    return Engine(bookstore.domain, bookstore.technical)


# ── Which kinds an operation offers for a target ───────────────────


@pytest.mark.parametrize(
    ("operation", "target", "kinds"),
    [
        ("create", "book", ["create"]),
        ("create", "collection_books", ["link"]),
        ("read", "book", ["query", "read_one"]),
        ("read", "collection_books", []),
        ("update", "book", ["replace", "transition", "update"]),
        ("update", "review", ["replace", "update"]),  # no lifecycle, so no transition
        ("update", "book_publisher", []),
        ("delete", "review", ["delete"]),
        ("delete", "collection_books", ["unlink"]),
        ("delete", "book_reviews", []),  # a composition is not a target
    ],
)
def test_offered_kinds(engine, operation, target, kinds):
    answers = engine.query(f"offered_kind({operation}, {target}, K, _)")
    assert sorted(a["K"] for a in answers) == kinds


def test_offered_kinds_carry_a_reason(engine):
    answers = engine.query("offered_kind(update, book, K, Why)")
    assert {a["K"]: a["Why"] for a in answers} == {
        "update": "target_type",
        "replace": "target_type",
        "transition": "has_lifecycle",
    }


def test_compiled_operation_facts(bookstore):
    program = compile_knowledge(bookstore.domain, bookstore.technical)
    for fact in (
        "operation(update).",
        "operation_kind(update, transition).",
        "kind_accepts(transition, moves).",
        "association_description(collection_books, 'Books placed in a curated collection').",
    ):
        assert fact in program


# ── Validation of operations ────────────────────────────────────────

READ = "    description: Look\n    examples: [view]\n"


def test_kind_in_no_operation(project):
    path = factory_override(project)
    path.write_text("operations:\n  delete:\n" + READ + "    kinds: [delete]\n")
    report = validate_project(project)
    [error] = report.errors
    assert error.message.startswith("kind 'unlink' is in no operation")
    assert error.path == ("kinds", "unlink")
    assert error.file.name == "technical.yaml" and error.file != path  # the factory's file


def test_kind_in_two_operations(project):
    factory_override(project).write_text(
        "operations:\n  read:\n" + READ + "    kinds: [read_one, query, delete]\n"
        "    question: One or many?\n"
    )
    [error] = validate_project(project).errors
    # operations keep the factory's order, so the clash shows on the later one
    assert error.message.startswith("kind 'delete' is already in operation 'read'")
    assert error.path == ("operations", "delete", "kinds")


def test_unknown_kind_in_operation(project):
    factory_override(project).write_text(
        "operations:\n  delete:\n" + READ + "    kinds: [delete, unlink, purge]\n"
    )
    assert "unknown kind 'purge'" in messages(validate_project(project))[0]


def test_question_needed_for_several_kinds(project):
    factory_override(project).write_text(
        "operations:\n  read:\n" + READ + "    kinds: [read_one, query]\n"
    )
    [error] = validate_project(project).errors
    assert "add a 'question'" in error.message
    assert error.path == ("operations", "read")


def test_override_replaces_an_operation(project):
    factory_override(project).write_text(
        "operations:\n  read:\n" + READ + "    kinds: [read_one, query]\n"
        "    question: One book or many?\n"
    )
    report = validate_project(project)
    assert report.ok, messages(report)
    assert report.technical.operations["read"].question == "One book or many?"
    assert report.technical.operations["create"].kinds == ["create", "link"]  # defaults kept


def test_too_many_targets(project):
    extra = "".join(
        f"  Thing{i}:\n    area: catalogue\n    description: Thing {i}\n    identifier: id\n"
        "    attributes:\n      id: { type: id, generated: true }\n\n"
        for i in range(10)
    )
    edit(project / ".harness/domain.yaml", "  Review:\n", extra + "  Review:\n")
    error = next(e for e in validate_project(project).errors if "targets" in e.message)
    assert error.message.startswith("14 entities and 2 aggregations give 16 targets")


# ── Logic checks on operations ──────────────────────────────────────


def logic_codes(report) -> list[str]:
    return [p.message.split(":")[0] for p in report.errors]


def test_service_kind_not_offered(project):
    # create now needs a lifecycle, which Review and Collection do not have
    kinds = validate_project(BOOKSTORE, logic=False).technical.kinds
    create = kinds["create"].model_dump(by_alias=True, exclude_none=True)
    create["accepts"] = ["moves"]
    factory_override(project).write_text(yaml.safe_dump({"kinds": {"create": create}}))
    report = validate_project(project)
    failing = {e.path for e in report.errors if "service_kind_not_offered" in e.message}
    assert failing == {("services", "add_review"), ("services", "create_collection")}


def test_operation_never_offered(bookstore):
    domain = bookstore.domain.model_copy(deep=True)
    for name in ("collection_books", "book_publisher"):
        del domain.associations[name]
    for name in ("set_book_publisher", "add_book_to_collection", "remove_book_from_collection"):
        del domain.services[name]
    technical = bookstore.technical.model_copy(deep=True)
    technical.operations["create"].kinds = ["create"]
    technical.operations["connect"] = technical.operations["create"].model_copy(
        update={"kinds": ["link"]}
    )
    found = {(p.name, p.code) for p in Engine(domain, technical).problems()}
    assert found == {("connect", "operation_never_offered")}


def test_target_name_clash(bookstore):
    domain = bookstore.domain.model_copy(deep=True)
    domain.entities["BookPublisher"] = Entity.model_validate(
        {
            "area": "catalogue",
            "description": "x",
            "identifier": "id",
            "attributes": {"id": {"type": "id", "generated": True}},
        }
    )
    problems = Engine(domain, bookstore.technical).problems()
    assert [(p.name, p.code) for p in problems] == [("BookPublisher", "target_name_clash")]
