# Prompt 11f: Lists projection (Cohort D step 4)

**Phase:** L3 / Cohort D step 4 — first projection prompt of Cohort D. Adds the 13th projection per [D18]. Parent draft: `prompts/11f-lists-projection.md` (merged PR #177).
**Branch:** `claude/11f-c<N>` per c-band.
**Stop condition:** NEW `adminme/projections/lists/` with `__init__.py` declaring `class ListsProjection(Projection)` (`name="lists"`, `version=1`, `subscribes_to=["list_item.added"]`); `schema.sql` defining `list_items` table with composite PK `(tenant_id, list_external_id, item_external_id)`; `handlers.py` implementing idempotent UPSERT on `list_item.added`; `queries.py` exposing 4 scope-filtered queries; `ListsProjection` registered in `adminme/projections/runner.py`. `verify_invariants.sh` exits 0.
**Depends-on:** canonical 11a/11b-i/11b-iii/11b-iv all on main; `list_item.added@v1` schema registered (11b-i c1); parent draft merged (PR #177, main `9fc6edf6`).
**Quality-bar reference:** `adminme/projections/tasks/` (closest structural analog — per-tenant UPSERT with scope-filtered queries); recent canonical-build precedent `prompts/_active/11b-iv-google-tasks-central.md` for c1-c4 structure.
**Estimated duration:** c1 ~15 min (verify-only) · c2 ~90 min (schema+handlers+queries+__init__) · c3 ~60 min (handler+query tests) · c4 ~60 min (integration+runner registration+BUILD_LOG) · total ~3-3.5 hrs.

---

## §1 Behavioral grounding

11f is the **first projection prompt of Cohort D** and the **13th projection** per [D18]. Three load-bearing items:

1. **NEW `lists` projection at `adminme/projections/lists/`** mirroring the `tasks` projection's four-file shape (`__init__.py` + `schema.sql` + `handlers.py` + `queries.py`). Subscribes ONLY to `list_item.added@v1` per [UT-27]-style asymmetry — declaring subscriptions for `list_item.updated/completed/deleted` without handler implementations + producers on main would fail rebuild correctness (no events to replay). Follow-up sub-prompts (11b-i-β / 11b-iv-β) ship the additional producers, and a paired 11f-β extends subscriptions then.

2. **Composite PK `(tenant_id, list_external_id, item_external_id)`** — guards against `item_external_id` collisions across source adapters (Apple Reminders `EKReminder.calendarItemIdentifier` + Google Tasks `task["id"]` are distinct namespaces but byte-format may overlap). `list_external_id` distinguishes by source-list.

3. **UPSERT (INSERT...ON CONFLICT(...) DO UPDATE SET ...) per §2 invariant 4** — handler is idempotent: re-applying the same envelope produces the same row state, including ALL field columns + envelope-level columns (`tenant_id`, `owner_scope`, `visibility_scope`, `sensitivity`, `last_event_id`). Pattern mirrors `apply_task_created` in `adminme/projections/tasks/handlers.py`.

OUT OF SCOPE per parent draft §"Out of scope":
- `list_item.updated/completed/deleted@v1` subscriptions (no producers).
- `list.added/updated/deleted@v1` (tasklist-level CRUD).
- Cross-source unification (Apple Reminders + Google Tasks "same list" merge).
- xlsx reverse-projection.
- Console view (Cohort F).
- Soft-delete / archival.
- Cross-adapter changes.

Citations: [§2 invariant 1, 2, 4] (event log append-only; projections never write event log; apply idempotent), [§6.1] (Session required for query access), [§6.4] (filter_rows scope discipline), [§7.3, D17] (adapters never write projections — confirmed), [D7] (schemas v1 immutable; uses existing `list_item.added@v1`), [D18] (lists first-class as 13th projection). Memo 05 §5.3, §2.5. Memo 08 §3.4. [VISION §"Real family"]: closes events→projection→UI chain's middle link.

## §2 Read first (required)

**c1 (verify-only no-op) — 3 files**

- `prompts/11f-lists-projection.md` (merged parent draft).
- `adminme/events/schemas/lists.py` (on main) — **READ CONTENTS** per Gate 0. `ListItemAddedV1` field set is the handler's read target.
- `adminme/events/registry.py` (on main) — `registry.get(name, version)` API.

**c2 (projection module) — 6 files**

- `adminme/projections/base.py` (on main) — `Projection` ABC contract.
- `adminme/projections/tasks/__init__.py` (on main) — class structure precedent.
- `adminme/projections/tasks/schema.sql` (on main) — DDL precedent (PRIMARY KEY shape, indexes, sensitivity CHECK).
- `adminme/projections/tasks/handlers.py` (on main) — `apply_event` dispatch + `apply_*` handler pattern (UPSERT via `INSERT ... ON CONFLICT(...) DO UPDATE SET ...`).
- `adminme/projections/tasks/queries.py` (on main) — query pattern with `session: Session` + `filter_rows`.
- `adminme/projections/runner.py` (on main) — projection registration list.

**c3 (handler + query tests) — 5 files**

- `tests/unit/projections/tasks/` (existing directory, scan for test file structure).
- `adminme/projections/lists/__init__.py` (on the c2 branch).
- `adminme/projections/lists/handlers.py` (on the c2 branch).
- `adminme/projections/lists/queries.py` (on the c2 branch).
- `adminme/events/schemas/lists.py::ListItemAddedV1` (handler input fixtures).

**c4 (integration + runner registration + BUILD_LOG) — 4 files**

- `tests/integration/` (look for an existing projection-runner integration test as precedent).
- `adminme/projections/runner.py` (modify to register `ListsProjection`).
- `docs/build_log.md` (append 11f entry).
- `prompts/_active/11b-iv-google-tasks-central.md` (c4 BUILD_LOG entry shape).

## §3 Reconciliation notes (carry-forward + 11f-specific)

- **F-1 (HARD/from cycle #17): READ FILE CONTENTS for the schema in c1.** Grep `class ListItemAddedV1` in `adminme/events/schemas/lists.py` to confirm field set + `Literal[0,1,5,9]` priority enum + `extra="forbid"` model_config before authoring c2's handler.
- **F-2 (HARD/standing): Idempotent UPSERT per §2 invariant 4.** The handler MUST be safe to replay. Mirror `apply_task_created` in `adminme/projections/tasks/handlers.py:78-145` — `INSERT INTO list_items (...) VALUES (...) ON CONFLICT(tenant_id, list_external_id, item_external_id) DO UPDATE SET <every column>=excluded.<column>, last_event_id=excluded.last_event_id`.
- **F-3 (HARD): Scope discipline on queries.** Every public query MUST take a `session: Session` parameter and route results through `filter_rows(session, rows)` or `filter_one(session, row)` per [§6.1, §6.4]. Per `CONSOLE_PATTERNS.md §6/§7` child sessions drop rows whose tags overlap `CHILD_FORBIDDEN_TAGS` — handled inside `filter_rows`.
- **F-4 (HARD/standing): test basename uniqueness.** `tests/unit/projections/lists/test_handlers.py` and `test_queries.py` are unique against existing test files (verified — no existing `lists` projection tests on main).
- **F-5 (SOFT): adapter-style F-5 doesn't apply here.** Projection diff size is bounded by the column count, not adapter logic. Expected c2 net diff: ~250-350 lines across the 4 NEW files. No ceiling beyond reason.
- **F-6 (HARD/from canonical 11a): `tests/unit/projections/lists/__init__.py` IS required.** The package marker for the new test dir; per the pyproject testpath convention. NOT a pack-internal tests dir — F-6's no-`__init__.py` rule for pack-internal dirs DOES NOT apply here.
- **F-7 (HARD/new for 11f): subscribes_to is `["list_item.added"]` ONLY.** Adding the other three list_item event types to `subscribes_to` without handler dispatch + producers on main breaks rebuild correctness (the runner will look for handlers that 11f doesn't implement and reproduce events that no adapter emits). The asymmetric subscription is the right shape per [UT-27]-style discipline applied at the L3 layer.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `Projection` ABC at `adminme/projections/base.py`** — required attrs: `name`, `version`, `subscribes_to`, `schema_path`, `apply(envelope, conn)`; optional: `after_batch`, `on_connection_opened`. Verified per Partner pre-authorship read.
- **A-2: `apply_event` dispatch pattern** — single-function module export `apply_event(envelope, conn)` that dispatches on `envelope["type"]` to per-event-type handlers. Mirror tasks projection.
- **A-3: `filter_rows` / `filter_one` import path** — `from adminme.lib.scope import filter_rows, filter_one` per `adminme/projections/tasks/queries.py:13`.
- **A-4: `Session` import path** — `from adminme.lib.session import Session`.
- **A-5: `runner.py` registration shape** — add `ListsProjection` to whatever registration list / factory the runner uses (read the file at orientation to confirm exact pattern).
- **A-6: `ListItemAddedV1` field set on main** — `{member_id, list_external_id, item_external_id, list_name, title, notes, due_at, priority, completed, last_modified_at, sync_cycle_id}` per `adminme/events/schemas/lists.py`. Verified at Partner pre-authorship.
- **A-7: Envelope-level columns** — `tenant_id`, `owner_scope`, `visibility_scope`, `sensitivity` come from the envelope (not the payload). `last_event_id` is `envelope["event_id"]` per `tasks/handlers.py` precedent.

## §5 Objective

Build the 13th projection at `adminme/projections/lists/`. `ListsProjection` subscribes to `list_item.added@v1`; UPSERTs rows in a `list_items` table keyed by `(tenant_id, list_external_id, item_external_id)`; exposes 4 scope-filtered queries. Register in the projections runner.

## §6 Out of scope

(Same as parent draft.)

## §7 Deliverables

**c1: VERIFY-ONLY NO-OP** — assert `registry.get("list_item.added", 1) is ListItemAddedV1`. 0 production-code lines.

**c2: NEW projection module + runner registration**:

1. `adminme/projections/lists/schema.sql` (~35 lines):

```sql
CREATE TABLE IF NOT EXISTS list_items (
    tenant_id         TEXT NOT NULL,
    list_external_id  TEXT NOT NULL,
    item_external_id  TEXT NOT NULL,
    member_id         TEXT NOT NULL,
    list_name         TEXT NOT NULL,
    title             TEXT NOT NULL,
    notes             TEXT,
    due_at            TEXT,
    priority          INTEGER NOT NULL CHECK (priority IN (0, 1, 5, 9)),
    completed         INTEGER NOT NULL DEFAULT 0,
    last_modified_at  TEXT NOT NULL,
    sync_cycle_id     TEXT NOT NULL,
    owner_scope       TEXT NOT NULL,
    visibility_scope  TEXT NOT NULL,
    sensitivity       TEXT NOT NULL DEFAULT 'normal'
                      CHECK (sensitivity IN ('normal','sensitive','privileged')),
    last_event_id     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, list_external_id, item_external_id)
);

CREATE INDEX IF NOT EXISTS idx_list_items_tenant_member ON list_items(tenant_id, member_id);
CREATE INDEX IF NOT EXISTS idx_list_items_tenant_list ON list_items(tenant_id, list_external_id);
CREATE INDEX IF NOT EXISTS idx_list_items_tenant_completed ON list_items(tenant_id, completed);
CREATE INDEX IF NOT EXISTS idx_list_items_tenant_due ON list_items(tenant_id, due_at);
```

2. `adminme/projections/lists/handlers.py` (~80 lines):
   - `apply_event(envelope, conn)` dispatches on `envelope["type"]`; handles only `"list_item.added"`.
   - `apply_list_item_added(envelope, conn)`: UPSERT via `INSERT ... ON CONFLICT(tenant_id, list_external_id, item_external_id) DO UPDATE SET ...` per F-2.

3. `adminme/projections/lists/queries.py` (~120 lines): four queries with `session: Session` + `filter_rows` / `filter_one`:
   - `get_item(conn, session, *, list_external_id, item_external_id)`.
   - `items_for_list(conn, session, *, list_external_id)`.
   - `items_for_member(conn, session, *, member_id)`.
   - `open_items_for_member(conn, session, *, member_id)` (filters `completed = 0`).

4. `adminme/projections/lists/__init__.py` (~25 lines):
   - `class ListsProjection(Projection)` with `name="lists"`, `version=1`, `subscribes_to=["list_item.added"]`, `schema_path`.
   - `apply` delegates to `handlers.apply_event`.

5. `adminme/projections/runner.py` (MODIFY): append `ListsProjection` to the projections-registry list (~3 lines added).

**c3: tests (≥18 floor)** + `tests/unit/projections/lists/__init__.py`:

- `tests/unit/projections/lists/test_handlers.py` (~10 tests):
  - `test_apply_list_item_added_inserts_row_with_all_columns`
  - `test_apply_list_item_added_upserts_on_conflict_idempotent` (apply same envelope twice → row state identical)
  - `test_apply_list_item_added_envelope_scope_fields_propagate` (owner_scope/visibility_scope/sensitivity/last_event_id)
  - `test_apply_list_item_added_priority_literal_values_accepted` (0/1/5/9)
  - `test_apply_list_item_added_completed_bool_coerces_to_integer`
  - `test_apply_list_item_added_optional_notes_due_at_null_handling`
  - `test_apply_event_unhandled_type_is_noop` (e.g. `list_item.updated`)
  - `test_apply_event_dispatch_only_for_subscribed_type`
  - `test_apply_list_item_added_distinct_lists_distinct_rows` (composite PK)
  - `test_apply_list_item_added_distinct_items_within_list_distinct_rows`

- `tests/unit/projections/lists/test_queries.py` (~8 tests):
  - `test_get_item_returns_filtered_row`
  - `test_get_item_returns_none_for_missing`
  - `test_items_for_list_returns_filtered_rows`
  - `test_items_for_member_returns_filtered_rows`
  - `test_open_items_for_member_filters_completed_out`
  - `test_queries_apply_filter_rows_scope_discipline` (Session with limited scope returns subset)
  - `test_queries_apply_filter_one_for_get_item` ([§6.1])
  - `test_queries_order_by_due_at_then_created` (or analogous deterministic order)

**c4: integration + runner registration + BUILD_LOG**:

- `tests/integration/test_lists_projection_runner.py` (NEW, ~3 tests):
  - `test_lists_projection_registered_in_runner` (ListsProjection appears in the runner's registry).
  - `test_runner_replays_list_item_added_into_lists_db` (events.list_item.added → row in projection DB).
  - `test_rebuild_produces_identical_state` (§2 invariant 1 — rebuild idempotence).

- `docs/build_log.md` — append 11f entry per the canonical 11a/11b-iv c4 template.

## §8 Commit structure

Four commits. Format `phase 11f commit <N>: <imperative summary>`.

- **c1: `phase 11f commit 1: verify list_item.added@v1 registered (no-op)`** — 0 production lines.
- **c2: `phase 11f commit 2: lists projection module + runner registration`** — 5 files (4 new + 1 modified).
- **c3: `phase 11f commit 3: handler + query tests for lists projection`** — ≥18 tests.
- **c4: `phase 11f commit 4: integration + runner replay + rebuild + BUILD_LOG`** — closes Cohort D step 4.

## §8.5 Branch strategy — STACKED

c-band file scopes non-overlapping: c1 verify-only; c2 new projection dir + runner modify (only `runner.py` is shared but the c2 modification is additive — append one entry to the registry); c3 new test dir; c4 new integration + BUILD_LOG. Ghost-conflict risk LOW (single-line runner registration is the only existing-file touchpoint).

Test floor: ≥10 handler + ≥8 query + ≥3 integration = **≥21 total**.

## §9 Verification

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.lists import ListItemAddedV1; assert registry.get('list_item.added', 1) is ListItemAddedV1; print('list_item.added@v1 verified ✓')"
```

**c2**:
```bash
poetry run python -c "from adminme.projections.lists import ListsProjection; from adminme.projections.base import Projection; assert issubclass(ListsProjection, Projection); assert ListsProjection.name == 'lists'; assert ListsProjection.version == 1; assert ListsProjection.subscribes_to == ['list_item.added']; print('11f projection class ✓')"
poetry run mypy adminme/projections/lists/ 2>&1 | tail -5
```

**c3**:
```bash
poetry run pytest tests/unit/projections/lists/ -v
# Expected: ≥18 passed
```

**c4**:
```bash
poetry run pytest tests/integration/test_lists_projection_runner.py -v
poetry run pytest tests/ -v 2>&1 | tail -3
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When the lists projection registers via the runner + UPSERTs `list_item.added@v1` envelopes idempotently + queries return scope-filtered rows + rebuild produces identical row state + the existing `list_item.added@v1` schema on main is UNCHANGED + no other projection touched, c4 is done. **Closes the events→projection chain for the `list_item.*` family.**

After merge: Partner runs three-job QC. Batched partner-state-snapshot PR (post-11a + post-11b-i + post-11b-iii + post-11b-iv + post-11f) via Chrome MCP `file_upload`.
