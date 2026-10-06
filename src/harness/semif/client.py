"""The decision-model interface: what the harness asks, and the shape of the answers.

Classification only talks to this interface, never to a particular model, so
the model behind it (SemIf today; Kev or Jev later) can be swapped.
"""

from dataclasses import dataclass, field
from typing import Protocol

NONE = "none"


@dataclass(frozen=True)
class Option:
    name: str
    description: str
    examples: tuple[str, ...] = ()
    not_: str | None = None

    def text(self) -> str:
        """Everything the model reads about this option, in one string."""
        parts = [self.description]
        if self.examples:
            parts.append("Examples: " + "; ".join(self.examples) + ".")
        if self.not_:
            parts.append(self.not_)
        return " ".join(parts)


NONE_OPTION = Option(NONE, "None of the other options fits this task.")


@dataclass(frozen=True)
class Question:
    id: str
    instructions: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Choice:
    answer: str
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)


class DecisionModel(Protocol):
    def choose(self, task: str, question: Question) -> Choice:
        """Pick one option for the task, with a confidence from 0 to 1."""
        ...

    def yes_no(self, task: str, question_id: str, instructions: str) -> float:
        """Probability (0 to 1) that the answer to a yes/no question is yes."""
        ...


class DecisionModelError(RuntimeError):
    """The decision model could not be reached, or answered in an unexpected way."""
