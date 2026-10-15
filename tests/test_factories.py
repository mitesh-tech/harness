import shutil
from pathlib import Path

import pytest
from conftest import BOOKSTORE, edit, factory_override

from harness.factories import FACTORIES_DIR, installed_factories
from harness.knowledge import validate as validate_module
from harness.knowledge.validate import validate_project


def messages(report) -> list[str]:
    return [p.message for p in report.errors]


# ── Loader ──────────────────────────────────────────────────────────


def test_crud_api_is_installed():
    factories = installed_factories()
    assert list(factories) == ["crud-api", "domain-model"]
    assert factories["crud-api"].root == FACTORIES_DIR / "crud-api"


def test_domain_model_factory_is_planned():
    report = validate_project(BOOKSTORE)
    assert report.ok
    assert list(report.factories) == ["crud-api"]  # available
    assert list(report.planned) == ["domain-model"]
    assert not report.planned["domain-model"].available


def test_available_factory_needs_a_pack(factories_dir: Path):
    edit(factories_dir / "crud-api" / "factory.yaml", "packs: [python-fastapi]", "packs: []")
    errors = validate_project(BOOKSTORE).errors
    assert any("at least one stack pack" in e.message for e in errors)


def test_folder_without_manifest_is_ignored(tmp_path: Path):
    (tmp_path / "half-built").mkdir()
    (tmp_path / "ready").mkdir()
    (tmp_path / "ready" / "factory.yaml").write_text("name: ready\n")
    assert list(installed_factories(tmp_path)) == ["ready"]


# ── Bookstore with the factory ──────────────────────────────────────


def test_bookstore_uses_the_crud_api_factory():
    report = validate_project(BOOKSTORE)
    assert report.ok, messages(report)
    assert list(report.factories) == ["crud-api"]
    loaded = report.factories["crud-api"]
    assert loaded.manifest.description.startswith("Create, read, update and delete")
    assert report.technical is loaded.technical
    assert loaded.checks == FACTORIES_DIR / "crud-api" / "checks.pl"


# ── harness.yaml › factories ────────────────────────────────────────


def test_unknown_factory_in_harness_yaml(project: Path):
    edit(project / "harness.yaml", "knowledge: .harness", "knowledge: .harness\nfactories: [crud]")
    [error] = validate_project(project).errors
    assert error.message == "unknown factory 'crud' (installed: crud-api, domain-model)"
    assert error.path == ("factories", 0)
    assert error.line is not None


def test_factories_listed_explicitly(project: Path):
    edit(
        project / "harness.yaml",
        "knowledge: .harness",
        "knowledge: .harness\nfactories: [crud-api, domain-model]",
    )
    assert validate_project(project).ok


def test_no_factory_enabled(project: Path):
    edit(project / "harness.yaml", "knowledge: .harness", "knowledge: .harness\nfactories: []")
    assert "no factory is enabled" in messages(validate_project(project))


# ── Project overrides ───────────────────────────────────────────────


def test_old_override_location_is_reported(project: Path):
    (project / ".harness" / "technical.yaml").write_text("kinds: {}\n")
    assert any("now live per factory" in m for m in messages(validate_project(project)))


def test_per_factory_override(project: Path):
    factory_override(project).write_text(
        'askable:\n  requires_login: "Must the user be signed in?"\n'
    )
    report = validate_project(project)
    assert report.ok, messages(report)
    assert report.technical.askable["requires_login"] == "Must the user be signed in?"


# ── A broken factory (copied to a temporary folder) ─────────────────


@pytest.fixture
def factories_dir(tmp_path: Path, monkeypatch) -> Path:
    folder = tmp_path / "factories"
    shutil.copytree(FACTORIES_DIR / "crud-api", folder / "crud-api")
    monkeypatch.setattr(validate_module, "installed_factories", lambda: installed_factories(folder))
    return folder


def test_manifest_name_must_match_folder(factories_dir: Path):
    edit(factories_dir / "crud-api" / "factory.yaml", "name: crud-api", "name: crud")
    errors = validate_project(BOOKSTORE).errors
    assert any("must match the folder name 'crud-api'" in e.message for e in errors)


def test_manifest_needs_examples(factories_dir: Path):
    manifest = factories_dir / "crud-api" / "factory.yaml"
    text = manifest.read_text()
    manifest.write_text(
        text.split("examples:")[0]
        + "examples: []\n"
        + text.split('  - "Remove a cancelled order"\n')[1]
    )
    assert any("examples" in str(e.path) for e in validate_project(BOOKSTORE).errors)


def test_missing_checks_file(factories_dir: Path):
    (factories_dir / "crud-api" / "checks.pl").unlink()
    errors = validate_project(BOOKSTORE).errors
    assert any("file 'checks.pl' not found in the factory" in e.message for e in errors)


def test_factory_checks_are_used(project: Path):
    """The composition checks now come from the factory's checks.pl."""
    edit(
        project / ".harness/domain.yaml",
        "acts_on: Review\n    within: Book\n\n  list_reviews",
        "acts_on: Review\n\n  list_reviews",
    )
    errors = validate_project(project).errors
    assert any(e.message.startswith("composition_child_outside_parent") for e in errors)
