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

from pydantic import ValidationError

from harness.factories import Factory, installed_factories
from harness.knowledge import references
from harness.knowledge.data import LoadedData, check_data, load_data
from harness.knowledge.load import Problem, YamlFile, parse, read_yaml
from harness.knowledge.schema import (
    ApiConventions,
    ClassifyEvals,
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
    rules: Path | None = None

    @property
    def logic(self) -> list[Path]:
        """The factory's Prolog files: its decision rules, then its checks."""
        return [p for p in (self.rules, self.checks) if p is not None and p.is_file()]


@dataclass
class Report:
    root: Path | None = None
    checked: list[Checked] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    settings: HarnessFile | None = None
    factories: dict[str, LoadedFactory] = field(default_factory=dict)  # available ones
    planned: dict[str, FactoryManifest] = field(default_factory=dict)  # offered, not built yet
    domain: Domain | None = None
    # Until the factory router exists, commands use the first enabled factory's knowledge.
    technical: Technical | None = None
    evals: ClassifyEvals | None = None  # .harness/evals/classify.yaml, when present
    data: LoadedData | None = None  # .harness/data.yaml: where things are stored
    introspection: str | None = None  # summary of --introspect, when it ran
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


def validate_project(
    start: Path, strict: bool = False, logic: bool = True, introspect: bool = False
) -> Report:
    """Check a project's knowledge.

    strict:     skipped logic checks (no Prolog) count as an error.
    logic:      run layer 4; turn off when only layers 1-3 are needed.
    introspect: also compare data.yaml with the real data sources (read-only).
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
    _check_version(report, project_file, settings.harness)
    from harness.packs import installed_packs

    packs = installed_packs()
    if settings.pack not in packs:
        report.problems.append(
            Problem(
                project_file,
                ("pack",),
                f"pack '{settings.pack}' is not installed (installed: {', '.join(packs)}); "
                "it is needed to generate code",
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
        if isinstance(loaded, LoadedFactory):
            report.factories[factory.name] = loaded
        elif loaded is not None:
            report.planned[factory.name] = loaded
    if not report.factories and names and report.ok:
        report.problems.append(
            Problem(project_file, ("factories",), "no available factory is enabled", None)
        )

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
        _load_data_file(report, knowledge_dir / DATA, domain)
        if logic and report.ok:
            _check_logic(report, domain, primary, domain_file, strict)
        if introspect and report.data is not None:
            _introspect(report)
        _load_evals(report, knowledge_dir / EVALS, domain, primary.technical)
    return report


DATA = Path("data.yaml")


def _load_data_file(report: Report, path: Path, domain: Domain) -> None:
    if not path.is_file():
        report.problems.append(
            Problem(
                path,
                (),
                "no data.yaml: nothing says where entities are stored, "
                "so every task ends as no_mapping",
                None,
                "warning",
            )
        )
        return
    report.checked.append(Checked(path))
    data, problems = load_data(path)
    report.problems.extend(problems)
    if data is None:
        return
    report.checked[-1].summary = data.summary()
    report.data = data
    _attach(report, check_data(data, domain), {(): data.file})


def _introspect(report: Report) -> None:
    """Compare data.yaml with each real source; differences are warnings."""
    data, found, unread = report.data, 0, 0
    for source in data.sources.values():
        reader = source.type.introspect()
        if reader is None:
            continue
        url = source.url(report.root)
        if url is None:
            report.problems.append(
                data.file.problem(
                    ("sources", source.name),
                    f"cannot read {source.name}: set {source.connection.url_env}",
                    "warning",
                )
            )
            unread += 1
            continue
        try:
            structure = reader.read_structure(url)
        except Exception as exc:  # a missing driver, an unreachable database, …
            report.problems.append(
                data.file.problem(
                    ("sources", source.name), f"cannot read {source.name}: {exc}", "warning"
                )
            )
            unread += 1
            continue
        for path, message in _differences(data, source.name, structure):
            report.problems.append(data.file.problem(path, message, "warning"))
            found += 1
    summary = "matches" if not found else f"{found} difference{'s' * (found != 1)}"
    if unread:
        summary = f"{unread} source{'s' * (unread != 1)} not read" + (
            f", {summary}" if found else ""
        )
    report.introspection = summary


def _differences(data: LoadedData, source: str, structure: dict[str, set[str]]):
    """Tables and columns data.yaml names that the real source lacks, and unmapped columns."""
    for name, mapping in data.entities.items():
        if mapping.source != source:
            continue
        at = ("entities", name)
        if mapping.table not in structure:
            yield (
                at + ("table",),
                f"table '{mapping.table}' not found (it is created when {name}'s first API "
                "is generated)",
            )
            continue
        columns = structure[mapping.table]
        mapped = {mapping.column_of(a) for a in mapping.columns} | {mapping.parent_key}
        for attribute in mapping.columns:
            column = mapping.column_of(attribute)
            if column is not None and column not in columns:
                yield at + ("columns", attribute), f"column '{column}' not found in {mapping.table}"
        if mapping.parent_key and mapping.parent_key not in columns:
            yield (
                at + ("parent_key",),
                f"column '{mapping.parent_key}' not found in {mapping.table}",
            )
        for column in sorted(columns - mapped - _reference_columns(data, mapping.table)):
            yield at, f"{mapping.table}.{column} is not in the domain (ignored)"
    for name, mapping in data.relationships.items():
        if mapping.source != source:
            continue
        at = ("relationships", name)
        if mapping.stored_as == "link_table":
            if mapping.table not in structure:
                yield at + ("table",), f"table '{mapping.table}' not found"
                continue
            for entity, key in mapping.keys.items():
                if key not in structure[mapping.table]:
                    yield at + ("keys", entity), f"column '{key}' not found in {mapping.table}"
        else:
            table, column = mapping.column.split(".")
            if table in structure and column not in structure[table]:
                yield at + ("column",), f"column '{column}' not found in {table}"


def _reference_columns(data: LoadedData, table: str) -> set[str]:
    """Columns of `table` that store a one-to-many relationship."""
    return {
        m.column.split(".")[1]
        for m in data.relationships.values()
        if m.stored_as == "reference" and m.column.split(".")[0] == table
    }


EVALS = Path("evals") / "classify.yaml"


def _load_evals(report: Report, path: Path, domain: Domain, technical: Technical) -> None:
    if not path.is_file():
        return
    evals = _load(report, ClassifyEvals, path)
    if evals is None:
        return
    groups = {case.group for case in evals.cases if case.group}
    report.checked[-1].summary = f"{len(evals.cases)} cases, {len(groups)} paraphrase groups"
    report.evals = evals
    factories = set(report.factories) | set(report.planned)
    findings = references.check_evals(evals, domain, technical, factories)
    _attach(report, findings, {(): _yaml_file(path)})


def _check_version(report: Report, project_file: Path, wanted: str | None) -> None:
    """The installed harness must be one the project says it works with."""
    if wanted is None:
        return
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    from harness import __version__

    where = ("harness",)
    try:
        allowed = SpecifierSet(wanted)
    except InvalidSpecifier:
        report.problems.append(
            Problem(
                project_file,
                where,
                f"'{wanted}' is not a version range (e.g. \">=0.1,<0.2\")",
                _line(project_file, where),
            )
        )
        return
    if __version__ not in allowed:
        report.problems.append(
            Problem(
                project_file,
                where,
                f"this project works with harness {wanted}, but {__version__} is installed",
                _line(project_file, where),
                "warning",
            )
        )


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


def _load_factory(
    report: Report, factory: Factory, knowledge_dir: Path
) -> LoadedFactory | FactoryManifest | None:
    """An available factory with its knowledge, a planned factory's manifest, or None."""
    manifest = _load(report, FactoryManifest, factory.manifest_path)
    if manifest is None:
        return None
    manifest_file = _yaml_file(factory.manifest_path)
    if manifest.name != factory.name:
        report.problems.append(
            manifest_file.problem(
                ("name",), f"name '{manifest.name}' must match the folder name '{factory.name}'"
            )
        )
    if not manifest.available:
        report.checked[-1].summary = "planned: offered to the router, not built yet"
        return manifest
    report.checked[-1].summary = f"packs: {', '.join(manifest.packs)}"
    for field_name in ("technical", "rules", "checks"):
        if (
            getattr(manifest, field_name)
            and not (factory.root / getattr(manifest, field_name)).is_file()
        ):
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
    rules = factory.root / manifest.rules if manifest.rules else None
    return LoadedFactory(
        factory, manifest, technical, origins, factory.root / manifest.checks, rules
    )


LOGIC_PATHS = {"service": "services", "entity": "entities", "association": "associations"}


def _check_logic(
    report: Report, domain, primary: LoadedFactory, domain_file: YamlFile, strict: bool
) -> None:
    """Layer 4: load the knowledge into Prolog and ask the logic checks."""
    from harness.logic.engine import Engine, PrologUnavailable

    flow_file = primary.origins.get(("classification",)) or primary.origins[()]
    checks = [path for f in report.factories.values() for path in f.logic]
    try:
        engine = Engine(domain, primary.technical, checks, report.data)
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
        elif problem.where == "relationship" and report.data is not None:
            path = ("relationships", problem.name)
            report.problems.append(report.data.file.problem(path, problem.message))
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
        f"{len(default.components)} components"
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

    if override.api is not None:
        conventions = merged.api.model_dump()
        for section in ("paths", "paging", "errors"):
            conventions[section].update(getattr(override.api, section))
        try:
            merged.api = ApiConventions.model_validate(conventions)
        except ValidationError as exc:
            for error in exc.errors():
                path = ("api",) + tuple(error["loc"])
                message = error["msg"].removeprefix("Value error, ")
                report.problems.append(override_file.problem(path, message))
            return None, origins
        origins[("api",)] = override_file

    known = {rule.id for rule in merged.rules}
    for index, rule_id in enumerate(override.remove_rules):
        if rule_id not in known:
            report.problems.append(
                override_file.problem(("remove_rules", index), f"no rule '{rule_id}' to remove")
            )
    if override.remove_rules:
        # Later rules move up; re-key their origins so errors keep pointing at their file.
        position, kept = {}, []
        for index, rule in enumerate(merged.rules):
            if rule.id not in override.remove_rules:
                position[index] = len(kept)
                kept.append(rule)
        merged.rules = kept
        for key in [k for k in origins if k[:1] == ("rules",)]:
            origin = origins.pop(key)
            if key[1] in position:
                origins[("rules", position[key[1]]) + key[2:]] = origin
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
