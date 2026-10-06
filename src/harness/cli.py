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
app.add_typer(schema_app, name="schema")
app.add_typer(kb_app, name="kb")

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
