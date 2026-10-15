"""The `harness` command-line entry point."""

import json
from pathlib import Path
from typing import Annotated

import typer

from harness import __version__
from harness.factories import FACTORIES_DIR
from harness.knowledge.load import Problem
from harness.knowledge.validate import Report, validate_project
from harness.schemas import SCHEMAS

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

# The published schemas, for the `$schema` line of files the harness writes.
SCHEMAS_URL = "https://raw.githubusercontent.com/mitesh-tech/harness/main/schemas"

EXIT_CODES = {
    "matched": 0,
    "new_service": 0,
    "unsure": 2,
    "no_entity": 2,
    "no_mapping": 2,
    "unsupported": 2,
}
HINTS = {
    "new_service": "No existing service fits; a new one can be designed from these facts.",
    "no_entity": "No entity in the domain fits; the domain needs changing first "
    "(the domain-model factory's job).",
    "no_mapping": "data.yaml doesn't say where this is stored; add its entry first.",
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
    introspect: Annotated[
        bool,
        typer.Option(
            "--introspect", help="Also compare data.yaml with the real data sources (read-only)."
        ),
    ] = False,
) -> None:
    """Check a project's harness.yaml and knowledge files."""
    report = validate_project(project, strict=strict, introspect=introspect)

    if report.root:
        typer.echo(f"Project   {report.root}")
    for checked in report.checked:
        failed = any(p.file == checked.path and p.severity == "error" for p in report.problems)
        mark = "✗" if failed or checked.summary is None else "✓"
        typer.echo(f"  {mark} {_show(report, checked.path):<36} {checked.summary or ''}".rstrip())
    if report.logic:
        mark = {"no problems": "✓", "skipped": "!"}.get(report.logic, "✗")
        typer.echo(f"  {mark} {'logic checks (Prolog)':<36} {report.logic}")
    if report.introspection:
        mark = "✓" if report.introspection == "matches" else "!"
        typer.echo(f"  {mark} {'data sources (introspection)':<36} {report.introspection}")
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
    program = (
        compile_knowledge(report.domain, report.technical, report.data)
        + "\n"
        + library(_checks(report))
    )
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
        engine = Engine(report.domain, report.technical, _checks(report), report.data)
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
    ctx = Context(
        report.domain, report.technical, Engine(report.domain, report.technical), report.data
    )
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
            report.data,
        )
    except (DecisionModelError, PrologUnavailable) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    trace.url = settings.url
    return trace


@app.command()
def resolve(
    task: Annotated[str | None, typer.Argument(help="The task, in business language.")] = None,
    service: Annotated[
        str | None,
        typer.Option("--service", "-s", help="Design this existing service instead (no SemIf)."),
    ] = None,
    project: ProjectOption = Path("."),
) -> None:
    """Design a task's API and save it as a spec in .harness/runs/<id>/."""
    from harness.logic.engine import PrologUnavailable
    from harness.resolve import ResolveError, resolve_service, resolve_task
    from harness.semif.client import DecisionModelError

    if (task is None) == (service is None):
        typer.echo("Give a task, or an existing service with --service.", err=True)
        raise typer.Exit(1)
    report = validate_project(project)
    if not report.ok or report.domain is None or report.technical is None:
        _print_problems(report, errors_only=True)
        typer.echo("The knowledge has errors; fix them first (see `harness validate`).", err=True)
        raise typer.Exit(1)
    try:
        if service is not None:
            run = resolve_service(report, service)
        else:
            run = resolve_task(report, task, make_model(report))
    except (ResolveError, PrologUnavailable, DecisionModelError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if run.stopped:
        outcome = run.trace.outcome
        typer.echo(f"Outcome   {outcome.upper()}: nothing to design")
        hint = HINTS.get(outcome) or "a person needs to decide (see `harness classify --trace`)."
        typer.echo(f"          {hint}")
        raise typer.Exit(2)
    spec = run.spec
    name = spec["service"]["name"] + (" (new)" if spec["service"]["new"] else "")
    typer.echo(f"Service   {name}")
    typer.echo(f"API       {spec['api']['method']} {spec['api']['path']}")
    if spec["open_questions"]:
        typer.echo("Open      " + "\n          ".join(spec["open_questions"]))
    else:
        typer.echo("Open      no open questions")
    folder = _show(report, run.folder)
    typer.echo(f"Saved     {folder}/spec.yaml, openapi.yaml, trace.jsonl")


@app.command()
def confirm(
    run: Annotated[str, typer.Argument(help="The run id (a folder in .harness/runs/).")],
    approve: Annotated[
        bool, typer.Option("--approve", help="Check the spec again and confirm it.")
    ] = False,
    reject: Annotated[bool, typer.Option("--reject", help="Reject the spec.")] = False,
    project: ProjectOption = Path("."),
) -> None:
    """Review a run's spec, then approve it (after checking it again) or reject it."""
    from harness.resolve import ResolveError, find_run, read_spec, spec_problems
    from harness.resolve import confirm as confirm_spec

    if approve and reject:
        typer.echo("Choose --approve or --reject, not both.", err=True)
        raise typer.Exit(1)
    report = _valid_knowledge(project)
    try:
        folder = find_run(report, run)
        if approve or reject:
            spec = confirm_spec(report, folder, approve)
            typer.echo(f"Run {run}: spec {spec['status']}.")
            if approve:
                typer.echo(f"Generate it with: harness generate {run}")
            return
    except ResolveError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    spec = read_spec(folder)
    api = spec["api"]
    typer.echo(f"Run       {run}   status: {spec['status']}")
    typer.echo(f"Task      {spec['task']}")
    name = spec["service"]["name"] + (" (new)" if spec["service"]["new"] else "")
    typer.echo(f"Service   {name}")
    typer.echo(f"API       {api['method']} {api['path']}  →  {api['response']['status']}")
    typer.echo(f"Tests     {len(spec.get('tests', []))} cases")
    for case in spec.get("tests", []):
        typer.echo(f"          - {case['name']} → {case['expect']['status']}")
    problems = spec_problems(report, spec)
    if problems:
        typer.echo("To do     " + "\n          ".join(problems))
    typer.echo(f"Spec      {_show(report, folder / 'spec.yaml')}")
    typer.echo(
        f"Next      edit the spec if needed, then: harness confirm {run} --approve  (or --reject)"
    )


@app.command()
def generate(
    run: Annotated[str, typer.Argument(help="The run id of a confirmed spec.")],
    in_place: Annotated[
        bool,
        typer.Option("--in-place", help="Write into the project itself, not a new git worktree."),
    ] = False,
    project: ProjectOption = Path("."),
) -> None:
    """Generate code from a confirmed spec: its tests first (they must fail), then the code."""
    from harness.generate import GenerateError
    from harness.generate import generate as generate_code
    from harness.resolve import ResolveError, find_run

    report = _valid_knowledge(project)
    try:
        result = generate_code(report, find_run(report, run), in_place=in_place)
    except (ResolveError, GenerateError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    where = (
        result.workspace
        if result.branch is None
        else f"{result.workspace}  (branch {result.branch})"
    )
    typer.echo(f"Code      {where}")
    for target in result.written:
        typer.echo(f"  wrote   {target}")
    for target in result.kept:
        typer.echo(f"  kept    {target}")
    red, green = result.red, result.green
    typer.echo(f"Tests     {result.test_file}")
    typer.echo(f"  before  {len(red.failed)} failing, {len(red.passed)} passing  (should all fail)")
    for name in red.passed:
        typer.echo(f"          ! {name} passed before the code existed: it tests nothing")
    typer.echo(f"  after   {len(green.passed)} passing, {len(green.failed)} failing")
    for name in green.failed:
        typer.echo(f"          ✗ {name}")
    if not result.ok:
        typer.echo(green.output, err=True)
        typer.echo("FAILED: the generated code does not pass its tests.", err=True)
        raise typer.Exit(1)
    typer.echo("OK: the code passes the tests from the spec.")


OVERRIDE_HEADER = (
    "# yaml-language-server: $schema={schema}\n"
    "#\n"
    "# Project overrides of the {factory} factory's technical knowledge.\n"
    "# Write only what differs: entries replace the factory's by name (rules by\n"
    "# id); `api:` changes only the settings given. See `harness override --help`.\n\n"
)


@app.command()
def override(
    entry: Annotated[
        str,
        typer.Argument(
            help="What to override: kinds.<name>, operations.<name>, components.<name>, "
            "rules.<id> or api.<paths|paging|errors>."
        ),
    ],
    project: ProjectOption = Path("."),
) -> None:
    """Start an override: copy one factory entry into the project's override file."""
    import yaml

    report = _valid_knowledge(project)
    loaded = next(iter(report.factories.values()))
    section, _, name = entry.partition(".")
    factory_file = loaded.factory.root / loaded.manifest.technical
    factory_data = yaml.safe_load(factory_file.read_text())

    if section == "rules":
        found = [r for r in factory_data.get("rules", []) if r.get("id") == name]
        snippet = {"rules": found}
    elif section in ("kinds", "operations", "components", "api"):
        value = factory_data.get(section, {}).get(name)
        found = [value] if value is not None else []
        snippet = {section: {name: value}}
    else:
        typer.echo(f"unknown section '{section}'; see `harness override --help`", err=True)
        raise typer.Exit(1)
    if not found:
        typer.echo(f"the {loaded.factory.name} factory has no {entry}", err=True)
        raise typer.Exit(1)

    text = yaml.safe_dump(snippet, sort_keys=False, allow_unicode=True)
    target = report.root / report.settings.knowledge / "factories" / loaded.factory.name
    path = target / "technical.yaml"
    if path.exists():
        typer.echo(f"# Add this to {_show(report, path)} and edit it:\n")
        typer.echo(text, nl=False)
        return
    target.mkdir(parents=True, exist_ok=True)
    schema = f"{SCHEMAS_URL}/technical-override.schema.json"
    path.write_text(OVERRIDE_HEADER.format(schema=schema, factory=loaded.factory.name) + text)
    typer.echo(f"wrote {_show(report, path)} with {entry} from the factory; edit it, then run")
    typer.echo("`harness validate` to check it.")


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
    """Each enabled factory's Prolog files: decision rules and checks."""
    return [path for f in report.factories.values() for path in f.logic]


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
