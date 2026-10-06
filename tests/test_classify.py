from pathlib import Path

import pytest
import yaml
from conftest import BOOKSTORE, SEMIF_URL, edit
from typer.testing import CliRunner

import harness.cli
from harness.classify.questions import OPTION_SOURCES
from harness.classify.walk import classify
from harness.cli import app
from harness.knowledge.validate import validate_project
from harness.semif.client import NONE, NONE_OPTION, DecisionModelError, Option, Question
from harness.semif.http import SemIfHttp

TASKS = yaml.safe_load((Path(__file__).parent / "classify_tasks.yaml").read_text())
runner = CliRunner()


@pytest.fixture(scope="module")
def bookstore():
    report = validate_project(BOOKSTORE, logic=False)
    assert report.ok
    return report


@pytest.fixture
def model(semif_client):
    return SemIfHttp(SEMIF_URL, client=semif_client)


def names(options):
    return [o.name for o in options]


# ── Options come from the knowledge base ────────────────────────────


def test_option_sources(bookstore):
    d, t = bookstore.domain, bookstore.technical
    assert names(OPTION_SOURCES["areas"](d, t, {})) == ["catalogue", "community", "curation"]
    assert names(OPTION_SOURCES["services_in_area"](d, t, {"area": "catalogue"})) == [
        "add_book",
        "list_books",
        "get_book",
        "change_book_price",
        "replace_book",
        "archive_book",
        "set_book_publisher",
    ]
    assert names(OPTION_SOURCES["kinds"](d, t, {})) == list(t.kinds)
    assert names(OPTION_SOURCES["entities_in_area"](d, t, {"area": "catalogue"})) == [
        "Book",
        "Publisher",
    ]
    assert names(OPTION_SOURCES["aggregations_in_area"](d, t, {"area": "curation"})) == [
        "collection_books"
    ]


def test_aggregation_options_never_offer_compositions(bookstore):
    d, t = bookstore.domain, bookstore.technical
    for area in d.areas:
        assert "book_reviews" not in names(
            OPTION_SOURCES["aggregations_in_area"](d, t, {"area": area})
        )


def test_option_text_combines_description_examples_and_not():
    option = Option("update", "Change some details", ("edit", "change"), "Not for replace")
    assert option.text() == "Change some details Examples: edit; change. Not for replace"


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
                "criteria": {"catalogue": "Books", NONE: NONE_OPTION.description},
            }
        },
    }


def test_unreachable_server_has_a_clear_message():
    model = SemIfHttp("http://127.0.0.1:9/v1/systemone", timeout=2)
    question = Question("area", "Which area?", (Option("a", "A"), NONE_OPTION))
    with pytest.raises(DecisionModelError, match="not reachable.*semif-serve"):
        model.choose("task", question)


# ── Classifying real tasks (recorded SemIf answers) ─────────────────


@pytest.mark.parametrize("case", TASKS, ids=[c["task"] for c in TASKS])
def test_classification(bookstore, model, case):
    result = classify(case["task"], bookstore.domain, bookstore.technical, model, 0.8)
    expect = dict(case["expect"])
    outcomes = expect.pop("outcome")
    outcomes = outcomes if isinstance(outcomes, list) else [outcomes]
    assert result.outcome in outcomes, _explain(result)
    for key, value in expect.items():
        assert result.answers.get(key) == value, _explain(result)
    # every step is recorded with its question, options, answer and confidence
    for step in result.steps:
        assert step.options[-1] == NONE and step.answer in step.options
        assert 0.0 <= step.confidence <= 1.0
        assert step.accepted == (step.confidence >= 0.8)


def test_strict_threshold_turns_answers_into_unsure(bookstore, model):
    result = classify("Add a new book", bookstore.domain, bookstore.technical, model, 1.0)
    assert result.outcome == "unsure"
    assert [s.id for s in result.steps] == ["area"]


def _explain(result) -> str:
    lines = [f"task: {result.task}  →  {result.outcome}"]
    for s in result.steps:
        top = sorted(s.probabilities.items(), key=lambda kv: -kv[1])[:3]
        lines.append(f"  {s.id}: {s.answer} ({s.confidence:.2f}) top: {top}")
    return "\n".join(lines)


# ── The command ─────────────────────────────────────────────────────


@pytest.fixture
def cli_model(monkeypatch, semif_client):
    monkeypatch.setattr(
        harness.cli, "make_model", lambda report: SemIfHttp(SEMIF_URL, client=semif_client)
    )


def test_cli_classify_matched(cli_model):
    result = runner.invoke(app, ["classify", "Add a new book", "-p", str(BOOKSTORE)])
    assert result.exit_code == 0, result.output
    assert "Outcome   MATCHED: catalogue › add_book" in result.output


def test_cli_classify_needs_a_person(cli_model):
    result = runner.invoke(
        app, ["classify", "Manage shipping addresses for orders", "-p", str(BOOKSTORE)]
    )
    assert result.exit_code == 2, result.output
    assert "a person needs to decide" in result.output or "new entity" in result.output


def test_cli_classify_json(cli_model):
    result = runner.invoke(app, ["classify", "Add a new book", "-p", str(BOOKSTORE), "--json"])
    assert result.exit_code == 0
    data = __import__("json").loads(result.output)
    assert data["outcome"] == "matched"
    assert data["answers"] == {"area": "catalogue", "service": "add_book"}


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


def override_flow(project: Path, steps_yaml: str, start="area") -> None:
    (project / ".harness/technical.yaml").write_text(
        "classification:\n"
        f"  start: {start}\n"
        "  low_confidence: unsure\n"
        "  outcomes: [matched, unsure]\n"
        "  steps:\n" + steps_yaml
    )


def test_flow_with_unknown_target(project):
    override_flow(
        project, "    area: { question: Q, options: areas, on_answer: servce, on_none: unsure }\n"
    )
    errors = validate_project(project).errors
    assert any("'servce' is neither a step nor an outcome" in e.message for e in errors)


def test_flow_loop_and_dead_end_found_by_prolog(project):
    override_flow(
        project,
        "    area: { question: Q, options: areas, on_answer: kind, on_none: kind }\n"
        "    kind: { question: Q, options: kinds, on_answer: area, on_none: area }\n",
    )
    codes = {e.message.split(":")[0] for e in validate_project(project).errors}
    assert "flow_cycle" in codes


def test_flow_unreachable_step(project):
    override_flow(
        project,
        "    area: { question: Q, options: areas, on_answer: matched, on_none: unsure }\n"
        "    kind: { question: Q, options: kinds, on_answer: matched, on_none: unsure }\n",
    )
    [error] = validate_project(project).errors
    assert error.message.startswith("flow_unreachable")
    assert error.path == ("classification", "steps", "kind")


def test_flow_branch_must_cover_every_kind_target(project):
    edit(project / "harness.yaml", "knowledge: .harness", "knowledge: .harness")
    override_flow(
        project,
        "    area: { question: Q, options: kinds, on_none: unsure,\n"
        "            on_answer: { by: kind_target, cases: { entity: matched } } }\n",
    )
    errors = validate_project(project).errors
    assert any("no case for kind target(s): association" in e.message for e in errors)
