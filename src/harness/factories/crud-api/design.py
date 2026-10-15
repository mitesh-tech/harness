"""The CRUD API factory's design: work out a service's API, and write its OpenAPI version.

Prolog decides the shape (path, parameters, which fields, errors, delete
effects), each with a rule id; this module adds what the domain and data.yaml
hold (types, required, ranges, tables and columns) and records where every
value came from. For a new service it first asks SemIf the details the task
states (which attributes change, which filters, which move) with yes/no
questions; what SemIf isn't sure about becomes an open question.

The platform (harness.resolve) calls design_task / design_service and
to_openapi, then saves the run folder.
"""

import re
from dataclasses import dataclass, field

from harness.classify.walk import render
from harness.knowledge.schema import Domain, Service, Transition
from harness.knowledge.validate import Report
from harness.logic.compile import atom, plural, snake
from harness.logic.engine import Engine
from harness.resolve import ResolveError
from harness.semif.client import NONE, NONE_OPTION, DecisionModel, Option, Question
from harness.trace import Event, Trace, margin

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


@dataclass
class Design:
    service: str
    body: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # dotted path -> where it came from
    open_questions: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)  # SemIf's detail answers, for the trace

    def to_dict(self) -> dict:
        return {**self.body, "sources": self.sources}


def design_service(
    report: Report,
    service: str,
    engine: Engine | None = None,
    domain: Domain | None = None,
) -> Design:
    """The design of a service in the domain (by default the project's own)."""
    domain = domain or report.domain
    technical = report.technical
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
    s = atom(service)

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
        "service": {"name": service, "new": False},
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
                    "affects": dependent,
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
    design.body["tests"] = derive_tests(design.body)
    design.sources["tests"] = "derived from the design: one success case, one per error"
    return design


# ── A classified task ───────────────────────────────────────────────


def design_task(report: Report, trace: Trace, model: DecisionModel) -> Design:
    """The design for a classified task: its existing service, or a new one."""
    if trace.outcome == "matched":
        return design_service(report, trace.answers["service"])
    if trace.outcome == "new_service":
        return _design_new(report, trace, model)
    raise ResolveError(f"a task that ends as {trace.outcome} has nothing to design")


def _design_new(report: Report, trace: Trace, model: DecisionModel) -> Design:
    answers, domain = trace.answers, report.domain.model_copy(deep=True)
    target, kind_name = answers["target"], answers["kind"]
    kind = report.technical.kinds[kind_name]
    asker = _Asker(trace, model, domain, target)
    service = {"description": trace.task, "kind": kind_name}
    if target in domain.associations:
        service["association"] = target
    else:
        service["acts_on"] = target
        if answers.get("parent"):
            service["within"] = answers["parent"]
    details: dict[str, object] = {}

    entity = domain.entities.get(target)
    if kind_name == "change" and entity is not None:
        editable = [a for a, spec in entity.attributes.items() if not spec.generated]
        changes = asker.yes_no_each(
            editable, "changes", "Does the task change the {attribute} of a {target}?"
        )
        if changes:
            service["changes"] = details["changes"] = changes
        elif not asker.unsure:
            asker.open.append(f"Which details of a {target} does the task change?")
    elif kind_name == "query" and entity is not None:
        shown = entity.summary if entity.summary is not None else list(entity.attributes)
        candidates = [a for a in shown if a != entity.identifier]
        if entity.lifecycle:
            candidates.append(entity.lifecycle.field)
        filters = asker.yes_no_each(
            candidates, "filters", "Does the task list {targets} filtered by their {attribute}?"
        )
        if filters:
            service["filters"] = details["filters"] = filters
    elif kind_name == "transition" and entity is not None and entity.lifecycle:
        move = asker.choose_move(entity.lifecycle.transitions)
        if move is not None:
            service["moves"] = {"from": move.from_, "to": move.to}
            details["moves"] = f"{move.from_} -> {move.to}"

    name, missing = _new_name(kind.names_as, domain, target, details)
    if missing:
        what = " and ".join(missing)
        asker.open.append(
            f"What should the new service be called? Kind {kind_name} names services "
            f"{kind.names_as}; the {what} is not known yet"
        )
    domain.services[name] = Service.model_validate(service)

    design = design_service(report, name, domain=domain)
    design.body["service"] = {"name": name, "new": True}
    design.body = _with_details(design.body, details)
    design.sources["service.name"] = (
        "open question" if missing else f"rule: kind {kind_name} names services {kind.names_as}"
    )
    if details:
        design.sources["details"] = "SemIf yes/no questions about the task"
    design.open_questions = asker.open
    design.events = asker.events
    return design


def _with_details(body: dict, details: dict) -> dict:
    """Put `details` right after the classification fields, before the API."""
    if not details:
        return body
    ordered = {}
    for key, value in body.items():
        if key == "api":
            ordered["details"] = details
        ordered[key] = value
    return ordered


def _new_name(names_as: str, domain: Domain, target: str, details: dict):
    """The name from the kind's pattern, with <part> for what isn't known yet."""
    if target in domain.associations:
        a = domain.associations[target]
        parts = {"entity": snake(a.parent), "target": snake(a.child)}
    else:
        parts = {"entity": snake(target)}
    parts["entities"] = plural(parts["entity"])
    changes = details.get("changes") or []
    if len(changes) == 1:
        parts["attribute"] = changes[0]
    missing = [p for p in re.findall(r"{(\w+)}", names_as) if p not in parts]
    name = re.sub(r"{(\w+)}", lambda m: parts.get(m.group(1), f"<{m.group(1)}>"), names_as)
    return name, missing


class _Asker:
    """Asks SemIf about the task's details; unsure answers become open questions."""

    def __init__(self, trace: Trace, model: DecisionModel, domain: Domain, target: str):
        self.trace, self.model, self.domain, self.target = trace, model, domain, target
        self.threshold = trace.threshold
        self.events: list[Event] = []
        self.open: list[str] = []
        self.unsure = False

    def _words(self, text: str, **extra) -> str:
        filled = text.format(target=self.target, targets=plural(self.target), **extra)
        return render("This task is for {system}. ", self.domain, {}) + filled

    def yes_no_each(self, candidates: list[str], topic: str, question: str) -> list[str]:
        chosen = []
        for attribute in candidates:
            text = self._words(question, attribute=attribute)
            p = self.model.yes_no(self.trace.task, f"{topic}_{attribute}", text)
            if p >= self.threshold:
                answer = "yes"
                chosen.append(attribute)
            elif p <= 1 - self.threshold:
                answer = "no"
            else:
                answer, self.unsure = "unsure", True
                self.open.append(f"{text.split('. ', 1)[1]} (SemIf {p:.2f})")
            self.events.append(
                Event(
                    stage="resolve",
                    step=f"details.{topic}.{attribute}",
                    by="semif",
                    answer=answer,
                    accepted=answer != "unsure",
                    question=text,
                    options=["yes", "no"],
                    confidence=p,
                )
            )
        return chosen

    def choose_move(self, moves: list[Transition]) -> Transition | None:
        options = [Option(f"{m.from_}_to_{m.to}", f"From {m.from_} to {m.to}") for m in moves]
        text = self._words("Which move through its stages does the task make for a {target}?")
        question = Question("move", text, (*options, NONE_OPTION))
        choice = self.model.choose(self.trace.task, question)
        accepted = choice.confidence >= self.threshold and choice.answer != NONE
        self.events.append(
            Event(
                stage="resolve",
                step="details.moves",
                by="semif",
                answer=choice.answer,
                accepted=accepted,
                question=text,
                options=[o.name for o in question.options],
                confidence=choice.confidence,
                margin=margin(choice.probabilities),
                probabilities=choice.probabilities,
            )
        )
        if not accepted:
            self.open.append(f"Which move does the task make for a {self.target}?")
            return None
        return next(m for m in moves if f"{m.from_}_to_{m.to}" == choice.answer)


# ── Tests derived from the design ───────────────────────────────────

SUCCESS = {
    "create": "creates a {target}",
    "read_one": "shows one {target}",
    "query": "lists {targets}",
    "change": "changes a {target}",
    "replace": "replaces a {target}",
    "transition": "moves a {target} to another stage",
    "delete": "deletes a {target}",
    "link": "connects them",
    "unlink": "disconnects them",
}


def derive_tests(body: dict) -> list[str]:
    """One success case, one case per error, and each delete effect: what S5 verifies."""
    target = body.get("target") or ""
    response = body["api"]["response"]
    success = SUCCESS[body["kind"]].format(target=target, targets=plural(target))
    extra = " with Location" if "Location" in response.get("headers", []) else ""
    tests = [f"{success} → {response['status']}{extra}"]
    tests += [f"{e['when']} → {e['status']}" for e in body["api"].get("errors", [])]
    tests += [f"after the delete: {e['means']}" for e in body.get("delete_effects", [])]
    return tests


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


# ── OpenAPI, from the spec only ─────────────────────────────────────

JSON_TYPES = {
    "id": {"type": "integer"},
    "text": {"type": "string"},
    "integer": {"type": "integer"},
    "money": {"type": "number"},
    "boolean": {"type": "boolean"},
    "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"},
}
REQUEST_SUFFIX = {
    "create": "Create",
    "replace": "Replace",
    "change": "Change",
    "transition": "Move",
    "link": "Link",
}
PROBLEM = {
    "type": "object",
    "description": "An error, as application/problem+json (RFC 9457)",
    "properties": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "status": {"type": "integer"},
        "detail": {"type": "string"},
    },
    "required": ["title", "status"],
}


def to_openapi(spec: dict) -> dict:
    """The spec's API as an OpenAPI 3.1 document. Reads nothing but the spec."""
    api, target = spec["api"], spec.get("target") or "Item"
    schemas: dict[str, dict] = {"Problem": PROBLEM}
    operation: dict = {
        "operationId": spec["service"]["name"],
        "summary": spec.get("task", spec["service"]["name"]),
        "responses": {},
    }

    parameters = []
    for name, detail in api.get("path_params", {}).items():
        parameters.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "description": f"identifies a {detail['refers_to']}",
                "schema": _schema(detail),
            }
        )
    for name, detail in api.get("query_params", {}).items():
        parameters.append(
            {"name": name, "in": "query", "required": False, "schema": _schema(detail)}
        )
    if parameters:
        operation["parameters"] = parameters

    request = api.get("request")
    if request:
        body_name = target + REQUEST_SUFFIX.get(spec["kind"], "Request")
        schemas[body_name] = _object(request["fields"])
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": f"#/components/schemas/{body_name}"}}
            },
        }

    response = api["response"]
    success: dict = {
        "description": SUCCESS[spec["kind"]].format(target=target, targets=plural(target))
    }
    if response["shape"] == "item":
        schemas[target] = _object(response["fields"])
        success["content"] = {
            "application/json": {"schema": {"$ref": f"#/components/schemas/{target}"}}
        }
    elif response["shape"] == "list":
        summary = f"{target}Summary"
        schemas[summary] = _object(response["fields"])
        success["content"] = {"application/json": {"schema": _list_envelope(summary, api)}}
    if "Location" in response.get("headers", []):
        success["headers"] = {
            "Location": {"description": "where the new item is", "schema": {"type": "string"}}
        }
    operation["responses"][str(response["status"])] = success

    for status, whens in _errors_by_status(api.get("errors", [])).items():
        operation["responses"][str(status)] = {
            "description": "; ".join(whens),
            "content": {
                "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
            },
        }

    order = ("operationId", "summary", "parameters", "requestBody", "responses")
    operation = {key: operation[key] for key in order if key in operation}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": spec["service"]["name"],
            "version": spec.get("run", "draft"),
            "description": spec.get("task", ""),
        },
        "paths": {api["path"]: {api["method"].lower(): operation}},
        "components": {"schemas": schemas},
    }


def _schema(detail: dict) -> dict:
    schema = dict(JSON_TYPES.get(detail.get("type", "text"), {"type": "string"}))
    if "one_of" in detail:
        schema["enum"] = detail["one_of"]
    for key, name in (("min", "minimum"), ("max", "maximum"), ("default", "default")):
        if key in detail:
            schema[name] = detail[key]
    return schema


def _object(fields: dict) -> dict:
    required = [name for name, detail in fields.items() if detail.get("required")]
    schema: dict = {
        "type": "object",
        "properties": {name: _schema(detail) for name, detail in fields.items()},
    }
    if required:
        schema["required"] = required
    return schema


def _list_envelope(summary: str, api: dict) -> dict:
    filters = {n: _schema(d) for n, d in api.get("query_params", {}).items() if "filters" in d}
    return {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"$ref": f"#/components/schemas/{summary}"}},
            "paging": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "size": {"type": "integer"},
                    "total": {"type": "integer"},
                },
            },
            "applied_filters": {"type": "object", "properties": filters},
        },
        "required": ["items", "paging", "applied_filters"],
    }


def _errors_by_status(errors: list[dict]) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for error in errors:
        grouped.setdefault(error["status"], []).append(error["when"])
    return grouped
