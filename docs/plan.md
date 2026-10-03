# Harness plan

## Goal

A public, model-agnostic harness for backend API development:

- A task written in business language is classified through three knowledge-base layers — **domain → architecture → implementation**.
- SemIf reads the business facts from the task; a **resolver** turns those facts into the architecture and components using fixed links, decision tables and rules.
- Known patterns are built by deterministic **generators**; LLM agents write only what generators can't.
- Everything is verified with tests and SemIf checks, then recorded in an audit report.
- A human confirms every new knowledge-base option, and **evals across all layers** protect quality.

See [architecture.md](architecture.md) for the end-to-end diagram.

## Principles

1. **A human confirms every knowledge-base change** — new options, links, rules and eval answers.
2. **Deterministic first** — stated facts, links, tables and rules before SemIf; generators and code checks before LLMs; LLMs before asking a human.
3. **SemIf makes one fuzzy decision per task** — reading the business facts. Everything after that follows rules.
4. **Decisions live in YAML; the engine lives in Python.**
5. **Language-agnostic** — each stack (language + framework) plugs in as a pack.
6. **Nothing is trusted without evals** — every layer is measured; security layers allow zero misses.
7. **Public engine, private knowledge** — real knowledge base, evals and reports live in a private config repo.

## Components

| Component | Role |
|---|---|
| **Knowledge base** | Domain (entities, capabilities as typed facts, rules, sensitivity) → architecture (endpoint, pattern) → implementation (components, complexity) |
| **SemIf client + classifier** | Identifies the domain capability, or fills domain facts from fixed lists; flags "unsure" |
| **Resolver** | Turns domain facts into architecture and components, deterministically, recording the source of every value |
| **Review queue** | "None" or "unsure" → LLM drafts an option → human confirms → knowledge-base pull request |
| **Evals** | One file per case, per-layer expected answers, runner, scorecard and gates |
| **Stack packs** | `pack.yaml`: paths, commands, introspection, generators, agent hints, sample app |
| **Generators + tools registry** | Input schemas, input gathering, validation, templates, extension points; also an MCP server |
| **Executors + agents** | Aider, Claude Code, Codex, Copilot adapters; `.agents/*.md` roles; `models.yaml` maps complexity level → model |
| **Flow runner** | Tests first, units, cross-cutting steps, escalation, one git worktree per task |
| **Verifier** | Pack test/lint commands, scope check, generated-code checksum, SemIf yes/no per criterion |
| **Reports + feedback** | Audit report, automatic eval capture, stats, new-generator candidates |

## The resolver

The resolver makes the links between layers deterministic. SemIf only extracts the domain facts; the resolver derives everything else.

### Domain capabilities are typed facts

```yaml
# domain.yaml
capabilities:
  book_appointment:
    description: "Reserve a time slot for a customer with a staff member"
    entity: Appointment
    operation: create            # create | read | update | delete | state_change | action
    cardinality: one             # one | many
    parent: Customer             # or null
    actors: [staff]
    data: [pii]                  # sensitive | pii | financial | public
    owned_by: customer
    rules: [no_overlap]
```

### Architecture comes from a decision table

```yaml
# rules/architecture.yaml
table: endpoint_pattern
inputs:  [operation, cardinality, parent]
outputs: [method, pattern]
policy: first_match
rows:
  - { when: { operation: create, cardinality: one, parent: set },  then: { method: POST,   pattern: create_nested } }
  - { when: { operation: create, cardinality: one, parent: null }, then: { method: POST,   pattern: create } }
  - { when: { operation: create, cardinality: many },              then: { method: POST,   pattern: bulk_create } }
  - { when: { operation: read,   cardinality: one },               then: { method: GET,    pattern: get_by_id } }
  - { when: { operation: read,   cardinality: many, parent: set }, then: { method: GET,    pattern: nested_list } }
  - { when: { operation: read,   cardinality: many },              then: { method: GET,    pattern: list } }
  - { when: { operation: update },                                 then: { method: PATCH,  pattern: partial_update } }
  - { when: { operation: state_change },                           then: { method: PATCH,  pattern: state_transition } }
  - { when: { operation: delete },                                 then: { method: DELETE, pattern: soft_delete } }
```

### Components come from implication rules

```yaml
# rules/components.yaml
implies:
  - { when: { data: sensitive },        add: [field_masking, access_audit] }
  - { when: { actors: not_empty },      add: [authentication, role_check] }
  - { when: { owned_by: set },          add: [ownership_scope] }
  - { when: { operation: create },      add: [body_validation, db_insert, serialization] }
  - { when: { rules: no_overlap },      add: [conflict_check, transaction] }
  - { when: { component: read_cache },  add: [cache_invalidation] }   # rules can chain
requires:
  - { component: transaction, needs_any: [db_insert, db_update] }
forbids:
  - { pattern: get_by_id, with: [db_insert, db_update] }
```

### Precedence

| Order | Source | Example |
|---|---|---|
| 1 | Stated in the task or contract | "PATCH /appointments/{id}" |
| 2 | Capability link in the knowledge base | `book_appointment` → POST `create_nested` |
| 3 | Decision table or implication rule | `operation: state_change` → PATCH |
| 4 | SemIf, only for what is still open | "Does this also need caching?" |
| 5 | Human | Anything unsure or conflicting |

Every resolved value records its source; the audit report shows it.

### Rule validation

`harness validate` checks the rules themselves:

- **Completeness** — every input combination matches a row.
- **No overlaps** — two rows never match the same input with different results, unless order is intended.
- **No contradictions** — an implied component is never also forbidden.
- **References** — every component a rule adds exists in the implementation knowledge base.

## Repository layout

```
harness/                          # PUBLIC (Apache-2.0)
├── harness/
│   ├── config/        # schemas, loaders, validator (KB, rules, models, flows, packs, tools, eval cases)
│   ├── semif/         # client
│   ├── classify/      # domain layer: capability or facts via SemIf
│   ├── resolve/       # resolver: links → decision tables → rules → SemIf gaps
│   ├── review/        # unsure queue, option drafter, KB pull requests
│   ├── evals/         # case loader, runner, scorecard, gates, import from PRs
│   ├── packs/         # pack loader + interface
│   ├── generators/    # input gathering, validation, runner, generated-code guard
│   ├── tools/         # tools registry + MCP server
│   ├── executors/     # aider, claude, codex, copilot
│   ├── flows/         # TDD runner, units, escalation, worktrees
│   ├── context/       # candidate search + SemIf relevance
│   ├── verify/        # checks + SemIf criteria
│   └── report/        # audit report + stats
├── packs/
│   ├── python-fastapi/   # first pack
│   └── node-express/     # second pack — proves the core is language-agnostic
├── examples/config/      # generic sample KB, rules, models, flows, made-up eval cases
├── docs/
└── CONTRIBUTING.md · SECURITY.md · LICENSE

your-harness-config/              # PRIVATE (loaded with --config-dir)
├── knowledge_base/{domain,architecture,implementation}.yaml
├── rules/{architecture,components}.yaml
├── models.yaml · flows.yaml · agents/
├── evals/cases/<domain>/<case>.yaml
└── reports/
```

## Phases

### Milestone 1 — Decide: classify tasks reliably (useful before any code generation)

| Phase | Build | Done when |
|---|---|---|
| **0. Public scaffold** | `uv`, `ruff`, `pytest`, CI, Apache-2.0, CONTRIBUTING, SECURITY, secrets scan, `--config-dir` | CI is green and the harness runs against an external config folder |
| **1. Schemas and validator** | Schemas for the three KB layers, typed capabilities, rules (decision tables, implications), `models.yaml`, `flows.yaml`, `pack.yaml`, `tools.yaml`, eval cases. `harness validate` rejects: > 20 options per level, missing descriptions, broken links, unquoted yes/no, incomplete or overlapping tables, contradictory rules | Example config validates; broken files and broken rules fail with clear errors |
| **2. SemIf client and domain classifier** | Capability identification or fact extraction from fixed lists; per-layer thresholds; explicit values skip SemIf | `harness classify "..."` prints the capability or facts with confidence |
| **3. Resolver** | Precedence chain (stated → link → table → rule → SemIf gap → human); source recorded per value | `harness resolve "..."` prints architecture and components with the source of each value; same facts always give the same result |
| **4. Evals across all layers** | Case files, runner, scorecard (per layer, confusion, calibration, hard cases), gates: domain/architecture no drop vs baseline, zero missed security components, zero false passes (verification, from phase 11). CI runs evals on every KB or rules PR | A deliberately bad KB or rule change is blocked by CI |
| **5. Review loop and auto eval capture** | Unsure queue → LLM draft → `harness review` → KB pull request; confirmed or corrected paths saved as eval cases (corrections marked hard) | An unknown task becomes a new KB option and a new eval case, both approved by a human |
| **6. Import past work** | `harness eval import --from-prs`: LLM drafts cases and KB suggestions, pending until confirmed | 30+ confirmed cases and a baseline scorecard |

### Milestone 2 — Build: generate and implement code

| Phase | Build | Done when |
|---|---|---|
| **7. Stack pack interface** | `pack.yaml` schema, loader, `python-fastapi` pack with sample app, paths, commands, introspection | Sample app installs, tests and lints purely through the pack |
| **8. Executors, agents, models** | Four CLI adapters, `.agents/*.md`, `models.yaml`, dry-run, smoke tests | The same agent runs on Aider + local Qwen and on Claude Code by changing one line |
| **9. Contract and context** | OpenAPI contract draft for approval; context finder (pack paths + SemIf relevance) | Approved contract and the right context files for 10 sample tasks |
| **10. Generators and tools** | Input schemas per pattern/component; input gathering (contract → introspection → resolver → SemIf → LLM → human); validation, spec confirmation, templates, extension points (stub + failing test); tools registry, MCP server, golden-file tests, generated-code checksums | "POST → create" for a sample resource is fully generated and passes its tests |
| **11. Flow runner and verifier** | TDD order, units with `depends_on`, cross-cutting steps, escalation, worktrees; verifier with pack commands, scope, checksums, SemIf criteria with `expect`; verification evals with known-good and known-bad code | End-to-end task passes; broken code is caught; unsure verdicts go to a human |

### Milestone 3 — Learn and prove

| Phase | Build | Done when |
|---|---|---|
| **12. Reports and feedback** | Audit report (path per layer, source per value, models, retries, generated vs LLM-written, gaps); `harness stats` (pass rates, routing suggestions, rule hit counts, generator candidates) | The weekly review can be done from `harness stats` alone |
| **13. Second pack** | `node-express` with the same KB and rules, different templates and commands | Same eval cases and same task run on both stacks |
| **14. Pilot and publish** | Real backend with the private config repo; tune descriptions, rules and thresholds; docs, "write a pack" guide, demo, v0.1 | 20 real tasks completed with reports; v0.1 published |

## Risks

| Risk | Mitigation |
|---|---|
| Wrong domain facts cascade through every layer | Strict domain thresholds; hard cases in evals; unsure → human; the resolver keeps the rest deterministic |
| Rules grow inconsistent | Validator checks completeness, overlaps, contradictions and references; rule changes go through PRs and evals |
| Evals mark SemIf against its own answers | Only human-confirmed answers count; imported cases stay pending until confirmed |
| Hand edits to generated code drift from the spec | Checksums on generated regions; changes go through the spec or the template |
| Private data leaks into the public repo | Separate private config repo, secrets scan, made-up examples only |
| SemIf slow on a 16 GB Mac | Fewer questions thanks to the resolver; batch per layer; smaller model for yes/no checks |
| Claude Pro usage limits | Generators and local Qwen take routine work; usage logged per run |
| Agents changing code unsupervised | Worktrees, dry-run by default, no automatic merges |
| CLI flags change | One adapter file per tool, plus smoke tests |

## Open decisions

1. Licence: Apache-2.0 (recommended) or MIT.
2. First pack: `python-fastapi`?
3. Name and hosting of the private config repo.
4. Project name before publishing v0.1.
