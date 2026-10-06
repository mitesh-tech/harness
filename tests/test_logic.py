import pytest
from conftest import BOOKSTORE, edit

from harness.knowledge.validate import validate_project
from harness.logic import engine as engine_module
from harness.logic.compile import atom, compile_knowledge, snake
from harness.logic.engine import Engine, PrologUnavailable


@pytest.fixture(scope="module")
def bookstore():
    report = validate_project(BOOKSTORE, logic=False)
    assert report.ok
    return report


@pytest.fixture
def engine(bookstore) -> Engine:
    return Engine(bookstore.domain, bookstore.technical)


def values(answers, name):
    return sorted(a[name] for a in answers)


# ── Compiling ───────────────────────────────────────────────────────


def test_names_become_prolog_atoms():
    assert snake("Book") == "book"
    assert snake("BookAuthor") == "book_author"
    assert atom("add_book") == "add_book"
    assert atom("create-validates-body") == "'create-validates-body'"
    assert atom("it's") == "'it\\'s'"


def test_compiled_facts(bookstore):
    program = compile_knowledge(bookstore.domain, bookstore.technical)
    for fact in (
        "entity(book, catalogue).",
        "identifier(book, id).",
        "lifecycle(book, status, active).",
        "association(book_reviews, composition, book, review, one_to_many).",
        "within(add_review, book).",
        "uses(add_book_to_collection, collection_books).",
        "kind_method(create, post).",
    ):
        assert fact in program


def test_each_rule_carries_its_id(bookstore):
    program = compile_knowledge(bookstore.domain, bookstore.technical)
    assert (
        "component(S, body_validation, 'create-validates-body') :- service(S), kind(S, create)."
        in program
    )


# ── Asking the engine ───────────────────────────────────────────────


def test_core_decisions(engine):
    assert engine.query("method(add_book, M)") == [{"M": "post"}]
    assert engine.query("status(remove_review, C)") == [{"C": 204}]
    assert engine.query("persistence(archive_book, P)") == [{"P": "update_row"}]


def test_components_with_rule_ids(engine):
    answers = engine.query("component(add_review, C, R)")
    assert {(a["C"], a["R"]) for a in answers} == {
        ("serialization", "always-serialize"),
        ("body_validation", "create-validates-body"),
        ("parent_exists_check", "composition-nested"),
    }


def test_each_condition_type(engine):
    # kind_in
    assert values(engine.query("route_param(S, identifier, _)"), "S") == [
        "archive_book",
        "change_book_price",
        "get_book",
        "remove_review",
        "replace_book",
    ]
    # has: filters, fired once even though list_books has two filters
    assert engine.query("response_includes(list_books, X, _)") == [
        {"X": "applied_filters"},
        {"X": "paging"},
    ]
    # cardinality
    assert engine.query("persistence_target(add_book_to_collection, T, _)") == [{"T": "link_table"}]
    assert engine.query("persistence_target(set_book_publisher, T, _)") == [{"T": "reference"}]
    # has: within
    assert engine.query("route(add_review, R, _)") == [{"R": "nested"}]


def test_askable_fact_switches_a_rule_on(engine):
    assert not engine.ask("component(add_book, auth_check, _)")
    engine.ask("assertz(fact(add_book, requires_login))")
    assert engine.query("component(add_book, auth_check, R)") == [{"R": "login-needs-auth"}]


def test_engines_do_not_share_facts(bookstore, engine):
    engine.ask("assertz(fact(add_book, requires_login))")
    fresh = Engine(bookstore.domain, bookstore.technical)
    assert not fresh.ask("component(add_book, auth_check, _)")


def test_inputs_are_passed_as_values(engine):
    answers = engine.query("atom_string(S, N), method(S, M)", {"N": "archive_book"})
    assert answers == [{"S": "archive_book", "M": "patch"}]


# ── Logic checks ────────────────────────────────────────────────────


def test_bookstore_has_no_logic_problems(engine):
    assert engine.problems() == []


def logic_errors(project):
    report = validate_project(project)
    return [p for p in report.errors if p.message.split(":")[0] in engine_module.MESSAGES]


def test_composition_child_created_outside_parent(project):
    edit(
        project / ".harness/domain.yaml",
        "acts_on: Review\n    within: Book\n\n  list_reviews",
        "acts_on: Review\n\n  list_reviews",
    )
    [error] = logic_errors(project)
    assert error.path == ("services", "add_review")
    assert error.message == (
        "composition_child_outside_parent: Review can only be created within Book; "
        "add `within: Book`"
    )
    assert error.line is not None


def test_delete_parent_without_cascade(project):
    edit(
        project / ".harness/domain.yaml",
        "  # community",
        "  remove_book:\n    description: Remove a book\n    examples: [Delete a book]\n"
        "    kind: delete\n    acts_on: Book\n\n  # community",
    )
    (project / ".harness/technical.yaml").write_text(
        "rules:\n"
        "  - id: composition-cascade\n"
        "    when: { kind: delete, parent_of_composition: true }\n"
        "    then: { components: [serialization] }\n"
    )
    [error] = logic_errors(project)
    assert error.path == ("services", "remove_book")
    assert "delete_parent_without_cascade" in error.message


def test_cascade_rule_prevents_the_problem(project):
    edit(
        project / ".harness/domain.yaml",
        "  # community",
        "  remove_book:\n    description: Remove a book\n    examples: [Delete a book]\n"
        "    kind: delete\n    acts_on: Book\n\n  # community",
    )
    assert logic_errors(project) == []


def test_composition_cycle(project):
    edit(
        project / ".harness/domain.yaml",
        "  collection_books:",
        "  review_books:\n    kind: composition\n    parent: Review\n    child: Book\n"
        "    cardinality: one_to_many\n\n  collection_books:",
    )
    errors = logic_errors(project)
    cycles = {e.path for e in errors if e.message.startswith("composition_cycle")}
    assert cycles == {("entities", "Book"), ("entities", "Review")}
    # Book is now also a composition child, so add_book must create it within Review
    assert any(e.path == ("services", "add_book") for e in errors)


def test_conflicting_decisions(project):
    (project / ".harness/technical.yaml").write_text(
        "rules:\n"
        "  - id: transition-bad-request\n"
        "    when: { kind: transition }\n"
        "    then: { error_status: 400 }\n"
    )
    [error] = logic_errors(project)
    assert error.path == ("services", "archive_book")
    assert error.message == (
        "conflicting_decisions: rules disagree on error_status: "
        "400 (rule transition-bad-request) vs 409 (rule transition-guard)"
    )


# ── Without Prolog ──────────────────────────────────────────────────


@pytest.fixture
def no_prolog(monkeypatch):
    def unavailable():
        raise PrologUnavailable("SWI-Prolog is not available (test)")

    monkeypatch.setattr(engine_module, "_janus", unavailable)


def test_missing_prolog_is_a_warning(no_prolog):
    report = validate_project(BOOKSTORE)
    assert report.ok
    assert report.logic == "skipped"
    assert any("logic checks skipped" in w.message for w in report.warnings)


def test_missing_prolog_fails_when_strict(no_prolog):
    report = validate_project(BOOKSTORE, strict=True)
    assert not report.ok
    assert "logic checks skipped" in report.errors[0].message
