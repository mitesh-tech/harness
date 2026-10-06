"""The `harness` command-line entry point."""

import json
from pathlib import Path
from typing import Annotated

import typer

from harness import __version__
from harness.knowledge.load import Problem
from harness.knowledge.schema import SCHEMAS
from harness.knowledge.validate import DEFAULT_TECHNICAL, validate_project

app = typer.Typer(
    help="A model-agnostic AI harness for building backend APIs.",
    no_args_is_help=True,
)
schema_app = typer.Typer(help="Work with the schemas of the knowledge files.", no_args_is_help=True)
app.add_typer(schema_app, name="schema")


@app.callback()
def main() -> None:
    """Classify a task, resolve its design, build it and verify it."""


@app.command()
def version() -> None:
    """Show the installed harness version."""
    typer.echo(f"harness {__version__}")


@app.command()
def validate(
    project: Annotated[
        Path,
        typer.Option(
            "--project",
            "-p",
            help="Project folder (or any folder inside it). Defaults to the current folder.",
        ),
    ] = Path("."),
) -> None:
    """Check a project's harness.yaml and knowledge files."""
    report = validate_project(project)

    def show(path: Path) -> str:
        if path == DEFAULT_TECHNICAL:
            return "technical.yaml (harness default)"
        if report.root and path.is_relative_to(report.root):
            return str(path.relative_to(report.root))
        return str(path)

    def where(problem: Problem) -> str:
        line = f":{problem.line}" if problem.line else ""
        trail = "  " + " › ".join(str(p) for p in problem.path) if problem.path else ""
        return f"{show(problem.file)}{line}{trail}"

    if report.root:
        typer.echo(f"Project   {report.root}")
    for checked in report.checked:
        failed = any(p.file == checked.path and p.severity == "error" for p in report.problems)
        mark = "✗" if failed or checked.summary is None else "✓"
        typer.echo(f"  {mark} {show(checked.path):<36} {checked.summary or ''}".rstrip())
    for problem in report.problems:
        mark = "✗" if problem.severity == "error" else "!"
        typer.echo(f"  {mark} {where(problem)}")
        typer.echo(f"      {problem.message}")

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
