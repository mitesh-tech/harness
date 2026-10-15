"""Shapes of the knowledge files: harness.yaml, factory.yaml, domain.yaml and technical.yaml.

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

# SemIf answers at most 16 options per question; one is always "none".
MAX_OPTIONS = 15

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


class SemIfSettings(Model):
    """Where the decision model runs and how sure it must be."""

    url: Annotated[str, StringConstraints(strict=True, pattern=r"^https?://\S+$")] = (
        "http://localhost:8010/v1/systemone"
    )
    threshold: float = Field(default=0.8, ge=0, le=1)
    timeout: float = Field(default=30, gt=0)


class HarnessFile(Model):
    """The link between a project and the harness."""

    version: Literal[1]
    pack: KebabName
    knowledge: Text = ".harness"
    factories: list[KebabName] | None = None  # None: every installed factory
    semif: SemIfSettings = Field(default_factory=SemIfSettings)


# ── factory.yaml ────────────────────────────────────────────────────


class FactoryManifest(Model):
    """What a factory builds and where its knowledge lives."""

    name: KebabName
    description: Text
    examples: list[Text] = Field(min_length=1)
    # false: planned, not built yet. The router still offers it, so a task
    # that needs it ends as unsupported with a clear reason.
    available: StrictBool = True
    technical: Text = "technical.yaml"
    checks: Text = "checks.pl"
    packs: list[KebabName] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_packs(self) -> "FactoryManifest":
        if self.available and not self.packs:
            raise ValueError("an available factory names at least one stack pack in 'packs'")
        return self


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
    description: Text | None = None  # what the connection means; offered to SemIf
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
    # What the business is, in one line; questions to SemIf use it as {system}.
    description: Text | None = None
    areas: dict[SnakeName, Area] = Field(min_length=1, max_length=MAX_OPTIONS)
    entities: dict[EntityName, Entity] = Field(min_length=1)
    associations: dict[SnakeName, Association] = Field(default_factory=dict)
    services: dict[SnakeName, Service] = Field(min_length=1)


# ── .harness/evals/classify.yaml ────────────────────────────────────


class EvalExpect(Model):
    """What a good classification of the task looks like, and how it should get there."""

    outcome: SnakeName | list[SnakeName]  # a list: any of these is acceptable
    answers: dict[SnakeName, Text] = Field(default_factory=dict)  # step -> answer
    path: list[SnakeName] | None = None  # the steps, in order
    not_asked: list[SnakeName] = Field(default_factory=list)
    semif_calls: StrictInt | None = Field(default=None, ge=0)  # at most
    note: Text | None = None

    @property
    def outcomes(self) -> list[str]:
        return self.outcome if isinstance(self.outcome, list) else [self.outcome]


class EvalCase(Model):
    task: Text
    group: KebabName | None = None  # paraphrases: same path and answers across the group
    expect: EvalExpect


class ClassifyEvals(Model):
    """A project's classification eval cases: tasks and what should happen."""

    cases: list[EvalCase] = Field(min_length=1)


# ── data.yaml ───────────────────────────────────────────────────────
# Where each entity and relationship is stored. The shape of a source's
# connection and of each entry depends on the source's type, so those parts
# are checked by the source-type plugin (harness.datasources.<type>).


class SourceSpec(Model):
    type: KebabName  # a source-type plugin, e.g. relational
    description: Text | None = None
    connection: dict[str, object] = Field(default_factory=dict)


class DataFile(Model):
    """A project's .harness/data.yaml: the data sources and what is stored where."""

    sources: dict[SnakeName, SourceSpec] = Field(min_length=1)
    entities: dict[EntityName, dict[str, object]] = Field(default_factory=dict)
    relationships: dict[SnakeName, dict[str, object]] = Field(default_factory=dict)


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


class Operation(Model):
    """A broad business operation (create, read, …) that groups one or more kinds.

    SemIf answers the operation first; the kind follows from the target and, when
    several kinds are still possible, from `question`.
    """

    description: Text
    examples: list[Text] = Field(min_length=1)
    not_: Text | None = Field(default=None, alias="not")
    kinds: list[SnakeName] = Field(min_length=1)
    question: Text | None = None


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


OptionSource = Literal[
    "targets",
    "target_parent",
    "operations",
    "offered_kinds",
    "services_for_target_kind",
    "target_storage",
]
# Steps the platform adds around a factory's flow: choosing the factory,
# and naming a new service.
PLATFORM_STEPS = ("factory", "name")
# Placeholders texts sent to SemIf may use, besides the answers of earlier steps.
PLACEHOLDERS = ("system", "entities", "targets", "target_context")
UNSUPPORTED = "unsupported"


class Branch(Model):
    """Choose the next step by the answer given (a step with `choices`)."""

    by: Literal["answer"]
    cases: dict[SnakeName, SnakeName] = Field(min_length=1)


class FlowChoice(Model):
    description: Text
    examples: list[Text] = Field(default_factory=list)


class FlowStep(Model):
    # Questions and the "none" text may use {system} (domain.yaml › description),
    # {entities} (the entity names) and the answer of an earlier step, e.g. {target}.
    question: Text
    none: Text | None = None  # what "none" means at this step; a general text when omitted
    question_from: Literal["operation"] | None = None  # ask the chosen operation's question
    options: OptionSource | None = None  # options from the knowledge base…
    choices: dict[SnakeName, FlowChoice] | None = Field(default=None, max_length=MAX_OPTIONS)
    skip_if_single: StrictBool = False  # one option: take it by rule, without asking
    # Accept SemIf's top answer below the threshold when the task names exactly
    # one entity and it is that answer: two independent signals agree.
    accept_named: StrictBool = False
    on_answer: SnakeName | Branch
    on_none: SnakeName
    on_empty: SnakeName | None = None  # no options at all; defaults to on_none

    @model_validator(mode="after")
    def _check_options(self) -> "FlowStep":
        if (self.options is None) == (self.choices is None):
            raise ValueError("a step has either 'options' or 'choices'")
        if isinstance(self.on_answer, Branch) and self.choices is None:
            raise ValueError("'by: answer' needs fixed 'choices' to branch on")
        return self


class ClassificationFlow(Model):
    """The questions SemIf answers about a task, as a small flow of steps."""

    start: SnakeName
    low_confidence: SnakeName
    outcomes: list[SnakeName] = Field(min_length=1)
    steps: dict[SnakeName, FlowStep] = Field(min_length=1)


class Technical(Model):
    kinds: dict[SnakeName, Kind] = Field(min_length=1, max_length=MAX_OPTIONS)
    operations: dict[SnakeName, Operation] = Field(min_length=1, max_length=MAX_OPTIONS)
    components: dict[SnakeName, Component] = Field(min_length=1)
    rules: list[Rule] = Field(min_length=1)
    askable: dict[SnakeName, Text] = Field(default_factory=dict)
    classification: ClassificationFlow


class TechnicalOverride(Model):
    """A project's .harness/factories/<name>/technical.yaml: replaces or adds to the factory's."""

    kinds: dict[SnakeName, Kind] = Field(default_factory=dict)
    operations: dict[SnakeName, Operation] = Field(default_factory=dict)
    components: dict[SnakeName, Component] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)
    askable: dict[SnakeName, Text] = Field(default_factory=dict)
    classification: ClassificationFlow | None = None  # replaces the whole flow


SCHEMAS: dict[str, type[Model]] = {
    "harness": HarnessFile,
    "factory": FactoryManifest,
    "domain": Domain,
    "technical": Technical,
    "technical-override": TechnicalOverride,
    "classify-evals": ClassifyEvals,
}
