"""Load compiled knowledge into SWI-Prolog (via janus-swi) and ask it questions.

Prolog runs inside this Python process. Each Engine loads its knowledge into
its own Prolog module, so engines never see each other's facts.
"""

import itertools
from dataclasses import dataclass
from types import ModuleType

from harness.knowledge.schema import Domain, Technical
from harness.logic.compile import NameMap, compile_knowledge, library

INSTALL_HINT = "install SWI-Prolog (macOS: brew install swi-prolog) and the harness[prolog] extra"

_modules = itertools.count()


class PrologUnavailable(RuntimeError):
    """SWI-Prolog or the janus-swi package is not installed."""


def _janus() -> ModuleType:
    try:
        import janus_swi
    except Exception as exc:  # ImportError, or the Prolog library failing to load
        raise PrologUnavailable(f"SWI-Prolog is not available ({exc}); {INSTALL_HINT}") from exc
    return janus_swi


MESSAGES = {
    "composition_child_outside_parent": "{0} can only be created within {1}; add `within: {1}`",
    "delete_parent_without_cascade": (
        "deleting a {0} must also remove its {1} children, but no rule adds cascade_children"
    ),
    "composition_cycle": "{name} is composed in itself through a chain of compositions",
    "conflicting_decisions": "rules disagree on {0}: {1} (rule {2}) vs {3} (rule {4})",
    "flow_cycle": "step '{name}' can lead back to itself; the classification could loop forever",
    "flow_unreachable": "step '{name}' can never be reached from the start",
    "flow_dead_end": "step '{name}' can never finish with an outcome",
}


@dataclass(frozen=True)
class LogicProblem:
    where: str  # service | entity | association
    name: str  # the name as written in the YAML
    code: str
    message: str


class Engine:
    def __init__(self, domain: Domain, technical: Technical):
        self._janus = _janus()
        self.names = NameMap(domain)
        self.module = f"harness_kb_{next(_modules)}"
        self.program = compile_knowledge(domain, technical)
        self._janus.consult(f"{self.module}_knowledge", data=self.program, module=self.module)
        self._janus.consult(f"{self.module}_library", data=library(), module=self.module)

    def query(self, goal: str, inputs: dict | None = None) -> list[dict]:
        """All answers to `goal`, as dicts of variable bindings."""
        answers = self._janus.query(f"{self.module}:({goal})", inputs or {})
        return [{k: v for k, v in answer.items() if k != "truth"} for answer in answers]

    def ask(self, goal: str, inputs: dict | None = None) -> bool:
        return bool(self._janus.query_once(f"{self.module}:({goal})", inputs or {})["truth"])

    def problems(self) -> list[LogicProblem]:
        found = []
        for a in self.query("problem(Where, Name, Code, Args)"):
            where, code = a["Where"], a["Code"]
            name = self.names.entity(a["Name"]) if where == "entity" else a["Name"]
            args = [self.names.entity(x) if isinstance(x, str) else x for x in a["Args"]]
            template = MESSAGES.get(code, code + ": {args}")
            message = template.format(*args, name=name, args=args)
            problem = LogicProblem(where, name, code, f"{code}: {message}")
            if problem not in found:
                found.append(problem)
        return found
