# Prompt 11f: Lists projection (Cohort D step 4)

**Phase:** L3 / Cohort D step 4 — first projection prompt since Cohort A's tasks/commitments/parties/etc. ships the **13th projection** per [D18] (`lists` first-class as the 13th projection alongside the 12 from Amendment-2). Per `docs/08-architecture-amendment-adapter-rebase.md` §3.4 ordering: "11f `lists` projection — depends on Cat-B emissions to consume" — those emissions now exist on main via 11b-i (Apple Reminders dual-emit, PRs #160-#163) and 11b-iv (Google Tasks central, PRs #173-#176).

**Depends on:** canonical 11a/11b-i/11b-iii/11b-iv all closed; `list_item.added@v1` schema registered (11b-i c1 commit `d833e4b9`); two producers on main (`AppleEventKitAdapter._emit_inbound_synced` + `_write_one_outbound`; `GoogleTasksAdapter._emit_list_item_added`); Cat-B `family` Literal includes `"tasks"` (PR #170 sidecar).

**Estimated duration:** ~3-3.5 hours. **NOT a pre-split candidate** at this narrow scope: subscribe to `list_item.added@v1` only (the only family member with producers on main); `list_item.updated/completed/deleted` subscriptions defer until those producers ship in follow-ups (11b-i-β / 11b-iv-β).

**Stop condition:** NEW projection at `adminme/projections/lists/` with `__init__.py` declaring `class ListsProjection(Projection)` (name=`"lists"`, version=`1`, subscribes_to=`["list_item.added"]`, schema_path); `schema.sql` defining a `list_items` table with composite PK `(tenant_id, list_external_id, item_external_id)`; `handlers.py` implementing idempotent `apply_event` that UPSERTs on `list_item.added`; `queries.py` exposing `get_item`, `items_for_list`, `items_for_member`, `open_items_for_member` (all with `session: Session` + `filter_rows` per [§6.1]); `tests/` covering handler idempotence, scope filter, and runner integration. Registered in the projections runner. `verify_invariants.sh` exits 0.

---

## Read first

1. `docs/08-architecture-amendment-adapter-rebase.md` §3.4 ordering (this prompt is item "11f after 11b").
2. `docs/05-architecture-amendment-personal-data-layer.md` §5.3 (canonical `list_item.*` taxonomy) + §2.5 (sharing_model_discriminator on Cat-B projections — `owner_member_id` is the column the projection's queries pivot on).
3. `docs/DECISIONS.md` [D18] (lists first-class as the 13th projection); [D17] (adapters never write projections directly — confirmed: this projection reads events, never writes back); [D7] (schemas v1 immutable).
4. `adminme/projections/base.py` (on main) — `Projection` ABC: `name`, `version`, `subscribes_to`, `schema_path`, `apply(envelope, conn)`, optional `after_batch` / `on_connection_opened`.
5. `adminme/projections/tasks/` (on main, all four files) — quality-bar reference. The tasks projection is the closest structural analog: per-tenant-keyed projection with status/UPDATE flows, scope-filtered queries via `filter_rows(session, rows)`.
6. `adminme/projections/runner.py` (on main) — projection registration + replay path.
7. `adminme/events/schemas/lists.py` (on main) — **READ CONTENTS** per Gate 0. `ListItemAddedV1` field set is the payload shape the handler reads.
8. `adminme/lib/session.py` + `adminme/lib/scope.py` (on main) — `Session` shape + `filter_rows` / `filter_one` contracts.
9. Recent canonical-build precedent: `prompts/_active/11b-iv-google-tasks-central.md` — c1-c4 structure carries; the c2 deliverables shift from "adapter pack" to "projection module".

## Operating context

11f closes the inbound visibility loop for the `list_item.*@v1` family. Two adapters on main emit `list_item.added@v1` (Apple Reminders dual-emit + Google Tasks); without a projection consuming them, those events sit dark in the log. 11f makes them queryable for downstream products (Cohort F console views, future CLI list commands, future morning_digest mention of "X items on your reminder list").

Three load-bearing items 11f establishes:

1. **NEW `lists` projection** at `adminme/projections/lists/` — fourth canonical-build prompt to ADD a top-level surface to the framework (canonical 11a/b-i/b-iii/b-iv added adapter packs; 11f adds a projection). Subscribes ONLY to `list_item.added@v1` for now per [D7]/[UT-27]-style asymmetry — declaring subscriptions for the other three list_item kinds without handler implementations would fail rebuild correctness when no producers exist. The other three subscriptions extend in follow-up prompts when 11b-i-β / 11b-iv-β ship.

2. **Composite primary key `(tenant_id, list_external_id, item_external_id)`** — guarantees uniqueness across sources (Apple Reminders list IDs + Google Tasks tasklist IDs may overlap by chance; the composite PK prevents collision). `item_external_id` alone is stable per-source but NOT globally — Apple Reminders' `EKReminder.calendarItemIdentifier` + Google Tasks' `task["id"]` are distinct namespaces.

3. **UPSERT (INSERT...ON CONFLICT...DO UPDATE) idempotence** — re-applying the same `list_item.added@v1` envelope produces the same row state per §2 invariant 4. Required for `rebuild()` correctness. Mirror the `tasks` projection's `apply_task_created` pattern.

OUT OF SCOPE per the narrow-scope decision:

- **`list_item.updated/completed/deleted@v1` subscriptions** — defer until producers ship in 11b-i-β / 11b-iv-β (or earlier if those producers are added to existing adapters in a sidecar). Subscribing without producers makes rebuild correctness untestable.
- **`list.added/updated/deleted@v1` (tasklist-level CRUD)** — those schemas exist (11b-i registered them) but no producer emits them yet. A future `lists` table on the projection (sibling to `list_items`) ships when producers exist.
- **Cross-source UNIFICATION** — merging "the same list" across Apple Reminders + Google Tasks (e.g. "Groceries" might exist in both) is out of scope. Each `(list_external_id, item_external_id)` pair is its own row.
- **xlsx workbook reverse-projection** — the [§7.3, D17] reverse-projection flow for Cat-B projections (per memo 05 §2.6) is its own prompt.
- **Console view of lists** — Cohort F territory.
- **Soft-delete / archival** — defer until `list_item.deleted` producer exists.
- **Cross-adapter changes** — Gmail / Plaid / Apple Reminders / Google Calendar / Google Tasks preserved byte-for-byte.

Citations: [§2 invariant 1] (projections never write event log), [§2 invariant 4] (apply is idempotent), [§6.1] (Session is required for query access), [§7.3, D17] (adapters never write projections), [D7] (schemas v1 immutable; uses existing `list_item.added@v1`), [D18] (lists first-class as 13th projection). Memo 05 §5.3 (canonical taxonomy), §2.5 (sharing_model_discriminator), §2.6 (reverse-projection out of scope). Memo 08 §3.4 (11f after 11b).

[VISION §"Real family"]: 11f makes Apple Reminders + Google Tasks events visible in the canonical event-sourced layer. Without it, the events landed by 11b-i + 11b-iv are functionally dark — there's no read path for downstream UIs. 11f is the **events→projection→UI** chain's middle link.

## Objective

Build the 13th projection at `adminme/projections/lists/` (NEW directory). `ListsProjection` subscribes to `list_item.added@v1` and UPSERTs rows in a `list_items` table keyed by `(tenant_id, list_external_id, item_external_id)`. Queries read with `session: Session` + `filter_rows` per [§6.1] / [§6.4] scope discipline. Register in the projections runner.

## Out of scope

(See above.)

## Approach (architectural sketch)

The fresh projection ships:

1. **`adminme/projections/lists/schema.sql`** (~30 lines): `list_items` table with columns matching `ListItemAddedV1` payload (`member_id`, `list_external_id`, `item_external_id`, `list_name`, `title`, `notes`, `due_at`, `priority`, `completed`, `last_modified_at`, `sync_cycle_id`) + envelope-level columns (`tenant_id`, `owner_scope`, `visibility_scope`, `sensitivity`, `last_event_id`). Composite PK; indexes on `(tenant_id, member_id)`, `(tenant_id, list_external_id)`, `(tenant_id, completed)`.

2. **`adminme/projections/lists/handlers.py`** (~80 lines): `apply_event(envelope, conn)` dispatches on `event_type`; only `"list_item.added"` is handled today. `apply_list_item_added` UPSERTs via `INSERT ... ON CONFLICT(tenant_id, list_external_id, item_external_id) DO UPDATE SET ...` (idempotence per §2 invariant 4).

3. **`adminme/projections/lists/queries.py`** (~100 lines): four queries with `session: Session` + `filter_rows`:
   - `get_item(conn, session, *, list_external_id, item_external_id)` → single item or None
   - `items_for_list(conn, session, *, list_external_id)` → list ordered by `due_at`
   - `items_for_member(conn, session, *, member_id)` → all items for a member
   - `open_items_for_member(conn, session, *, member_id)` → `completed = 0` only

4. **`adminme/projections/lists/__init__.py`** (~30 lines): `class ListsProjection(Projection)` with `name="lists"`, `version=1`, `subscribes_to=["list_item.added"]`, `schema_path`.

5. **Register in `adminme/projections/runner.py`**: add `ListsProjection` to the registered-projections list.

6. **Tests**: framework unit tests for each handler + each query + idempotence rebuild test; pack-internal-equivalent (projection-level invariant test); integration test through the runner.

## Deliverables

### NEW projection module
- `adminme/projections/lists/__init__.py`
- `adminme/projections/lists/schema.sql`
- `adminme/projections/lists/handlers.py`
- `adminme/projections/lists/queries.py`

### Runner registration
- `adminme/projections/runner.py` — append `ListsProjection` to the registry.

### Tests
- `tests/unit/projections/lists/__init__.py`
- `tests/unit/projections/lists/test_handlers.py` (~10 tests covering UPSERT happy path + idempotence + envelope scope-fields propagation + unhandled event-type no-op)
- `tests/unit/projections/lists/test_queries.py` (~8 tests covering each query + scope filtering)
- `tests/integration/test_lists_projection_runner.py` (~3 tests covering register + replay + rebuild idempotence)

Test floor: ≥18 tests.

### Documentation
- `docs/build_log.md` — append the 11f entry per the canonical 11a/11b-i/11b-iii/11b-iv c4 template.

## Verification

```bash
poetry run python -c "from adminme.projections.lists import ListsProjection; assert ListsProjection.name == 'lists'; assert ListsProjection.version == 1; assert ListsProjection.subscribes_to == ['list_item.added']; print('11f projection class ✓')"

poetry run pytest tests/unit/projections/lists/test_handlers.py tests/unit/projections/lists/test_queries.py tests/integration/test_lists_projection_runner.py -v
# Expected: ≥18 passed

poetry run mypy adminme/projections/lists/ 2>&1 | tail -5
poetry run ruff check adminme/projections/lists/ 2>&1 | tail -3

bash scripts/verify_invariants.sh
# Expected: exit 0
```

## Stop

When the lists projection registers via the runner + UPSERTs `list_item.added@v1` envelopes idempotently + queries return scope-filtered rows + rebuild produces identical row state, c4 is done. **Closes the events→projection chain for the `list_item.*` family.** Do NOT proceed to 11b-i-β / 11b-iv-β (which add update/completed/deleted producers) or to a Cohort F console view of lists without operator review.

## What this PR is NOT

Parent draft only. v4 refactor follows in `partner/refactor-11f`.

No constitutional doc edits. No PROMPT_SEQUENCE rows touched.
