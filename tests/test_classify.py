from pathlib import Path

import pytest
from conftest import BOOKSTORE, SEMIF_URL, edit, factory_override
from typer.testing import CliRunner

import harness.cli
from harness.classify.questions import OPTION_SOURCES, Context
from harness.classify.walk import (
    FACTORY_QUESTION,
    _name_new_service,
    classify,
    named_entities,
    plural,
    render,
    step_options,
    target_words,
)
from harness.cli import app
from harness.evals import check_case
from harness.knowledge.validate import validate_project
from harness.logic.engine import Engine
from harness.semif.client import NONE, NONE_OPTION, DecisionModelError, Option, Question
from harness.semif.http import SemIfHttp
from harness.trace import Trace

CASES = validate_project(BOOKSTORE, logic=False).evals.cases
runner = CliRunner()


@pytest.fixture(scope="module")
def bookstore():
    report = validate_project(BOOKSTORE, logic=False)
    assert report.ok
    return report


@pytest.fixture(scope="module")
def manifests(bookstore):
    """The factories the router offers: available and planned."""
    return {n: f.manifest for n, f in bookstore.factories.items()} | bookstore.planned


@pytest.fixture
def model(semif_client):
    return SemIfHttp(SEMIF_URL, client=semif_client)


def names(options):
    return [o.name for o in options]


# ── Options come from the knowledge base ────────────────────────────


@pytest.fixture(scope="module")
def ctx(bookstore):
    return Context(
        bookstore.domain, bookstore.technical, Engine(bookstore.domain, bookstore.technical)
    )


def options(ctx, source, **answers):
    return names(OPTION_SOURCES[source](ctx, answers))


def test_targets_are_entities_and_connections(ctx):
    assert options(ctx, "targets") == [
        "Book",
        "Publisher",
        "Review",
        "Collection",
        "collection_books",
        "book_publisher",
    ]  # a composition (book_reviews) is reached through its child, Review


def test_connection_text(ctx):
    [text] = [o.text() for o in OPTION_SOURCES["targets"](ctx, {}) if o.name == "collection_books"]
    assert (
        text == "Books placed in a curated collection (Relationship between Collection and Book)."
    )


def test_parent_of_a_composition_child(ctx):
    [parent] = OPTION_SOURCES["target_parent"](ctx, {"target": "Review"})
    assert (parent.name, parent.reason) == (
        "Book",
        "Review is part of a Book (composition book_reviews)",
    )
    assert options(ctx, "target_parent", target="Book") == []


def test_offered_kinds_come_from_prolog(ctx):
    assert options(ctx, "offered_kinds", operation="update", target="Book") == [
        "change",
        "replace",
        "transition",
    ]
    assert options(ctx, "offered_kinds", operation="update", target="Review") == [
        "change",
        "replace",
    ]
    [link] = OPTION_SOURCES["offered_kinds"](
        ctx, {"operation": "create", "target": "collection_books"}
    )
    assert (link.name, link.reason) == (
        "link",
        "the only kind of create that works on collection_books",
    )
    assert options(ctx, "offered_kinds", operation="read", target="collection_books") == []


def test_services_for_target_and_kind(ctx):
    assert options(ctx, "services_for_target_kind", target="Book", kind="create") == ["add_book"]
    assert options(ctx, "services_for_target_kind", target="collection_books", kind="link") == [
        "add_book_to_collection"
    ]
    assert options(ctx, "services_for_target_kind", target="Publisher", kind="create") == []


# ── Questions in domain language ────────────────────────────────────


def test_render_fills_in_the_domain_and_earlier_answers(bookstore):
    text = render(FACTORY_QUESTION, bookstore.domain, {})
    assert text.startswith("This task is for an online bookstore. ")
    assert "domain entities: Book, Publisher, Review, Collection." in text
    question = "What does the task do with {target}?"
    assert render(question, bookstore.domain, {"target": "Review"}) == (
        "What does the task do with Review?"
    )


def test_target_words_come_from_the_domain(bookstore):
    words = target_words(bookstore.domain, "Review")
    assert words["target"] == "Review" and words["targets"] == "Reviews"
    assert words["target_context"] == (
        "It is about Review: A reader's rating and comment about one book. "
        "A Review is part of a Book. A Review keeps: rating (integer, 1 to 5), comment (text)."
    )
    assert (
        "It moves through stages: active, archived."
        in target_words(bookstore.domain, "Book")["target_context"]
    )
    link = target_words(bookstore.domain, "collection_books")
    assert link["target"] == "link between Collection and Book"


def test_options_are_worded_for_the_target(ctx):
    spec = ctx.technical.classification.steps["operation"]
    texts = {o.name: o.description for o in step_options(spec, ctx, {"target": "Review"})}
    assert texts["create"] == "Make a new Review"
    assert texts["read"] == "Look at Reviews without changing them"


@pytest.mark.parametrize(
    ("task", "named"),
    [
        ("Show all books by an author", ["Book"]),  # plural counts
        ("Archive a book", ["Book"]),
        ("Set the publisher of a book", ["Book", "Publisher"]),  # two: no help
        ("Add reviews to a bookshelf", ["Review"]),  # whole words only
        ("Let customers save a wishlist", []),
    ],
)
def test_named_entities(bookstore, task, named):
    assert named_entities(task, bookstore.domain) == named


# ── Naming a new service ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("word", "many"),
    [
        ("book", "books"),
        ("category", "categories"),
        ("address", "addresses"),
        ("day", "days"),
        ("box", "boxes"),
    ],
)
def test_plural(word, many):
    assert plural(word) == many


@pytest.mark.parametrize(
    ("target", "kind", "name"),
    [
        ("Publisher", "create", "add_publisher"),
        ("Publisher", "query", "list_publishers"),
        ("Book", "delete", "remove_book"),
        ("collection_books", "link", "add_book_to_collection"),
        ("Book", "transition", None),  # the verb is not known yet
        ("Book", "change", None),  # nor the attribute
    ],
)
def test_new_service_names(bookstore, target, kind, name):
    trace = Trace("a task", 0.8, answers={"target": target, "kind": kind})
    _name_new_service(trace, bookstore.domain, bookstore.technical)
    [event] = trace.events
    assert (event.step, event.by) == ("name", "rule")
    assert trace.answers.get("name") == name
    assert event.answer == (name or "open")


def test_option_text_combines_description_examples_and_not():
    option = Option("update", "Change some details", ("edit", "change"), "Not for replace")
    assert option.text() == "Change some details. Examples: edit; change. Not for replace"


# ── The HTTP adapter ────────────────────────────────────────────────


def test_choice_request_shape():
    question = Question("area", "Which area?", (Option("catalogue", "Books"), NONE_OPTION))
    body = SemIfHttp.choice_request("Add a new book", question)
    assert body == {
        "state": "Add a new book",
        "model": "semif",
        "questions": {
            "area": {
                "type": "choice",
                "instructions": "Which area?",
                "criteria": {"catalogue": "Books.", NONE: NONE_OPTION.description},
            }
        },
    }


def test_unreachable_server_has_a_clear_message():
    model = SemIfHttp("http://127.0.0.1:9/v1/systemone", timeout=2)
    question = Question("area", "Which area?", (Option("a", "A"), NONE_OPTION))
    with pytest.raises(DecisionModelError, match="not reachable.*semif-serve"):
        model.choose("task", question)


# ── Classifying real tasks (recorded SemIf answers) ─────────────────


@pytest.mark.parametrize("case", CASES, ids=[c.task for c in CASES])
def test_classification(bookstore, manifests, model, case):
    trace = classify(
        case.task, bookstore.domain, bookstore.technical, model, 0.8, manifests, bookstore.data
    )
    result = check_case(case, trace)
    assert result.passed, _explain(result)
    # every step is recorded with its question, options, answer and confidence
    for event in trace.events:
        assert event.stage == "classify"
        if event.by == "rule":
            assert event.reason  # every rule decision says why
            continue
        assert event.options[-1] == NONE and event.answer in event.options
        assert 0.0 <= event.confidence <= 1.0
        assert 0.0 <= event.margin <= 1.0
        if event.confidence < 0.8 and event.accepted:
            assert event.reason  # accepted below the threshold only with a reason


def test_strict_threshold_turns_answers_into_unsure(bookstore, manifests, model):
    task = "Readers should be able to rate a book from 1 to 5 through a new endpoint"
    trace = classify(
        task, bookstore.domain, bookstore.technical, model, 1.0, manifests, bookstore.data
    )
    assert trace.outcome == "unsure"
    assert not trace.events[-1].accepted


def _explain(result) -> str:
    lines = [f"task: {result.case.task}  →  {result.trace.outcome}"]
    lines.append(f"first wrong step: {result.first_wrong}")
    lines += result.failures
    for e in result.trace.events:
        top = sorted(e.probabilities.items(), key=lambda kv: -kv[1])[:3]
        lines.append(f"  {e.step}: {e.answer} ({e.confidence:.2f}) top: {top}")
    return "\n".join(lines)


# ── The command ─────────────────────────────────────────────────────


@pytest.fixture
def cli_model(monkeypatch, semif_client):
    monkeypatch.setattr(
        harness.cli, "make_model", lambda report: SemIfHttp(SEMIF_URL, client=semif_client)
    )


def test_cli_classify_matched(cli_model):
    task = "Add a new book through an endpoint"
    result = runner.invoke(app, ["classify", task, "-p", str(BOOKSTORE)])
    assert result.exit_code == 0, result.output
    assert "Outcome   MATCHED: Book › bookstore_db › create › create › add_book" in result.output
    assert "data         rule   bookstore_db" in result.output
    assert "kind         rule   create" in result.output


def test_cli_classify_needs_a_person(cli_model):
    result = runner.invoke(
        app, ["classify", "Stop storing comments on reviews", "-p", str(BOOKSTORE)]
    )
    assert result.exit_code == 2, result.output
    assert "domain-model is planned but not available yet" in result.output
    assert "Outcome   UNSUPPORTED" in result.output


def test_cli_classify_json(cli_model):
    task = "Add a new book through an endpoint"
    result = runner.invoke(app, ["classify", task, "-p", str(BOOKSTORE), "--json"])
    assert result.exit_code == 0
    data = __import__("json").loads(result.output)
    assert data["outcome"] == "matched"
    assert data["answers"] == {
        "factory": "crud-api",
        "target": "Book",
        "data": "bookstore_db",
        "operation": "create",
        "kind": "create",
        "service": "add_book",
    }
    assert [e["step"] for e in data["events"]] == [
        "factory",
        "target",
        "parent",
        "data",
        "operation",
        "kind",
        "service",
    ]
    assert data["semif_calls"] == 4


def test_cli_classify_trace(cli_model, tmp_path):
    out = tmp_path / "trace.jsonl"
    result = runner.invoke(
        app,
        [
            "classify",
            "Let staff add a new publisher through a new endpoint",
            "-p",
            str(BOOKSTORE),
            "--trace",
            "--trace-out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Which kind of software work does the task need?" in result.output
    assert "name         rule   add_publisher" in result.output
    assert "(3 SemIf calls," in result.output
    trace = Trace.from_jsonl(out.read_text())
    assert trace.outcome == "new_service"
    assert trace.answers["name"] == "add_publisher"
    assert trace.path == [
        "factory",
        "target",
        "parent",
        "data",
        "operation",
        "kind",
        "service",
        "name",
    ]
    assert trace.url == SEMIF_URL


def test_cli_eval_classify(cli_model):
    result = runner.invoke(app, ["eval", "classify", "-p", str(BOOKSTORE)])
    assert result.exit_code == 0, result.output
    assert "23/23 cases passed" in result.output


def test_cli_eval_classify_reports_the_first_wrong_step(cli_model, project):
    edit(
        project / ".harness/evals/classify.yaml",
        "answers: { target: Review, parent: Book, operation: create, service: add_review }",
        "answers: { target: Book, parent: Book, operation: create, service: add_review }",
    )
    result = runner.invoke(app, ["eval", "classify", "-p", str(project)])
    assert result.exit_code == 1
    assert "✗ Readers should be able to rate a book from 1 to 5 through a new endpoint" in (
        result.output
    )
    assert "first wrong step: target: expected Book, got Review (" in result.output
    assert "22/23 cases passed" in result.output


def test_cli_classify_semif_down(monkeypatch):
    monkeypatch.setattr(
        harness.cli,
        "make_model",
        lambda report: SemIfHttp("http://127.0.0.1:9/v1/systemone", timeout=2),
    )
    result = runner.invoke(app, ["classify", "Add a new book", "-p", str(BOOKSTORE)])
    assert result.exit_code == 1
    assert "not reachable" in result.output


def test_cli_semif_check(cli_model):
    result = runner.invoke(app, ["semif", "check", "-p", str(BOOKSTORE)])
    assert result.exit_code == 0, result.output
    assert "✓ SemIf reachable" in result.output


# ── Flow checks in validate ─────────────────────────────────────────


def override_flow(project: Path, steps_yaml: str, start="target") -> None:
    # Bookstore's eval cases are written for the real flow; this test flow has other steps
    (project / ".harness/evals/classify.yaml").unlink()
    factory_override(project).write_text(
        "classification:\n"
        f"  start: {start}\n"
        "  low_confidence: unsure\n"
        "  outcomes: [matched, unsure, unsupported]\n"
        "  steps:\n" + steps_yaml
    )


def test_flow_with_unknown_target(project):
    override_flow(
        project,
        "    target: { question: Q, options: targets, on_answer: servce, on_none: unsure }\n",
    )
    errors = validate_project(project).errors
    assert any("'servce' is neither a step nor an outcome" in e.message for e in errors)


def test_flow_loop_found_by_prolog(project):
    override_flow(
        project,
        "    target: { question: Q, options: targets, on_answer: operation, on_none: operation }\n"
        "    operation: { question: Q, options: operations, on_answer: target, on_none: target }\n",
    )
    codes = {e.message.split(":")[0] for e in validate_project(project).errors}
    assert "flow_cycle" in codes


def test_flow_unreachable_step(project):
    override_flow(
        project,
        "    target: { question: Q, options: targets, on_answer: matched, on_none: unsure }\n"
        "    operation: { question: Q, options: operations,\n"
        "                 on_answer: matched, on_none: unsure }\n",
    )
    [error] = validate_project(project).errors
    assert error.message.startswith("flow_unreachable")
    assert error.path == ("classification", "steps", "operation")


def test_on_empty_is_an_edge(project):
    # 'operation' is only reachable through on_empty
    override_flow(
        project,
        "    target: { question: Q, options: targets, on_answer: matched, on_none: unsure,\n"
        "              on_empty: operation }\n"
        "    operation: { question: Q, options: operations,\n"
        "                 on_answer: matched, on_none: unsure }\n",
    )
    assert validate_project(project).ok


def test_branch_must_cover_every_choice(project):
    override_flow(
        project,
        "    target:\n"
        "      question: Q\n"
        "      choices: { a: { description: A }, b: { description: B } }\n"
        "      on_answer: { by: answer, cases: { a: matched, c: matched } }\n"
        "      on_none: unsure\n",
    )
    messages = [e.message for e in validate_project(project).errors]
    assert "no case for choice(s): b" in messages
    assert "case(s) c are not choices of this step" in messages


def test_step_needs_options_or_choices(project):
    override_flow(project, "    target: { question: Q, on_answer: matched, on_none: unsure }\n")
    assert any(
        "either 'options' or 'choices'" in e.message for e in validate_project(project).errors
    )


def test_flow_needs_unsupported_and_no_platform_step_names(project):
    (project / ".harness/evals/classify.yaml").unlink()
    factory_override(project).write_text(
        "classification:\n  start: factory\n  low_confidence: unsure\n"
        "  outcomes: [matched, unsure]\n  steps:\n"
        "    factory: { question: Q, options: targets, on_answer: matched, on_none: unsure }\n"
    )
    messages = [e.message for e in validate_project(project).errors]
    assert "'factory' is a step the platform adds; rename it" in messages
    assert any(m.startswith("'unsupported' must be an outcome") for m in messages)
