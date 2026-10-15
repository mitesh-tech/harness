"""Options for each classification step, taken from the knowledge base.

Each option source is named in technical.yaml (`classification › steps › … › options`).
Sources use earlier answers: the kinds offered depend on the chosen operation
and target, the services on the target and kind. Which kinds an operation
offers for a target is a Prolog rule (`offered_kind/4` in core.pl).
"""

from collections.abc import Callable
from dataclasses import dataclass

from harness.knowledge.schema import Domain, Technical
from harness.logic.compile import atom, snake
from harness.logic.engine import Engine
from harness.semif.client import Option

Answers = dict[str, str]


@dataclass
class Context:
    domain: Domain
    technical: Technical
    engine: Engine
    data: object | None = None  # the project's loaded data.yaml

    def target_atom(self, target: str) -> str:
        """Entities are lowercase in Prolog (Book -> book); association names already are."""
        return atom(snake(target) if target in self.domain.entities else target)


Source = Callable[[Context, Answers], list[Option]]


def connection_text(domain: Domain, name: str) -> str:
    a = domain.associations[name]
    generated = f"Relationship between {a.parent} and {a.child}"
    return f"{a.description} ({generated})" if a.description else generated


def entity_text(entity) -> str:
    """An entity's description plus the details it keeps and its stages, from domain.yaml."""
    text = entity.description.rstrip(".") + "."
    details = [
        name
        for name, a in entity.attributes.items()
        if name != entity.identifier and not a.generated
    ]
    if details:
        text += f" Keeps: {', '.join(details)}."
    if entity.lifecycle:
        text += f" Stages: {', '.join(sorted(entity.lifecycle.states))}."
    return text


def targets(ctx: Context, answers: Answers) -> list[Option]:
    d = ctx.domain
    entities = [Option(name, entity_text(e)) for name, e in d.entities.items()]
    connections = [
        Option(name, connection_text(d, name))
        for name, a in d.associations.items()
        if a.kind == "aggregation"
    ]
    return entities + connections


def target_parent(ctx: Context, answers: Answers) -> list[Option]:
    target = answers["target"]
    return [
        Option(
            a.parent,
            ctx.domain.entities[a.parent].description,
            reason=f"{target} is part of a {a.parent} (composition {name})",
        )
        for name, a in ctx.domain.associations.items()
        if a.kind == "composition" and a.child == target
    ]


def operations(ctx: Context, answers: Answers) -> list[Option]:
    return [
        Option(name, o.description, tuple(o.examples), o.not_)
        for name, o in ctx.technical.operations.items()
    ]


REASONS = {
    "target_type": "the only kind of {operation} that works on {target}",
    "has_lifecycle": "{target} has a lifecycle",
}


def offered_kinds(ctx: Context, answers: Answers) -> list[Option]:
    operation, target = answers["operation"], answers["target"]
    found = ctx.engine.query(f"offered_kind({atom(operation)}, {ctx.target_atom(target)}, K, Why)")
    why = {a["K"]: a["Why"] for a in found}
    options = []
    for name in ctx.technical.operations[operation].kinds:  # keep the operation's order
        if name in why:
            k = ctx.technical.kinds[name]
            reason = REASONS.get(why[name], why[name]).format(operation=operation, target=target)
            options.append(Option(name, k.description, tuple(k.examples), k.not_, reason))
    return options


def services_for_target_kind(ctx: Context, answers: Answers) -> list[Option]:
    target, kind = answers["target"], answers["kind"]
    return [
        Option(name, s.description, tuple(s.examples), s.not_)
        for name, s in ctx.domain.services.items()
        if s.kind == kind and target in (s.acts_on, s.association)
    ]


def target_storage(ctx: Context, answers: Answers) -> list[Option]:
    """Where the target is stored (and its parent, for a composition), from data.yaml."""
    if ctx.data is None:
        return []
    target = answers["target"]
    mapping = ctx.data.entities.get(target) or ctx.data.relationships.get(target)
    if mapping is None:
        return []
    parent = answers.get("parent")
    if parent and parent not in ctx.data.entities:
        return []
    source = ctx.data.sources[mapping.source]
    if target in ctx.data.entities:
        where = f"table {mapping.table}"
    elif mapping.stored_as == "link_table":
        where = f"link table {mapping.table}"
    else:
        where = f"column {mapping.column}"
    reason = f"{target} is stored in {source.name} ({source.type.name}), {where}"
    return [Option(source.name, source.spec.description or source.type.name, reason=reason)]


OPTION_SOURCES: dict[str, Source] = {
    "targets": targets,
    "target_parent": target_parent,
    "operations": operations,
    "offered_kinds": offered_kinds,
    "services_for_target_kind": services_for_target_kind,
    "target_storage": target_storage,
}

# Why a step had nothing to choose from; recorded in the trace.
EMPTY = {
    "target_parent": "{target} is not part of another thing",
    "offered_kinds": "no kind of {operation} works on {target}",
    "services_for_target_kind": "no service acts on {target} with kind {kind}",
    "target_storage": "{target} (or its parent) has no entry in data.yaml",
}
