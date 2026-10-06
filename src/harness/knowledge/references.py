"""Checks that span several entries or files: do the names point at things that exist?

Each check yields (path, message, severity). The caller attaches the file and line.
"""

from collections import Counter
from collections.abc import Iterator

from harness.knowledge.schema import KIND_TARGETS, MAX_OPTIONS, Domain, Technical

Finding = tuple[tuple[str | int, ...], str, str]

# Kinds that are easy to confuse; each should carry a "not" hint pointing at the other.
CONFUSABLE_KINDS = [
    ("update", "replace"),
    ("update", "transition"),
    ("create", "link"),
    ("delete", "unlink"),
]


def _known(names) -> str:
    return ", ".join(sorted(names)) or "none"


def check_domain(domain: Domain, technical: Technical) -> Iterator[Finding]:
    yield from _check_entities(domain)
    yield from _check_associations(domain)
    yield from _check_services(domain, technical)
    yield from _check_area_sizes(domain)


def _check_entities(domain: Domain) -> Iterator[Finding]:
    for name, entity in domain.entities.items():
        if entity.area not in domain.areas:
            yield (
                ("entities", name, "area"),
                f"unknown area '{entity.area}' (known: {_known(domain.areas)})",
                "error",
            )


def _check_associations(domain: Domain) -> Iterator[Finding]:
    for name, assoc in domain.associations.items():
        for end in ("parent", "child"):
            entity = getattr(assoc, end)
            if entity not in domain.entities:
                yield (
                    ("associations", name, end),
                    f"unknown entity '{entity}' (known: {_known(domain.entities)})",
                    "error",
                )

    owners = Counter(a.child for a in domain.associations.values() if a.kind == "composition")
    for name, assoc in domain.associations.items():
        if assoc.kind == "composition" and owners[assoc.child] > 1:
            yield (
                ("associations", name),
                f"'{assoc.child}' is composed in more than one parent; a composition "
                "child belongs to exactly one parent",
                "error",
            )


def _check_services(domain: Domain, technical: Technical) -> Iterator[Finding]:
    for name, service in domain.services.items():
        at = ("services", name)
        kind = technical.kinds.get(service.kind)
        if kind is None:
            yield (
                at + ("kind",),
                f"unknown kind '{service.kind}' (known: {_known(technical.kinds)})",
                "error",
            )
            continue

        if kind.target == "association":
            yield from _check_association_service(domain, service, at)
            entity = None
        else:
            entity = yield from _check_entity_service(domain, service, at)

        for field in ("filters", "changes", "moves"):
            if getattr(service, field) and field not in kind.accepts:
                yield (at + (field,), f"'{field}' is not used by kind '{service.kind}'", "error")

        if entity is not None:
            for field in ("filters", "changes"):
                for index, attr in enumerate(getattr(service, field)):
                    if attr not in entity.attributes and not (
                        entity.lifecycle and attr == entity.lifecycle.field
                    ):
                        yield (
                            at + (field, index),
                            f"'{attr}' is not an attribute of {service.acts_on} "
                            f"(attributes: {_known(entity.attributes)})",
                            "error",
                        )
            for index, attr in enumerate(service.changes):
                spec = entity.attributes.get(attr)
                if spec and (spec.generated or attr == entity.identifier):
                    yield (
                        at + ("changes", index),
                        f"'{attr}' is generated or the identifier and cannot be changed",
                        "error",
                    )
            if service.moves:
                if entity.lifecycle is None:
                    yield (at + ("moves",), f"{service.acts_on} has no lifecycle", "error")
                elif not entity.lifecycle.allows(service.moves):
                    yield (
                        at + ("moves",),
                        f"{service.acts_on}'s lifecycle has no move from "
                        f"'{service.moves.from_}' to '{service.moves.to}'",
                        "error",
                    )

        if not service.examples:
            yield (at, "no examples; SemIf matches tasks better with a few examples", "warning")


def _check_entity_service(domain: Domain, service, at):
    if service.association is not None:
        yield (
            at + ("association",),
            f"kind '{service.kind}' acts on an entity; use 'acts_on', not 'association'",
            "error",
        )
    if service.acts_on is None:
        yield (at, f"'acts_on' is required for kind '{service.kind}'", "error")
        return None
    entity = domain.entities.get(service.acts_on)
    if entity is None:
        yield (
            at + ("acts_on",),
            f"unknown entity '{service.acts_on}' (known: {_known(domain.entities)})",
            "error",
        )
        return None
    if service.within is not None:
        if service.within not in domain.entities:
            yield (
                at + ("within",),
                f"unknown entity '{service.within}' (known: {_known(domain.entities)})",
                "error",
            )
        elif not any(
            a.kind == "composition" and a.parent == service.within and a.child == service.acts_on
            for a in domain.associations.values()
        ):
            yield (
                at + ("within",),
                f"'{service.acts_on}' is not composed in '{service.within}'; 'within' needs "
                "a composition association",
                "error",
            )
    return entity


def _check_association_service(domain: Domain, service, at) -> Iterator[Finding]:
    for field in ("acts_on", "within"):
        if getattr(service, field) is not None:
            yield (
                at + (field,),
                f"kind '{service.kind}' acts on an association; use 'association', not '{field}'",
                "error",
            )
    if service.association is None:
        yield (at, f"'association' is required for kind '{service.kind}'", "error")
        return
    assoc = domain.associations.get(service.association)
    if assoc is None:
        yield (
            at + ("association",),
            f"unknown association '{service.association}' (known: {_known(domain.associations)})",
            "error",
        )
    elif assoc.kind == "composition":
        yield (
            at + ("association",),
            f"'{service.association}' is a composition; {service.kind} only works on "
            "aggregations, because a composition child cannot exist without its parent",
            "error",
        )


def _area_of(domain: Domain, service) -> str | None:
    entity_name = service.acts_on
    if entity_name is None and service.association in domain.associations:
        entity_name = domain.associations[service.association].parent
    entity = domain.entities.get(entity_name) if entity_name else None
    return entity.area if entity else None


def _check_area_sizes(domain: Domain) -> Iterator[Finding]:
    """Each area's services, entities and aggregations become SemIf options; keep them few."""
    aggregations = [a for a in domain.associations.values() if a.kind == "aggregation"]
    counts = {
        "services": Counter(_area_of(domain, s) for s in domain.services.values()),
        "entities": Counter(e.area for e in domain.entities.values()),
        "aggregations": Counter(
            domain.entities[a.parent].area for a in aggregations if a.parent in domain.entities
        ),
    }
    for what, per_area in counts.items():
        for area, count in per_area.items():
            if area in domain.areas and count > MAX_OPTIONS:
                yield (
                    ("areas", area),
                    f"{count} {what} in this area (at most {MAX_OPTIONS}); split it so SemIf "
                    "chooses among fewer options",
                    "error",
                )


def check_technical(technical: Technical) -> Iterator[Finding]:
    seen: dict[str, int] = {}
    used_facts: set[str] = set()
    for index, rule in enumerate(technical.rules):
        at = ("rules", index)
        if rule.id in seen:
            yield (
                at + ("id",),
                f"duplicate rule id '{rule.id}' (also rule {seen[rule.id]})",
                "error",
            )
        seen.setdefault(rule.id, index)

        kinds = ([rule.when.kind] if rule.when.kind else []) + (rule.when.kind_in or [])
        for kind in kinds:
            if kind not in technical.kinds:
                yield (
                    at + ("when",),
                    f"unknown kind '{kind}' (known: {_known(technical.kinds)})",
                    "error",
                )
        if rule.when.fact:
            used_facts.add(rule.when.fact)
            if rule.when.fact not in technical.askable:
                yield (
                    at + ("when", "fact"),
                    f"unknown askable fact '{rule.when.fact}' (known: {_known(technical.askable)})",
                    "error",
                )
        for component in rule.then.components:
            if component not in technical.components:
                yield (
                    at + ("then", "components"),
                    f"unknown component '{component}' (known: {_known(technical.components)})",
                    "error",
                )

    for fact in technical.askable:
        if fact not in used_facts:
            yield (("askable", fact), f"no rule uses the askable fact '{fact}'", "warning")

    for first, second in CONFUSABLE_KINDS:
        for name, other in ((first, second), (second, first)):
            kind = technical.kinds.get(name)
            if kind is not None and other in technical.kinds and kind.not_ is None:
                yield (
                    ("kinds", name),
                    f"'{name}' is easily confused with '{other}'; add a 'not' hint",
                    "warning",
                )

    yield from _check_flow(technical.classification)


def _check_flow(flow) -> Iterator[Finding]:
    """Names in the classification flow point at steps or outcomes.

    Whether the flow can loop or stop halfway is checked in Prolog (checks.pl).
    """
    at = ("classification",)
    targets = set(flow.steps) | set(flow.outcomes)
    clash = set(flow.steps) & set(flow.outcomes)
    for name in sorted(clash):
        yield (at + ("outcomes",), f"'{name}' is both a step and an outcome", "error")
    if flow.start not in flow.steps:
        yield (
            at + ("start",),
            f"unknown step '{flow.start}' (steps: {_known(flow.steps)})",
            "error",
        )
    if flow.low_confidence not in flow.outcomes:
        yield (
            at + ("low_confidence",),
            f"'{flow.low_confidence}' is not an outcome (outcomes: {_known(flow.outcomes)})",
            "error",
        )
    for name, step in flow.steps.items():
        where = at + ("steps", name)
        nexts = [("on_none", step.on_none)]
        if isinstance(step.on_answer, str):
            nexts.append(("on_answer", step.on_answer))
        else:
            missing = set(KIND_TARGETS) - set(step.on_answer.cases)
            if missing:
                yield (
                    where + ("on_answer", "cases"),
                    f"no case for kind target(s): {', '.join(sorted(missing))}",
                    "error",
                )
            nexts += [("on_answer", target) for target in step.on_answer.cases.values()]
        for field, target in nexts:
            if target not in targets:
                yield (
                    where + (field,),
                    f"'{target}' is neither a step nor an outcome "
                    f"(steps: {_known(flow.steps)}; outcomes: {_known(flow.outcomes)})",
                    "error",
                )
