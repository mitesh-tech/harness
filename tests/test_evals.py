from pathlib import Path

import pytest
from conftest import BOOKSTORE, edit

from harness.evals import check_case, check_groups
from harness.knowledge.references import option_names
from harness.knowledge.schema import EvalCase, OptionSource
from harness.knowledge.validate import validate_project
from harness.trace import Event, Trace, margin


def event(step, answer, confidence=0.95, by="semif", accepted=None):
    return Event(
        stage="classify",
        step=step,
        by=by,
        answer=answer,
        accepted=confidence >= 0.8 if accepted is None else accepted,
        confidence=confidence,
        margin=confidence - 0.05,
    )


def trace(outcome, answers, *events) -> Trace:
    return Trace("a task", 0.8, events=list(events), outcome=outcome, answers=answers)


def case(**expect) -> EvalCase:
    return EvalCase.model_validate({"task": "a task", "expect": expect})


PUBLISHER = trace(
    "new_service",
    {"area": "catalogue", "kind": "create", "entity": "Publisher"},
    event("area", "catalogue"),
    event("service", "none"),
    event("kind", "create"),
    event("entity", "Publisher"),
)


# ── The trace ───────────────────────────────────────────────────────


def test_margin():
    assert margin({"a": 0.7, "b": 0.2, "none": 0.1}) == pytest.approx(0.5)
    assert margin({"a": 1.0}) == 1.0


def test_trace_round_trips_as_jsonl():
    text = PUBLISHER.to_jsonl()
    lines = text.splitlines()
    assert len(lines) == 6  # run, four events, result
    assert '"type": "run"' in lines[0] and '"type": "result"' in lines[-1]
    again = Trace.from_jsonl(text)
    assert again.events == PUBLISHER.events
    assert (again.outcome, again.answers, again.run_id) == (
        "new_service",
        PUBLISHER.answers,
        PUBLISHER.run_id,
    )


def test_semif_calls_count_only_semif():
    t = trace("matched", {}, event("area", "x"), event("kind", "create", by="rule"))
    assert t.semif_calls == 1
    assert t.path == ["area", "kind"]


# ── Checking one case ───────────────────────────────────────────────


def test_case_passes():
    result = check_case(
        case(
            outcome="new_service",
            answers={"entity": "Publisher"},
            path=["area", "service", "kind", "entity"],
            semif_calls=4,
        ),
        PUBLISHER,
    )
    assert result.passed and result.first_wrong is None


def test_one_of_several_outcomes():
    assert check_case(case(outcome=["unsure", "new_service"]), PUBLISHER).passed


def test_wrong_answer_names_the_first_wrong_step():
    result = check_case(
        case(outcome="new_service", answers={"kind": "link", "entity": "Collection"}),
        PUBLISHER,
    )
    assert result.first_wrong == "kind: expected link, got create (0.95, margin 0.90)"
    assert result.failures == [
        "answer at kind: expected link, got create",
        "answer at entity: expected Collection, got Publisher",
    ]


def test_answer_below_threshold_is_wrong():
    t = trace("unsure", {}, event("area", "community", confidence=0.6))
    result = check_case(case(outcome="matched", answers={"area": "community"}), t)
    assert (
        result.first_wrong
        == "area: expected community, got community (0.60, margin 0.55, below the threshold)"
    )


def test_path_mismatch():
    result = check_case(case(outcome="new_service", path=["area", "service"]), PUBLISHER)
    assert "path: expected area → service, got area → service → kind → entity" in result.failures
    assert result.first_wrong == "kind: expected step (none, the run should end)"


def test_path_too_short():
    t = trace("unsure", {}, event("area", "none"))
    result = check_case(case(outcome="unsure", path=["area", "service"]), t)
    assert result.first_wrong == "service: never reached (the run ended as unsure)"


def test_step_that_should_not_be_asked():
    result = check_case(case(outcome="new_service", not_asked=["kind"]), PUBLISHER)
    assert result.failures == ["asked kind, which should not be asked"]
    assert result.first_wrong == "kind: should not be asked"


def test_too_many_semif_calls():
    result = check_case(case(outcome="new_service", semif_calls=3), PUBLISHER)
    assert result.failures == ["SemIf calls: expected at most 3, got 4"]


def test_wrong_outcome_without_other_expectations():
    result = check_case(case(outcome="matched"), PUBLISHER)
    assert result.failures == ["outcome: expected matched, got new_service"]
    assert result.first_wrong == "entity: led to new_service, Publisher (0.95, margin 0.90)"


# ── Paraphrase groups ───────────────────────────────────────────────


def grouped(task, t):
    c = EvalCase.model_validate({"task": task, "group": "g", "expect": {"outcome": t.outcome}})
    return check_case(c, t)


def test_group_with_the_same_path_passes():
    assert check_groups([grouped("one", PUBLISHER), grouped("two", PUBLISHER)]) == []


def test_group_with_different_paths_fails():
    other = trace("unsure", {}, event("area", "none"))
    [failure] = check_groups([grouped("one", PUBLISHER), grouped("two", other)])
    assert failure.startswith("group g: paraphrases differ")
    assert '"two": area → unsure {}' in failure


# ── Eval cases in validate ──────────────────────────────────────────


def test_option_names_cover_every_source():
    report = validate_project(BOOKSTORE, logic=False)
    for source in OptionSource.__args__:
        assert option_names(source, report.domain, report.technical)


def test_bookstore_evals_load():
    report = validate_project(BOOKSTORE)
    assert len(report.evals.cases) == 8


def eval_errors(project: Path) -> list[str]:
    return [p.message for p in validate_project(project).problems if "evals" in str(p.file)]


def test_unknown_outcome_step_and_answer(project):
    path = project / ".harness/evals/classify.yaml"
    edit(path, "outcome: [unsure, no_entity]", "outcome: [unsure, lost]")
    edit(path, "not_asked: [kind]\n", "not_asked: [knd]\n")
    edit(path, "service: list_books }", "service: list_bookz }")
    assert eval_errors(project) == [
        "'list_bookz' is not a possible answer for step 'service'",
        "unknown outcome 'lost' (outcomes: matched, new_service, no_entity, unsure)",
        "unknown step 'knd' (steps: area, association, entity, kind, service)",
    ]


def test_step_both_in_path_and_not_asked(project):
    edit(
        project / ".harness/evals/classify.yaml",
        "not_asked: [kind, entity]",
        "not_asked: [service]",
    )
    assert eval_errors(project) == ["step(s) service are both in path and in not_asked"]


def test_group_of_one_is_a_warning(project):
    edit(
        project / ".harness/evals/classify.yaml",
        '- task: "Add a new book"\n',
        '- task: "Add a new book"\n    group: add-book\n',
    )
    report = validate_project(project)
    assert report.ok
    assert any("only one case" in w.message for w in report.warnings)


def test_eval_line_numbers(project):
    edit(
        project / ".harness/evals/classify.yaml",
        "outcome: [unsure, no_entity]",
        "outcome: [unsure, lost]",
    )
    [problem] = [p for p in validate_project(project).errors]
    lines = (project / ".harness/evals/classify.yaml").read_text().splitlines()
    assert "lost" in lines[problem.line - 1]
