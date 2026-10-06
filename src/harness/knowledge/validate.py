"""Load a project's knowledge in layers and check it.

Checks run in four layers, each only when the earlier ones pass:
    1. YAML syntax   2. shape (schemas)   3. references   4. logic (Prolog)

Layers of technical knowledge, later ones replacing earlier entries:
    harness default (ships with the harness) -> stack pack (not yet) -> project override
"""

from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from harness.knowledge import references
from harness.knowledge.load import Problem, YamlFile, parse, read_yaml
from harness.knowledge.schema import (
    Domain,
    HarnessFile,
    Technical,
    TechnicalOverride,
)
from harness.project import PROJECT_FILE, find_project_file

DEFAULT_TECHNICAL = Path(str(resources.files("harness.knowledge") / "technical.yaml"))


@dataclass
class Checked:
    """One file that was checked, with a one-line summary when it loaded."""

    path: Path
    summary: str | None = None


@dataclass
class Report:
    root: Path | None = None
    checked: list[Checked] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    domain: Domain | None = None
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
    report.checked[-1].summary = f"pack {settings.pack}, knowledge in {settings.knowledge}/"
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

    technical, technical_files = _load_technical(report, knowledge_dir / "technical.yaml")
    domain = _load(report, Domain, knowledge_dir / "domain.yaml")
    if domain is not None:
        report.checked[-1].summary = (
            f"{len(domain.areas)} areas, {len(domain.entities)} entities, "
            f"{len(domain.associations)} associations, {len(domain.services)} services"
        )
    report.domain, report.technical = domain, technical

    if technical is not None:
        _attach(report, references.check_technical(technical), technical_files)
    if domain is not None and technical is not None:
        domain_file = _yaml_file(knowledge_dir / "domain.yaml")
        _attach(report, references.check_domain(domain, technical), {(): domain_file})
        if logic and report.ok:
            _check_logic(report, domain, technical, domain_file, strict)
    return report


LOGIC_PATHS = {"service": "services", "entity": "entities", "association": "associations"}


def _check_logic(report: Report, domain, technical, domain_file: YamlFile, strict: bool) -> None:
    """Layer 4: load the knowledge into Prolog and ask the logic checks."""
    from harness.logic.engine import Engine, PrologUnavailable

    try:
        engine = Engine(domain, technical)
    except PrologUnavailable as exc:
        report.logic = "skipped"
        severity = "error" if strict else "warning"
        report.problems.append(
            Problem(domain_file.path, (), f"logic checks skipped: {exc}", None, severity)
        )
        return
    problems = engine.problems()
    for problem in problems:
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


def _load_technical(report: Report, override_path: Path):
    """Merge the harness default with the project's override, remembering where each came from."""
    default = _load(report, Technical, DEFAULT_TECHNICAL)
    if default is None:
        return None, {}
    report.checked[-1].summary = (
        f"{len(default.kinds)} kinds, {len(default.rules)} rules, "
        f"{len(default.components)} components, {len(default.askable)} askable facts"
    )
    default_file = _yaml_file(DEFAULT_TECHNICAL)
    origins: dict[tuple, YamlFile] = {(): default_file}
    if not override_path.is_file():
        return default, origins

    override = _load(report, TechnicalOverride, override_path)
    if override is None:
        return None, origins
    report.checked[-1].summary = "project override"
    override_file = _yaml_file(override_path)

    merged = default.model_copy(deep=True)
    for section in ("kinds", "components", "askable"):
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
        origin = origins.get(tuple(path[:2])) or origins[()]
        report.problems.append(origin.problem(path, message, severity))


def _yaml_file(path: Path) -> YamlFile:
    file, _ = read_yaml(path)
    assert file is not None
    return file


def _line(path: Path, key: tuple) -> int | None:
    file, _ = read_yaml(path)
    return file.line_of(key) if file else None
