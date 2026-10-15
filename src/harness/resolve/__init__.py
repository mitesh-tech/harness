"""Resolve a service into its API design: what Prolog derives, filled in from the domain and data.

Prolog decides the shape (path, parameters, which fields, errors, delete
effects), each with a rule id; this module adds the details the domain and
data.yaml hold (types, required, ranges, tables and columns) and records
where every value came from. S4b writes the result as spec.yaml and openapi.yaml.
"""

from dataclasses import dataclass, field

from harness.knowledge.validate import Report
from harness.logic.compile import plural, snake
from harness.logic.engine import Engine

ERROR_TEXT = {
    "unknown": "no {subject} has this {param}",
    "invalid_body": "invalid or missing fields",
    "unique_taken": "another {entity} already has this {subject}",
    "move_not_allowed": "{entity} can't move from its current stage to that one",
    "still_referenced": "{subject_plural} still point at this {entity}",
}
EFFECT_TEXT = {
    "cascade": "its {subject} are deleted with it",
    "restrict": "refused (409) while {subject_plural} point at it",
    "set_null": "{subject_plural} that point at it lose their reference",
    "remove_links": "its {subject} links are removed; the other side stays",
}


class ResolveError(RuntimeError):
    """The service can't be resolved (unknown, or the knowledge has problems)."""


@dataclass
class Design:
    service: str
    body: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # dotted path -> where it came from

    def to_dict(self) -> dict:
        return {**self.body, "sources": self.sources}


def design_service(report: Report, service: str, engine: Engine | None = None) -> Design:
    domain, technical = report.domain, report.technical
    if service not in domain.services:
        known = ", ".join(domain.services)
        raise ResolveError(f"unknown service '{service}' (services: {known})")
    if engine is None:
        logic = [path for f in report.factories.values() for path in f.logic]
        engine = Engine(domain, technical, logic, report.data)
    names = _Names(report)
    spec = domain.services[service]
    kind = technical.kinds[spec.kind]
    design = Design(service)
    s = f"'{service}'" if not service.isidentifier() else service

    def one(goal: str) -> dict:
        answers = engine.query(goal)
        if not answers:
            raise ResolveError(f"no answer for {goal}")
        return answers[0]

    entity = spec.acts_on
    association = domain.associations.get(spec.association) if spec.association else None
    target = entity or (association.child if association else None)
    parent = _parent(domain, entity)

    path = one(f"api_path({s}, P, R)")
    api = {
        "method": spec_method(one(f"method({s}, M)")["M"]),
        "path": path["P"],
    }
    design.sources["api.method"] = f"kind {spec.kind}"
    design.sources["api.path"] = _rule(path["R"])

    params = engine.query(f"path_param({s}, N, E, R)")
    if params:
        api["path_params"] = {}
        for a in params:
            refers = names.entity(a["E"])
            api["path_params"][a["N"]] = {"type": "id", "refers_to": refers}
            design.sources[f"api.path_params.{a['N']}"] = _rule(a["R"])

    query = engine.query(f"query_param({s}, N, K, R)")
    if query:
        api["query_params"] = {}
        paging = technical.api.paging
        for a in query:
            if a["K"] == "filter":
                attribute = _field(domain, target, a["N"])
                # a filter is optional and only needs the value's type
                detail = {"filters": a["N"]} | {
                    k: v for k, v in attribute.items() if k in ("type", "one_of")
                }
            elif a["N"] == paging.page:
                detail = {"type": "integer", "default": 1, "min": 1}
            else:
                detail = {
                    "type": "integer",
                    "default": paging.default_size,
                    "min": 1,
                    "max": paging.max_size,
                }
            api["query_params"][a["N"]] = detail
            design.sources[f"api.query_params.{a['N']}"] = _rule(a["R"])

    body = engine.query(f"request_field({s}, A, R)")
    if body:
        api["request"] = {"fields": {}}
        for a in body:
            field_name = a["A"]
            if a["R"] == "reference-link-body":
                detail = {"type": "id", "required": True, "refers_to": association.parent}
            else:
                detail = _field(domain, target, field_name)
                if spec.kind == "change":
                    detail["required"] = False  # PATCH: send only what changes
            api["request"]["fields"][field_name] = detail
            design.sources[f"api.request.fields.{field_name}"] = _rule(a["R"]) + _domain_note(
                target, field_name, a["R"]
            )

    shape = one(f"response({s}, Shape, R)")
    response = {"status": kind.status, "shape": shape["Shape"]}
    design.sources["api.response.status"] = f"kind {spec.kind}"
    design.sources["api.response.shape"] = _rule(shape["R"])
    fields = engine.query(f"response_field({s}, A, R)")
    if fields:
        response["fields"] = {a["A"]: _field(domain, target, a["A"]) for a in fields}
        design.sources["api.response.fields"] = _rule(fields[0]["R"])
    if spec.kind == "create":
        response["headers"] = ["Location"]
    if shape["Shape"] == "list":
        response["includes"] = ["items", "paging", "applied_filters"]
    api["response"] = response

    errors = []
    for a in engine.query(f"api_error({s}, C, W, X, R)"):
        subject = names.entity(a["X"]) if a["X"] != "none" else ""
        param = next(
            (n for n, d in api.get("path_params", {}).items() if d["refers_to"] == subject), ""
        )
        when = ERROR_TEXT[a["W"]].format(
            subject=subject or a["X"],
            subject_plural=_plural_words(subject),
            entity=target or "",
            param=param,
        )
        errors.append({"status": a["C"], "when": when, "rule": a["R"]})
    if errors:
        api["errors"] = errors
    design.body = {
        "service": service,
        "kind": spec.kind,
        "target": target,
        **({"parent": parent} if parent else {}),
        **({"association": spec.association} if spec.association else {}),
        "api": api,
    }

    data = _data(report, spec, entity, association, kind.persistence)
    if data:
        design.body["data"] = data
        design.sources["data"] = "data.yaml"

    if spec.kind == "delete":
        effects = []
        for a in engine.query(f"delete_effect({snake(entity)}, D, E, R)"):
            dependent = names.entity(a["D"]) if a["E"] != "remove_links" else a["D"]
            effects.append(
                {
                    "effect": a["E"],
                    "on": dependent,
                    "means": EFFECT_TEXT[a["E"]].format(
                        subject=_plural_words(dependent), subject_plural=_plural_words(dependent)
                    ),
                    "rule": a["R"],
                }
            )
        if effects:
            design.body["delete_effects"] = effects

    components = engine.query(f"component({s}, C, R)")
    design.body["components"] = [{"name": a["C"], "rule": a["R"]} for a in components]
    return design


def spec_method(method: str) -> str:
    return method.upper()


def _rule(rule_id: str) -> str:
    return "service override (domain.yaml)" if rule_id == "service-override" else f"rule {rule_id}"


def _domain_note(entity: str | None, attribute: str, rule_id: str) -> str:
    if rule_id == "reference-link-body" or entity is None:
        return ""
    return f"; details from domain.yaml {entity}.{attribute}"


def _field(domain, entity_name: str | None, attribute: str) -> dict:
    """An attribute's type and rules, from domain.yaml."""
    entity = domain.entities.get(entity_name) if entity_name else None
    if entity is None:
        return {"type": "text"}
    if entity.lifecycle and attribute == entity.lifecycle.field:
        return {"type": "text", "one_of": sorted(entity.lifecycle.states)}
    spec = entity.attributes[attribute]
    detail = {"type": spec.type}
    for flag in ("required", "unique", "generated"):
        if getattr(spec, flag):
            detail[flag] = True
    if spec.type in ("integer", "money"):
        if spec.min is not None:
            detail["min"] = spec.min
        if spec.max is not None:
            detail["max"] = spec.max
    return detail


def _parent(domain, entity: str | None) -> str | None:
    for a in domain.associations.values():
        if a.kind == "composition" and a.child == entity:
            return a.parent
    return None


def _data(report: Report, spec, entity, association, persistence: str) -> dict | None:
    data = report.data
    if data is None:
        return None
    if entity is not None:
        mapping = data.entities.get(entity)
        if mapping is None:
            return None
        source = data.sources[mapping.source]
        columns = {}
        for attribute in mapping.columns:
            column = mapping.column_of(attribute)
            if column is not None:
                columns[attribute] = column
        result = {
            "source": source.name,
            "type": source.type.name,
            "table": mapping.table,
            "columns": columns,
            "operation": persistence,
        }
        if mapping.parent_key:
            result["parent_key"] = mapping.parent_key
        return result
    mapping = data.relationships.get(spec.association)
    if mapping is None:
        return None
    source = data.sources[mapping.source]
    result = {"source": source.name, "type": source.type.name, "operation": persistence}
    if mapping.stored_as == "link_table":
        result.update(link_table=mapping.table, keys=dict(mapping.keys))
    else:
        result.update(reference_column=mapping.column)
    return result


def _plural_words(name: str) -> str:
    return plural(name) if name and name[0].isupper() else name


class _Names:
    """Prolog atoms back to the names in the YAML (book -> Book)."""

    def __init__(self, report: Report):
        self._entities = {snake(name): name for name in report.domain.entities}

    def entity(self, atom: str) -> str:
        return self._entities.get(atom, atom)
