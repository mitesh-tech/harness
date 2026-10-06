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
    shutil.copytree(BOOKSTORE / ".harness", root / ".harness")
    return root


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path.name}"
    path.write_text(text.replace(old, new, 1))
