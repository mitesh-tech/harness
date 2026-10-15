# CRUD API factory

The first **software factory** of the harness. Given a task in business language, it designs, generates and verifies a REST API that creates, reads, updates or deletes the records of an entity, or changes an existing one.

See [the architecture](../architecture.md) for how factories fit into the harness, and [the plan](../plan.md) for the build stages.

## Scope (phase 1)

| In scope | Not yet |
|---|---|
| Create, read one, list (query), change (some fields), replace (all fields), transition (archive, approve), delete | Reading or updating a connection itself |
| Entities and connections (aggregations) from the domain knowledge base: link and unlink | |
| Composition: a child created, listed and deleted within its parent (`/books/{id}/reviews`) | Entities that don't exist yet (phase 2: domain-model factory) |
| Relational data sources | Other data sources (REST APIs, documents) |
| APIs for entities and connections that already exist in the domain | Changing the domain itself: new entities, attributes or relationships (the **domain-model factory**) |

A task outside this scope ends as `UNSUPPORTED`, with a reason.

## Anatomy of a CRUD API

Every API this factory produces has six parts, each with fixed rules:

**REQUEST + METHOD + RESOURCE + BUSINESS LOGIC + DATA + RESPONSE**

| Part | Comes from | Rules |
|---|---|---|
| **Resource** | An entity in the domain knowledge base | One resource per entity; its fields are the entity's attributes |
| **Method** | The operation | create → `POST` · read one / list → `GET` · change → `PATCH` · replace → `PUT` · delete → `DELETE` |
| **Request** | Derived | The body is the resource for `POST` and `PUT`, excluding generated fields such as `id` · `PATCH` sends only the changed attributes · `GET` and `DELETE` have no body · the **path parameter** is the resource's identifier (`/books/{id}`) · **query parameters** can only be fields of the response (filters) |
| **Business logic** | Rules + extension points | Validation from attribute rules (required, unique, min/max); domain rules as extension points written by a person or an agent |
| **Data** | `data.yaml` | Every resource field maps to a data source; the API talks to a repository interface, never to the source directly |
| **Response** | Derived | The resource, or a list of resources plus paging information · statuses per method: 201 create, 200 read/change/replace, 204 delete, 404 unknown identifier, 409 conflict, 422 invalid input |

These rules live in the factory's knowledge as Prolog rules. A check rejects any design that breaks them, for example a query parameter that isn't a response field.

## Targets

A task acts on a **target**:

- an **entity** (Book, Review), or
- a **connection**: an aggregation between two entities (`collection_books`, `book_publisher`).

Compositions are not targets of their own: a task about reviews acts on the child entity `Review`, and its parent `Book` follows from the composition.

## Operations

SemIf first answers the broad **operation**. The kind follows from the operation and the target; SemIf is asked the operation's question only when more than one kind remains.

| Operation | Kinds | On an entity | On a connection | Question when several remain |
|---|---|---|---|---|
| create | create, link | create | link | — |
| read | read_one, query | read_one, query | — | "One specific item, or a list of items?" |
| update | change, replace, transition | change, replace, transition (only with a lifecycle) | — | "A few details, all the details, or another stage of its life?" |
| delete | delete, unlink | delete | unlink | — |

The kinds under update differ in method and logic: change is `PATCH` with only the changed attributes, replace is `PUT` with the whole resource, transition is `PATCH` on the lifecycle field, guarded by the allowed moves (409 otherwise).

This is a Prolog rule, `offered_kind(Operation, Target, Kind, Reason)` in `core.pl`:

```
$ harness kb query "offered_kind(update, book, K, Why)"
K = change      Why = target_type
K = replace     Why = target_type
K = transition  Why = has_lifecycle
```

Checks: every kind is in exactly one operation; an operation with several kinds for the same target has a question; every operation is usable on some target; every service's kind is offered for its target; entities and connections together are at most 15 (SemIf's limit with "none").

## Associations and dependents

| Association | Example | Create / update | Read | Delete of the parent |
|---|---|---|---|---|
| Composition | Book ◆— Review | The child only within its parent (`POST /books/{id}/reviews`), parent must exist | Nested (`/books/{id}/reviews`) | **Cascade**: children go with it, all the way down |
| Aggregation, one-to-many | Publisher ◇— Book | The child carries a reference that must exist | The reference is in the response | Per policy: **restrict** (409 while referenced) or **set_null** |
| Aggregation, many-to-many | Collection ◇—◇ Book | Link and unlink; both sides must exist | List the members | Link rows are removed; the other side stays |

Dependents and the delete policy (`on_delete`) are derived in Prolog when the design is resolved; impact analysis for changes uses the same rules.

## Flow

![CRUD API factory flow](../diagrams/end-to-end.svg)

| Step | Who | Outcome when it can't continue |
|---|---|---|
| **Factory router** | SemIf picks the factory from installed factories' descriptions, including planned ones (`available: false`, like `domain-model`) | `UNSUPPORTED`: no factory fits, or the one that fits is only planned |
| **Which entity or connection?** | SemIf, options from the domain knowledge base | `NO_ENTITY`: the domain must change first (the domain-model factory) |
| **Part of a parent?** | Rule: the composition parent, if any | — |
| **Which operation?** → kind | SemIf for the operation; the kind by rule, or SemIf when several remain | `UNSUPPORTED`: no kind fits this target · `UNSURE`: you choose |
| **Find service** | A lookup of existing services for this target and kind; SemIf confirms among them, always with "none" | No match → a **new service**, named by rule (`add_{entity}`, `list_{entities}`, …) |
| **Data mapping exists?** | Check `data.yaml` | `NO_MAPPING`: you add the mapping |
| **Resolve** | Prolog applies the API rules and the data mapping; SemIf answers only yes/no questions the knowledge base leaves open (for example "is login required?") | Logic problems block the design |
| **Generate spec** | The harness writes `spec.yaml` and `openapi.yaml` in the run folder | Open questions are listed in the spec |
| **Confirm spec** | You approve, edit or reject it. Edits are checked again | Reject → nothing is written |
| **Generate code** | The factory's templates for the project's stack pack, from the confirmed spec only | — |
| **Verify** | Tests, lint, changed files within scope, generated files untouched, results against the spec | Fail → retry, then you |
| **Report** | The spec, the proof of every decision, files, test results, gaps | — |
| **Learn** | New services and attributes go to the knowledge base through a pull request you approve; the confirmed classification becomes an eval case | — |

## A task

The entity (or connection) exists in the domain. The task asks for an API to create, read, update or delete it.

> "Let staff add a new publisher through a new endpoint" → entity `Publisher`, operation create, no existing service → new service `add_publisher` → `POST /publishers`.

Write tasks in business language and name the endpoint ("…through an endpoint", "…through a new endpoint").

## Changes to the domain: the domain-model factory

Adding or removing an attribute, a new entity or a relationship changes **the domain itself**, so it is not CRUD. The **domain-model factory** owns these tasks: it drafts the domain change for your approval, then the CRUD factory updates the affected APIs, found by impact analysis:

| Place | Change |
|---|---|
| Domain knowledge base | Add or remove the attribute (domain-model factory) |
| Data mapping | Map it (and change the database schema), or remove it |
| Request bodies | Every `POST` / `PUT` / `PATCH` for the resource |
| Responses | Every API returning the resource |
| Filters | Query parameters that used the attribute |
| Validation and tests | For each affected API |

Until that factory exists, such tasks end as `UNSUPPORTED` or `UNSURE`. The CRUD factory must never turn them into CRUD on existing entities ("stop storing comments" is not "remove a review").

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
| `NO_ENTITY` | No entity in the knowledge base fits | The domain changes first (domain-model factory) |
| `NO_MAPPING` | The entity has no data mapping | You add it to `data.yaml` |
| `UNSUPPORTED` | No factory, or no kind of CRUD API, handles this task | Out of scope for now |

## Factory layout

```
src/harness/factories/crud-api/
├── factory.yaml        # name, description, examples (for the router), packs
├── technical.yaml      # operations → kinds, API rules, components, askable facts, flow
├── checks.pl           # factory-specific logic checks
├── templates/
│   └── python-fastapi/ # route, schemas, repository interface, tests
└── evals/              # tasks and expected outcomes
```

```yaml
# factory.yaml
name: crud-api
description: "Create, read, update and delete the domain entities of a business through a REST API, changing their state"
examples: ["Add a new customer", "List orders placed this week", "Change a product's price", "Remove a cancelled order"]
technical: technical.yaml
checks: checks.pl
packs: [python-fastapi]
```

## Phase 2 and later

| Item | Where it fits |
|---|---|
| LLM-guided design of larger domain changes, considering the existing domain model | The **domain-model factory**, extended |
| REST APIs and document stores as data sources | New source-type plugins |
| Agent-driven runs from Claude Code, Codex, Copilot | Platform, using the same spec |
