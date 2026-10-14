"""Run the classification flow from technical.yaml, one question at a time.

The flow decides the order of questions and where each answer leads; this loop
only applies the same three rules at every step: an answer below the threshold
goes to `low_confidence`, "none" goes to `on_none`, anything else to `on_answer`.
"""

import time

from harness.classify.questions import OPTION_SOURCES
from harness.knowledge.schema import Branch, Domain, Technical
from harness.semif.client import NONE, NONE_OPTION, Choice, DecisionModel, Question
from harness.trace import Event, Trace, margin


def classify(
    task: str, domain: Domain, technical: Technical, model: DecisionModel, threshold: float
) -> Trace:
    """Walk the flow for one task; the trace holds every step, the outcome and the answers."""
    flow = technical.classification
    trace = Trace(task, threshold)
    started = time.perf_counter()
    current = flow.start
    while current not in flow.outcomes:
        spec = flow.steps[current]
        options = OPTION_SOURCES[spec.options](domain, technical, trace.answers)
        question = Question(current, spec.question, (*options, NONE_OPTION))
        asked = time.perf_counter()
        choice: Choice = model.choose(task, question)
        accepted = choice.confidence >= threshold
        trace.events.append(
            Event(
                stage="classify",
                step=current,
                by="semif",
                answer=choice.answer,
                accepted=accepted,
                question=spec.question,
                options=[o.name for o in question.options],
                confidence=choice.confidence,
                margin=margin(choice.probabilities),
                probabilities=choice.probabilities,
                ms=(time.perf_counter() - asked) * 1000,
            )
        )
        if not accepted:
            current = flow.low_confidence
        elif choice.answer == NONE:
            current = spec.on_none
        else:
            trace.answers[current] = choice.answer
            current = _next(spec.on_answer, choice.answer, technical)
    trace.outcome = current
    trace.ms = (time.perf_counter() - started) * 1000
    return trace


def _next(on_answer: str | Branch, answer: str, technical: Technical) -> str:
    if isinstance(on_answer, str):
        return on_answer
    # by: kind_target — the chosen kind acts on an entity or on an association
    return on_answer.cases[technical.kinds[answer].target]
