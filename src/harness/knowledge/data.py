"""Load a project's data.yaml with its source-type plugins, and check it against the domain.

data.yaml says where each entity and relationship is stored. Its general shape
is checked here; each source's connection and each entry are checked by the
plugin for that source's type (harness.datasources.<type>).
"""

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from harness.datasources import SourceType, installed_source_types
from harness.knowledge.load import Problem, YamlFile, parse, parse_at, read_yaml
from harness.knowledge.schema import DataFile, Domain, SourceSpec

Finding = tuple[tuple[str | int, ...], str, str]


@dataclass
class Source:
    name: str
    spec: SourceSpec
    type: SourceType
    connection: object  # the plugin's Connection

    def url(self, root: Path) -> str | None:
        """The connection URL: from the environment, else the local default."""
        url = os.environ.get(self.connection.url_env) or self.connection.default_url
        prefix = "sqlite:///"
        if url and url.startswith(prefix) and not Path(url[len(prefix) :]).is_absolute():
            # a relative SQLite path is relative to the project, not to where we run
            url = prefix + str((root / url[len(prefix) :]).resolve())
        return url


@dataclass
class LoadedData:
    file: YamlFile
    sources: dict[str, Source] = field(default_factory=dict)
    entities: dict[str, object] = field(default_factory=dict)  # name -> plugin EntityMapping
    relationships: dict[str, object] = field(default_factory=dict)  # name -> RelationshipMapping

    @property
    def path(self) -> Path:
        return self.file.path

    def summary(self) -> str:
        counts = [
            (len(self.sources), "source"),
            (len(self.entities), "entity"),
            (len(self.relationships), "relationship"),
        ]
        return ", ".join(f"{n} {_plural(word, n)}" for n, word in counts)


def _plural(word: str, n: int) -> str:
    if n == 1:
        return word
    return word[:-1] + "ies" if word.endswith("y") else word + "s"


def load_data(path: Path) -> tuple[LoadedData | None, list[Problem]]:
    file, problems = read_yaml(path)
    if file is None:
        return None, problems
    data, problems = parse(DataFile, file)
    if data is None:
        return None, problems
    loaded = LoadedData(file)
    types = installed_source_types()

    for name, spec in data.sources.items():
        at = ("sources", name)
        source_type = types.get(spec.type)
        if source_type is None:
            known = ", ".join(types) or "none"
            problems.append(
                file.problem(
                    at + ("type",), f"unknown source type '{spec.type}' (installed: {known})"
                )
            )
            continue
        connection, found = parse_at(source_type.schema.Connection, file, at + ("connection",))
        problems += found
        if connection is not None:
            loaded.sources[name] = Source(name, spec, source_type, connection)

    for section, model_name in (
        ("entities", "EntityMapping"),
        ("relationships", "RelationshipMapping"),
    ):
        for name, entry in getattr(data, section).items():
            at = (section, name)
            source_name = entry.get("source")
            source = loaded.sources.get(source_name) if isinstance(source_name, str) else None
            if source is None:
                if source_name is None:
                    message = "'source' is required"
                elif source_name in data.sources:
                    continue  # its source has an error of its own
                else:
                    known = ", ".join(data.sources)
                    message = f"unknown source '{source_name}' (sources: {known})"
                problems.append(file.problem(at + ("source",), message))
                continue
            model = getattr(source.type.schema, model_name)
            mapping, found = parse_at(model, file, at)
            problems += found
            if mapping is not None:
                getattr(loaded, section)[name] = mapping
    return loaded, problems


def check_data(data: LoadedData, domain: Domain) -> Iterator[Finding]:
    """The entries match the domain: every attribute has a column, each link is stored."""
    yield from _check_entities(data, domain)
    yield from _check_relationships(data, domain)
    for name in domain.entities:
        if name not in data.entities:
            yield (
                ("entities",),
                f"{name} has no entry: tasks about it end as no_mapping",
                "warning",
            )
    for name, a in domain.associations.items():
        if a.kind == "aggregation" and name not in data.relationships:
            yield (
                ("relationships",),
                f"{name} has no entry: tasks about it end as no_mapping",
                "warning",
            )


def _check_entities(data: LoadedData, domain: Domain) -> Iterator[Finding]:
    children = {a.child: a.parent for a in domain.associations.values() if a.kind == "composition"}
    for name, mapping in data.entities.items():
        at = ("entities", name)
        entity = domain.entities.get(name)
        if entity is None:
            known = ", ".join(domain.entities)
            yield at, f"unknown entity '{name}' (entities in domain.yaml: {known})", "error"
            continue
        expected = list(entity.attributes)
        if entity.lifecycle:
            expected.append(entity.lifecycle.field)
        for attribute in expected:
            if attribute not in mapping.columns:
                yield (
                    at + ("columns",),
                    f"'{attribute}' has no column; give one, or `{attribute}: "
                    "{ stored: false }` if it is not stored",
                    "error",
                )
        converters = data.sources[mapping.source].type.manifest.converters
        for attribute, spec in mapping.columns.items():
            where = at + ("columns", attribute)
            if attribute not in expected:
                yield where, f"'{attribute}' is not an attribute of {name}", "error"
                continue
            convert = getattr(spec, "convert", None)
            if convert is None:
                continue
            converter = converters.get(convert)
            kind = entity.attributes[attribute].type if attribute in entity.attributes else "text"
            if converter is None:
                known = ", ".join(converters) or "none"
                yield (
                    where + ("convert",),
                    f"unknown converter '{convert}' (known: {known})",
                    "error",
                )
            elif kind not in converter.types:
                yield (
                    where + ("convert",),
                    f"converter '{convert}' is for {', '.join(converter.types)}, "
                    f"but {attribute} is {kind}",
                    "error",
                )
        if name in children and mapping.parent_key is None:
            yield (
                at,
                f"{name} is part of a {children[name]}; add `parent_key`, the column "
                f"pointing at its {children[name]}",
                "error",
            )
        if name not in children and mapping.parent_key is not None:
            yield at + ("parent_key",), f"{name} is not part of another entity", "error"


def _check_relationships(data: LoadedData, domain: Domain) -> Iterator[Finding]:
    for name, mapping in data.relationships.items():
        at = ("relationships", name)
        association = domain.associations.get(name)
        if association is None:
            known = ", ".join(n for n, a in domain.associations.items() if a.kind == "aggregation")
            yield at, f"unknown relationship '{name}' (aggregations: {known})", "error"
            continue
        if association.kind == "composition":
            yield (
                at,
                f"'{name}' is a composition: it is stored by {association.child}'s parent_key",
                "error",
            )
            continue
        if association.cardinality == "many_to_many":
            if mapping.stored_as != "link_table":
                yield at, f"'{name}' is many-to-many: give a link table with keys", "error"
            elif set(mapping.keys) != {association.parent, association.child}:
                yield (
                    at + ("keys",),
                    f"keys are needed for {association.parent} and {association.child}",
                    "error",
                )
        else:
            if mapping.stored_as != "reference":
                yield (
                    at,
                    f"'{name}' is one-to-many: give the reference column on "
                    f"{association.child}'s table",
                    "error",
                )
                continue
            child = data.entities.get(association.child)
            table = mapping.column.split(".")[0]
            if child is not None and child.table != table:
                yield (
                    at + ("column",),
                    f"the reference column belongs on {association.child}'s table "
                    f"({child.table}), not {table}",
                    "error",
                )
