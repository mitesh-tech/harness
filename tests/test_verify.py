"""Verify and report (S5b), on copies of the Bookstore app in git repositories."""

import subprocess
from pathlib import Path

import pytest
from conftest import SEMIF_URL, copy_bookstore, git_init
from typer.testing import CliRunner

import harness.cli
from harness.cli import app
from harness.generate import generate
from harness.knowledge.validate import validate_project
from harness.packs import installed_packs
from harness.resolve import confirm, factory_design, resolve_service
from harness.semif.http import SemIfHttp
from harness.verify import Tools, _artifact, _lint, verify

runner = CliRunner()


def generated_repo(path: Path, service: str = "add_review"):
    """A git copy of Bookstore with one service generated into a worktree (not verified yet)."""
    repo = git_init(copy_bookstore(path / "shop"))
    report = validate_project(repo)
    folder = resolve_service(report, service).folder
    confirm(report, folder, approve=True)
    generation = generate(report, folder)
    assert generation.ok
    return repo, folder, generation


def head(cwd: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def remove_worktrees(tmp_path):
    yield
    for worktree in (tmp_path / "shop-harness").glob("*"):
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)], cwd=tmp_path / "shop"
        )


# ── All checks pass ─────────────────────────────────────────────────


def test_all_checks_pass_and_the_branch_is_committed(tmp_path, remove_worktrees):
    repo, folder, generation = generated_repo(tmp_path)
    result = verify(validate_project(repo), folder)
    assert [(c.name, c.status) for c in result.checks] == [
        ("spec tests", "pass"),
        ("project tests", "pass"),
        ("lint", "pass"),
        ("contract", "pass"),
        ("scope", "pass"),
        ("untouched", "pass"),
    ]
    assert result.ok and result.warnings == []
    assert head(generation.workspace).startswith(result.commit)  # the branch's new commit
    assert head(repo, generation.branch) != generation.base  # committed on the branch
    log = subprocess.run(
        ["git", "log", "--format=%s", "-1", generation.branch],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout
    assert log.strip() == f"harness: add_review (run {folder.name})"

    report = (folder / "report.md").read_text()
    assert report.startswith("# add_review: ✓ ready to merge")
    for section in ("## Checks", "## Files", "## Decisions", "## Where", "## Next"):
        assert section in report
    assert (
        "| contract | ✓ POST /books/{book_id}/reviews, 3 statuses, 2 fields in, 3 out |" in report
    )
    assert f"`git worktree remove {generation.workspace}`" in report

    trace = (folder / "trace.jsonl").read_text()
    assert '"stage": "verify", "step": "contract"' in trace


# ── Failing checks: nothing is committed ────────────────────────────


def test_unplanned_change_fails_scope_and_nothing_is_committed(tmp_path, remove_worktrees):
    repo, folder, generation = generated_repo(tmp_path)
    (generation.workspace / "app/extra.py").write_text("x = 1\n")
    result = verify(validate_project(repo), folder)
    scope = next(c for c in result.checks if c.name == "scope")
    assert scope.status == "fail" and scope.items == ["app/extra.py"]
    assert not result.ok and result.commit is None
    assert head(repo, generation.branch) == generation.base
    assert (folder / "report.md").read_text().startswith("# add_review: ✗ not ready")


def test_hand_edit_fails_untouched_and_verify_again_after_the_fix(tmp_path, remove_worktrees):
    repo, folder, generation = generated_repo(tmp_path)
    route = generation.workspace / "app/api/add_review.py"
    original = route.read_text()
    route.write_text(original + "# edited\n")
    first = verify(validate_project(repo), folder)
    untouched = next(c for c in first.checks if c.name == "untouched")
    assert untouched.status == "fail" and untouched.items == ["app/api/add_review.py"]

    route.write_text(original)  # the fix
    again = runner.invoke(app, ["verify", folder.name, "-p", str(repo)])
    assert again.exit_code == 0, again.output
    assert "OK: ready to merge (commit" in again.output


def test_a_broken_project_test_fails(tmp_path, remove_worktrees):
    repo, folder, generation = generated_repo(tmp_path)
    (generation.workspace / "tests/test_existing.py").write_text(
        "def test_broken():\n    assert False\n"
    )
    result = verify(validate_project(repo), folder)
    project = next(c for c in result.checks if c.name == "project tests")
    assert project.status == "fail"
    assert any("test_broken" in item for item in project.items)


# ── The contract (the factory's check) ──────────────────────────────


class FakeTools:
    def __init__(self, document):
        self.document = document

    def app_openapi(self):
        return self.document


@pytest.fixture(scope="module")
def contract_parts(tmp_path_factory):
    shop = copy_bookstore(tmp_path_factory.mktemp("contract") / "shop")
    report = validate_project(shop)
    spec = resolve_service(report, "add_review").spec
    design = factory_design(report.factories["crud-api"])
    return design, spec


def test_contract_finds_a_missing_status(contract_parts):
    design, spec = contract_parts
    document = design.to_openapi(spec)  # the app as the spec describes it…
    del document["paths"]["/books/{book_id}/reviews"]["post"]["responses"]["404"]  # …minus one
    [check] = design.verify(spec, FakeTools(document))
    assert check.status == "fail"
    assert check.items == ["statuses missing in the app: 404"]


def test_contract_finds_field_differences(contract_parts):
    design, spec = contract_parts
    document = design.to_openapi(spec)
    body = document["components"]["schemas"]["ReviewCreate"]
    body["properties"]["stars"] = {"type": "integer"}
    body["required"] = []
    [check] = design.verify(spec, FakeTools(document))
    assert check.items == [
        "request fields not in the spec: stars",
        "request required fields differ: spec ['rating'], app []",
    ]


def test_contract_when_the_app_can_not_be_built(contract_parts):
    design, spec = contract_parts

    class Broken:
        def app_openapi(self):
            raise RuntimeError("ImportError: no module named app.api.add_review")

    [check] = design.verify(spec, Broken())
    assert check.status == "fail"
    assert "could not be read" in check.detail


def test_lint_without_the_tool_is_skipped(tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    class Generation:
        written = ["app/api/x.py"]

    pack = installed_packs()["python-fastapi"]
    check = _lint(tmp_path, pack, Generation(), missing)
    assert check.status == "skip"


def test_tools_need_an_app_factory(tmp_path):
    pack = installed_packs()["python-fastapi"]
    manifest = pack.manifest.model_copy(update={"app_factory": None})
    tools = Tools(tmp_path, type(pack)(pack.name, pack.root, manifest), subprocess.run)
    with pytest.raises(RuntimeError, match="doesn't say how to build the app"):
        tools.app_openapi()


# ── harness run ─────────────────────────────────────────────────────


@pytest.fixture
def replayed(monkeypatch, semif_client):
    monkeypatch.setattr(
        harness.cli, "make_model", lambda report: SemIfHttp(SEMIF_URL, client=semif_client)
    )


def test_run_from_task_to_a_ready_branch(tmp_path, replayed, remove_worktrees):
    repo = git_init(copy_bookstore(tmp_path / "shop"))
    result = runner.invoke(
        app,
        [
            "run",
            "Let staff add a new publisher through a new endpoint",
            "--approve",
            "-p",
            str(repo),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Service   add_publisher (new)" in result.output
    assert "  wrote   app/models/publisher.py" in result.output  # a new table
    assert "✓ contract       POST /publishers" in result.output
    assert "OK: ready to merge (commit" in result.output
    report = next((repo / ".harness/runs").glob("*/report.md")).read_text()
    assert "add_publisher: { description: Let staff add a new publisher" in report  # next step


def test_run_asks_and_lets_you_edit(tmp_path, replayed, remove_worktrees, monkeypatch):
    repo = git_init(copy_bookstore(tmp_path / "shop"))
    monkeypatch.setenv("EDITOR", "true")  # an editor that changes nothing
    result = runner.invoke(
        app,
        ["run", "Let staff add a new publisher through a new endpoint", "-p", str(repo)],
        input="e\na\n",
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Approve, edit or reject the spec?") == 2
    assert "OK: ready to merge" in result.output


def test_run_reject(tmp_path, replayed):
    repo = git_init(copy_bookstore(tmp_path / "shop"))
    result = runner.invoke(
        app,
        ["run", "Let staff add a new publisher through a new endpoint", "-p", str(repo)],
        input="r\n",
    )
    assert result.exit_code == 2
    assert "Rejected: nothing was generated." in result.output


def test_run_approve_refuses_open_questions(tmp_path, replayed):
    repo = git_init(copy_bookstore(tmp_path / "shop"))
    result = runner.invoke(
        app, ["run", "List publishers by name through a new endpoint", "--approve", "-p", str(repo)]
    )
    assert result.exit_code == 2
    assert "open question: Does the task list Publishers filtered by their name?" in result.output
    assert "Not confirmed: the spec has open points" in result.output


def test_stack_leftovers_are_not_changes():
    pack = installed_packs()["python-fastapi"]
    assert _artifact("app/__pycache__/db.cpython-312.pyc", pack)
    assert _artifact(".pytest_cache/v/cache/nodeids", pack)
    assert not _artifact("app/api/add_review.py", pack)
