"""Finding the project a command works on."""

from pathlib import Path

PROJECT_FILE = "harness.yaml"


def find_project_file(start: Path) -> Path | None:
    """Look for harness.yaml in `start`, then in each parent folder."""
    start = start.resolve()
    for folder in (start, *start.parents):
        candidate = folder / PROJECT_FILE
        if candidate.is_file():
            return candidate
    return None
