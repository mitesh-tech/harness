"""Verify generated code against its spec, commit it when everything passes, and report.

The same for every factory. The platform runs what is true of any generated
code (the spec's tests, the project's tests, lint, scope, untouched files);
a factory adds its own checks and report lines through optional `verify()`
and `summary()` functions in its design code (CRUD: the API contract).
"""

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Literal

from harness.generate import (
    REGISTRY,
    Generation,
    file_hash,
    git,
    load,
    load_registry,
    run_tests,
)
from harness.knowledge.validate import Report
from harness.packs import Pack, installed_packs
from harness.resolve import confirmed_spec, factory_design
from harness.trace import Event, Trace

Runner = Callable[..., subprocess.CompletedProcess]
REPORT = "report.md"


@dataclass
class Check:
    name: str
    status: Literal["pass", "fail", "skip"]
    detail: str = ""
    items: list[str] = field(default_factory=list)  # what went wrong, one per line


@dataclass
class Verification:
    checks: list[Check]
    warnings: list[str]
    commit: str | None = None
    report: Path | None = None

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)


class Tools:
    """What a factory's verify() may use: the project and its pack's commands."""

    def __init__(self, project: Path, pack: Pack, run: Runner):
        self.project, self.pack, self.run = project, pack, run

    def app_openapi(self) -> dict:
        """Build the app in the project's own environment and return its OpenAPI."""
        manifest = self.pack.manifest
        python = manifest.commands.get("python")
        if not python or not manifest.app_factory:
            raise RuntimeError(f"pack {self.pack.name} doesn't say how to build the app")
        module, function = manifest.app_factory.split(":")
        script = (
            "import importlib, json; "
            f"app = getattr(importlib.import_module({module!r}), {function!r})"
            f"(*{manifest.app_factory_args!r}); "
            "print(json.dumps(app.openapi()))"
        )
        done = self.run([*python, "-c", script], cwd=self.project, capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(done.stderr.strip()[-500:])
        return json.loads(done.stdout.strip().splitlines()[-1])


def verify(report: Report, folder: Path, run: Runner = subprocess.run) -> Verification:
    spec, digest = confirmed_spec(report, folder)
    generation = load(folder)
    pack = installed_packs()[report.settings.pack]
    project = generation.workspace
    design = factory_design(report.factories[spec["factory"]])

    checks = [
        _spec_tests(project, pack, generation, run),
        _project_tests(project, pack, run),
        _lint(project, pack, generation, run),
    ]
    if hasattr(design, "verify"):
        checks += design.verify(spec, Tools(project, pack, run))
    checks.append(_scope(report, project, generation, pack, run))
    checks.append(_untouched(report, project))
    warnings = [
        f"{name} passed before the code existed: it tests nothing" for name in generation.red.passed
    ]
    result = Verification(checks, warnings)

    if result.ok and generation.branch:
        status = git(project, ["status", "--porcelain", "."], run)
        if status.strip():
            message = f"harness: {spec['service']['name']} (run {folder.name})"
            git(project, ["add", "-A", "."], run)
            git(project, ["commit", "-q", "-m", message], run)
        result.commit = git(project, ["rev-parse", "--short", "HEAD"], run).strip()

    summary = design.summary(spec) if hasattr(design, "summary") else {"design": "", "next": []}
    result.report = folder / REPORT
    result.report.write_text(_report(spec, digest, folder, generation, result, summary))
    _trace(folder, result)
    return result


# ── The platform's checks ───────────────────────────────────────────


def _spec_tests(project: Path, pack: Pack, generation: Generation, run: Runner) -> Check:
    tests = run_tests(project, pack, generation.test_file, run)
    red = generation.red
    detail = f"{len(red.failed)} failed before the code, {len(tests.passed)} pass after"
    if tests.failed or not tests.passed:
        return Check("spec tests", "fail", detail, tests.failed or ["no tests ran"])
    return Check("spec tests", "pass", detail)


def _project_tests(project: Path, pack: Pack, run: Runner) -> Check:
    command = pack.manifest.commands.get("test")
    if not command:
        return Check("project tests", "skip", "the pack has no test command")
    done = run(command, cwd=project, capture_output=True, text=True)
    last = (done.stdout.strip().splitlines() or [""])[-1]
    if done.returncode != 0:
        return Check("project tests", "fail", last, _failures(done.stdout))
    return Check("project tests", "pass", last)


def _failures(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(("FAILED", "ERROR"))][:20]


def _lint(project: Path, pack: Pack, generation: Generation, run: Runner) -> Check:
    command = pack.manifest.commands.get("lint")
    files = [f for f in generation.written if f.endswith(".py")]
    if not command or not files:
        return Check("lint", "skip", "nothing to lint, or no lint command")
    try:
        done = run([*command, *files], cwd=project, capture_output=True, text=True)
    except FileNotFoundError:
        return Check("lint", "skip", f"{command[0]} is not installed")
    output = done.stdout + done.stderr
    if done.returncode != 0 and _tool_missing(output):
        return Check("lint", "skip", f"the project has no `{' '.join(command)}`")
    if done.returncode != 0:
        problems = [line for line in output.splitlines() if line.strip()][:20]
        return Check("lint", "fail", f"{len(files)} generated files", problems)
    return Check("lint", "pass", f"{len(files)} generated files")


def _tool_missing(output: str) -> bool:
    """The lint tool isn't installed (a broken config is a failure, not this)."""
    signs = ("Failed to spawn", "program not found", "command not found")
    return any(sign in output for sign in signs)


def _artifact(path: str, pack: Pack) -> bool:
    """A file running the stack leaves behind (a cache, a compiled file)."""
    for pattern in pack.manifest.artifacts:
        if pattern.endswith("/") and f"/{pattern}" in f"/{path}":
            return True
        if not pattern.endswith("/") and fnmatch(Path(path).name, pattern):
            return True
    return False


def _scope(report: Report, project: Path, generation: Generation, pack: Pack, run: Runner) -> Check:
    """Only the planned files changed: generated ones, marker blocks, the registry."""
    knowledge = report.settings.knowledge
    planned = set(generation.written) | set(generation.registration) | {f"{knowledge}/{REGISTRY}"}
    if generation.branch is None:
        missing = [f for f in generation.written if not (project / f).exists()]
        if missing:
            return Check("scope", "fail", "generated files are missing", missing)
        return Check(
            "scope",
            "pass",
            f"{len(generation.written)} generated files (in place: other changes aren't checked)",
        )
    changed = git(project, ["diff", "--name-only", "--relative", generation.base], run)
    untracked = git(project, ["ls-files", "--others", "--exclude-standard"], run)
    files = {f for f in (changed + untracked).splitlines() if f and not _artifact(f, pack)}
    unplanned = sorted(files - planned)
    if unplanned:
        return Check("scope", "fail", "files changed that the spec didn't plan", unplanned)
    return Check("scope", "pass", f"{len(files)} files, all planned")


def _untouched(report: Report, project: Path) -> Check:
    registry = load_registry(project / report.settings.knowledge / REGISTRY)
    edited = [
        target
        for target, entry in registry.items()
        if not (project / target).exists()
        or file_hash((project / target).read_text()) != entry["sha256"]
    ]
    if edited:
        return Check("untouched", "fail", "generated files were changed by hand", edited)
    return Check("untouched", "pass", f"{len(registry)} generated files match")


# ── Report and trace ────────────────────────────────────────────────

MARK = {"pass": "✓", "fail": "✗", "skip": "–"}


def _report(spec, digest, folder, generation: Generation, result: Verification, summary) -> str:
    name = spec["service"]["name"]
    verdict = "✓ ready to merge" if result.ok else "✗ not ready: see the checks"
    trace = Trace.from_jsonl((folder / "trace.jsonl").read_text())
    semif = [e for e in trace.events if e.by == "semif"]
    rules = [e for e in trace.events if e.by == "rule"]
    lines = [
        f"# {name}: {verdict}",
        "",
        f"- **Task:** {spec['task']}",
        f"- **Spec:** confirmed {spec.get('confirmed_at', '?')} ({digest[:19]}), "
        f"{len(spec.get('open_questions', []))} open questions, "
        f"{len(spec.get('answers', {}))} answers recorded",
    ]
    if summary.get("design"):
        lines.append(f"- **Design:** {summary['design']}")
    lines += ["", "## Checks", "", "| Check | Result |", "|---|---|"]
    for check in result.checks:
        lines.append(f"| {check.name} | {MARK[check.status]} {check.detail} |")
    for check in result.checks:
        if check.items:
            lines += ["", f"**{check.name}:**", *[f"- {item}" for item in check.items]]
    if result.warnings:
        lines += ["", "**Warnings:**", *[f"- {w}" for w in result.warnings]]
    lines += ["", "## Files", ""]
    lines += [f"- written: `{f}`" for f in generation.written]
    lines += [f"- kept (yours or already there): `{f}`" for f in generation.kept]
    lines += [
        "",
        "## Decisions",
        "",
        f"{len(semif)} SemIf answers and {len(rules)} rule decisions, all in "
        f"[trace.jsonl](trace.jsonl); the design in [spec.yaml](spec.yaml).",
        "",
        "## Where",
        "",
    ]
    if generation.branch:
        lines += [
            f"- branch `{generation.branch}`"
            + (f", commit `{result.commit}`" if result.commit else " (not committed)"),
            f"- worktree `{generation.workspace}`",
        ]
    else:
        lines.append(f"- in place: `{generation.workspace}` (not committed)")
    lines += ["", "## Next", ""]
    steps = []
    if not result.ok:
        steps.append("Fix what the checks found, then run `harness verify " + folder.name + "`")
    elif generation.branch:
        steps += [
            f"Review: `git diff {generation.base[:12]}...{generation.branch}`",
            "Merge the branch, or open a pull request from it",
        ]
    else:
        steps.append("Review the changes, then commit them")
    steps += summary.get("next", [])
    if generation.branch:
        steps.append(
            f"When done, remove the worktree: `git worktree remove {generation.workspace}`"
        )
    lines += [f"{n}. {step}" for n, step in enumerate(steps, 1)]
    return "\n".join(lines) + "\n"


def _trace(folder: Path, result: Verification) -> None:
    path = folder / "trace.jsonl"
    trace = Trace.from_jsonl(path.read_text())
    trace.events = [e for e in trace.events if e.stage != "verify"]  # a re-run replaces them
    for check in result.checks:
        trace.events.append(
            Event(
                stage="verify",
                step=check.name,
                by="rule",
                answer=check.status,
                reason=check.detail,
                accepted=check.status != "fail",
            )
        )
    path.write_text(trace.to_jsonl())
