import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BOOKSTORE = REPO / "examples" / "bookstore"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway copy of Bookstore's harness files, safe to break."""
    root = tmp_path / "bookstore"
    root.mkdir()
    shutil.copy(BOOKSTORE / "harness.yaml", root)
    shutil.copytree(BOOKSTORE / ".harness", root / ".harness")
    return root


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1))
