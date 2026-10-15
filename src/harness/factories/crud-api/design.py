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

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path

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
    design.body["tests"] = derive_tests(design.body, domain)
    design.sources["tests"] = (
        "derived from the design and domain.yaml: a success case, one per error, "
        "invalid values; example values by rule"
    )
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
# Concrete cases (given / request / expect) that S5 turns into tests before
# any code exists. Example values are made up by rule from the domain.

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
MISSING_ID = 999999


def example(attribute: str, spec, n: int = 1):
    """A valid example value for an attribute, by rule: no AI, the same every time."""
    kind = spec.type if spec is not None else "text"
    low = spec.min if spec is not None and spec.min is not None else None
    if kind == "integer":
        return int(low) if low is not None else n
    if kind == "money":
        return round(max(float(low or 0), 9.99) + n - 1, 2)
    if kind == "boolean":
        return True
    if kind == "date":
        return f"2026-01-{n:02d}"
    if kind == "datetime":
        return f"2026-01-{n:02d}T10:00:00Z"
    if kind == "id":
        return n
    return f"{attribute.replace('_', ' ').capitalize()} {n}"


def derive_tests(body: dict, domain: Domain) -> list[dict]:
    """One success case, one per error: the cases you confirm, then S5 generates and runs."""
    kind, api, target = body["kind"], body["api"], body.get("target")
    entity = domain.entities.get(target) if target else None
    builder = _Cases(domain, body)
    cases = [builder.success()]
    for error in api.get("errors", []):
        case = builder.error(error)
        if case is not None:
            cases.append(case)
    if kind == "create" and entity is not None:
        cases += builder.invalid_values(entity)
    for effect in body.get("delete_effects", []):
        if effect["effect"] == "cascade":
            cases.append(builder.cascade(effect))
    return cases


class _Cases:
    def __init__(self, domain: Domain, body: dict):
        self.domain, self.body = domain, body
        self.api, self.kind, self.target = body["api"], body["kind"], body.get("target")
        self.params = self.api.get("path_params", {})

    # what must exist before the request: everything the path names
    def _given(self, skip_target: bool) -> tuple[list[dict], dict[str, str]]:
        given, path_values = [], {}
        parent = self.body.get("parent")
        for param, detail in self.params.items():
            entity_name = detail["refers_to"]
            if skip_target and entity_name == self.target:
                continue
            alias = snake(entity_name)
            step = {"create": entity_name, "as": alias, "with": self._required(entity_name)}
            if parent and entity_name == self.target:
                step["within"] = snake(parent)
            given.append(step)
            identifier = self.domain.entities[entity_name].identifier
            path_values[param] = f"{{{alias}.{identifier}}}"
        return given, path_values

    def _required(self, entity_name: str, n: int = 1) -> dict:
        entity = self.domain.entities[entity_name]
        return {
            a: example(a, s, n)
            for a, s in entity.attributes.items()
            if s.required and not s.generated
        }

    def _path(self, values: dict[str, str]) -> str:
        path = self.api["path"]
        for param, value in values.items():
            path = path.replace(f"{{{param}}}", value)
        return path

    def _body(self, n: int = 1) -> dict | None:
        fields = self.api.get("request", {}).get("fields")
        if not fields:
            return None
        entity = self.domain.entities.get(self.target)
        values = {}
        for name, detail in fields.items():
            spec = entity.attributes.get(name) if entity else None
            values[name] = (
                example(name, spec, n) if spec is not None else _example_detail(name, detail, n)
            )
        return values

    def _request(self, values: dict[str, str], body: dict | None) -> dict:
        request = {"method": self.api["method"], "path": self._path(values)}
        if body is not None:
            request["body"] = body
        return request

    def success(self) -> dict:
        target_exists = self.kind not in ("create", "link")
        given, values = self._given(skip_target=not target_exists)
        body = self._body(n=2 if target_exists else 1)
        response = self.api["response"]
        expect: dict = {"status": response["status"]}
        if response.get("headers"):
            expect["headers"] = list(response["headers"])
        if body and response["shape"] == "item":
            expect["body"] = {k: v for k, v in body.items() if k in response.get("fields", {})}
        case = {
            "name": SUCCESS[self.kind].format(
                target=self.target, targets=plural(self.target or "")
            ),
            "request": self._request(values, body),
            "expect": expect,
            "rule": f"kind {self.kind}",
        }
        return _with_given(case, given)

    def error(self, error: dict) -> dict | None:
        rule = error["rule"]
        target_exists = self.kind not in ("create", "link")
        given, values = self._given(skip_target=not target_exists)
        if rule == "not-found":
            subject = error["when"].split()[1]  # "no Book has this book_id"
            param = error["when"].split()[-1]
            given = [
                g for g in given if g["create"] != subject and g.get("within") != snake(subject)
            ]
            values[param] = str(MISSING_ID)
        elif rule == "unique-taken":
            attribute = error["when"].rsplit(" ", 1)[-1]
            body = self._body()
            given.append(
                {
                    "create": self.target,
                    "as": f"existing_{snake(self.target)}",
                    "with": {**self._required(self.target), attribute: body[attribute]},
                }
            )
        elif rule in ("invalid-body", "transition-guard", "delete-restricted"):
            return None  # invalid bodies get their own cases; the others come with S6
        case = {
            "name": error["when"],
            "request": self._request(values, self._body()),
            "expect": {"status": error["status"], "problem": error["when"]},
            "rule": rule,
        }
        return _with_given(case, given)

    def cascade(self, effect: dict) -> dict:
        """Deleting the target also deletes its composition children."""
        given, values = self._given(skip_target=False)
        child = effect["affects"]
        given.append(
            {
                "create": child,
                "as": snake(child),
                "with": self._required(child),
                "within": snake(self.target),
            }
        )
        return _with_given(
            {
                "name": f"after the delete: {effect['means']}",
                "request": self._request(values, None),
                "expect": {"status": self.api["response"]["status"], "gone": [snake(child)]},
                "rule": effect["rule"],
            },
            given,
        )

    def invalid_values(self, entity) -> list[dict]:
        """A missing required field, and a value outside its range: both 422."""
        given, values = self._given(skip_target=True)
        fields = self.api.get("request", {}).get("fields", {})
        invalid = next(
            (e for e in self.api.get("errors", []) if e["rule"] == "invalid-body"),
            {"status": 422, "when": "invalid or missing fields"},
        )
        expect = {"status": invalid["status"], "problem": invalid["when"]}
        cases = []
        required = [name for name, d in fields.items() if d.get("required")]
        if required:
            body = self._body()
            del body[required[0]]
            cases.append(
                {
                    "name": f"{required[0]} is required",
                    "request": self._request(values, body),
                    "expect": dict(expect),
                    "rule": "invalid-body",
                }
            )
        for name, detail in fields.items():
            if "max" in detail or "min" in detail:
                body = self._body()
                if "max" in detail:
                    body[name] = detail["max"] + 1
                    name_text = f"{name} above {detail['max']}"
                else:
                    body[name] = detail["min"] - 1
                    name_text = f"{name} below {detail['min']}"
                cases.append(
                    {
                        "name": f"{name_text} is invalid",
                        "request": self._request(values, body),
                        "expect": dict(expect),
                        "rule": "invalid-body",
                    }
                )
                break
        return [_with_given(c, given) for c in cases]


def _with_given(case: dict, given: list[dict]) -> dict:
    """`given` goes first in a case, and only when there is something to set up."""
    # a copy per case, so the YAML has no shared &anchors a reader must follow
    given = copy.deepcopy(given)
    return {"name": case.pop("name"), **({"given": given} if given else {}), **case}


def _example_detail(name: str, detail: dict, n: int):
    if detail.get("type") == "id":
        return n
    return f"{name.replace('_', ' ').capitalize()} {n}"


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
        states = sorted(entity.lifecycle.states)
        return {"type": "text", "one_of": states, "initial": entity.lifecycle.initial}
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
            parent = _parent(report.domain, entity)
            parent_mapping = data.entities.get(parent) if parent else None
            if parent_mapping is not None:
                identifier = report.domain.entities[parent].identifier
                result["parent_table"] = parent_mapping.table
                result["parent_column"] = parent_mapping.column_of(identifier)
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


# ── Generation plan: which files a confirmed spec becomes ───────────
# Only the spec (and the pack's layout) go in; the platform renders the
# templates, writes the files and registers them.

PYTHON_TYPES = {
    "id": "int",
    "text": "str",
    "integer": "int",
    "money": "Decimal",
    "boolean": "bool",
    "date": "date",
    "datetime": "datetime",
}
SQL_TYPES = {
    "id": "Integer",
    "text": "String(200)",
    "integer": "Integer",
    "money": "Numeric(10, 2)",
    "boolean": "Boolean",
    "date": "Date",
    "datetime": "DateTime(timezone=True)",
}
GENERATED_KINDS = ("create",)  # S5; the other kinds come in S6


def generation_plan(spec: dict, pack, templates: dict[str, Path]) -> dict:
    """The files to write for a confirmed spec, with their template and values.

    templates: folders of templates by owner: factory, source, pack.
    Returns {"files": [...], "register": {"routers": [...], "models": [...]}}.
    """
    if spec["kind"] not in GENERATED_KINDS:
        raise ResolveError(
            f"generating code for kind {spec['kind']} comes in S6; S5 generates "
            f"{', '.join(GENERATED_KINDS)}"
        )
    layout, imports = pack.manifest.layout, pack.manifest.imports
    api, data = spec["api"], spec["data"]
    entity, service = spec["target"], spec["service"]["name"]
    module = snake(entity)
    mod = {
        key: _module(getattr(layout, key)) for key in ("models", "schemas", "repositories", "api")
    }
    mod["rules"], mod["problems"] = _module(layout.rules), _module(layout.problems)
    base_module, base_name = imports["base"].split(":")
    session_module, session_name = imports["session"].split(":")

    response_fields = api["response"]["fields"]
    request_fields = api["request"]["fields"]
    unique = [n for n, d in request_fields.items() if d.get("unique")]
    statuses = {e["rule"]: e["status"] for e in api.get("errors", [])}
    parent = None
    if spec.get("parent") and data.get("parent_key"):
        param = next(n for n, d in api["path_params"].items() if d["refers_to"] == spec["parent"])
        parent = {
            "entity": spec["parent"],
            "module": snake(spec["parent"]),
            "param": param,
            "key": data["parent_key"],
        }
    identifier = next(
        (n for n, d in response_fields.items() if d.get("type") == "id" and d.get("generated")),
        "id",
    )
    shared = {
        "service": service,
        "task": spec["task"],
        "entity": entity,
        "module": module,
        "request_class": entity + REQUEST_SUFFIX[spec["kind"]],
        "parent": parent,
        "unique": unique,
        "models_module": mod["models"],
        "schemas_module": mod["schemas"],
        "repositories_module": mod["repositories"],
        "rules_module": mod["rules"],
        "problems_module": mod["problems"],
    }
    schema_imports, request_list = _pydantic(request_fields)
    response_imports, response_list = _pydantic(response_fields, response=True)
    stdlib_imports, sqlalchemy_import, columns = _columns(data, response_fields, parent)
    app_imports = [
        f"from {mod['models']}.{module} import {entity} as {entity}Row",
        f"from {session_module} import {session_name}",
    ]
    if parent:
        app_imports.append(
            f"from {mod['models']}.{parent['module']} import {parent['entity']} as "
            f"{parent['entity']}Row"
        )

    files = [
        _file(
            templates["factory"] / "schemas.py.j2",
            f"{layout.schemas}/{service}.py",
            shared
            | {
                "schema_imports": sorted(set(schema_imports + response_imports)),
                "request_fields": request_list,
                "response_fields": response_list,
            },
        ),
        _file(
            templates["source"] / "repository.py.j2",
            f"{layout.repositories}/{service}.py",
            shared | {"session_name": session_name, "app_imports": sorted(app_imports)},
        ),
        _file(
            templates["factory"] / "route_create.py.j2",
            f"{layout.api}/{service}.py",
            shared
            | {
                "method": api["method"],
                "path": api["path"],
                "status": api["response"]["status"],
                "path_params": list(api.get("path_params", {})),
                "tag": plural(module),
                "location": api["path"] + "/{row." + identifier + "}",
                "not_found": statuses.get("not-found", 404),
                "conflict": statuses.get("unique-taken", 409),
            },
        ),
        _file(
            templates["factory"] / "rules.py.j2",
            f"{layout.rules}/{service}.py",
            shared,
            owner="you",
        ),
        _file(
            templates["factory"] / "problems.py.j2",
            layout.problems,
            shared | {"invalid": statuses.get("invalid-body", 422)},
            owner="shared",
        ),
        _file(
            templates["pack"] / "test_api.py.j2",
            f"{layout.tests}/test_{service}.py",
            shared | _test_context(spec, pack),
            kind="test",
        ),
    ]
    model = _file(
        templates["source"] / "model.py.j2",
        f"{layout.models}/{module}.py",
        shared
        | {
            "table": data["table"],
            "columns": columns,
            "stdlib_imports": stdlib_imports,
            "sqlalchemy_import": sqlalchemy_import,
            "base_module": base_module,
            "base_name": base_name,
        },
        kind="model",
    )
    files.insert(0, model)
    for package in (layout.schemas, layout.repositories, layout.rules):
        files.append(
            {
                "template": None,
                "target": f"{package}/__init__.py",
                "context": {},
                "owner": "shared",
                "kind": "package",
            }
        )
    api_module = service
    return {
        "files": files,
        "register": {
            "routers": [
                f"from {mod['problems']} import install as install_problems",
                "install_problems(application)",
                f"from {mod['api']} import {api_module}",
                f"application.include_router({api_module}.router)",
            ],
            "models": [f"from {mod['models']}.{module} import {entity}  # noqa: F401"],
        },
    }


def _file(template, target, context, owner="harness", kind="code") -> dict:
    return {
        "template": template,
        "target": target,
        "context": context,
        "owner": owner,
        "kind": kind,
    }


def _module(path: str) -> str:
    return path.removesuffix(".py").replace("/", ".")


def _pydantic(fields: dict, response: bool = False) -> tuple[list[str], list[dict]]:
    imports, out = [], []
    for name, detail in fields.items():
        python = PYTHON_TYPES.get(detail.get("type", "text"), "str")
        if python == "Decimal":
            imports.append("from decimal import Decimal")
        elif python in ("date", "datetime"):
            imports.append(f"from datetime import {python}")
        if "one_of" in detail:
            choices = ", ".join(repr(c) for c in detail["one_of"])
            imports.append("from typing import Literal")
            python = f"Literal[{choices}]"
        constraints = []
        if "min" in detail:
            constraints.append(f"ge={detail['min']}")
        if "max" in detail:
            constraints.append(f"le={detail['max']}")
        required = detail.get("required") or (response and detail.get("generated"))
        if required:
            annotation, default = (
                python,
                (f" = Field({', '.join(constraints)})" if constraints else ""),
            )
        else:
            annotation = f"{python} | None"
            default = (
                f" = Field(default=None, {', '.join(constraints)})" if constraints else " = None"
            )
        out.append({"name": name, "annotation": annotation, "default": default})
    return imports, out


def _columns(data: dict, fields: dict, parent: dict | None) -> tuple[list[str], str, list[dict]]:
    sql, python, columns = set(), set(), []
    for attribute, column in data["columns"].items():
        detail = fields.get(attribute, {"type": "text"})
        kind = detail.get("type", "text")
        sql_type = SQL_TYPES.get(kind, "String(200)")
        sql.add(sql_type.split("(")[0])
        py = PYTHON_TYPES.get(kind, "str")
        if py == "Decimal":
            python.add("from decimal import Decimal")
        elif py in ("date", "datetime"):
            python.add(f"from datetime import {py}")
        args = [f'"{column}"'] if column != attribute else []
        args.append(sql_type)
        primary = kind == "id" and detail.get("generated")
        if primary:
            args.append("primary_key=True")
        required = primary or detail.get("required")
        if detail.get("unique"):
            args.append("unique=True")
        if "initial" in detail:
            args.append(f'default="{detail["initial"]}"')
            required = True
        if not required:
            py = f"{py} | None"
        columns.append({"attribute": attribute, "python": py, "args": ", ".join(args)})
    if parent is not None:
        sql.update({"ForeignKey", "Integer"})
        target = f"{data['parent_table']}.{data['parent_column']}"
        columns.append(
            {
                "attribute": parent["key"],
                "python": "int",
                "args": f'Integer, ForeignKey("{target}"), nullable=False',
            }
        )
    return sorted(python), f"from sqlalchemy import {', '.join(sorted(sql))}", columns


def _test_context(spec: dict, pack) -> dict:
    parent_key = (spec.get("data") or {}).get("parent_key")
    cases = []
    for case in spec.get("tests", []):
        request, expect = case["request"], case["expect"]
        given = []
        for step in case.get("given", []):
            parents = f", {parent_key}={step['within']}.id" if step.get("within") else ""
            given.append(
                {
                    "alias": step["as"],
                    "entity": step["create"],
                    "module": snake(step["create"]),
                    "fields": repr(step.get("with", {})),
                    "parents": parents,
                }
            )
        cases.append(
            {
                "slug": re.sub(r"[^a-z0-9]+", "_", case["name"].lower()).strip("_"),
                "name": case["name"],
                "rule": case.get("rule", ""),
                "given": given,
                "method": request["method"].lower(),
                # an f-string only when the path uses a `given` value
                "path": ("f" if "{" in request["path"] else "") + f'"{request["path"]}"',
                "json": f", json={request['body']!r}" if "body" in request else "",
                "status": expect["status"],
                "headers": expect.get("headers", []),
                "body": [(k, repr(v)) for k, v in expect.get("body", {}).items()],
                "problem": repr(expect["problem"]) if "problem" in expect else None,
            }
        )
    return {
        "cases": cases,
        "client": pack.manifest.fixtures.get("client", "client"),
        "session": pack.manifest.fixtures.get("session", "session"),
    }
