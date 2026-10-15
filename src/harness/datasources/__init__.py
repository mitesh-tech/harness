"""Source-type plugins: how the harness works with one kind of data source.

A project's data.yaml names its sources and gives each a `type`. The plugin
for that type (a folder here) says what may be written in data.yaml for it,
what it can do, and how to read a real source's structure:

    <type>/source.yaml    description, capabilities, converters
    <type>/schema.py      Connection, EntityMapping, RelationshipMapping (pydantic)
    <type>/introspect.py  read_structure(url): the tables and columns that exist
"""

import importlib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import ModuleType

import yaml
from pydantic import Field

from harness.knowledge.schema import KebabName, Model, SnakeName, Text

SOURCES_DIR = Path(str(resources.files("harness.datasources")))
MANIFEST = "source.yaml"


class Converter(Model):
    description: Text
    types: list[SnakeName] = Field(min_length=1)  # attribute types it fits


class SourceManifest(Model):
    name: KebabName
    description: Text
    # What the type can do: the kinds' persistence names, plus filter and paging.
    capabilities: list[SnakeName] = Field(min_length=1)
    converters: dict[SnakeName, Converter] = Field(default_factory=dict)


@dataclass(frozen=True)
class SourceType:
    name: str
    manifest: SourceManifest
    schema: ModuleType  # Connection, EntityMapping, RelationshipMapping

    def introspect(self) -> ModuleType | None:
        try:
            return importlib.import_module(f"harness.datasources.{self.module}.introspect")
        except ModuleNotFoundError:
            return None

    @property
    def module(self) -> str:
        return self.name.replace("-", "_")


def installed_source_types(folder: Path = SOURCES_DIR) -> dict[str, SourceType]:
    """Every source type: a subfolder with a source.yaml and a schema module."""
    found = {}
    for path in sorted(folder.iterdir()):
        if not (path / MANIFEST).is_file():
            continue
        manifest = SourceManifest.model_validate(yaml.safe_load((path / MANIFEST).read_text()))
        schema = importlib.import_module(f"harness.datasources.{path.name}.schema")
        found[manifest.name] = SourceType(manifest.name, manifest, schema)
    return found
