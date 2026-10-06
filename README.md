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
├── harness.yaml          # which stack pack, where the knowledge lives
└── .harness/
    ├── domain.yaml       # areas, entities, associations, services (business language)
    └── technical.yaml    # optional: overrides the harness's general technical knowledge
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

```bash
uv run harness classify "Let staff add a new publisher" --project examples/bookstore
```

```
  area         catalogue                1.00 ✓   (none 0.00 · community 0.00 · curation 0.00)
  service      none                     0.88 ✓   (add_book 0.08 · list_books 0.02 · get_book 0.00)
  kind         create                   1.00 ✓   (read_one 0.00 · none 0.00 · query 0.00)
  entity       Publisher                0.99 ✓   (Book 0.01 · none 0.00)

Outcome   NEW_SERVICE: catalogue › create › Publisher
```

SemIf answers one question at a time, choosing only among options from the knowledge base. The order of questions is configuration (`classification:` in [technical.yaml](src/harness/knowledge/technical.yaml)): area, then service; when no service fits, the kind of operation and the entity (or, for link/unlink, the association). Answers below the threshold stop with `UNSURE` so a person decides. Exit codes: `0` matched or new service, `2` needs a person, `1` error. Use `-v` to see exactly what SemIf reads, `--json` for machine-readable output.

Tests replay real SemIf answers recorded in `tests/recordings/`, so they run without the server. Re-record after changing the knowledge base or the questions: `uv run pytest --record-semif` (with SemIf running).

## Roadmap

The first milestone is a small end-to-end example: a "Bookstore" API where the harness classifies tasks, resolves the design, generates endpoints, verifies them and writes a report. See [docs/plan.md](docs/plan.md) for the full plan.

## Contributing

The project is at an early stage. Issues and ideas are welcome. All changes go through pull requests and need an approving review before merging.

## License

[Apache-2.0](LICENSE)
