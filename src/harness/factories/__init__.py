"""Software factories: each folder here builds one kind of software.

A factory is a folder with a factory.yaml manifest, its technical knowledge
(technical.yaml) and its logic checks (checks.pl). They are found by name.
"""

from dataclasses import dataclass
from importlib import resources
from pathlib import Path

FACTORIES_DIR = Path(str(resources.files("harness.factories")))
MANIFEST = "factory.yaml"


@dataclass(frozen=True)
class Factory:
    name: str  # the folder name
    root: Path

    @property
    def manifest_path(self) -> Path:
        return self.root / MANIFEST


def installed_factories(folder: Path = FACTORIES_DIR) -> dict[str, Factory]:
    """Every subfolder with a factory.yaml, by name, in alphabetical order."""
    return {
        path.name: Factory(path.name, path)
        for path in sorted(folder.iterdir())
        if path.is_dir() and (path / MANIFEST).is_file()
    }
