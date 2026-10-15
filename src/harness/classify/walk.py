"""Run the classification flow, one step at a time, and record every decision.

The platform first asks which factory fits the task. Then the factory's flow
(technical.yaml) decides the order of steps and where each answer leads; this
loop applies the same rules at every step:

- no options at all            -> `on_empty`, decided by rule
- one option, `skip_if_single` -> that option, decided by rule
- otherwise SemIf answers: below the threshold -> `low_confidence`,
  "none" -> `on_none`, anything else -> `on_answer`

A new service gets a name from its kind's `names_as` when every part is known.
"""

import re
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace

from harness.classify.questions import EMPTY, OPTION_SOURCES, Context
from harness.knowledge.schema import (
    UNSUPPORTED,
    Branch,
    Domain,
    FactoryManifest,
    FlowStep,
    Technical,
)
from harness.logic.compile import snake
from harness.logic.engine import Engine
from harness.semif.client import NONE, NONE_OPTION, DecisionModel, Option, Question
from harness.trace import Event, Trace, margin

FACTORY_QUESTION = (
    "This task is for {system}. The business keeps these domain entities: {entities}. "
    "Which kind of software work does the task need?"
)
FACTORY_NONE = "None: the task needs a kind of software work that is not listed"


def classify(
    task: str,
    domain: Domain,
    technical: Technical,
    model: DecisionModel,
    threshold: float,
    factories: Mapping[str, FactoryManifest] | None = None,
) -> Trace:
    """Classify one task; the trace holds every step, the outcome and the answers.

    factories: the enabled factories to choose from; without them the factory
    step is skipped (the knowledge given is the factory's).
    """
    trace = Trace(task, threshold)
    started = time.perf_counter()
    flow = technical.classification
    ctx = Context(domain, technical, Engine(domain, technical))

    current = flow.start
    if factories:
        current = _route(trace, model, domain, factories, threshold, flow.low_confidence) or current

    while current not in flow.outcomes:
        spec = flow.steps[current]
        options = step_options(spec, ctx, trace.answers)
        if not options:
            reason = EMPTY.get(spec.options or "", "nothing to choose from")
            _rule(trace, current, NONE, reason.format_map(defaultdict(str, trace.answers)))
            current = spec.on_empty or spec.on_none
            continue
        if spec.skip_if_single and len(options) == 1:
            _rule(trace, current, options[0].name, options[0].reason or "the only option")
            trace.answers[current] = options[0].name
            current = _next(spec.on_answer, options[0].name)
            continue
        question = render(_question(spec, technical, trace.answers), domain, trace.answers)
        accepted = _ask(
            trace,
            model,
            current,
            question,
            [*options, none_option(spec, domain, trace.answers)],
            threshold,
        )
        if accepted is None and spec.accept_named:
            accepted = _accept_named(trace, domain, current)
        if accepted is None:
            current = flow.low_confidence
        elif trace.events[-1].answer == NONE:
            current = spec.on_none
        else:
            current = _next(spec.on_answer, trace.events[-1].answer)

    trace.outcome = current
    if current == "new_service":
        _name_new_service(trace, domain, technical)
    trace.ms = (time.perf_counter() - started) * 1000
    return trace


def named_entities(task: str, domain: Domain) -> list[str]:
    """Entities the task names as a whole word, singular or plural (Book: book, books)."""
    words = task.lower()
    found = []
    for name in domain.entities:
        spoken = snake(name).replace("_", " ")  # BookAuthor -> book author
        if re.search(rf"\b({re.escape(spoken)}|{re.escape(plural(spoken))})\b", words):
            found.append(name)
    return found


def _accept_named(trace: Trace, domain: Domain, step: str) -> str | None:
    """Take an unsure answer when the task names exactly that one entity."""
    event = trace.events[-1]
    named = named_entities(trace.task, domain)
    if named != [event.answer]:
        return None
    event.accepted = True
    event.reason = (
        f"the task names {event.answer}, and SemIf's top answer agrees "
        f"(confidence {event.confidence:.2f} below {trace.threshold:g})"
    )
    trace.answers[step] = event.answer
    return event.answer


def _route(
    trace: Trace,
    model: DecisionModel,
    domain: Domain,
    factories: Mapping[str, FactoryManifest],
    threshold: float,
    unsure: str,
) -> str | None:
    """Ask which factory fits; return an outcome to stop at, or None to go on.

    The winner decides, whatever the confidence:
    - "none" or a planned factory wins -> unsupported (nothing can build it yet)
    - an available factory wins -> go on; when unsure, go on only if the
      runner-up is "none" (a gate against out-of-scope tasks), otherwise
      another factory competes and a person decides (unsure)
    """
    options = [Option(n, m.description, tuple(m.examples)) for n, m in factories.items()]
    question = render(FACTORY_QUESTION, domain, trace.answers)
    none = Option(NONE, render(FACTORY_NONE, domain, trace.answers))
    _ask(trace, model, "factory", question, [*options, none], threshold)
    event = trace.events[-1]
    winner = event.answer
    sure = f"{event.confidence:.2f}" + ("" if event.accepted else f", below {threshold:g}")

    if winner == NONE:
        event.reason = f"no factory fits ({sure})"
        return UNSUPPORTED
    trace.answers["factory"] = winner
    if not factories[winner].available:
        event.reason = f"{winner} is planned but not available yet ({sure})"
        return UNSUPPORTED
    if event.accepted:
        return None
    runner_up = sorted(event.probabilities, key=lambda o: -event.probabilities[o])[1]
    if runner_up == NONE:
        event.accepted = True
        event.reason = f"{winner} wins and only 'none' competes ({sure})"
        return None
    del trace.answers["factory"]
    event.reason = f"{winner} and {runner_up} compete ({sure})"
    return unsure


def step_options(spec: FlowStep, ctx: Context, answers: dict[str, str]) -> list[Option]:
    """The options a step offers, given the answers so far (without "none").

    Their texts are worded for the target, e.g. "Make a new {target}".
    """
    if spec.choices is not None:
        options = [Option(n, c.description, tuple(c.examples)) for n, c in spec.choices.items()]
    else:
        options = OPTION_SOURCES[spec.options](ctx, answers)
    return [
        replace(
            o,
            description=render(o.description, ctx.domain, answers),
            not_=render(o.not_, ctx.domain, answers) if o.not_ else None,
        )
        for o in options
    ]


def none_option(spec: FlowStep, domain: Domain, answers: dict[str, str]) -> Option:
    """What "none" means at this step."""
    if spec.none is None:
        return NONE_OPTION
    return Option(NONE, render(spec.none, domain, answers))


def render(text: str, domain: Domain, answers: dict[str, str]) -> str:
    """Fill in {system}, {entities}, the target and earlier answers, e.g. {operation}."""
    values = {
        "system": domain.description or "this business",
        "entities": ", ".join(domain.entities),
        **answers,
    }
    target = answers.get("target")
    if target:
        values.update(target_words(domain, target))
    filled = re.sub(r"{(\w+)}", lambda m: values.get(m.group(1), "it"), text)
    return re.sub(r" {2,}", " ", filled)


def target_words(domain: Domain, target: str) -> dict[str, str]:
    """{target}, {targets} and {target_context}: the target in the domain's own words."""
    if target in domain.associations:
        a = domain.associations[target]
        name = f"link between {a.parent} and {a.child}"
        context = f"It is about the {name}"
        context += f": {a.description}." if a.description else "."
        return {
            "target": name,
            "targets": f"links between {a.parent} and {a.child}",
            "target_context": context,
        }
    entity = domain.entities[target]
    sentences = [f"It is about {target}: {entity.description}."]
    parents = [
        a.parent
        for a in domain.associations.values()
        if a.kind == "composition" and a.child == target
    ]
    if parents:
        sentences.append(f"A {target} is part of a {parents[0]}.")
    details = [
        _detail(name, attribute)
        for name, attribute in entity.attributes.items()
        if name != entity.identifier and not attribute.generated
    ]
    if details:
        sentences.append(f"A {target} keeps: {', '.join(details)}.")
    if entity.lifecycle:
        stages = ", ".join(sorted(entity.lifecycle.states))
        sentences.append(f"It moves through stages: {stages}.")
    return {"target": target, "targets": plural(target), "target_context": " ".join(sentences)}


def _detail(name: str, attribute) -> str:
    if attribute.min is not None and attribute.max is not None:
        return f"{name} ({attribute.type}, {attribute.min} to {attribute.max})"
    return f"{name} ({attribute.type})"


def _question(spec: FlowStep, technical: Technical, answers: dict[str, str]) -> str:
    if spec.question_from == "operation":
        return technical.operations[answers["operation"]].question or spec.question
    return spec.question


def _ask(trace, model, step, question, options, threshold) -> str | None:
    """Ask SemIf; record the event; keep the answer when it is sure enough.

    options end with the step's "none" option. Returns the answer when
    accepted, otherwise None.
    """
    full = Question(step, question, tuple(options))
    asked = time.perf_counter()
    choice = model.choose(trace.task, full)
    accepted = choice.confidence >= threshold
    trace.events.append(
        Event(
            stage="classify",
            step=step,
            by="semif",
            answer=choice.answer,
            accepted=accepted,
            question=question,
            options=[o.name for o in full.options],
            confidence=choice.confidence,
            margin=margin(choice.probabilities),
            probabilities=choice.probabilities,
            ms=(time.perf_counter() - asked) * 1000,
        )
    )
    if not accepted:
        return None
    if choice.answer != NONE:
        trace.answers[step] = choice.answer
    return choice.answer


def _rule(trace: Trace, step: str, answer: str, reason: str) -> None:
    trace.events.append(Event(stage="classify", step=step, by="rule", answer=answer, reason=reason))


def _next(on_answer: str | Branch, answer: str) -> str:
    return on_answer if isinstance(on_answer, str) else on_answer.cases[answer]


# ── Naming a new service ────────────────────────────────────────────


def plural(word: str) -> str:
    if re.search(r"[^aeiou]y$", word):
        return word[:-1] + "ies"
    if re.search(r"(s|x|z|ch|sh)$", word):
        return word + "es"
    return word + "s"


def _name_new_service(trace: Trace, domain: Domain, technical: Technical) -> None:
    kind = technical.kinds[trace.answers["kind"]]
    target = trace.answers["target"]
    if target in domain.associations:  # a connection: entity is the parent, target the child
        a = domain.associations[target]
        parts = {"entity": snake(a.parent), "target": snake(a.child)}
    else:
        parts = {"entity": snake(target)}
    parts["entities"] = plural(parts["entity"])
    missing = sorted(set(re.findall(r"{(\w+)}", kind.names_as)) - set(parts))
    if missing:
        reason = f"{kind.names_as}: {', '.join(missing)} not known yet"
        trace.events.append(
            Event(stage="classify", step="name", by="rule", answer="open", reason=reason)
        )
        return
    name = kind.names_as.format(**parts)
    trace.answers["name"] = name
    _rule(trace, "name", name, f"kind {trace.answers['kind']} names new services {kind.names_as}")
