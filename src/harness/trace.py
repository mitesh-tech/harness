"""The trace of a run: every decision, in order, with who made it and why.

A trace is written as JSONL: a `run` header line, one `event` line per decision,
and a `result` line. Each stage adds its own events (`classify` today; resolve,
spec, generation and verification later), so one trace covers a whole run.
Evals read traces to check not only the outcome but how it was reached.
"""

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal

from harness import __version__

By = Literal["semif", "rule", "person"]


@dataclass
class Event:
    stage: str
    step: str
    by: By
    answer: str
    accepted: bool = True
    question: str | None = None
    options: list[str] = field(default_factory=list)
    confidence: float | None = None
    margin: float | None = None  # top probability minus the second one
    probabilities: dict[str, float] = field(default_factory=dict)
    reason: str | None = None  # why a rule decided this
    ms: float = 0.0


@dataclass
class Trace:
    task: str
    threshold: float
    run_id: str = field(default_factory=lambda: new_run_id())
    started: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    harness: str = __version__
    decision_model: str = "semif"
    url: str | None = None
    events: list[Event] = field(default_factory=list)
    outcome: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    ms: float = 0.0

    @property
    def path(self) -> list[str]:
        return [e.step for e in self.events]

    @property
    def semif_calls(self) -> int:
        return sum(e.by == "semif" for e in self.events)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["semif_calls"] = self.semif_calls
        return data

    def to_jsonl(self) -> str:
        header = {
            "type": "run",
            "run_id": self.run_id,
            "task": self.task,
            "harness": self.harness,
            "decision_model": self.decision_model,
            "url": self.url,
            "threshold": self.threshold,
            "started": self.started,
        }
        result = {
            "type": "result",
            "outcome": self.outcome,
            "answers": self.answers,
            "semif_calls": self.semif_calls,
            "ms": round(self.ms),
        }
        lines = [header] + [{"type": "event", **asdict(e)} for e in self.events] + [result]
        return "".join(json.dumps(line) + "\n" for line in lines)

    @classmethod
    def from_jsonl(cls, text: str) -> "Trace":
        lines = [json.loads(line) for line in text.splitlines() if line.strip()]
        header = next(line for line in lines if line["type"] == "run")
        result = next(line for line in lines if line["type"] == "result")
        events = [
            Event(**{k: v for k, v in line.items() if k != "type"})
            for line in lines
            if line["type"] == "event"
        ]
        fields = {k: v for k, v in header.items() if k != "type"}
        return cls(
            **fields,
            events=events,
            outcome=result["outcome"],
            answers=result["answers"],
            ms=result["ms"],
        )


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)


def margin(probabilities: dict[str, float]) -> float:
    """How far the top answer is ahead of the next one: an early sign of doubt."""
    top = sorted(probabilities.values(), reverse=True) + [0.0, 0.0]
    return top[0] - top[1]
