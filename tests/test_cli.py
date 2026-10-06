import json
import shutil
import subprocess

import pytest
from conftest import BOOKSTORE, REPO, edit
from typer.testing import CliRunner

from harness import __version__
from harness.cli import app
from harness.knowledge.schema import SCHEMAS

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("version", "validate", "schema", "kb"):
        assert command in result.output


def test_version_prints_installed_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"harness {__version__}"


def test_validate_bookstore_ok():
    result = runner.invoke(app, ["validate", "--project", str(BOOKSTORE), "--strict"])
    assert result.exit_code == 0, result.output
    assert "logic checks (Prolog)                no problems" in result.output
    assert "OK: 0 errors, 1 warning" in result.output


def test_validate_reports_logic_errors(project):
    edit(
        project / ".harness/domain.yaml",
        "acts_on: Review\n    within: Book\n\n  list_reviews",
        "acts_on: Review\n\n  list_reviews",
    )
    result = runner.invoke(app, ["validate", "--project", str(project)])
    assert result.exit_code == 1
    assert ".harness/domain.yaml:" in result.output
    assert "services › add_review" in result.output
    assert "composition_child_outside_parent" in result.output


def test_kb_query_prints_answers():
    result = runner.invoke(
        app, ["kb", "query", "component(add_review, C, Rule)", "--project", str(BOOKSTORE)]
    )
    assert result.exit_code == 0, result.output
    assert "C = parent_exists_check   Rule = composition-nested" in result.output
    assert "3 answers" in result.output


def test_kb_query_yes_no_and_errors():
    def ask(goal):
        return runner.invoke(app, ["kb", "query", goal, "--project", str(BOOKSTORE)])

    assert ask("method(add_book, post).").output.strip() == "true"
    assert ask("kind(S, teleport)").output.strip() == "false (no answers)"
    broken = ask("method(add_book M)")
    assert broken.exit_code == 1
    assert "Prolog error" in broken.output


def test_kb_export_loads_in_swipl(tmp_path):
    swipl = shutil.which("swipl")
    if swipl is None:
        pytest.skip("swipl not installed")
    out = tmp_path / "kb.pl"
    result = runner.invoke(app, ["kb", "export", "--project", str(BOOKSTORE), "--out", str(out)])
    assert result.exit_code == 0, result.output
    run = subprocess.run(
        [swipl, "-q", "-g", "method(add_book, M), write(M)", "-t", "halt", str(out)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert run.stdout == "post"
    assert run.stderr == ""  # loads without warnings


def test_kb_commands_refuse_broken_knowledge(project):
    edit(project / ".harness/domain.yaml", "kind: composition", "kind: composite")
    result = runner.invoke(app, ["kb", "export", "--project", str(project)])
    assert result.exit_code == 1
    assert "fix them first" in result.output


def test_validate_reports_errors(project):
    edit(project / ".harness/domain.yaml", "kind: composition", "kind: composite")
    result = runner.invoke(app, ["validate", "--project", str(project)])
    assert result.exit_code == 1
    assert "associations › book_reviews › kind" in result.output
    assert "FAILED: 1 error, 1 warning" in result.output


def test_schema_export_writes_all_schemas(tmp_path):
    result = runner.invoke(app, ["schema", "export", "--out", str(tmp_path)])
    assert result.exit_code == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        f"{name}.schema.json" for name in SCHEMAS
    )


def test_committed_schemas_are_up_to_date(tmp_path):
    runner.invoke(app, ["schema", "export", "--out", str(tmp_path)])
    for generated in tmp_path.iterdir():
        committed = REPO / "schemas" / generated.name
        assert json.loads(committed.read_text()) == json.loads(generated.read_text()), (
            f"schemas/{generated.name} is out of date; run `uv run harness schema export`"
        )
