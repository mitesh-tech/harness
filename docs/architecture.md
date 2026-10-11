# Architecture

The harness is a **platform of small software factories**. Each factory knows how to produce one kind of software from a task written in business language. The first is the [CRUD API factory](factories/crud-api.md).

![End-to-end flow](diagrams/end-to-end.svg)

## Platform and factories

| Part | Provides | Shared or per factory |
|---|---|---|
| **Factory router** | Picks which factory handles a task (SemIf), or reports it as unsupported | Platform |
| **Domain knowledge base** (`.harness/domain.yaml`) | Entities, identifiers, attributes, associations, existing services | Platform (your project) |
| **Data knowledge base** (`.harness/data.yaml`) | Data sources and how each resource maps onto them | Platform (your project) |
| **Factory knowledge** (`technical.yaml`, `checks.pl`) | Operations, rules, components, the question flow | Per factory |
| **Prolog engine** | Compiles the knowledge to Prolog, applies the rules, runs the checks, explains every decision | Platform |
| **SemIf** | Reads the task: picks among options taken from the knowledge, answers yes/no questions | Platform |
| **Templates** | Code for a factory and a stack pack (e.g. CRUD API × FastAPI) | Per factory and pack |
| **Data-source plugins** | How a source type is configured, mapped and accessed (relational first) | Platform |
| **Run, verify, report, learn** | Spec, confirmation, worktree, generation, checks, report, knowledge-base updates | Platform |

## Stages of a run

| Stage | What happens |
|---|---|
| **Route** | SemIf picks the factory, or the task is unsupported |
| **Classify** | The factory's flow: entity, then new API or change, then operation and existing service (CRUD factory) |
| **Resolve** | Prolog derives the design from the knowledge and the factory's rules. SemIf is asked only yes/no questions the knowledge leaves open. Every decision carries its proof and source |
| **Spec** | The design is written as `spec.yaml` and `openapi.yaml` in the run folder |
| **Confirm** | You approve, edit (re-checked) or reject the spec. Nothing is written before this |
| **Generate** | Code is generated from the confirmed spec only, on a separate git branch |
| **Verify** | Tests, lint, scope and generated-code checks, results against the spec |
| **Report** | The spec, proofs, sources, files, results and gaps |
| **Learn** | New services and attributes go to the knowledge base through a pull request you approve; confirmed classifications become eval cases |

## Principles

- **SemIf turns language into facts; Prolog turns facts into decisions.** The same facts always give the same design.
- **Deterministic first:** knowledge and rules before SemIf, templates before LLMs, a person for anything unsure.
- **A person confirms** every spec and every knowledge-base change.
- **Language-agnostic:** stacks plug in as packs, data sources as plugins.

Colour key in the diagram: teal = SemIf, grey = code and Prolog, coral = you and your knowledge, pink = templates and plugins.
