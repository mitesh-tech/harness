"""Load a project's knowledge in layers and check it.

Checks run in four layers, each only when the earlier ones pass:
    1. YAML syntax   2. shape (schemas)   3. references   4. logic (Prolog)

Technical knowledge belongs to factories. For each enabled factory, later
layers replace earlier entries:
    the factory's technical.yaml -> stack pack (not yet) -> project override
    (.harness/factories/<name>/technical.yaml)
"""

from dataclasses import dataclass, field
from pathlib import Path

from harness.factories import Factory, installed_factories
from harness.knowledge import references
from harness.knowledge.load import Problem, YamlFile, parse, read_yaml
from harness.knowledge.schema import (
    Domain,
    FactoryManifest,
    HarnessFile,
    Technical,
    TechnicalOverride,
)
from harness.project import PROJECT_FILE, find_project_file


@dataclass
class Checked:
    """One file that was checked, with a one-line summary when it loaded."""

    path: Path
    summary: str | None = None


@dataclass
class LoadedFactory:
    """A factory with its manifest and its technical knowledge (project overrides applied)."""

    factory: Factory
    manifest: FactoryManifest
    technical: Technical
    origins: dict  # where each technical entry came from, for error lines
    checks: Path


@dataclass
class Report:
    root: Path | None = None
    checked: list[Checked] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    settings: HarnessFile | None = None
    factories: dict[str, LoadedFactory] = field(default_factory=dict)
    domain: Domain | None = None
    # Until the factory router exists, commands use the first enabled factory's knowledge.
    technical: Technical | None = None
    logic: str | None = None  # summary of layer 4, when it ran or was skipped

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_project(start: Path, strict: bool = False, logic: bool = True) -> Report:
    """Check a project's knowledge.

    strict: skipped logic checks (no Prolog) count as an error.
    logic:  run layer 4; turn off when only layers 1-3 are needed.
    """
    report = Report()
    project_file = find_project_file(start)
    if project_file is None:
        report.problems.append(
            Problem(
                start.resolve() / PROJECT_FILE,
                (),
                f"no {PROJECT_FILE} found in {start.resolve()} or any parent folder",
            )
        )
        return report
    report.root = project_file.parent

    settings = _load(report, HarnessFile, project_file)
    if settings is None:
        return report
    report.settings = settings
    names = settings.factories if settings.factories is not None else list(installed_factories())
    report.checked[-1].summary = f"pack {settings.pack}, factories: {', '.join(names) or 'none'}"
    report.problems.append(
        Problem(
            project_file,
            ("pack",),
            f"pack '{settings.pack}' is not installed yet; it is needed to generate code",
            _line(project_file, ("pack",)),
            "warning",
        )
    )

    knowledge_dir = report.root / settings.knowledge
    if not knowledge_dir.is_dir():
        report.problems.append(
            Problem(
                project_file,
                ("knowledge",),
                f"knowledge folder '{settings.knowledge}' not found",
                _line(project_file, ("knowledge",)),
            )
        )
        return report

    old_override = knowledge_dir / "technical.yaml"
    if old_override.is_file():
        report.problems.append(
            Problem(
                old_override,
                (),
                "project overrides now live per factory: move this file to "
                f"{settings.knowledge}/factories/<factory>/technical.yaml",
            )
        )

    for factory in _enabled_factories(report, project_file, names):
        loaded = _load_factory(report, factory, knowledge_dir)
        if loaded is not None:
            report.factories[factory.name] = loaded

    domain = _load(report, Domain, knowledge_dir / "domain.yaml")
    if domain is not None:
        report.checked[-1].summary = (
            f"{len(domain.areas)} areas, {len(domain.entities)} entities, "
            f"{len(domain.associations)} associations, {len(domain.services)} services"
        )
    primary = next(iter(report.factories.values()), None)
    report.domain = domain
    report.technical = primary.technical if primary else None

    for loaded in report.factories.values():
        _attach(report, references.check_technical(loaded.technical), loaded.origins)
    if domain is not None and primary is not None:
        domain_file = _yaml_file(knowledge_dir / "domain.yaml")
        _attach(report, references.check_domain(domain, primary.technical), {(): domain_file})
        if logic and report.ok:
            _check_logic(report, domain, primary, domain_file, strict)
    return report


def _enabled_factories(report: Report, project_file: Path, names: list[str]) -> list[Factory]:
    installed = installed_factories()
    enabled = []
    for index, name in enumerate(names):
        if name in installed:
            enabled.append(installed[name])
        else:
            known = ", ".join(installed) or "none"
            report.problems.append(
                Problem(
                    project_file,
                    ("factories", index),
                    f"unknown factory '{name}' (installed: {known})",
                    _line(project_file, ("factories", index)),
                )
            )
    if not names:
        report.problems.append(
            Problem(
                project_file,
                ("factories",),
                "no factory is enabled",
                _line(project_file, ("factories",)),
            )
        )
    return enabled


def _load_factory(report: Report, factory: Factory, knowledge_dir: Path) -> LoadedFactory | None:
    manifest = _load(report, FactoryManifest, factory.manifest_path)
    if manifest is None:
        return None
    report.checked[-1].summary = f"packs: {', '.join(manifest.packs)}"
    manifest_file = _yaml_file(factory.manifest_path)
    if manifest.name != factory.name:
        report.problems.append(
            manifest_file.problem(
                ("name",), f"name '{manifest.name}' must match the folder name '{factory.name}'"
            )
        )
    for field_name in ("technical", "checks"):
        if not (factory.root / getattr(manifest, field_name)).is_file():
            report.problems.append(
                manifest_file.problem(
                    (field_name,),
                    f"file '{getattr(manifest, field_name)}' not found in the factory",
                )
            )
    if not (factory.root / manifest.technical).is_file():
        return None
    override = knowledge_dir / "factories" / factory.name / "technical.yaml"
    technical, origins = _load_technical(report, factory.root / manifest.technical, override)
    if technical is None:
        return None
    return LoadedFactory(factory, manifest, technical, origins, factory.root / manifest.checks)


LOGIC_PATHS = {"service": "services", "entity": "entities", "association": "associations"}


def _check_logic(
    report: Report, domain, primary: LoadedFactory, domain_file: YamlFile, strict: bool
) -> None:
    """Layer 4: load the knowledge into Prolog and ask the logic checks."""
    from harness.logic.engine import Engine, PrologUnavailable

    flow_file = primary.origins.get(("classification",)) or primary.origins[()]
    checks = [f.checks for f in report.factories.values() if f.checks.is_file()]
    try:
        engine = Engine(domain, primary.technical, checks)
    except PrologUnavailable as exc:
        report.logic = "skipped"
        severity = "error" if strict else "warning"
        report.problems.append(
            Problem(domain_file.path, (), f"logic checks skipped: {exc}", None, severity)
        )
        return
    problems = engine.problems()
    for problem in problems:
        if problem.where == "flow":
            path = ("classification", "steps", problem.name)
            report.problems.append(flow_file.problem(path, problem.message))
        elif problem.where == "operation":
            path = ("operations", problem.name)
            origin = primary.origins.get(path) or primary.origins[()]
            report.problems.append(origin.problem(path, problem.message))
        else:
            path = (LOGIC_PATHS[problem.where], problem.name)
            report.problems.append(domain_file.problem(path, problem.message))
    count = len(problems)
    report.logic = "no problems" if not count else f"{count} problem{'s' * (count != 1)}"


def _load(report: Report, model, path: Path):
    report.checked.append(Checked(path))
    file, problems = read_yaml(path)
    if file is None:
        report.problems.extend(problems)
        return None
    obj, problems = parse(model, file)
    report.problems.extend(problems)
    return obj


def _load_technical(report: Report, factory_path: Path, override_path: Path):
    """Merge a factory's technical knowledge with the project's override, remembering origins."""
    default = _load(report, Technical, factory_path)
    if default is None:
        return None, {}
    report.checked[-1].summary = (
        f"{len(default.kinds)} kinds, {len(default.operations)} operations, "
        f"{len(default.rules)} rules, "
        f"{len(default.components)} components, {len(default.askable)} askable facts"
    )
    default_file = _yaml_file(factory_path)
    origins: dict[tuple, YamlFile] = {(): default_file}
    if not override_path.is_file():
        return default, origins

    override = _load(report, TechnicalOverride, override_path)
    if override is None:
        return None, origins
    report.checked[-1].summary = "project override"
    override_file = _yaml_file(override_path)

    merged = default.model_copy(deep=True)
    for section in ("kinds", "operations", "components", "askable"):
        entries = getattr(override, section)
        getattr(merged, section).update(entries)
        for name in entries:
            origins[(section, name)] = override_file

    rules_by_id = {rule.id: index for index, rule in enumerate(merged.rules)}
    for override_index, rule in enumerate(override.rules):
        if rule.id in rules_by_id:
            merged.rules[rules_by_id[rule.id]] = rule
            merged_index = rules_by_id[rule.id]
        else:
            merged.rules.append(rule)
            merged_index = len(merged.rules) - 1
        origins[("rules", merged_index)] = _Shifted(override_file, merged_index, override_index)

    if override.classification is not None:
        merged.classification = override.classification
        origins[("classification",)] = override_file
    return merged, origins


class _Shifted:
    """Maps a merged rule index back to its position in the override file."""

    def __init__(self, file: YamlFile, merged_index: int, file_index: int):
        self.file, self.merged_index, self.file_index = file, merged_index, file_index

    def problem(self, path, message, severity):
        local = ("rules", self.file_index) + tuple(path[2:])
        return self.file.problem(local, message, severity)


def _attach(report: Report, findings, origins: dict) -> None:
    for path, message, severity in findings:
        origin = origins.get(tuple(path[:2])) or origins.get(tuple(path[:1])) or origins[()]
        report.problems.append(origin.problem(path, message, severity))


def _yaml_file(path: Path) -> YamlFile:
    file, _ = read_yaml(path)
    assert file is not None
    return file


def _line(path: Path, key: tuple) -> int | None:
    file, _ = read_yaml(path)
    return file.line_of(key) if file else None
