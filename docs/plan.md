# Harness plan

## Goal

A public, model-agnostic platform of **small software factories** for backend development. Each factory turns a task written in business language into verified code of one kind:

- **SemIf** (a small open decision model) reads the task and picks among options taken from your knowledge base.
- **Prolog** derives the design from that knowledge and the factory's rules, and explains every decision.
- A **spec** is written and **confirmed by you** before any code is generated.
- **Templates** generate the code from the confirmed spec; checks verify it; a report records everything.
- Confirmed results flow back into the knowledge base, with your approval.

The first factory is the [CRUD API factory](factories/crud-api.md). See [architecture.md](architecture.md) for the end-to-end flow.

## Principles

1. **A person confirms** every spec and every knowledge-base change.
2. **Deterministic first:** knowledge and rules before SemIf, templates before LLMs, a person for anything unsure.
3. **SemIf turns language into facts; Prolog turns facts into decisions.** Every decision records its source.
4. **Decisions live in YAML; the engine lives in Python.** Factories, packs and data sources are plugins.
5. **Small factories:** each has a narrow job, its own rules and its own evals.
6. **Nothing is trusted without evals.**
7. **Public engine, private knowledge:** a project's real knowledge stays in the project.

## Status

| Part | Status |
|---|---|
| Project scaffold, CI, licence (Apache-2.0), src layout | Done |
| Bookstore sample app (`examples/bookstore`) | Done |
| Knowledge files, schemas, `harness validate` (syntax, shape, references) | Done |
| Prolog engine, logic checks, `harness kb export` / `kb query` | Done |
| SemIf over HTTP (Qwen3.5-4B on MLX), `harness semif check`, recorded tests | Done |
| Classification (area → service flow, configuration-driven) | Done; replaced in S2 by the factory flow |
| Factory structure, router, data layer, resolve, spec, generation, verification, learning | Planned below |

## Stages

Each stage is one or two pull requests, ends with something you can run, and starts only after you approve its detailed plan.

| Stage | Content | You can run |
|---|---|---|
| **S0: Design docs** | CRUD API factory specification, this plan, architecture and end-to-end diagram | Read the design |
| **S1: Factory structure** | `src/harness/factories/crud-api/` with `factory.yaml`, factory loader; `technical.yaml` moved into the factory, trimmed to CRUD, with the **operation layer** (create, read, update, delete → kinds); Bookstore's non-CRUD services removed; `validate` covers factories | `harness validate` |
| **S2: Classification** | **Factory router**; entity first; new API or change; operation → kind → lookup → SemIf confirms; outcomes `UNSUPPORTED`, `NO_ENTITY`, `NEW_SERVICE`, `MATCHED`, `UNSURE`; SemIf answers re-recorded | `harness classify "…"` |
| **S3: Data layer** | `.harness/data.yaml` (sources, conventions, mappings); **relational** source plugin (schema, capabilities, optional introspection); mapping checks; `NO_MAPPING` | `harness validate --introspect` |
| **S4: Resolve and spec** | **API anatomy rules** in Prolog (request body, path and query parameters, response, statuses), data mapping, askable yes/no questions to SemIf, sources for every value, open questions; `spec.yaml` + `openapi.yaml` | `harness resolve "…"` |
| **S5: Confirm, generate, verify, report (create)** | Confirm the spec (approve, edit and re-check, reject); FastAPI templates (route, schemas, repository interface, relational adapter, tests); router registration; git worktree; verification against the spec; report | `harness run "Let staff add a new publisher"` |
| **S6: All CRUD kinds** | read one, list (filters are response fields, paging), update, replace, delete; nested APIs for composition | `harness run` for any CRUD task |
| **S7: Change existing APIs** | Add or remove an attribute: impact analysis, then domain, mapping, schemas and tests updated together | `harness run "Add an isbn field to books"` |
| **S8: Learn and evals** | New services and attributes saved to the knowledge base after approval; eval cases; `harness eval` | Track classification accuracy |

## Phase 2 and later

| Item | Notes |
|---|---|
| **Domain-model factory** | LLM-guided design of new entities: asks the right questions, considers the existing domain model, drafts YAML for your approval |
| More operations | Lifecycle transitions, linking aggregations |
| More data sources | REST APIs of other services, document stores |
| Agent-driven runs | Claude Code, Codex, Copilot skills that drive the same spec-based flow |
| More stack packs | e.g. Node/Express, to prove the factories are language-agnostic |
| More factories | e.g. authentication, export and reports |

## Repository layout (target)

```
harness/
├── src/harness/
│   ├── knowledge/       # schemas, loading, validation (domain, data, technical)
│   ├── logic/           # Prolog: compile, engine, core and checks
│   ├── semif/           # decision-model interface and HTTP adapter
│   ├── classify/        # generic flow runner, option sources, lookups
│   ├── factories/
│   │   └── crud-api/    # factory.yaml, technical.yaml, checks.pl, templates/, evals/
│   ├── datasources/
│   │   └── relational/  # source.yaml, schema, introspect, templates/
│   ├── resolve/         # design, naming, facts, askable loop, spec
│   ├── run/             # confirm, worktree, orchestration
│   ├── verify/          # checks against the spec
│   └── report/          # run reports
├── examples/bookstore/  # sample app + its harness.yaml and .harness/ knowledge
├── schemas/             # JSON Schemas for editor support
├── tests/
└── docs/
```

## Risks

| Risk | Mitigation |
|---|---|
| A wrong entity or operation leads to a wrong design | Confidence threshold; SemIf always confirms lookups with a "none" option; you confirm the spec |
| SemIf is slow when memory is short | It runs only when needed; close memory-heavy apps; tests replay recorded answers |
| Rules or knowledge grow inconsistent | Four validation layers, including Prolog checks; changes go through pull requests |
| Generated code edited by hand drifts from the spec | Checksums on generated files; changes go through the spec or the templates |
| Data mappings drift from the real database | Optional `validate --introspect` |
| Private data leaks into the public repo | Real knowledge stays in each project; examples use made-up domains |

## Open decisions

1. Project name before publishing v0.1.
2. Whether each project's `.harness/` knowledge lives in the project repo or a separate private repo.
