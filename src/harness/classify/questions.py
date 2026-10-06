"""Options for each classification question, taken from the knowledge base.

Each option source is named in technical.yaml (`classification › steps › … › options`).
Sources can use earlier answers, e.g. the services offered depend on the chosen area.
"""

from collections.abc import Callable

from harness.knowledge.schema import Domain, Technical
from harness.semif.client import Option

Answers = dict[str, str]
Source = Callable[[Domain, Technical, Answers], list[Option]]


def area_of_service(domain: Domain, service) -> str | None:
    entity_name = service.acts_on
    if entity_name is None and service.association in domain.associations:
        entity_name = domain.associations[service.association].parent
    entity = domain.entities.get(entity_name) if entity_name else None
    return entity.area if entity else None


def areas(domain: Domain, technical: Technical, answers: Answers) -> list[Option]:
    return [Option(name, area.description) for name, area in domain.areas.items()]


def services_in_area(domain: Domain, technical: Technical, answers: Answers) -> list[Option]:
    return [
        Option(name, s.description, tuple(s.examples), s.not_)
        for name, s in domain.services.items()
        if area_of_service(domain, s) == answers["area"]
    ]


def kinds(domain: Domain, technical: Technical, answers: Answers) -> list[Option]:
    return [
        Option(name, k.description, tuple(k.examples), k.not_)
        for name, k in technical.kinds.items()
    ]


def entities_in_area(domain: Domain, technical: Technical, answers: Answers) -> list[Option]:
    return [
        Option(name, e.description)
        for name, e in domain.entities.items()
        if e.area == answers["area"]
    ]


def aggregations_in_area(domain: Domain, technical: Technical, answers: Answers) -> list[Option]:
    options = []
    for name, a in domain.associations.items():
        parent, child = domain.entities.get(a.parent), domain.entities.get(a.child)
        if a.kind != "aggregation" or parent is None or child is None:
            continue
        if parent.area == answers["area"]:
            options.append(
                Option(
                    name,
                    f"Connects a {a.parent} ({parent.description}) with a {a.child} "
                    f"({child.description})",
                )
            )
    return options


OPTION_SOURCES: dict[str, Source] = {
    "areas": areas,
    "services_in_area": services_in_area,
    "kinds": kinds,
    "entities_in_area": entities_in_area,
    "aggregations_in_area": aggregations_in_area,
}
