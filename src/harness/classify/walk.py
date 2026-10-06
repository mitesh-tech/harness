"""Run the classification flow from technical.yaml, one question at a time.

The flow decides the order of questions and where each answer leads; this loop
only applies the same three rules at every step: an answer below the threshold
goes to `low_confidence`, "none" goes to `on_none`, anything else to `on_answer`.
"""

from dataclasses import asdict, dataclass, field

from harness.classify.questions import OPTION_SOURCES
from harness.knowledge.schema import Branch, Domain, Technical
from harness.semif.client import NONE, NONE_OPTION, Choice, DecisionModel, Question


@dataclass
class Step:
    id: str
    question: str
    options: list[str]
    answer: str
    confidence: float
    probabilities: dict[str, float]
    accepted: bool


@dataclass
class Classification:
    task: str
    threshold: float
    outcome: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    steps: list[Step] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def classify(
    task: str, domain: Domain, technical: Technical, model: DecisionModel, threshold: float
) -> Classification:
    flow = technical.classification
    result = Classification(task, threshold)
    current = flow.start
    while current not in flow.outcomes:
        spec = flow.steps[current]
        options = OPTION_SOURCES[spec.options](domain, technical, result.answers)
        question = Question(current, spec.question, (*options, NONE_OPTION))
        choice: Choice = model.choose(task, question)
        accepted = choice.confidence >= threshold
        result.steps.append(
            Step(
                current,
                spec.question,
                [o.name for o in question.options],
                choice.answer,
                choice.confidence,
                choice.probabilities,
                accepted,
            )
        )
        if not accepted:
            current = flow.low_confidence
        elif choice.answer == NONE:
            current = spec.on_none
        else:
            result.answers[current] = choice.answer
            current = _next(spec.on_answer, choice.answer, technical)
    result.outcome = current
    return result


def _next(on_answer: str | Branch, answer: str, technical: Technical) -> str:
    if isinstance(on_answer, str):
        return on_answer
    # by: kind_target — the chosen kind acts on an entity or on an association
    return on_answer.cases[technical.kinds[answer].target]
