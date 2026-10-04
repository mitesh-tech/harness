# Architecture

![Harness end-to-end flow](diagrams/end-to-end.svg)

The harness turns a task written in business language into verified backend code. It works in four stages.

| Stage | What happens |
|---|---|
| **Decide** | SemIf reads the task against the **domain** knowledge base and returns a capability with its facts. The **resolver** then derives the rest with fixed rules: the lookup table gives the **architecture** (method and pattern), the component rules give the **implementation** (components). SemIf is asked only about gaps the rules leave open. When nothing fits, SemIf is unsure, or rules conflict, an LLM drafts an option and a human confirms it through a knowledge-base pull request. |
| **Build** | A human approves the API contract. Context is found in the stack pack's paths. If a generator exists for the pattern, its inputs are gathered, a human confirms the spec, and the generator writes the code plus extension stubs. Otherwise LLM agents build the units. `models.yaml` picks the model for each step. |
| **Verify** | The stack pack's test, lint and type commands run, plus scope and generated-code checksum checks, then SemIf yes/no checks per acceptance criterion. Failures retry with a stronger model; unsure results go to a human. |
| **Learn** | An audit report records the path, the source of every decision, the models and any gaps. Human-confirmed decisions are saved as eval cases. CI runs the evals with per-layer gates on every knowledge-base change, and a human reviews and merges. |

SemIf makes one judgement per task: which capability it is. Everything after that follows fixed rules, so the same facts always give the same design. See [plan.md](plan.md#the-resolver) for how the resolver works.

Colour key: coral = human and knowledge base, teal = SemIf, purple = LLM agents, pink = stack pack, grey = fixed code and rules.
