"""Resolve a task into a spec, and save it in a run folder.

The same for every factory: classify the task, hand it to the chosen
factory's design code (factory.yaml › design), then save the run:

    .harness/runs/<run id>/
    ├── spec.yaml      the design, for you to read and (S5) confirm
    ├── openapi.yaml   the same API as an OpenAPI document, made from the spec
    └── trace.jsonl    every decision, from classification to design
"""

import hashlib
import importlib.util
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Literal

import yaml
from pydantic import Field, ValidationError

from harness.classify.walk import classify
from harness.knowledge.schema import KebabName, Model, Text
from harness.knowledge.validate import LoadedFactory, Report
from harness.semif.client import DecisionModel
from harness.trace import Event, Trace

DESIGNED = ("matched", "new_service")


class SpecService(Model):
    name: Text
    new: bool


class Spec(Model):
    """spec.yaml: the cover sheet the platform adds, and the factory's design."""

    run: Text
    task: Text
    factory: KebabName
    status: Literal["draft", "confirmed", "rejected"] = "draft"
    created: Text
    service: SpecService
    kind: Text
    target: Text | None = None
    parent: Text | None = None
    association: Text | None = None
    details: dict[str, object] = Field(default_factory=dict)
    api: dict[str, object]
    data: dict[str, object] | None = None
    delete_effects: list[dict[str, object]] = Field(default_factory=list)
    components: list[dict[str, object]] = Field(default_factory=list)
    tests: list[dict[str, object]] = Field(default_factory=list)
    open_questions: list[Text] = Field(default_factory=list)
    answers: dict[str, Text] = Field(default_factory=dict)  # your answers, kept as a record
    confirmed_at: Text | None = None
    sources: dict[str, Text] = Field(default_factory=dict)


@dataclass
class Run:
    trace: Trace
    folder: Path | None = None  # None when the task stopped before a design
    spec: dict | None = None

    @property
    def stopped(self) -> bool:
        return self.folder is None


class ResolveError(RuntimeError):
    """The task or service can't be designed."""


def resolve_task(report: Report, task: str, model: DecisionModel) -> Run:
    """Classify the task; when it names an existing or new service, design it and save the run."""
    manifests = {n: f.manifest for n, f in report.factories.items()} | report.planned
    trace = classify(
        task,
        report.domain,
        report.technical,
        model,
        report.settings.semif.threshold,
        manifests,
        report.data,
    )
    trace.url = report.settings.semif.url
    if trace.outcome not in DESIGNED:
        return Run(trace)
    factory = report.factories[trace.answers["factory"]]
    design = factory_design(factory).design_task(report, trace, model)
    return _save(report, factory, trace, design)


def resolve_service(report: Report, service: str) -> Run:
    """Design an existing service (no SemIf) and save the run."""
    factory = next(iter(report.factories.values()))
    trace = Trace(f"service {service}", report.settings.semif.threshold)
    design = factory_design(factory).design_service(report, service)
    return _save(report, factory, trace, design)


def factory_design(factory: LoadedFactory) -> ModuleType:
    """The factory's design code, named by `design:` in its factory.yaml."""
    if not factory.manifest.design:
        raise ResolveError(f"the {factory.factory.name} factory has no design code")
    path = factory.factory.root / factory.manifest.design
    name = f"harness_factory_{factory.factory.name.replace('-', '_')}_design"
    module_spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def _save(report: Report, factory: LoadedFactory, trace: Trace, design) -> Run:
    # The trace gets SemIf's detail answers, then where each designed value came from.
    trace.events += design.events
    trace.events += [
        Event(stage="resolve", step=path, by="rule", answer="decided", reason=source)
        for path, source in design.sources.items()
    ]
    body = design.to_dict()
    sources = body.pop("sources")
    spec = {
        "run": trace.run_id,
        "task": trace.task,
        "factory": factory.factory.name,
        "status": "draft",
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        **body,
        "open_questions": design.open_questions,
        "sources": sources,
    }
    Spec.model_validate(spec)

    runs = report.root / report.settings.knowledge / "runs"
    folder = runs / trace.run_id
    folder.mkdir(parents=True, exist_ok=True)
    (runs / ".gitignore").write_text("# Run folders are kept locally, not committed.\n*\n")
    (folder / "spec.yaml").write_text(_yaml(spec, SPEC_HEADER))
    openapi = factory_design(factory).to_openapi(spec)
    (folder / "openapi.yaml").write_text(_yaml(openapi, OPENAPI_HEADER))
    (folder / "trace.jsonl").write_text(trace.to_jsonl())
    return Run(trace, folder, spec)


SPEC_HEADER = (
    "# The design of this run, for you to read and confirm (S5).\n"
    "# `sources` says where every value came from; `open_questions` need your answer.\n\n"
)
OPENAPI_HEADER = "# Generated from spec.yaml. Do not edit: change the spec instead.\n\n"


def _yaml(data: dict, header: str) -> str:
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


# ── Confirming a spec ───────────────────────────────────────────────

CONFIRMED = ".confirmed"  # in the run folder: the hash of the spec you approved


def find_run(report: Report, run_id: str) -> Path:
    runs = report.root / report.settings.knowledge / "runs"
    folder = runs / run_id
    if not (folder / "spec.yaml").is_file():
        known = ", ".join(sorted(p.name for p in runs.glob("*") if p.is_dir())) or "none"
        raise ResolveError(f"no run '{run_id}' (runs: {known})")
    return folder


def read_spec(folder: Path) -> dict:
    return yaml.safe_load((folder / "spec.yaml").read_text())


def spec_problems(report: Report, spec: dict) -> list[str]:
    """What stops a spec from being confirmed (and generated from)."""
    try:
        Spec.model_validate(spec)
    except ValidationError as exc:
        return [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()]
    problems = [f"open question: {q}" for q in spec.get("open_questions", [])]
    api = spec["api"]
    for where, text in (("service.name", spec["service"]["name"]), ("api.path", api["path"])):
        for placeholder in re.findall(r"<\w+>", str(text)):
            problems.append(f"{where} still has {placeholder}; replace it with the real word")
    for param in api.get("path_params", {}):
        if f"{{{param}}}" not in api["path"]:
            problems.append(f"api.path_params.{param} is not in the path {api['path']}")
    entity = report.domain.entities.get(spec.get("target") or "")
    if entity is not None:
        known = set(entity.attributes) | ({entity.lifecycle.field} if entity.lifecycle else set())
        for name in api.get("request", {}).get("fields", {}):
            if name not in known and not name.endswith("_id"):
                problems.append(
                    f"api.request.fields.{name} is not an attribute of {spec['target']}"
                )
    for index, case in enumerate(spec.get("tests", [])):
        for key in ("name", "request", "expect"):
            if key not in case:
                problems.append(f"tests.{index} has no '{key}'")
    return problems


def confirm(report: Report, folder: Path, approve: bool) -> dict:
    """Approve (after checking it again) or reject a spec; approving regenerates openapi.yaml."""
    spec = read_spec(folder)
    if not approve:
        spec["status"] = "rejected"
        (folder / "spec.yaml").write_text(_yaml(spec, SPEC_HEADER))
        return spec
    problems = spec_problems(report, spec)
    if problems:
        raise ResolveError(
            "the spec can't be confirmed yet:\n  - "
            + "\n  - ".join(problems)
            + "\nEdit spec.yaml (record answers under `answers:` and remove the questions), "
            "then confirm again."
        )
    spec["status"] = "confirmed"
    spec["confirmed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    text = _yaml(spec, SPEC_HEADER)
    (folder / "spec.yaml").write_text(text)
    factory = report.factories[spec["factory"]]
    (folder / "openapi.yaml").write_text(
        _yaml(factory_design(factory).to_openapi(spec), OPENAPI_HEADER)
    )
    (folder / CONFIRMED).write_text(spec_hash(text) + "\n")
    return spec


def spec_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def confirmed_spec(report: Report, folder: Path) -> tuple[dict, str]:
    """The confirmed spec and its hash; refuses a spec changed after you confirmed it."""
    text = (folder / "spec.yaml").read_text()
    spec = yaml.safe_load(text)
    marker = folder / CONFIRMED
    if spec.get("status") != "confirmed" or not marker.is_file():
        raise ResolveError(
            f"run {folder.name} is not confirmed: run `harness confirm {folder.name}`"
        )
    digest = spec_hash(text)
    if marker.read_text().strip() != digest:
        raise ResolveError(
            f"spec.yaml of run {folder.name} changed after it was confirmed; confirm it again"
        )
    return spec, digest
