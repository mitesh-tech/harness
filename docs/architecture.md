# Architecture

![Harness end-to-end flow](diagrams/end-to-end.svg)

The harness turns a task written in business language into verified backend code. It works in four stages.

| Stage | What happens |
|---|---|
| **Decide** | SemIf reads the task and picks which **service** from the **domain** knowledge base it describes; this becomes a fact with a confidence score. A **Prolog engine** then derives the design from the facts, using the **technical** knowledge base and bridge rules: method, persistence and components. When a rule needs a fact the knowledge base doesn't state, the harness asks SemIf one yes/no question and feeds the answer back. Every decision carries a proof and its source. When no service fits, SemIf is unsure, or no rule applies, an LLM drafts the missing facts and a human approves them through a knowledge-base pull request. |
| **Build** | A human confirms the design and the API contract. Context is found in the stack pack's paths. If a generator exists for the pattern, its inputs are gathered, a human confirms the spec, and the generator writes the code plus extension stubs. Otherwise LLM agents build the units. `models.yaml` picks the model for each step. |
| **Verify** | The stack pack's test, lint and type commands run, plus scope and generated-code checksum checks, then SemIf yes/no checks per acceptance criterion. Failures retry with a stronger model; unsure results go to a human. |
| **Learn** | An audit report records the path, the source of every decision, the models and any gaps. Human-confirmed decisions are saved as eval cases. CI runs the evals with per-layer gates on every knowledge-base change, and a human reviews and merges. |

SemIf turns language into facts; Prolog turns facts into decisions. The domain knowledge base describes the business (entities, associations, state lifecycles, services); the technical knowledge base describes how backends are built and is reusable across projects. The same facts always give the same design.

Colour key: coral = human and knowledge base, teal = SemIf, purple = LLM agents, pink = stack pack, grey = harness code and Prolog rules.
