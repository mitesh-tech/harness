"""Stack packs: how generated code fits a project's stack.

A pack is a folder with a pack.yaml (where files go, how the app's own
pieces are imported, the commands that test and lint) and templates/ for
what is the same for every factory (tests from a spec). Factories and data
source types keep their own templates per pack:
    factories/<factory>/templates/<pack>/   datasources/<type>/templates/<pack>/
"""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml
from pydantic import Field

from harness.knowledge.schema import KebabName, Model, Text

PACKS_DIR = Path(str(resources.files("harness.packs")))
MANIFEST = "pack.yaml"


class Layout(Model):
    models: Text
    schemas: Text
    repositories: Text
    api: Text
    rules: Text
    tests: Text
    problems: Text  # a shared helper module for error responses


class Registration(Model):
    """Files that get generated lines between the markers."""

    routers: Text  # where routers are included
    models: Text  # where models are imported, so their tables exist


class PackManifest(Model):
    name: KebabName
    description: Text
    layout: Layout
    imports: dict[str, Text]  # the app's own pieces, as module:name
    registration: Registration
    markers: dict[str, Text]  # start / end
    commands: dict[str, list[Text]] = Field(default_factory=dict)  # test, lint, python
    fixtures: dict[str, Text] = Field(default_factory=dict)  # client, session
    # How to build the app, to read its own OpenAPI: module:function and its arguments.
    artifacts: list[Text] = Field(default_factory=list)  # left behind by running the stack
    app_factory: Text | None = None
    app_factory_args: list[object] = Field(default_factory=list)


@dataclass(frozen=True)
class Pack:
    name: str
    root: Path
    manifest: PackManifest

    @property
    def templates(self) -> Path:
        return self.root / "templates"


def installed_packs(folder: Path = PACKS_DIR) -> dict[str, Pack]:
    found = {}
    for path in sorted(folder.iterdir()):
        if (path / MANIFEST).is_file():
            manifest = PackManifest.model_validate(yaml.safe_load((path / MANIFEST).read_text()))
            found[manifest.name] = Pack(manifest.name, path, manifest)
    return found
