import json

from conftest import BOOKSTORE, REPO, edit
from typer.testing import CliRunner

from harness import __version__
from harness.cli import app
from harness.knowledge.schema import SCHEMAS

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("version", "validate", "schema"):
        assert command in result.output


def test_version_prints_installed_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"harness {__version__}"


def test_validate_bookstore_ok():
    result = runner.invoke(app, ["validate", "--project", str(BOOKSTORE)])
    assert result.exit_code == 0, result.output
    assert "OK: 0 errors, 1 warning" in result.output


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
