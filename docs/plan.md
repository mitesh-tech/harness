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
| Classification (area → service flow, configuration-driven) | Replaced in S2c by the factory flow |
| Factory structure (S1) | Done |
| Operations and targets (S2a) | Done |
| Traces and eval cases (S2b) | Done |
| Classification with the factory router, CRUD flow, planned domain-model factory (S2c) | Done |
| Data layer (S3) | Done |
| API anatomy (S4a) | Done |
| Spec and OpenAPI in the run folder (S4b) | Done |
| Confirm and generate, tests first (S5a) | Done |
| Verify, report, `harness run` (S5b) | Done |
| Resolve, spec, generation, verification, learning | Planned below |

## Stages

Each stage is one or two pull requests, ends with something you can run, and starts only after you approve its detailed plan.

| Stage | Content | You can run |
|---|---|---|
| **S0: Design docs** | CRUD API factory specification, this plan, architecture and end-to-end diagram | Read the design |
| **S1: Factory structure** | `src/harness/factories/crud-api/` with `factory.yaml`, factory loader; `technical.yaml` and the composition checks moved into the factory; per-factory project overrides; `validate` covers factories | `harness validate` |
| **S2a: CRUD knowledge** | **Operation layer** (create, read, update, delete → kinds); **targets**: entities and connections (aggregations); `offered_kind/4` in Prolog (kind by target, transition only with a lifecycle); operation checks | `harness kb query "offered_kind(update, book, K, Why)"` |
| **S2b: Traces and eval cases** | A **trace** of every decision (step, by SemIf or rule, options, answer, confidence, margin, time); eval cases that check the **path**, questions not asked, number of SemIf calls and paraphrase groups; the first wrong step is reported | `harness classify "…" --trace` |
| **S2c: Classification** | **Factory router** (a gate while there is one factory); target first (entity or connection); parent by rule; operation → kind (by rule when only one) → lookup → SemIf confirms; questions in domain language with the target's context; outcomes `UNSUPPORTED`, `NO_ENTITY`, `NEW_SERVICE`, `MATCHED`, `UNSURE`; kind `update` renamed `change`; SemIf answers re-recorded | `harness classify "…"` |
| **S3: Data layer** | `.harness/data.yaml` (sources, entities → tables and columns, relationships, written out in full); **relational** source-type plugin (capabilities, converters, introspection); checks in Python and Prolog; classification step `data` → `NO_MAPPING` | `harness validate --introspect` |
| **S4a: API anatomy** | Prolog anatomy rules (paths, parameters, body, response, errors) with conventions as overridable settings; list summaries; dependents and delete policy (`on_delete`); auth and caching out of the CRUD factory; `remove_rules`, per-service paths, `harness override`, version pin | `harness resolve --service add_review` |
| **S4b: Spec** | `spec.yaml` + `openapi.yaml` in the run folder; resolving a classified task; SemIf yes/no questions for new-service details; open questions; resolve events in the trace | `harness resolve "…"` |
| **S5a: Confirm and generate (create)** | Structured test cases in the spec; `harness confirm`; the python-fastapi pack; templates; tests generated first from the spec (must fail), then code (must pass); markers, header, registry; git worktree | `harness generate <run>` |
| **S5b: Verify and report** | Lint, contract (the app's OpenAPI against the spec's), scope, untouched generated files; `report.md`; `harness run "…"` from task to merged-ready branch | `harness run "Let staff add a new publisher"` |
| **S6: All CRUD kinds** | read one, list (filters are response fields, paging), update, replace, transition (guarded by the lifecycle), delete; link and unlink for connections; nested APIs for composition | `harness run` for any CRUD task |
| **S7: Domain-model factory** | Its manifest is already installed as planned (`available: false`), so the router sends domain changes there instead of to CRUD. A second factory for changes to the domain itself: add or remove an attribute, add an entity or a relationship. Drafts the domain change for your approval; impact analysis (including dependents); then the CRUD factory updates mapping, schemas, APIs and tests together | `harness run "Add an isbn field to books"` |
| **S8: Learn and evals** | New services and attributes saved to the knowledge base after approval; eval cases; `harness eval` over traces: accuracy per step, path accuracy, calibration, consistency across paraphrases, SemIf calls and time | Track classification accuracy and behaviour |

## Phase 2 and later

| Item | Notes |
|---|---|
| **Data-modelling factory** | Creates or updates `data.yaml`, e.g. by reading an existing database (S3's introspection) and drafting the mapping for your approval |
| **Domain-model factory, extended** | LLM-guided design of larger domain changes: asks the right questions, considers the existing domain model, drafts YAML for your approval |
| Trace export | Send traces to tools such as Langfuse or Inspect AI |
| More data sources | REST APIs of other services, document stores |
| Agent-driven runs | Claude Code, Codex, Copilot skills that drive the same spec-based flow |
| More stack packs | e.g. Node/Express, to prove the factories are language-agnostic |
| More factories | Authentication (login, roles), export and reports |
| Richer reads | `?fields=` (choose fields) and `?include=` (embed related things) |

## Repository layout (target)

```
harness/
├── src/harness/
│   ├── knowledge/       # schemas, loading, validation (domain, data, technical)
│   ├── logic/           # Prolog: compile, engine, core and checks
│   ├── semif/           # decision-model interface and HTTP adapter
│   ├── classify/        # generic flow runner, option sources, lookups
│   ├── factories/
│   │   └── crud-api/    # factory.yaml, technical.yaml, anatomy.pl, checks.pl, design.py, templates/
│   ├── datasources/
│   │   └── relational/  # source.yaml, schema, introspect, templates/
│   ├── resolve.py       # classify → the factory's design → save the run folder
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
