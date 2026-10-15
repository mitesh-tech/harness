"""Resolve a task into a spec, and save it in a run folder.

The same for every factory: classify the task, hand it to the chosen
factory's design code (factory.yaml › design), then save the run:

    .harness/runs/<run id>/
    ├── spec.yaml      the design, for you to read and (S5) confirm
    ├── openapi.yaml   the same API as an OpenAPI document, made from the spec
    └── trace.jsonl    every decision, from classification to design
"""

import importlib.util
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Literal

import yaml
from pydantic import Field

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
    tests: list[Text] = Field(default_factory=list)
    open_questions: list[Text] = Field(default_factory=list)
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
