# CRUD API factory

The first **software factory** of the harness. Given a task in business language, it designs, generates and verifies a REST API that creates, reads, updates or deletes the records of an entity, or changes an existing one.

See [the architecture](../architecture.md) for how factories fit into the harness, and [the plan](../plan.md) for the build stages.

## Scope (phase 1)

| In scope | Not yet |
|---|---|
| Create, read one, list (query), update (some fields), replace (all fields), delete | Lifecycle transitions (archive, approve) |
| Entities from the domain knowledge base | Linking and unlinking aggregations |
| Composition: a child created, listed and deleted within its parent (`/books/{id}/reviews`) | Entities that don't exist yet (phase 2: domain-model factory) |
| Relational data sources | Other data sources (REST APIs, documents) |
| New APIs and small changes to existing ones (add or remove an attribute) | Larger refactors |

A task outside this scope ends as `UNSUPPORTED`, with a reason.

## Anatomy of a CRUD API

Every API this factory produces has six parts, each with fixed rules:

**REQUEST + METHOD + RESOURCE + BUSINESS LOGIC + DATA + RESPONSE**

| Part | Comes from | Rules |
|---|---|---|
| **Resource** | An entity in the domain knowledge base | One resource per entity; its fields are the entity's attributes |
| **Method** | The operation | create → `POST` · read one / list → `GET` · update → `PATCH` · replace → `PUT` · delete → `DELETE` |
| **Request** | Derived | The body is the resource for `POST` and `PUT`, excluding generated fields such as `id` · `PATCH` sends only the changed attributes · `GET` and `DELETE` have no body · the **path parameter** is the resource's identifier (`/books/{id}`) · **query parameters** can only be fields of the response (filters) |
| **Business logic** | Rules + extension points | Validation from attribute rules (required, unique, min/max); domain rules as extension points written by a person or an agent |
| **Data** | `data.yaml` | Every resource field maps to a data source; the API talks to a repository interface, never to the source directly |
| **Response** | Derived | The resource, or a list of resources plus paging information · statuses per method: 201 create, 200 read/update/replace, 204 delete, 404 unknown identifier, 409 conflict, 422 invalid input |

These rules live in the factory's knowledge as Prolog rules. A check rejects any design that breaks them, for example a query parameter that isn't a response field.

## Operations

SemIf first answers the broad **operation**, then the specific **kind** only when the operation has more than one:

| Operation | Kinds | Distinguishing question |
|---|---|---|
| create | create | — |
| read | read_one, query | "One specific item, or a list?" |
| update | update, replace | "A few details, or everything?" |
| delete | delete | — |

## Flow

![CRUD API factory flow](../diagrams/end-to-end.svg)

| Step | Who | Outcome when it can't continue |
|---|---|---|
| **Factory router** | SemIf picks the factory from installed factories' descriptions | `UNSUPPORTED`: no factory handles this task |
| **Which entity?** | SemIf, options from the domain knowledge base | `NO_ENTITY`: you add the entity (phase 2: the domain-model factory helps) |
| **New API or change?** | SemIf | — |
| **A. Which operation?** → kind | SemIf, options from the factory's operations | `UNSURE`: you choose |
| **Find service** | A lookup of existing services for this entity and kind; SemIf confirms among them, always with "none" | No match → a **new service**, named by rule (`add_{entity}`, `list_{entities}`, …) |
| **Data mapping exists?** | Check `data.yaml` | `NO_MAPPING`: you add the mapping |
| **B. Change API** | SemIf: add or remove an attribute | The attribute's name and type come from you |
| **Impact analysis** (B) | Prolog: every API that exposes the resource, and which parts include the attribute | — |
| **Resolve** | Prolog applies the API rules and the data mapping; SemIf answers only yes/no questions the knowledge base leaves open (for example "is login required?") | Logic problems block the design |
| **Generate spec** | The harness writes `spec.yaml` and `openapi.yaml` in the run folder | Open questions are listed in the spec |
| **Confirm spec** | You approve, edit or reject it. Edits are checked again | Reject → nothing is written |
| **Generate code** | The factory's templates for the project's stack pack, from the confirmed spec only | — |
| **Verify** | Tests, lint, changed files within scope, generated files untouched, results against the spec | Fail → retry, then you |
| **Report** | The spec, the proof of every decision, files, test results, gaps | — |
| **Learn** | New services and attributes go to the knowledge base through a pull request you approve; the confirmed classification becomes an eval case | — |

## Two kinds of task

### A. New operation

The entity and its data mapping exist, but there is no API for this operation yet.

> "Let staff add a new publisher" → entity `Publisher`, operation create, no existing service → new service `add_publisher` → `POST /publishers`.

### B. Change an existing operation

The API exists and needs a small change: add or remove an attribute.

> "Add an isbn field to books" → entity `Book`, change: add attribute `isbn` (type confirmed by you).

The change touches several places together, all found by impact analysis:

| Place | Change |
|---|---|
| Domain knowledge base | Add or remove the attribute |
| Data mapping | Map it (and change the database schema), or remove it |
| Request bodies | Every `POST` / `PUT` / `PATCH` for the resource |
| Responses | Every API returning the resource |
| Filters | Query parameters that used the attribute |
| Validation and tests | For each affected API |

## Data layer

Data sources are **pluggable**. Generated APIs depend on a **repository interface** per resource (get, list, create, update, delete); a source adapter implements it.

```
API route → business logic → repository interface → source adapter → data store
```

### Source types are plugins

```
src/harness/datasources/<type>/
├── source.yaml      # description and capabilities
├── schema.py        # shape of its configuration and mappings
├── introspect.py    # optional: read the real schema to check mappings
└── templates/<pack>/
```

Phase 1 has one type, **relational** (SQLite, PostgreSQL, MySQL). A source type's **capabilities** (for example `delete`, `filter`, `paging`, `transactions`) feed the Prolog checks: an API a source can't support is reported before anything is generated.

### `.harness/data.yaml`

```yaml
sources:
  main:
    type: relational
    config:
      dialect: sqlite
      url_env: BOOKSTORE_DATABASE_URL        # connection details come from the environment
      default_url: sqlite:///./bookstore.db  # local default only; never secrets in YAML
    conventions:
      table_names: plural_snake              # Book → books
      column_names: same                     # column = attribute

mappings:
  Book:
    source: main
    fields:
      price: { column: price_cents, convert: money_cents }   # only the exceptions
  Review:
    source: main
    parent_key: book_id                      # composition: points at its Book
  Publisher:
    source: main
```

Checks: every mapping names a known source, fits that source type's mapping shape, maps every attribute (by convention or explicitly, or marks it `stored: false`), uses converters that fit the attribute types, and gives composition children a `parent_key`. `harness validate --introspect` optionally checks tables and columns against the real database. A new API requires its entity's mapping.

## The spec

Every run produces a spec before any code is written. Example:

```yaml
# .harness/runs/0007/spec.yaml
service: add_publisher                     # new, named by rule create → add_{entity}
factory: crud-api
task: "Let staff add a new publisher"
status: draft                              # confirmed after your approval

api:
  method: POST
  path: /publishers
  request:
    body: Publisher
    fields:
      name: { type: text, required: true, unique: true }   # id is generated
  response:
    status: 201
    body: Publisher
    headers: [Location]
  errors:
    - { status: 401, when: "not logged in" }
    - { status: 409, when: "a publisher with this name already exists" }
    - { status: 422, when: "invalid or missing fields" }

data:
  source: main (relational)
  table: publishers
  fields: { id: id, name: name }

components:
  - { name: body_validation, rule: create-validates-body }
  - { name: auth_check, rule: login-needs-auth, fact: "requires_login (SemIf 0.90)" }
  - { name: serialization, rule: always-serialize }

tests:
  - creates a publisher → 201 and Location
  - missing name → 422
  - duplicate name → 409
  - not logged in → 401

open_questions: []
sources:
  entity: SemIf 0.99
  operation: SemIf 1.00
  method, status: kind create
  fields: entity Publisher
```

The API contract is also written as `openapi.yaml`. Generation can be re-run from a confirmed spec without classifying again.

## Outcomes

| Outcome | Meaning | Next |
|---|---|---|
| `MATCHED` | An existing service fits | Resolve and continue |
| `NEW_SERVICE` | The entity and operation are known, no service exists yet | Name it, resolve, continue |
| `UNSURE` | SemIf is below the confidence threshold | You choose |
| `NO_ENTITY` | No entity in the knowledge base fits | You add it (phase 2: guided) |
| `NO_MAPPING` | The entity has no data mapping | You add it to `data.yaml` |
| `UNSUPPORTED` | No factory handles this task | Out of scope for now |

## Factory layout

```
src/harness/factories/crud-api/
├── factory.yaml        # name, description, examples (for the router), operations, packs
├── technical.yaml      # operations → kinds, API rules, components, askable facts, flow
├── checks.pl           # factory-specific logic checks
├── templates/
│   └── python-fastapi/ # route, schemas, repository interface, tests
└── evals/              # tasks and expected outcomes
```

```yaml
# factory.yaml
name: crud-api
description: "Create, read, update and delete records of an entity through a REST API"
examples: ["Add a new book", "List books by author", "Change a book's price", "Remove a review"]
operations: [create, read, update, delete]
packs: [python-fastapi]
```

## Phase 2 and later

| Item | Where it fits |
|---|---|
| LLM-guided design of new entities, considering the existing domain model | A **domain-model factory** whose output is domain knowledge |
| Lifecycle transitions, linking aggregations | New operations or a separate factory |
| REST APIs and document stores as data sources | New source-type plugins |
| Agent-driven runs from Claude Code, Codex, Copilot | Platform, using the same spec |
