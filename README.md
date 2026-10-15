# harness

**A model-agnostic AI harness for building backend APIs: deterministic where it can be, AI where it must be, human-approved where it matters.**

> **Status: early development.** The design is in place ([architecture](docs/architecture.md), [plan](docs/plan.md)); the code is being built step by step. Nothing here is ready for real projects yet.

## Why

AI coding agents are powerful but unpredictable. Given the same task twice, they may design it differently, pick a different pattern, or forget a security check. For backend APIs most of that work is already well understood: a "create" endpoint needs validation, an insert and a response; data owned by a user needs an ownership check.

`harness` turns that knowledge into a **reviewed, versioned knowledge base** and uses AI only for the parts that genuinely need judgement.

## How it works

![Harness end-to-end flow](docs/diagrams/end-to-end.svg)

A task written in plain business language goes through four stages:

1. **Decide.** A small decision model ([SemIf](https://github.com/TheoLeeCJ/SemIf-OpenJev)) reads the business facts from the task: what entity, what operation, who uses it, how sensitive the data is. A deterministic **resolver** then derives the endpoint, pattern and components from rules in the knowledge base. Anything unclear goes to a human.
2. **Build.** Known patterns are produced by **code generators** from templates, so the same spec always gives the same code. LLM agents write only what templates can't, such as business rules, on whichever model fits the step.
3. **Verify.** Tests, lint and scope checks run, plus yes/no checks that the result meets each requirement. Failures retry with a stronger model; uncertain results go to a human.
4. **Learn.** Every run produces an audit report. Human-confirmed decisions become evaluation cases, and every knowledge-base change must pass them before it's merged.

## Key ideas

- **Human-approved knowledge.** New categories, rules and contracts are confirmed by a person, through pull requests.
- **Deterministic first.** Stated facts, then knowledge-base links, then rules, then AI, then a human, in that order.
- **Model-agnostic.** Agents are defined once and run on any model through Aider, Claude Code, Codex or Copilot; a config file maps task complexity to model.
- **Language-agnostic.** The core is Python; each backend stack (e.g. FastAPI, Express, Spring) plugs in as a *stack pack*.
- **Measured, not assumed.** Evaluations cover every layer, with zero tolerance for missed security components.

## Getting started

Requires [uv](https://docs.astral.sh/uv/) and, for the logic checks, [SWI-Prolog](https://www.swi-prolog.org/) (macOS: `brew install swi-prolog`).

```bash
git clone https://github.com/mitesh-tech/harness.git
cd harness
uv sync
uv run harness --help
```

Run the checks:

```bash
uv run pytest
uv run ruff check .
```

## Linking a project

A project links to the harness with a `harness.yaml` at its root and keeps its knowledge in `.harness/`:

```
my-project/
├── harness.yaml          # which stack pack and factories, where the knowledge lives
└── .harness/
    ├── domain.yaml       # areas, entities, associations, services (business language)
    ├── data.yaml         # where it is stored: sources, entities → tables and columns
    ├── evals/            # classification eval cases
    └── factories/
        └── crud-api/
            └── technical.yaml   # optional: overrides the factory's technical knowledge
```

### Factories

The harness is a platform of small software factories, each good at one kind of task (see [docs/factories/crud-api.md](docs/factories/crud-api.md)). A factory lives in [src/harness/factories/](src/harness/factories/) as a folder with a `factory.yaml` manifest (name, description, examples, stack packs), its technical knowledge (`technical.yaml`) and its own Prolog checks (`checks.pl`). The first one is `crud-api`. A project enables factories with `factories: [crud-api]` in `harness.yaml`; without that list, every installed factory is enabled.

In `crud-api`, a task acts on a **target** (an entity, or a connection between entities) through an **operation** (create, read, update, delete). The kind of API follows from both; Prolog derives it with a reason:

```bash
uv run harness kb query "offered_kind(update, book, K, Why)" --project examples/bookstore
```

Check a project's knowledge files:

```bash
uv run harness validate --project examples/bookstore
```

`validate` checks in four layers: YAML syntax, the shape of each file, that every name points at something that exists, and finally **logic**: the knowledge is compiled to Prolog and checked for problems such as a composition child created outside its parent, a composition cycle, or rules that disagree. Each problem is reported with its file, line and field. See [examples/bookstore/.harness/domain.yaml](examples/bookstore/.harness/domain.yaml) for a complete example.

Without SWI-Prolog the logic layer is skipped with a warning; `--strict` (used in CI) makes that an error.

Explore the knowledge as Prolog:

```bash
uv run harness kb query "component(add_review, C, Rule)" --project examples/bookstore
uv run harness kb export --project examples/bookstore --out kb.pl && swipl kb.pl
```

Every derived decision carries the id of the rule that produced it, e.g. `C = parent_exists_check   Rule = composition-nested`.

For autocomplete and inline errors while editing, the files reference JSON Schemas in [schemas/](schemas/) (regenerate them with `uv run harness schema export`). Editors with YAML language support, such as VS Code with the YAML extension, pick them up automatically.

## Data sources and mappings

`.harness/data.yaml` says where the domain is stored: the data **sources** (type and how to connect, with connection details from environment variables), and for each **entity** its source, table and a column per attribute, and for each **relationship** its link table or reference column. See [Bookstore's](examples/bookstore/.harness/data.yaml).

Each source's `type` is a plugin in [src/harness/datasources/](src/harness/datasources/) (today: `relational`) that decides what may be written for it and what it can do. Compare the file with the real database:

```bash
uv run harness validate --project examples/bookstore --introspect
```

```
  ! data sources (introspection)         7 differences
  ! .harness/data.yaml:23  entities › Book › columns › isbn
      column 'isbn' not found in books
  ! .harness/data.yaml:31  entities › Publisher › table
      table 'publishers' not found (it is created when Publisher's first API is generated)
  …
```

Introspection needs SQLAlchemy (`harness[relational]`). A task about an entity with no entry in `data.yaml` ends as `NO_MAPPING`.

## Running SemIf

Classification uses [SemIf](https://github.com/TheoLeeCJ/SemIf-OpenJev), a small open decision model, through the HTTP server from [SemIf-server](https://github.com/someka-vrc/SemIf-server). Install it once, outside this repo (macOS, Apple Silicon):

```bash
git clone https://github.com/someka-vrc/SemIf-server ~/.harness/semif
cd ~/.harness/semif && uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e '.[mlx]'
```

Start it (the first start downloads Qwen3.5-4B; it needs about 10 GB of memory while running, so close other memory-heavy apps):

```bash
~/.harness/semif/.venv/bin/semif-serve --model Qwen/Qwen3.5-4B \
  --revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a --backend mlx --port 8010
```

Check it from any project:

```bash
uv run harness semif check --project examples/bookstore
```

The server address, confidence threshold and timeout can be set in `harness.yaml`:

```yaml
semif:
  url: http://localhost:8010/v1/systemone
  threshold: 0.8
  timeout: 30
```

## Classifying a task

Write the task in business language and name the endpoint: "…through a new endpoint" (or "through an endpoint"). A task without it still works, but SemIf is less sure which kind of work it is. The CRUD factory builds APIs for entities that exist; changing the domain itself (a new entity or attribute) is a separate factory, planned for S7.

```bash
uv run harness classify "Let staff add a new publisher through a new endpoint" --project examples/bookstore
```

```
  step         by     answer                      conf  margin
  factory      semif  crud-api                    0.93    0.90  ✓
  target       semif  Publisher                   0.86    0.81  ✓
  parent       rule   none                       Publisher is not part of another thing
  operation    semif  create                      1.00    1.00  ✓
  kind         rule   create                     the only kind of create that works on Publisher
  service      rule   none                       no service acts on Publisher with kind create
  name         rule   add_publisher              kind create names new services add_{entity}

Outcome   NEW_SERVICE: Publisher › create › create › add_publisher   (3 SemIf calls, 1.1s)
```

The router (SemIf) first picks the factory. Installed factories include planned ones (`domain-model`, for changes to the domain itself), so a task that needs one ends as `UNSUPPORTED` with that reason instead of being mistaken for CRUD. In `crud-api`, the questions follow `classification:` in [technical.yaml](src/harness/factories/crud-api/technical.yaml):

1. **target**: which domain entity, or relationship between two, the task is about (`none` → `NO_ENTITY`);
2. **parent**: by rule, the composition parent (Review is part of a Book);
3. **operation**: create, read, update or delete;
4. **kind**: by rule when only one kind fits the target (Prolog `offered_kind`), otherwise SemIf;
5. **service**: SemIf confirms among existing services for that target and kind, or a new service is named by rule.

Questions are in domain language: they start with `domain.yaml`'s `description` and, once the target is known, what the domain says about it (its description, parent, details and stages). Answers below the threshold stop with `UNSURE`, except where a second signal agrees (the task names exactly that entity). `margin` is how far the answer is ahead of the next option. Every rule decision shows its reason. Exit codes: `0` matched or new service, `2` needs a person or unsupported, `1` error.

| Option | Shows |
|---|---|
| `--trace` | Every step's question, each option's probability and the time taken |
| `-v` | Exactly what SemIf reads for each option |
| `--trace-out run.jsonl` | Writes the trace: a `run` line, one `event` line per decision, a `result` line |
| `--json` | The trace as JSON |

## Evals

A project's eval cases live in `.harness/evals/classify.yaml` (see [Bookstore's](examples/bookstore/.harness/evals/classify.yaml)). Each case states the expected outcome and, optionally, how it should be reached: the answers, the path of steps, steps that must not be asked, the most SemIf calls, and a paraphrase `group` whose cases must all take the same path. `harness validate` checks that the cases name real outcomes, steps and answers.

```bash
uv run harness eval classify --project examples/bookstore
```

```
  ✓ Add a new book through an endpoint
  ✗ Take Dune out of the Summer reads collection through an endpoint
      first wrong step: target: expected collection_books, got collection_books (0.62, margin 0.46, below the threshold)
      ...
22/23 cases passed   (68 SemIf calls, 18.3s)
```

Tests run the same cases against real SemIf answers recorded in `tests/recordings/`, so they run without the server. Re-record after changing the knowledge base or the questions: `uv run pytest --record-semif` (with SemIf running).

## Roadmap

The harness is becoming a platform of small **software factories**, each turning a business-language task into one kind of verified code. The first is the [CRUD API factory](docs/factories/crud-api.md): it classifies the task, resolves the design, writes a spec for you to confirm, then generates, verifies and reports. See [docs/architecture.md](docs/architecture.md) for the end-to-end flow and [docs/plan.md](docs/plan.md) for the stages.

## Contributing

The project is at an early stage. Issues and ideas are welcome. All changes go through pull requests and need an approving review before merging.

## License

[Apache-2.0](LICENSE)
