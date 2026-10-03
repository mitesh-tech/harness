# Architecture

![Harness end-to-end flow](diagrams/end-to-end.svg)

The harness turns a task written in business language into verified backend code. It works in four stages.

| Stage | What happens |
|---|---|
| **Decide** | SemIf classifies the task through three knowledge-base layers: domain → architecture → implementation. Links between layers fill in most answers; SemIf only answers the gaps. When nothing fits or SemIf is unsure, an LLM drafts an option and a human confirms it through a knowledge-base pull request. |
| **Build** | A human approves the API contract. Context is found in the stack pack's paths. If a generator exists for the pattern, its inputs are gathered, a human confirms the spec, and the generator writes the code plus extension stubs. Otherwise LLM agents build the units. `models.yaml` picks the model for each step. |
| **Verify** | The stack pack's test, lint and type commands run, plus scope and generated-code checksum checks, then SemIf yes/no checks per acceptance criterion. Failures retry with a stronger model; unsure results go to a human. |
| **Learn** | An audit report records the path, models and gaps. Human-confirmed decisions are saved as eval cases. CI runs the evals with per-layer gates on every knowledge-base change, and a human reviews and merges. |

Colour key: coral = human and knowledge base, teal = SemIf, purple = LLM agents, pink = stack pack, grey = harness code.
