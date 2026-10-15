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
| **Resolve** | Prolog applies the API anatomy rules (see [Design decisions](#design-decisions)) and the data mapping; for a new service, SemIf answers yes/no questions about details the task states (which attributes change, which filters) | Logic problems block the design; unclear details become open questions |
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

`domain.yaml` says **what** the business has; `.harness/data.yaml` says **where it is stored**. Generated APIs depend on a **repository interface** per resource (get, list, create, update, delete); a source adapter implements it.

```
API route → business logic → repository interface → source adapter → data store
```

### `.harness/data.yaml`

Three sections, everything written out:

```yaml
sources:                                    # the data stores
  bookstore_db:
    type: relational                        # a source-type plugin
    description: "The bookstore's own SQLite database"
    connection:
      url_env: BOOKSTORE_DATABASE_URL       # from the environment: never secrets in YAML
      default_url: sqlite:///./bookstore.db # local fallback only

entities:                                   # where each entity is stored
  Book:
    source: bookstore_db
    table: books
    columns: { id: id, isbn: isbn, title: title, author: author, price: price, status: status }
  Review:
    source: bookstore_db
    table: reviews
    parent_key: book_id                     # part of a Book: the column pointing at its Book
    columns: { id: id, rating: rating, comment: comment }

relationships:                              # how each connection is stored
  collection_books:                         # many-to-many: a link table, a key per side
    source: bookstore_db
    table: collection_books
    keys: { Collection: collection_id, Book: book_id }
  book_publisher:                           # one-to-many: a column on the child's table
    source: bookstore_db
    column: books.publisher_id
```

A column can also be written in long form: `price: { column: price_cents, convert: money_cents }`, or `{ stored: false }` for an attribute that isn't stored.

### Source types are plugins

`type:` names the plugin that handles a source. The plugin decides what may be written for it (a relational source maps an entity to a table and columns; a REST source would map it to an endpoint and fields), what it can do, and how to read the real source.

```
src/harness/datasources/<type>/
├── source.yaml      # description, capabilities, converters
├── schema.py        # Connection, EntityMapping, RelationshipMapping
├── introspect.py    # optional: read the real tables and columns
└── templates/<pack>/  (S5: the repository code for this type)
```

Phase 1 has one type, **relational** (SQLite, PostgreSQL, MySQL). Its **capabilities** are the kinds' persistence names (`insert_row`, `select_many`, `insert_link`, …) plus `filter` and `paging`; Prolog reports an API a source can't support (`source_cannot_persist`, `source_cannot_filter`) before anything is designed.

### Checks

`harness validate` checks that every entry names a known source of an installed type, fits that type's shape, matches `domain.yaml` (every attribute and the lifecycle field has a column or `stored: false`; converters fit the attribute types; a composition child has `parent_key`; a many-to-many relationship has a link table with a key per side; a one-to-many one has a column on the child's table), and that `default_url` is local with no password. An entity or relationship without an entry is a warning; a task about it ends as `NO_MAPPING`.

`harness validate --introspect` reads the real database (read-only) and reports differences as warnings: missing tables or columns, and columns not in the domain.

## The spec

`harness resolve` saves every design in a run folder, before any code is written:

```
.harness/runs/<run id>/
├── spec.yaml      the design, for you to read and confirm (S5)
├── openapi.yaml   the same API as OpenAPI 3.1, generated from the spec only
└── trace.jsonl    every decision, from classification to design
```

The run folders ignore themselves (`runs/.gitignore`). Who makes what:

| File | Content from | Code |
|---|---|---|
| `spec.yaml` | the factory's design (API, data, delete effects, tests, sources) and the platform's cover sheet (run, task, factory, status, open questions) | `factories/crud-api/design.py`, `harness/resolve.py` |
| `openapi.yaml` | the spec only | `factories/crud-api/design.py › to_openapi` |

A real example: [tests/specs/change_review_rating.yaml](../../tests/specs/change_review_rating.yaml) for *"Readers should be able to change their rating of a book through a new endpoint"*:

```yaml
task: Readers should be able to change their rating of a book through a new endpoint
factory: crud-api
status: draft                       # S5: confirmed, edited or rejected by you
service: { name: change_review_rating, new: true }
kind: change
target: Review
parent: Book
details:
  changes: [rating]                 # SemIf: rating yes
api:
  method: PATCH
  path: /books/{book_id}/reviews/{review_id}
  request: { fields: { rating: { type: integer, required: false, min: 1, max: 5 } } }
  response: { status: 200, shape: item, … }
  errors: [ 404 no Book…, 404 no Review…, 422 invalid… ]
data: { source: bookstore_db, table: reviews, parent_key: book_id, operation: update_row, … }
tests:
- changes a Review → 200
- no Book has this book_id → 404
- …
open_questions:
- Does the task change the comment of a Review? (SemIf 0.32)
sources:
  service.name: 'rule: kind change names services change_{entity}_{attribute}'
  details: SemIf yes/no questions about the task
  api.path: rule item-path
  …
```

**New services:** SemIf answers yes/no per candidate (attributes a change touches, filters a list has) and chooses a lifecycle move; yes at 0.8 or more, no at 0.2 or less, otherwise an open question. A new service is named by its kind's rule when every part is known; otherwise the name is an open question (a transition's verb shows as `<verb>` in its path until answered).

**`tests`** are derived from the design (one success case, one per error, one per delete effect); S5 verifies the generated code against them. **`openapi.yaml`** adds nothing to the spec: it is the same API in the format tools read.

## Design decisions

The decisions behind every API this factory designs (stage S4). The choices marked *setting* live in `technical.yaml › api` and can be overridden per project.

### API anatomy

| # | Decision |
|---|---|
| I2 | **Path for identity, query for lists.** The item a request acts on is a path parameter (`/books/{book_id}`). Query parameters exist only on lists: filters on summary fields, plus paging. A single item has no query parameters |
| I3 | The identifying value is the entity's `identifier` in `domain.yaml` |
| I4 | Paths are plural, kebab-case (`/books`, `/book-authors`); parameters are `{entity}_id` (*setting*) |
| I5 | Composition children are nested for lists and single items: `/books/{book_id}/reviews/{review_id}`; the parent must exist and own the child (*setting*) |
| I6 | Connections: many-to-many `PUT`/`DELETE /collections/{collection_id}/books/{book_id}`; one-to-many `PUT`/`DELETE /books/{book_id}/publisher` with body `{publisher_id}` |
| I7 | Transitions: `PATCH /books/{book_id}/archive`, the verb in the path, no body (*setting*: or the lifecycle field in the body, which shares the item's PATCH) |
| I8 | Request body: create and replace send every attribute except generated ones and the lifecycle field; change sends only its `changes`; others have no body |
| I9 | Responses: a single item has all attributes; a list has `items` (summaries), `paging` and `applied_filters`; delete, link and unlink return 204. Errors: 404 unknown identifier or parent; 422 invalid body; 409 unique value taken, move not allowed, or delete restricted (*setting*: the codes) |
| I10 | List summary: `summary: [...]` per entity (default: all attributes; identifier and lifecycle field always shown). A list's filters must be summary fields |

### Dependents and delete

| # | Decision |
|---|---|
| I11 | `on_delete: restrict \| set_null` on one-to-many aggregations in `domain.yaml` (default `restrict`: 409 while referenced). Compositions always cascade, all the way down. Many-to-many link rows are removed |
| I12 | Prolog `dependent/3`, `cascades/2`, `delete_effect/4`, each effect with its rule id. `set_null` can't clear a `required` reference |

### Scope and SemIf

| # | Decision |
|---|---|
| I13 | No auth, roles or caching in the CRUD factory: generated routers fit into the app's own auth. An auth factory is planned |
| I14 | SemIf only reads the task for new-service details (which attributes change, which filters, which lifecycle move); below the threshold → open question |

### Output and commands

| # | Decision |
|---|---|
| I15 | Run folder `.harness/runs/<id>/` (`trace.jsonl`, `spec.yaml`, `openapi.yaml`), git-ignored in projects |
| I16 | The spec is the source of truth; OpenAPI 3.1 is generated only from it; every value names its source |
| I17 | `harness resolve "task"` (S4b) and `harness resolve --service <name>`; unsure, no entity, no mapping and unsupported tasks stop before resolve |

### Customising

| # | Decision |
|---|---|
| I19 | Anatomy choices are settings in `technical.yaml › api`; Prolog holds only the mechanics |
| I20 | Overrides can remove a rule: `remove_rules: [id]` |
| I21 | One service can differ: `api: { path: … }` on it in `domain.yaml` |
| I22 | Logic checks can't be overridden |
| I23 | `?fields=` and `?include=` belong to a later "richer reads" stage |
| I24 | `harness override SECTION.NAME` starts an override from the factory's entry |
| I25 | `harness: "<range>"` in `harness.yaml` pins the harness versions a project works with |

## Generating the code

From a confirmed spec only, tests first:

```
harness confirm <run> --approve      re-check the spec; record its hash
harness generate <run>               tests (must fail) → code (must pass), in a git worktree
```

| Generated | Template | Owner |
|---|---|---|
| `tests/test_<service>.py` | `packs/python-fastapi/templates/test_api.py.j2`, from the spec's `tests` only | harness |
| `app/models/<entity>.py` (new tables only) | `datasources/relational/templates/python-fastapi/model.py.j2` | harness |
| `app/repositories/<service>.py` | `datasources/relational/templates/python-fastapi/repository.py.j2` | harness |
| `app/schemas/<service>.py`, `app/api/<service>.py` | `factories/crud-api/templates/python-fastapi/` | harness |
| `app/problems.py` | errors as `application/problem+json`, written once | shared |
| `app/rules/<service>.py` | the business-rules hook, written once | **you** |

- **Tests** are black-box: HTTP requests through the project's `client` fixture; `given` rows are set up through its `session` fixture. They never import the code under test.
- **Registration:** lines between `# harness ↓` / `# harness ↑` in `app/main.py` (routers) and `app/models/__init__.py` (models). Without the markers, generation stops and shows the lines to add.
- **Hand-written code** is never edited (apart from the marked lines). A hand-written model is reused only if it has every column `data.yaml` maps.
- **Every generated file** starts with a header naming its run and spec hash, and is recorded in `.harness/generated.yaml`; regenerating overwrites a file only if its hash still matches.
- **Files per service** (route, schemas, repository), so new services add files instead of merging into generated ones; the model is per entity.

### Decisions (S5)

| # | Decision |
|---|---|
| K1 | Two PRs: S5a (confirm, pack, templates, generate) and S5b (verify, report, `harness run`) |
| K2 | Confirm by editing `spec.yaml` and `harness confirm --approve`, which re-checks; no open questions or `<placeholders>` may remain; answers kept under `answers:` |
| K3 | Templates are factory × pack and source type × pack; the pack holds layout, imports, markers and commands |
| K4, K13 | Generated for create (flat and nested): model (new tables), repository, schemas, route, rules hook, registration; other kinds in S6 |
| K5, K6 | Hand-written code is never edited, except between markers; a hand-written model must match `data.yaml` |
| K7 | Tables come from the app's own `create_all`; migrations later |
| K8 | A git worktree on `harness/<run>-<service>` by default, committed when the tests pass; `--in-place` as the alternative |
| K9 | Header with run and spec hash; `.harness/generated.yaml` |
| K14 | No LLM: business rules go in the hook |
| K15 | The spec's `tests` are structured cases (`given`, `request`, `expect` with status, headers, body, problem title) with example values by rule |
| K16, K17 | Tests are generated first, from the confirmed spec only, and must fail before the code and pass after |

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
├── technical.yaml      # kinds, operations, API conventions, rules, components, flow
├── anatomy.pl          # API anatomy and delete rules (Prolog)
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
