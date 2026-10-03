# Example plan: Bookstore

A thin, end-to-end first version of the harness, built against a small sample app. Every stage of the [full plan](plan.md) (decide, build, verify, learn) appears here in its simplest form, so the whole flow can be seen working before the full system exists.

Each step is proposed with its implementation details, approved, built on its own branch, and merged through a reviewed pull request.

## The example

[`examples/bookstore`](../examples/bookstore) is a plain FastAPI + SQLite app. It knows nothing about the harness.

| Task | Domain facts | Resolver result | Built by |
|---|---|---|---|
| Add a book | create, one, no parent | POST `create` | Generator |
| List books | read, many | GET `list` | Generator |
| Archive a book | state_change | PATCH `state_transition` | LLM (no generator) |
| Export books as CSV | not in the knowledge base | none | You decide (unsure path) |

## Steps

| Step | What gets built | Run it | Status |
|---|---|---|---|
| **1. Scaffold** | Harness project setup, `harness` command, CI, README, licence | `harness version` | ✅ Done (#2) |
| **2. Sample app** | Bookstore: plain FastAPI app with `Book` model and health check | `pytest`, `uvicorn` in `examples/bookstore` | ✅ Done (#3) |
| **3. Knowledge base + link** | Domain knowledge base, architecture and component rules, list of components; `harness.yaml` linking Bookstore to the harness; code that finds and validates these files | `harness validate` | Next |
| **4. Classify** | SemIf client interface and a stand-in that answers from a file | `harness classify "Add a new book"` | |
| **5. Resolve** | Resolver: domain facts → method, pattern and components, with the source of each value | `harness resolve "Add a new book"` | |
| **6. First generator** | Stack pack `python-fastapi`: POST `create` templates and input schema; registering the new router in `main.py` | `harness generate add_book --dry-run` | |
| **7. End-to-end run** | Confirm spec → generate on a separate git branch → run tests → report | `harness run "Add a new book…"` | |
| **8. Second generator, evals, real SemIf** | GET `list` generator; 6–8 eval cases; real SemIf replaces the stand-in | `harness eval` | |
| **9a. LLM path, harness-driven** | "Archive a book" built by an LLM called from the harness (Aider with a local model, or `claude -p`) | `harness run "Archive a book"` | |
| **9b. LLM path, agent-driven** *(optional)* | Skills and agents for Claude Code, Codex, Copilot and Aider that call harness commands; local-model profiles | Ask your coding tool to "archive a book" | |
| **10. Unsure path** | "Export books as CSV" → no match → LLM drafts an option → you approve | `harness review` | |

## Notes per step

### Step 6: registering routers

Bookstore registers routers by hand in `app/main.py`, like most FastAPI projects. Generating an endpoint therefore means creating the endpoint file **and** adding the router to `main.py`. The verifier checks that this registration is the only change to `main.py`. How the generator finds the insertion point is decided in Step 6.

### Step 9: two ways to run the LLM part

| | 9a. Harness-driven | 9b. Agent-driven |
|---|---|---|
| Who starts | You run `harness run "Archive a book"` | You ask Claude Code, Codex or Copilot to "archive a book", or invoke the harness skill |
| Who is in charge | The harness calls the AI tool (`aider …`, `claude -p …`) | The AI tool calls harness commands (`classify`, `resolve`, `generate`, `verify`) |
| Good for | Automation, CI, repeatable runs | Interactive work in your usual tool |

Both use the same deterministic core: classification, resolver, generators and verifier.

#### Step 9b: agent files

One canonical set of definitions, with thin tool-specific files generated from it:

```
AGENTS.md                         shared instructions (Codex, Copilot, Claude Code, Aider)
.agents/skills/                   canonical skills (SKILL.md standard)
├── harness-build/SKILL.md          classify → resolve → generate → fill gaps → verify
├── harness-classify/SKILL.md
├── harness-verify/SKILL.md
└── harness-review/SKILL.md         unsure task → draft a KB option for human approval
.claude/
├── skills → ../.agents/skills      link to the canonical skills
└── agents/*.md                     roles: implementer, test-writer, reviewer
.codex/
├── config.toml                     profiles, including a local-model profile (Ollama)
└── agents/*.toml                   same roles, Codex format
.github/
├── agents/*.agent.md               same roles, Copilot format
└── copilot-instructions.md         points to AGENTS.md
.aider.conf.yml                   Aider with a local model
```

- **`harness agents sync`** generates the Claude, Codex and Copilot agent files from one neutral definition, so roles never drift between tools.
- **`harness init --agents claude,codex,copilot`** installs these files into a target project, so the tools work inside Bookstore or a real project. The harness repo carries them too, for working on the harness itself.
- **Local models:** Aider via `.aider.conf.yml`, Codex via a `local` profile pointing at Ollama. Claude Code and Copilot use their subscriptions.
- **Rules every skill and agent follows:**
  1. Run harness commands for the deterministic parts; never hand-write code a generator produces.
  2. Write only inside extension points or files the plan allows.
  3. Finish with `harness verify` and report the result honestly.
  4. Never change the knowledge base directly; only draft proposals for human approval.
- Codex loads a project's `.codex/` settings only after the project is marked as trusted.

**Defaults for 9b, to confirm before the step starts:** files in both the harness repo and target projects; Claude Code, Codex, Copilot and Aider covered; local model Qwen2.5-Coder 7B via Ollama.

## How it maps to the full plan

These steps are a thin first pass through phases 0–3, 7, 8, 10, 11 and 12 of [plan.md](plan.md). Everything built here is reused by the full phases; the SemIf stand-in stays useful for fast tests.
