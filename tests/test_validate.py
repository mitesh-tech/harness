from pathlib import Path

from conftest import BOOKSTORE, edit

from harness.knowledge.validate import validate_project


def messages(report) -> list[str]:
    return [p.message for p in report.errors]


def only_error(report):
    assert len(report.errors) == 1, messages(report)
    return report.errors[0]


# ── The real Bookstore knowledge ────────────────────────────────────


def test_bookstore_is_valid():
    report = validate_project(BOOKSTORE)
    assert report.ok, messages(report)
    assert [p.path for p in report.warnings] == [("pack",)]
    assert len(report.domain.services) == 13
    assert len(report.technical.kinds) == 9


def test_found_from_a_subfolder(project: Path):
    report = validate_project(project / ".harness")
    assert report.root == project
    assert report.ok


def test_missing_project_file(tmp_path: Path):
    report = validate_project(tmp_path)
    assert "no harness.yaml found" in only_error(report).message


def test_missing_knowledge_folder(project: Path):
    edit(project / "harness.yaml", "knowledge: .harness", "knowledge: .missing")
    assert "knowledge folder '.missing' not found" in only_error(validate_project(project)).message


# ── Layer 1: YAML syntax ────────────────────────────────────────────


def test_invalid_yaml_reports_line(project: Path):
    edit(project / ".harness/domain.yaml", "  catalogue:\n", "  catalogue: [\n")
    error = only_error(validate_project(project))
    assert error.message.startswith("not valid YAML")
    assert error.line is not None


# ── Layer 2: shape ──────────────────────────────────────────────────


def test_unknown_field_suggests_the_right_one(project: Path):
    domain = project / ".harness/domain.yaml"
    edit(
        domain,
        "    identifier: id\n    attributes:\n      id:   { type: id, generated: true }\n"
        "      name: { type: text, required: true, unique: true }\n\n  Review",
        "    identifier: id\n    atributes:\n      id:   { type: id, generated: true }\n"
        "      name: { type: text, required: true, unique: true }\n\n  Review",
    )
    errors = validate_project(project).errors
    unknown = next(e for e in errors if "unknown field" in e.message)
    assert unknown.message == "unknown field 'atributes' (did you mean 'attributes'?)"
    assert unknown.path == ("entities", "Publisher", "atributes")
    assert domain.read_text().splitlines()[unknown.line - 1].strip().startswith("atributes:")


def test_value_not_allowed(project: Path):
    edit(project / ".harness/domain.yaml", "kind: composition", "kind: composite")
    error = only_error(validate_project(project))
    assert error.path == ("associations", "book_reviews", "kind")
    assert "'composite' is not allowed" in error.message


def test_unquoted_no_is_caught(project: Path):
    edit(
        project / ".harness/domain.yaml",
        'description: "Show one book\'s details"',
        "description: no",
    )
    assert "put it in quotes" in only_error(validate_project(project)).message


def test_identifier_must_be_an_attribute(project: Path):
    review_attributes = "\n    attributes:\n      id:      { type: id, generated: true }\n"
    edit(
        project / ".harness/domain.yaml",
        "    identifier: id" + review_attributes,
        "    identifier: ref" + review_attributes,
    )
    assert (
        "identifier 'ref' is not one of the attributes"
        in only_error(validate_project(project)).message
    )


def test_bad_name_format(project: Path):
    edit(project / ".harness/domain.yaml", "  Collection:\n    area", "  collection:\n    area")
    assert "'collection' is not a valid name" in messages(validate_project(project))[0]


def test_composition_must_be_one_to_many(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "kind: composition\n    parent: Book\n    child: Review\n    cardinality: one_to_many",
        "kind: composition\n    parent: Book\n    child: Review\n    cardinality: many_to_many",
    )
    assert "a composition is one_to_many" in only_error(validate_project(project)).message


def test_unknown_placeholder_in_names_as(project: Path):
    (project / ".harness/technical.yaml").write_text(
        "kinds:\n"
        "  archive:\n"
        "    description: Archive it\n"
        "    examples: [archive]\n"
        "    effects: [It is archived]\n"
        "    method: PATCH\n"
        "    persistence: update_row\n"
        "    status: 200\n"
        "    names_as: '{action}_{entity}'\n"
    )
    assert "unknown placeholder(s) action" in only_error(validate_project(project)).message


# ── Layer 3: references ─────────────────────────────────────────────


def test_unknown_entity(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "acts_on: Review\n    within: Book\n\n  list_reviews",
        "acts_on: Reveiw\n    within: Book\n\n  list_reviews",
    )
    error = only_error(validate_project(project))
    assert error.path == ("services", "add_review", "acts_on")
    assert "unknown entity 'Reveiw'" in error.message


def test_filter_must_be_an_attribute(project: Path):
    edit(project / ".harness/domain.yaml", "filters: [author, status]", "filters: [author, colour]")
    assert "'colour' is not an attribute of Book" in only_error(validate_project(project)).message


def test_move_must_be_in_the_lifecycle(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "moves: { from: active, to: archived }",
        "moves: { from: archived, to: deleted }",
    )
    assert "no move from 'archived' to 'deleted'" in only_error(validate_project(project)).message


def test_field_not_used_by_kind(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "kind: create\n    acts_on: Book",
        "kind: create\n    acts_on: Book\n    filters: [author]",
    )
    assert "'filters' is not used by kind 'create'" in only_error(validate_project(project)).message


def test_within_needs_a_composition(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "acts_on: Review\n    within: Book\n\n  list_reviews",
        "acts_on: Review\n    within: Collection\n\n  list_reviews",
    )
    assert (
        "'Review' is not composed in 'Collection'" in only_error(validate_project(project)).message
    )


def test_link_needs_an_aggregation(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "association: collection_books\n\n  remove_book",
        "association: book_reviews\n\n  remove_book",
    )
    assert "'book_reviews' is a composition" in only_error(validate_project(project)).message


def test_link_cannot_use_acts_on(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "kind: link\n    association: book_publisher",
        "kind: link\n    acts_on: Book\n    association: book_publisher",
    )
    assert "use 'association', not 'acts_on'" in only_error(validate_project(project)).message


def test_composition_child_has_one_parent(project: Path):
    edit(
        project / ".harness/domain.yaml",
        "  collection_books:",
        "  publisher_reviews:\n    kind: composition\n    parent: Publisher\n    child: Review\n"
        "    cardinality: one_to_many\n\n  collection_books:",
    )
    errors = validate_project(project).errors
    assert errors and all("composed in more than one parent" in e.message for e in errors)


def test_unknown_area(project: Path):
    edit(project / ".harness/domain.yaml", "area: curation", "area: curated")
    assert "unknown area 'curated'" in only_error(validate_project(project)).message


# ── Technical overrides ─────────────────────────────────────────────


def test_project_override_replaces_a_default(project: Path):
    (project / ".harness/technical.yaml").write_text(
        "kinds:\n"
        "  delete:\n"
        "    description: Remove something\n"
        "    examples: [delete]\n"
        "    not: Not for taking a thing out of a group (use unlink)\n"
        "    effects: [The row is marked deleted]\n"
        "    method: DELETE\n"
        "    persistence: soft_delete\n"
        "    status: 200\n"
        "    names_as: remove_{entity}\n"
    )
    report = validate_project(project)
    assert report.ok, messages(report)
    assert report.technical.kinds["delete"].persistence == "soft_delete"
    assert report.technical.kinds["create"].status == 201  # defaults kept


def test_override_rule_with_unknown_component_points_at_override(project: Path):
    override = project / ".harness/technical.yaml"
    override.write_text(
        "rules:\n"
        "  - id: audit-writes\n"
        "    when: { kind: create }\n"
        "    then: { components: [audit_log] }\n"
    )
    error = only_error(validate_project(project))
    assert "unknown component 'audit_log'" in error.message
    assert error.file == override
    assert error.path[:2] == ("rules", 0)
    assert error.line == 4
