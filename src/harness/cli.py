"""The `harness` command-line entry point."""

import json
from pathlib import Path
from typing import Annotated

import typer

from harness import __version__
from harness.factories import FACTORIES_DIR
from harness.knowledge.load import Problem
from harness.knowledge.schema import SCHEMAS
from harness.knowledge.validate import Report, validate_project

app = typer.Typer(
    help="A model-agnostic AI harness for building backend APIs.",
    no_args_is_help=True,
)
schema_app = typer.Typer(help="Work with the schemas of the knowledge files.", no_args_is_help=True)
kb_app = typer.Typer(help="Work with a project's knowledge as Prolog.", no_args_is_help=True)
semif_app = typer.Typer(help="Work with the decision model (SemIf).", no_args_is_help=True)
eval_app = typer.Typer(
    help="Check the harness against a project's eval cases.", no_args_is_help=True
)
app.add_typer(schema_app, name="schema")
app.add_typer(kb_app, name="kb")
app.add_typer(semif_app, name="semif")
app.add_typer(eval_app, name="eval")

EXIT_CODES = {
    "matched": 0,
    "new_service": 0,
    "unsure": 2,
    "no_entity": 2,
    "unsupported": 2,
}
HINTS = {
    "new_service": "No existing service fits; a new one can be designed from these facts.",
    "no_entity": "No entity in the domain fits; the domain needs changing first "
    "(the domain-model factory's job).",
    "unsupported": "No factory, or no kind of CRUD API, handles this task yet.",
}

ProjectOption = Annotated[
    Path,
    typer.Option(
        "--project",
        "-p",
        help="Project folder (or any folder inside it). Defaults to the current folder.",
    ),
]
MAX_ANSWERS = 200


@app.callback()
def main() -> None:
    """Classify a task, resolve its design, build it and verify it."""


@app.command()
def version() -> None:
    """Show the installed harness version."""
    typer.echo(f"harness {__version__}")


@app.command()
def validate(
    project: ProjectOption = Path("."),
    strict: Annotated[
        bool, typer.Option("--strict", help="Treat skipped logic checks (no Prolog) as an error.")
    ] = False,
) -> None:
    """Check a project's harness.yaml and knowledge files."""
    report = validate_project(project, strict=strict)

    if report.root:
        typer.echo(f"Project   {report.root}")
    for checked in report.checked:
        failed = any(p.file == checked.path and p.severity == "error" for p in report.problems)
        mark = "✗" if failed or checked.summary is None else "✓"
        typer.echo(f"  {mark} {_show(report, checked.path):<36} {checked.summary or ''}".rstrip())
    if report.logic:
        mark = {"no problems": "✓", "skipped": "!"}.get(report.logic, "✗")
        typer.echo(f"  {mark} {'logic checks (Prolog)':<36} {report.logic}")
    _print_problems(report)

    errors, warnings = len(report.errors), len(report.warnings)
    tally = f"{errors} error{'s' * (errors != 1)}, {warnings} warning{'s' * (warnings != 1)}"
    typer.echo(f"{'OK' if report.ok else 'FAILED'}: {tally}")
    raise typer.Exit(0 if report.ok else 1)


@schema_app.command("export")
def schema_export(
    out: Annotated[Path, typer.Option("--out", "-o", help="Folder to write to.")] = Path("schemas"),
) -> None:
    """Write JSON Schemas for the knowledge files, for editor autocomplete."""
    out.mkdir(parents=True, exist_ok=True)
    for name, model in SCHEMAS.items():
        path = out / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(by_alias=True), indent=2) + "\n")
        typer.echo(f"wrote {path}")


@kb_app.command("export")
def kb_export(
    project: ProjectOption = Path("."),
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="File to write. Defaults to the screen.")
    ] = None,
) -> None:
    """Write the project's knowledge as a Prolog file you can load with `swipl`."""
    from harness.logic.compile import compile_knowledge, library

    report = _valid_knowledge(project)
    program = compile_knowledge(report.domain, report.technical) + "\n" + library(_checks(report))
    if out is None:
        typer.echo(program, nl=False)
    else:
        out.write_text(program)
        typer.echo(f"wrote {out}  (load it with: swipl {out})", err=True)


@kb_app.command("query")
def kb_query(
    goal: Annotated[str, typer.Argument(help='A Prolog goal, e.g. "method(add_book, M)".')],
    project: ProjectOption = Path("."),
) -> None:
    """Ask the project's knowledge one Prolog question and print every answer."""
    from harness.logic.engine import Engine, PrologUnavailable

    report = _valid_knowledge(project)
    try:
        engine = Engine(report.domain, report.technical, _checks(report))
        answers = engine.query(goal.strip().removesuffix("."))
    except PrologUnavailable as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    except Exception as exc:  # a Prolog error in the goal, e.g. a syntax error
        typer.echo(f"Prolog error: {exc}", err=True)
        raise typer.Exit(1) from exc

    if not answers:
        typer.echo("false (no answers)")
        return
    if answers == [{}]:
        typer.echo("true")
        return
    for answer in answers[:MAX_ANSWERS]:
        typer.echo("   ".join(f"{name} = {value}" for name, value in sorted(answer.items())))
    more = f" (showing the first {MAX_ANSWERS})" if len(answers) > MAX_ANSWERS else ""
    typer.echo(f"{len(answers)} answer{'s' * (len(answers) != 1)}{more}")


@app.command()
def classify(
    task: Annotated[str, typer.Argument(help="The task, in business language.")],
    project: ProjectOption = Path("."),
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Also show what SemIf reads for each option.")
    ] = False,
    show_trace: Annotated[
        bool,
        typer.Option("--trace", help="Show every step: question, each option's probability, time."),
    ] = False,
    trace_out: Annotated[
        Path | None, typer.Option("--trace-out", help="Write the trace to this file (JSONL).")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the trace as JSON.")] = False,
) -> None:
    """Classify a task: factory, target, operation and kind, then an existing or new service."""
    report = _valid_knowledge(project)
    settings = report.settings.semif
    trace = _classify(report, task)
    if trace_out is not None:
        trace_out.write_text(trace.to_jsonl())

    if as_json:
        typer.echo(json.dumps(trace.to_dict(), indent=2))
        raise typer.Exit(EXIT_CODES.get(trace.outcome, 2))

    typer.echo(f"Task      {task}")
    typer.echo(f"SemIf     {settings.url}, threshold {settings.threshold:g}")
    typer.echo("")
    typer.echo(f"  {'step':<12} {'by':<6} {'answer':<26} {'conf':>5} {'margin':>7}")
    answers: dict[str, str] = {}
    for event in trace.events:
        if event.by != "semif":
            typer.echo(f"  {event.step:<12} {event.by:<6} {event.answer:<26} {event.reason}")
            answers[event.step] = event.answer
            continue
        mark = "✓" if event.accepted else "✗"
        typer.echo(
            f"  {event.step:<12} {event.by:<6} {event.answer:<26} "
            f"{event.confidence:>5.2f} {event.margin:>7.2f}  {mark}"
        )
        if event.reason:
            typer.echo(f"      {event.reason}")
        if show_trace:
            typer.echo(f"      question: {event.question}")
            ranked = sorted(event.probabilities.items(), key=lambda kv: -kv[1])
            typer.echo("      " + " · ".join(f"{name} {p:.2f}" for name, p in ranked))
            typer.echo(f"      {event.ms:.0f} ms")
        if verbose:
            typer.echo(f"      question: {event.question}")
            for option in _options_read(report, event.step, answers):
                typer.echo(f"      - {option.name}: {option.text()}")
        if event.accepted:
            answers[event.step] = event.answer

    typer.echo("")
    found = " › ".join(v for k, v in trace.answers.items() if k != "factory")
    calls = (
        f"({trace.semif_calls} SemIf call{'s' * (trace.semif_calls != 1)}, {trace.ms / 1000:.1f}s)"
    )
    typer.echo(
        f"Outcome   {trace.outcome.upper()}" + (f": {found}" if found else "") + f"   {calls}"
    )
    last = trace.events[-1] if trace.events else None
    if trace.outcome == "unsure" and last and last.accepted:
        hint = (
            f"The task doesn't fit any {last.step} in the knowledge base; a person needs to decide."
        )
    elif trace.outcome == "unsure" and last:
        hint = (
            f"SemIf is not sure enough about the {last.step} "
            f"({last.confidence:.2f} < {settings.threshold:g}); a person needs to decide."
        )
    else:
        hint = HINTS.get(trace.outcome)
    if hint:
        typer.echo(f"          {hint}")
    raise typer.Exit(EXIT_CODES.get(trace.outcome, 2))


def _options_read(report: Report, step: str, answers: dict[str, str]):
    """The options SemIf read at a step, with their text (for --verbose)."""
    from harness.classify.questions import Context
    from harness.classify.walk import FACTORY_NONE, none_option, render, step_options
    from harness.logic.engine import Engine
    from harness.semif.client import NONE, Option

    if step == "factory":
        manifests = {n: f.manifest for n, f in report.factories.items()} | report.planned
        options = [Option(n, m.description, tuple(m.examples)) for n, m in manifests.items()]
        return [*options, Option(NONE, render(FACTORY_NONE, report.domain, answers))]
    spec = report.technical.classification.steps[step]
    ctx = Context(report.domain, report.technical, Engine(report.domain, report.technical))
    options = step_options(spec, ctx, answers)
    return [*options, none_option(spec, report.domain, answers)]


@eval_app.command("classify")
def eval_classify(project: ProjectOption = Path(".")) -> None:
    """Classify every eval case of the project and check the outcome and the path."""
    from harness.evals import check_case, check_groups

    report = _valid_knowledge(project)
    if report.evals is None:
        typer.echo("No eval cases: add .harness/evals/classify.yaml", err=True)
        raise typer.Exit(1)

    results = []
    for case in report.evals.cases:
        result = check_case(case, _classify(report, case.task))
        results.append(result)
        typer.echo(f"  {'✓' if result.passed else '✗'} {case.task}")
        if not result.passed:
            typer.echo(f"      first wrong step: {result.first_wrong}")
            for failure in result.failures:
                typer.echo(f"      {failure}")
    group_failures = check_groups(results)
    for failure in group_failures:
        typer.echo(f"  ✗ {failure}")

    passed = sum(r.passed for r in results)
    calls = sum(r.trace.semif_calls for r in results)
    seconds = sum(r.trace.ms for r in results) / 1000
    groups = f", {len(group_failures)} group(s) inconsistent" if group_failures else ""
    typer.echo("")
    typer.echo(
        f"{passed}/{len(results)} cases passed{groups}   ({calls} SemIf calls, {seconds:.1f}s)"
    )
    raise typer.Exit(0 if passed == len(results) and not group_failures else 1)


def _classify(report: Report, task: str):
    """Classify one task, or print why SemIf could not be asked and stop."""
    from harness.classify.walk import classify as run_classification
    from harness.logic.engine import PrologUnavailable
    from harness.semif.client import DecisionModelError

    settings = report.settings.semif
    manifests = {name: f.manifest for name, f in report.factories.items()} | report.planned
    try:
        trace = run_classification(
            task,
            report.domain,
            report.technical,
            make_model(report),
            settings.threshold,
            manifests,
        )
    except (DecisionModelError, PrologUnavailable) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    trace.url = settings.url
    return trace


@semif_app.command("check")
def semif_check(project: ProjectOption = Path(".")) -> None:
    """Check that SemIf is running and answering."""
    from harness.semif.client import DecisionModelError

    report = _valid_knowledge(project)
    model = make_model(report)
    try:
        status, seconds = model.health()
    except DecisionModelError as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from exc
    details = status.get("model") or {}
    name = details.get("source") or details.get("model") or "unknown model"
    typer.echo(f"✓ SemIf reachable at {model.url} ({name}); a test question took {seconds:.1f}s")


def make_model(report: Report):
    """The decision model for a project. Tests replace this with a replaying model."""
    from harness.semif.http import SemIfHttp

    settings = report.settings.semif
    return SemIfHttp(settings.url, settings.timeout)


def _valid_knowledge(project: Path) -> Report:
    """Load the project's knowledge (layers 1-3), or print why it can't be used and stop."""
    report = validate_project(project, logic=False)
    if not report.ok or report.domain is None or report.technical is None:
        _print_problems(report, errors_only=True)
        typer.echo("The knowledge has errors; fix them first (see `harness validate`).", err=True)
        raise typer.Exit(1)
    return report


def _checks(report: Report) -> list[Path]:
    """Each enabled factory's Prolog checks."""
    return [f.checks for f in report.factories.values() if f.checks.is_file()]


def _show(report: Report, path: Path) -> str:
    if path.is_relative_to(FACTORIES_DIR):
        name, *rest = path.relative_to(FACTORIES_DIR).parts
        return f"factory {name} › {'/'.join(rest)}"
    if report.root and path.is_relative_to(report.root):
        return str(path.relative_to(report.root))
    return str(path)


def _print_problems(report: Report, errors_only: bool = False) -> None:
    for problem in report.problems:
        if errors_only and problem.severity != "error":
            continue
        mark = "✗" if problem.severity == "error" else "!"
        typer.echo(f"  {mark} {_where(report, problem)}", err=errors_only)
        typer.echo(f"      {problem.message}", err=errors_only)


def _where(report: Report, problem: Problem) -> str:
    line = f":{problem.line}" if problem.line else ""
    trail = "  " + " › ".join(str(p) for p in problem.path) if problem.path else ""
    return f"{_show(report, problem.file)}{line}{trail}"
