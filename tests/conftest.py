import shutil
from pathlib import Path

import httpx
import pytest
from semif_recording import RecordingTransport, ReplayTransport

REPO = Path(__file__).resolve().parents[1]
BOOKSTORE = REPO / "examples" / "bookstore"
SEMIF_URL = "http://localhost:8010/v1/systemone"


def pytest_addoption(parser):
    group = parser.getgroup("semif")
    group.addoption(
        "--record-semif",
        action="store_true",
        help="Ask the live SemIf server and save its answers for replay.",
    )
    group.addoption(
        "--live-semif", action="store_true", help="Ask the live SemIf server; save nothing."
    )


@pytest.fixture(scope="session")
def semif_client(request):
    """An HTTP client for SemIf: replaying recorded answers unless asked to go live."""
    if request.config.getoption("--record-semif"):
        transport = RecordingTransport()
        yield httpx.Client(transport=transport, timeout=60)
        transport.save()
    elif request.config.getoption("--live-semif"):
        yield httpx.Client(timeout=60)
    else:
        yield httpx.Client(transport=ReplayTransport())


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A throwaway copy of Bookstore's harness files, safe to break."""
    root = tmp_path / "bookstore"
    root.mkdir()
    shutil.copy(BOOKSTORE / "harness.yaml", root)
    shutil.copytree(
        BOOKSTORE / ".harness", root / ".harness", ignore=shutil.ignore_patterns("runs")
    )
    return root


def factory_override(project: Path, factory: str = "crud-api") -> Path:
    """Where a project overrides a factory's technical knowledge (folder created)."""
    path = project / ".harness" / "factories" / factory / "technical.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1))


# ── Copies of the whole Bookstore app, for generate and verify ─────

APP_IGNORE = shutil.ignore_patterns(".venv", "runs", "*.db", "__pycache__", ".pytest_cache")


def copy_bookstore(folder: Path) -> Path:
    """The Bookstore app with its knowledge, without local files (venv, runs, database)."""
    shutil.copytree(BOOKSTORE, folder, ignore=APP_IGNORE)
    return folder


def git_init(repo: Path) -> Path:
    """Make a folder a git repository with one commit (CI machines have no git identity)."""
    import subprocess

    for command in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "start"],
    ):
        subprocess.run(command, cwd=repo, check=True)
    return repo
