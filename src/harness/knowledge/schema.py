"""Shapes of the knowledge files: harness.yaml, domain.yaml and technical.yaml.

These models check one file at a time (fields, types, allowed values).
Checks that span several entries or files live in `references.py`.
"""

import re
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    model_validator,
)

MAX_OPTIONS = 20  # SemIf chooses well among a small number of options

SnakeName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9_]*$")]
EntityName = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z][A-Za-z0-9]*$")]
KebabName = Annotated[str, StringConstraints(strict=True, pattern=r"^[a-z][a-z0-9-]*$")]
Text = Annotated[str, StringConstraints(strict=True, min_length=1)]
Number = StrictInt | StrictFloat

NAME_HINTS = {
    SnakeName.__metadata__[0].pattern: "lowercase words joined by underscores, e.g. add_book",
    EntityName.__metadata__[0].pattern: "a capitalised name, e.g. Book",
    KebabName.__metadata__[0].pattern: "lowercase words joined by hyphens, e.g. create-validates",
}

NAME_PLACEHOLDERS = {"entity", "entities", "target", "attribute", "verb"}


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── harness.yaml ────────────────────────────────────────────────────


class HarnessFile(Model):
    """The link between a project and the harness."""

    version: Literal[1]
    pack: KebabName
    knowledge: Text = ".harness"


# ── domain.yaml ─────────────────────────────────────────────────────


class Area(Model):
    description: Text


class Attribute(Model):
    type: Literal["id", "text", "integer", "money", "date", "datetime", "boolean"]
    required: StrictBool = False
    unique: StrictBool = False
    generated: StrictBool = False
    min: Number | None = None
    max: Number | None = None

    @model_validator(mode="after")
    def _check_range(self) -> "Attribute":
        has_range = self.min is not None or self.max is not None
        if has_range and self.type not in ("integer", "money"):
            raise ValueError("min/max are only allowed for integer and money attributes")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) is greater than max ({self.max})")
        return self


class Transition(Model):
    from_: SnakeName = Field(alias="from")
    to: SnakeName


class Lifecycle(Model):
    field: SnakeName
    initial: SnakeName
    transitions: list[Transition] = Field(min_length=1)

    @property
    def states(self) -> set[str]:
        return {t.from_ for t in self.transitions} | {t.to for t in self.transitions}

    def allows(self, move: Transition) -> bool:
        return any(t.from_ == move.from_ and t.to == move.to for t in self.transitions)

    @model_validator(mode="after")
    def _check_initial(self) -> "Lifecycle":
        if self.initial not in self.states:
            known = ", ".join(sorted(self.states))
            raise ValueError(
                f"initial state '{self.initial}' is not used by any transition (states: {known})"
            )
        return self


class Entity(Model):
    area: SnakeName
    description: Text
    identifier: SnakeName
    attributes: dict[SnakeName, Attribute] = Field(min_length=1)
    lifecycle: Lifecycle | None = None

    @model_validator(mode="after")
    def _check_identifier(self) -> "Entity":
        attribute = self.attributes.get(self.identifier)
        if attribute is None:
            raise ValueError(f"identifier '{self.identifier}' is not one of the attributes")
        if not (attribute.required or attribute.generated):
            raise ValueError(f"identifier '{self.identifier}' must be required or generated")
        if self.lifecycle and self.lifecycle.field in self.attributes:
            raise ValueError(
                f"'{self.lifecycle.field}' is the lifecycle field; "
                "do not also list it under attributes"
            )
        return self


class Association(Model):
    kind: Literal["composition", "aggregation"]
    parent: EntityName
    child: EntityName
    cardinality: Literal["one_to_many", "many_to_many"]

    @model_validator(mode="after")
    def _check_composition(self) -> "Association":
        if self.kind == "composition":
            if self.cardinality != "one_to_many":
                raise ValueError(
                    "a composition is one_to_many: a child belongs to exactly one parent"
                )
            if self.parent == self.child:
                raise ValueError("an entity cannot be composed of itself")
        return self


class Service(Model):
    description: Text
    examples: list[Text] = Field(default_factory=list)
    not_: Text | None = Field(default=None, alias="not")
    kind: SnakeName
    acts_on: EntityName | None = None
    association: SnakeName | None = None
    within: EntityName | None = None
    filters: list[SnakeName] = Field(default_factory=list, max_length=MAX_OPTIONS)
    changes: list[SnakeName] = Field(default_factory=list)
    moves: Transition | None = None


class Domain(Model):
    areas: dict[SnakeName, Area] = Field(min_length=1, max_length=MAX_OPTIONS)
    entities: dict[EntityName, Entity] = Field(min_length=1)
    associations: dict[SnakeName, Association] = Field(default_factory=dict)
    services: dict[SnakeName, Service] = Field(min_length=1)


# ── technical.yaml ──────────────────────────────────────────────────


class Kind(Model):
    description: Text
    examples: list[Text] = Field(min_length=1)
    not_: Text | None = Field(default=None, alias="not")
    effects: list[Text] = Field(min_length=1)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    persistence: SnakeName
    status: StrictInt = Field(ge=100, le=599)
    names_as: Text
    target: Literal["entity", "association"] = "entity"
    accepts: list[Literal["filters", "changes", "moves"]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_names_as(self) -> "Kind":
        unknown = set(re.findall(r"{(\w+)}", self.names_as)) - NAME_PLACEHOLDERS
        if unknown:
            allowed = ", ".join("{" + p + "}" for p in sorted(NAME_PLACEHOLDERS))
            raise ValueError(
                f"names_as uses unknown placeholder(s) "
                f"{', '.join(sorted(unknown))}; allowed: {allowed}"
            )
        return self


class Component(Model):
    description: Text


class Condition(Model):
    """All conditions given must hold for the rule to apply. No conditions: always applies."""

    kind: SnakeName | None = None
    kind_in: list[SnakeName] | None = None
    has: Literal["filters", "changes", "moves", "within", "association"] | None = None
    fact: SnakeName | None = None
    cardinality: Literal["one_to_many", "many_to_many"] | None = None
    parent_of_composition: StrictBool | None = None


class Outcome(Model):
    components: list[SnakeName] = Field(default_factory=list)
    error_status: StrictInt | None = Field(default=None, ge=400, le=599)
    route: Literal["nested"] | None = None
    route_param: Literal["identifier"] | None = None
    query_params: Literal["from_filters"] | None = None
    response_includes: list[SnakeName] = Field(default_factory=list)
    persistence_target: Literal["link_table", "reference"] | None = None

    @model_validator(mode="after")
    def _check_not_empty(self) -> "Outcome":
        if not self.model_fields_set:
            raise ValueError("a rule must decide at least one thing under 'then'")
        return self


class Rule(Model):
    id: KebabName
    when: Condition = Field(default_factory=Condition)
    then: Outcome


class Technical(Model):
    kinds: dict[SnakeName, Kind] = Field(min_length=1, max_length=MAX_OPTIONS)
    components: dict[SnakeName, Component] = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)
    askable: dict[SnakeName, Text] = Field(default_factory=dict)


class TechnicalOverride(Model):
    """A project's .harness/technical.yaml: entries here replace or add to the defaults."""

    kinds: dict[SnakeName, Kind] = Field(default_factory=dict)
    components: dict[SnakeName, Component] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    askable: dict[SnakeName, Text] = Field(default_factory=dict)


SCHEMAS: dict[str, type[Model]] = {
    "harness": HarnessFile,
    "domain": Domain,
    "technical": Technical,
    "technical-override": TechnicalOverride,
}
