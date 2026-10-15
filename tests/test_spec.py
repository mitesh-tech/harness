from pathlib import Path

import pytest
import yaml
from conftest import SEMIF_URL
from openapi_spec_validator import validate as validate_openapi
from typer.testing import CliRunner

import harness.cli
from harness.cli import app
from harness.knowledge.validate import validate_project
from harness.resolve import ResolveError, Spec, factory_design, resolve_service, resolve_task
from harness.semif.http import SemIfHttp
from harness.trace import Trace

EXPECTED = Path(__file__).parent / "specs"
runner = CliRunner()


@pytest.fixture
def report(project):
    """Bookstore in a temporary folder, so run folders don't land in the repo."""
    loaded = validate_project(project)
    assert loaded.ok
    return loaded


@pytest.fixture
def model(semif_client):
    return SemIfHttp(SEMIF_URL, client=semif_client)


def comparable(spec: dict) -> dict:
    """The spec without what changes every run."""
    return {k: v for k, v in spec.items() if k not in ("run", "created")}


def check_expected(name: str, spec: dict) -> None:
    """Compare with tests/specs/<name>.yaml; a missing file is written for review."""
    path = EXPECTED / f"{name}.yaml"
    text = yaml.safe_dump(comparable(spec), sort_keys=False, allow_unicode=True, width=100)
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        pytest.fail(f"wrote {path.name}; review it, then run the tests again")
    assert text == path.read_text(), f"spec differs from {path.name}"


# ── Specs for six cases (J1–J4) ─────────────────────────────────────


def test_matched_service_from_a_task(report, model):
    run = resolve_task(
        report, "Readers should be able to rate a book from 1 to 5 through a new endpoint", model
    )
    assert run.spec["service"] == {"name": "add_review", "new": False}
    check_expected("add_review", run.spec)


def test_new_service(report, model):
    run = resolve_task(report, "Let staff add a new publisher through a new endpoint", model)
    assert run.spec["service"] == {"name": "add_publisher", "new": True}
    assert run.spec["sources"]["service.name"] == ("rule: kind create names services add_{entity}")
    check_expected("add_publisher", run.spec)


def test_new_change_asks_which_details_change(report, model):
    task = "Readers should be able to change their rating of a book through a new endpoint"
    run = resolve_task(report, task, model)
    assert run.spec["details"]["changes"] == ["rating"]
    assert run.spec["service"]["name"] == "change_review_rating"  # one change: named by rule
    assert list(run.spec["api"]["request"]["fields"]) == ["rating"]
    check_expected("change_review_rating", run.spec)


def test_new_list_asks_which_filters(report, model):
    run = resolve_task(report, "List publishers by name through a new endpoint", model)
    assert run.spec["service"]["name"] == "list_publishers"
    check_expected("list_publishers", run.spec)


def test_new_delete_shows_its_effects(report, model):
    run = resolve_task(report, "Delete a book through a new endpoint", model)
    effects = [(e["effect"], e["affects"]) for e in run.spec["delete_effects"]]
    assert effects == [("cascade", "Review"), ("remove_links", "collection_books")]
    [cascade] = [c for c in run.spec["tests"] if c["rule"] == "composition-cascade"]
    assert cascade["name"] == "after the delete: its Reviews are deleted with it"
    assert cascade["expect"] == {"status": 204, "gone": ["review"]}  # checked from S6
    check_expected("remove_book", run.spec)


def test_new_transition_leaves_the_verb_open(report, model):
    """Classification of this task is unsure today; the design is tested from its answers."""
    trace = Trace(
        "Put an archived book back on sale through a new endpoint",
        0.8,
        outcome="new_service",
        answers={
            "factory": "crud-api",
            "target": "Book",
            "data": "bookstore_db",
            "operation": "update",
            "kind": "transition",
        },
    )
    design = factory_design(report.factories["crud-api"]).design_task(report, trace, model)
    body = design.to_dict()
    assert body["service"] == {"name": "<verb>_book", "new": True}
    assert body["api"]["path"] == "/books/{book_id}/<verb>"
    assert any(
        q.startswith("What should the new service be called?") for q in design.open_questions
    )
    check_expected("transition_book", {**body, "open_questions": design.open_questions})


# ── The run folder (J3) ─────────────────────────────────────────────


def test_run_folder(report, project):
    run = resolve_service(report, "list_books")
    runs = project / ".harness/runs"
    assert run.folder.parent == runs
    assert sorted(p.name for p in run.folder.iterdir()) == [
        "openapi.yaml",
        "spec.yaml",
        "trace.jsonl",
    ]
    assert (runs / ".gitignore").read_text().endswith("*\n")
    spec = yaml.safe_load((run.folder / "spec.yaml").read_text())
    Spec.model_validate(spec)
    assert spec["status"] == "draft"
    assert spec["tests"][0]["name"] == "lists Books"
    assert spec["tests"][0]["expect"] == {"status": 200}


def test_trace_records_where_each_value_came_from(report, model):
    task = "Readers should be able to change their rating of a book through a new endpoint"
    trace = resolve_task(report, task, model).trace
    resolve_events = [e for e in trace.events if e.stage == "resolve"]
    semif = [e for e in resolve_events if e.by == "semif"]
    assert [e.step for e in semif] == ["details.changes.rating", "details.changes.comment"]
    assert semif[0].answer == "yes"
    rules = {e.step: e.reason for e in resolve_events if e.by == "rule"}
    assert rules["api.path"] == "rule item-path"
    assert rules["details"] == "SemIf yes/no questions about the task"


def test_stopped_task_writes_nothing(report, model, project):
    run = resolve_task(report, "Send a weekly newsletter to customers", model)
    assert run.stopped and run.trace.outcome == "unsupported"
    assert not (project / ".harness/runs").exists()


def test_unknown_service(report):
    with pytest.raises(ResolveError, match="unknown service 'fly_book'"):
        resolve_service(report, "fly_book")


# ── OpenAPI (J5, J6) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "service",
    [
        "add_book",
        "list_books",
        "get_book",
        "change_book_price",
        "archive_book",
        "set_book_publisher",
        "remove_review",
        "add_book_to_collection",
    ],
)
def test_openapi_is_valid(report, service):
    run = resolve_service(report, service)
    document = yaml.safe_load((run.folder / "openapi.yaml").read_text())
    validate_openapi(document)
    assert list(document["paths"]) == [run.spec["api"]["path"]]


def test_openapi_list_and_errors(report):
    document = factory_design(report.factories["crud-api"]).to_openapi(
        resolve_service(report, "list_books").spec
    )
    operation = document["paths"]["/books"]["get"]
    assert [p["name"] for p in operation["parameters"]] == ["author", "status", "page", "size"]
    body = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert body["properties"]["items"]["items"] == {"$ref": "#/components/schemas/BookSummary"}
    summary = document["components"]["schemas"]["BookSummary"]["properties"]
    assert list(summary) == ["id", "title", "author", "price", "status"]


def test_openapi_errors_are_problems(report):
    document = factory_design(report.factories["crud-api"]).to_openapi(
        resolve_service(report, "remove_review").spec
    )
    responses = document["paths"]["/books/{book_id}/reviews/{review_id}"]["delete"]["responses"]
    assert responses["404"]["description"] == (
        "no Book has this book_id; no Review has this review_id"
    )
    assert "application/problem+json" in responses["404"]["content"]
    assert "content" not in responses["204"]


# ── The command (J7) ────────────────────────────────────────────────


def test_cli_resolve_service(project):
    result = runner.invoke(app, ["resolve", "--service", "add_review", "-p", str(project)])
    assert result.exit_code == 0, result.output
    assert "API       POST /books/{book_id}/reviews" in result.output
    assert "spec.yaml, openapi.yaml, trace.jsonl" in result.output


def test_cli_resolve_task(project, monkeypatch, semif_client):
    monkeypatch.setattr(
        harness.cli, "make_model", lambda report: SemIfHttp(SEMIF_URL, client=semif_client)
    )
    result = runner.invoke(
        app, ["resolve", "Let staff add a new publisher through a new endpoint", "-p", str(project)]
    )
    assert result.exit_code == 0, result.output
    assert "Service   add_publisher (new)" in result.output
    stopped = runner.invoke(
        app, ["resolve", "Send a weekly newsletter to customers", "-p", str(project)]
    )
    assert stopped.exit_code == 2
    assert "Outcome   UNSUPPORTED: nothing to design" in stopped.output


def test_cli_resolve_needs_a_task_or_a_service(project):
    result = runner.invoke(app, ["resolve", "-p", str(project)])
    assert result.exit_code == 1
    assert "Give a task, or an existing service" in result.output
