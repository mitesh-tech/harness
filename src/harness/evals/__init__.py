"""Check traces against eval cases: the outcome, and how it was reached.

`check_case` compares one trace with what its case expects and names the first
step that went wrong. `check_groups` compares paraphrases: every case in a
group must take the same path and give the same answers.
"""

from dataclasses import dataclass, field

from harness.knowledge.schema import EvalCase
from harness.trace import Event, Trace


@dataclass
class CaseResult:
    case: EvalCase
    trace: Trace
    failures: list[str] = field(default_factory=list)
    first_wrong: str | None = None  # the first step that went wrong, explained

    @property
    def passed(self) -> bool:
        return not self.failures


def check_case(case: EvalCase, trace: Trace) -> CaseResult:
    expect = case.expect
    result = CaseResult(case, trace)
    failures = result.failures

    if trace.outcome not in expect.outcomes:
        wanted = " or ".join(expect.outcomes)
        failures.append(f"outcome: expected {wanted}, got {trace.outcome}")
    for step, answer in expect.answers.items():
        got = trace.answers.get(step)
        if got != answer:
            failures.append(f"answer at {step}: expected {answer}, got {got or 'nothing'}")
    if expect.path is not None and trace.path != expect.path:
        failures.append(f"path: expected {_arrow(expect.path)}, got {_arrow(trace.path)}")
    asked = sorted(set(expect.not_asked) & set(trace.path))
    if asked:
        failures.append(f"asked {', '.join(asked)}, which should not be asked")
    if expect.semif_calls is not None and trace.semif_calls > expect.semif_calls:
        failures.append(
            f"SemIf calls: expected at most {expect.semif_calls}, got {trace.semif_calls}"
        )

    if failures:
        result.first_wrong = _first_wrong(case, trace)
    return result


def _first_wrong(case: EvalCase, trace: Trace) -> str:
    """The earliest step where the trace leaves what the case expects."""
    expect = case.expect
    for index, event in enumerate(trace.events):
        if expect.path is not None:
            wanted = expect.path[index] if index < len(expect.path) else None
            if event.step != wanted:
                return f"{event.step}: expected step {wanted or '(none, the run should end)'}"
        if event.step in expect.not_asked:
            return f"{event.step}: should not be asked"
        wanted_answer = expect.answers.get(event.step)
        if wanted_answer is not None and (event.answer != wanted_answer or not event.accepted):
            return f"{event.step}: expected {wanted_answer}, got {_answer(event)}"
    if expect.path is not None and len(trace.path) < len(expect.path):
        return f"{expect.path[len(trace.path)]}: never reached (the run ended as {trace.outcome})"
    missing = [s for s in expect.answers if s not in trace.path]
    if missing:
        return f"{missing[0]}: never reached (the run ended as {trace.outcome})"
    if trace.events:
        return f"{trace.events[-1].step}: led to {trace.outcome}, {_answer(trace.events[-1])}"
    return f"no steps; the run ended as {trace.outcome}"


def check_groups(results: list[CaseResult]) -> list[str]:
    """Paraphrases in a group must take the same path and give the same answers."""
    groups: dict[str, list[CaseResult]] = {}
    for r in results:
        if r.case.group:
            groups.setdefault(r.case.group, []).append(r)
    failures = []
    for name, members in groups.items():
        shapes = {(tuple(r.trace.path), tuple(sorted(r.trace.answers.items()))) for r in members}
        if len(shapes) > 1:
            lines = [
                f'    "{r.case.task}": {_arrow(r.trace.path)} → {r.trace.outcome} {r.trace.answers}'
                for r in members
            ]
            failures.append(f"group {name}: paraphrases differ\n" + "\n".join(lines))
    return failures


def _answer(event: Event) -> str:
    if event.by != "semif":
        return f"{event.answer} (by {event.by})"
    status = "" if event.accepted else ", below the threshold"
    return f"{event.answer} ({event.confidence:.2f}, margin {event.margin:.2f}{status})"


def _arrow(steps: list[str]) -> str:
    return " → ".join(steps) or "(no steps)"
