"""The `harness` command-line entry point."""

import json
from pathlib import Path
from typing import Annotated

import typer

from harness import __version__
from harness.knowledge.load import Problem
from harness.knowledge.schema import SCHEMAS
from harness.knowledge.validate import DEFAULT_TECHNICAL, Report, validate_project

app = typer.Typer(
    help="A model-agnostic AI harness for building backend APIs.",
    no_args_is_help=True,
)
schema_app = typer.Typer(help="Work with the schemas of the knowledge files.", no_args_is_help=True)
kb_app = typer.Typer(help="Work with a project's knowledge as Prolog.", no_args_is_help=True)
semif_app = typer.Typer(help="Work with the decision model (SemIf).", no_args_is_help=True)
app.add_typer(schema_app, name="schema")
app.add_typer(kb_app, name="kb")
app.add_typer(semif_app, name="semif")

EXIT_CODES = {"matched": 0, "new_service": 0, "unsure": 2, "no_entity": 2}

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
    program = compile_knowledge(report.domain, report.technical) + "\n" + library()
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
        engine = Engine(report.domain, report.technical)
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
    as_json: Annotated[bool, typer.Option("--json", help="Print the result as JSON.")] = False,
) -> None:
    """Ask SemIf which area and service a task belongs to (or which kind and entity)."""
    from harness.classify.questions import OPTION_SOURCES
    from harness.classify.walk import classify as run_classification
    from harness.semif.client import NONE_OPTION, DecisionModelError

    report = _valid_knowledge(project)
    settings = report.settings.semif
    try:
        result = run_classification(
            task, report.domain, report.technical, make_model(report), settings.threshold
        )
    except DecisionModelError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if as_json:
        typer.echo(json.dumps(result.to_dict(), indent=2))
        raise typer.Exit(EXIT_CODES.get(result.outcome, 2))

    typer.echo(f"Task      {task}")
    typer.echo(f"SemIf     {settings.url}, threshold {settings.threshold:g}")
    typer.echo("")
    answers: dict[str, str] = {}
    for step in result.steps:
        mark = "✓" if step.accepted else "✗"
        others = sorted(
            ((p, name) for name, p in step.probabilities.items() if name != step.answer),
            reverse=True,
        )[:3]
        rest = " · ".join(f"{name} {p:.2f}" for p, name in others)
        typer.echo(f"  {step.id:<12} {step.answer:<24} {step.confidence:.2f} {mark}   ({rest})")
        if verbose:
            spec = report.technical.classification.steps[step.id]
            options = OPTION_SOURCES[spec.options](report.domain, report.technical, answers)
            typer.echo(f"      question: {step.question}")
            for option in (*options, NONE_OPTION):
                typer.echo(f"      - {option.name}: {option.text()}")
        if step.accepted:
            answers[step.id] = step.answer

    typer.echo("")
    found = " › ".join(f"{v}" for v in result.answers.values())
    typer.echo(f"Outcome   {result.outcome.upper()}" + (f": {found}" if found else ""))
    last = result.steps[-1] if result.steps else None
    if result.outcome == "unsure" and last and last.accepted:
        hint = (
            f"The task doesn't fit any {last.id} in the knowledge base; a person needs to decide."
        )
    elif result.outcome == "unsure" and last:
        hint = (
            f"SemIf is not sure enough about the {last.id} "
            f"({last.confidence:.2f} < {settings.threshold:g}); a person needs to decide."
        )
    else:
        hint = {
            "new_service": "No existing service fits; a new one can be designed from these facts.",
            "no_entity": "Nothing in the knowledge base fits; a new entity or connection "
            "is needed.",
        }.get(result.outcome)
    if hint:
        typer.echo(f"          {hint}")
    raise typer.Exit(EXIT_CODES.get(result.outcome, 2))


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


def _show(report: Report, path: Path) -> str:
    if path == DEFAULT_TECHNICAL:
        return "technical.yaml (harness default)"
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
