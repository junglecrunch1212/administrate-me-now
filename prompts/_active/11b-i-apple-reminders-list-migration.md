# Prompt 11b-i: Apple Reminders list-item event-family migration (Cohort D step 2-α)

**Phase:** L1 / Cohort D step 2 — first of canonical 11b cohort per `docs/08-architecture-amendment-adapter-rebase.md` §3.4. First sub-prompt of the four-way 11b split (11b-i Apple Reminders dual + 11b-ii Apple Calendar dual + 11b-iii Google Calendar + 11b-iv Google Tasks). Parent draft: `prompts/11b-i-apple-reminders-list-migration.md` (merged PR #158).
**Branch:** `claude/11b-i-c<N>` per c-band (Executor's harness assigns).
**Stop condition:** Four new v1 schemas (`list_item.added`, `list_item.updated`, `list_item.completed`, `list_item.deleted`) registered at `adminme/events/schemas/lists.py`. `AppleEventKitAdapter` dual-emits the new `list_item.*@v1` events alongside the existing `reminder.list_item.synced@v1` on both outbound and inbound paths. Existing `reminder.list_item.synced@v1` schema is UNCHANGED per [D7]. `adminme adapters validate packs/adapters/reminders/apple-eventkit` continues to exit 0 with NO `advisory_pending_retrofit`.
**Depends-on:** canonical 11a closed (PRs #154-#157, main `ebfa56ed`); Cohort C closed (11r-a/b/c on main); parent draft merged (PR #158).
**Quality-bar reference:** `prompts/_active/11a-gmail-real-outbound.md` (canonical-build narrow scope) + `prompts/_active/11r-c-retrofit-reminders-to-external-state-mirror-adapter.md` (Cat-B precedent). 11b-i applies canonical 11a's "MAP DON'T REGISTER" inverted — here we DO register NEW v1 schemas (additive per [D7]) and dual-emit them alongside the existing v1 to give downstream consumers a migration window.
**Estimated duration:** c1 ~30-40 min (new schema file + registrations) · c2 ~60-90 min (dual-emit in adapter.py) · c3 ~45-60 min (framework tests) · c4 ~45-60 min (pack-internal + integration + BUILD_LOG) · total ~3-3.5 hrs wall / ~$50-80 spend.

---

## §1 Behavioral grounding

11b-i closes memo 08 §1.3's item (b) for Apple Reminders — the deferred [D18] event-taxonomy migration from `reminder.list_item.synced@v1` (single event with `direction` field) to the canonical `list_item.added/updated/completed/deleted@v1` family. The 11r-c retrofit (Cohort C step 3, PRs #145-#148) deliberately deferred this per memo 08 §5 ("inheritance + manifest only, not a rewrite"). 11b-i closes it via **dual-emit**: the adapter emits BOTH event families from the same source data. Downstream consumers migrate at their own pace; once all consumers have migrated, a future prompt may deprecate `reminder.list_item.synced@v1` per [D7] deprecation procedure.

Three load-bearing items this prompt establishes:

1. **Four new v1 schemas in `adminme/events/schemas/lists.py`** — `ListItemAddedV1`, `ListItemUpdatedV1`, `ListItemCompletedV1`, `ListItemDeletedV1`. All declare `model_config = {"extra": "forbid"}`. Field set MIRRORS `ReminderListItemSyncedV1` for migrability: `{member_id, list_external_id, item_external_id, list_name, title, notes, due_at, priority (Literal[0,1,5,9] matching EventKit's ordinal scale per the existing schema), completed, last_modified_at, sync_cycle_id}`. `ListItemUpdatedV1` adds `field_updates: dict[str, Any]` per `CalendarEventUpdatedV1` precedent. `ListItemDeletedV1` carries the minimal `{member_id, list_external_id, item_external_id, deleted_at, sync_cycle_id}` (no full body — the deletion already removed the source data). Registered at module-load via `registry.register("list_item.added", 1, ListItemAddedV1)` (+ 3 more) at bottom of `lists.py`.

2. **Dual-emit on the outbound write path** (`_apply_write_impl` → `_write_one_outbound`): after each successful EKReminder save, emit ONE `list_item.added@v1` event AND continue to emit `reminder.list_item.synced@v1` with `direction="outbound"` per the existing pattern. Both events share the same `causation_id` (source-event id from the upstream `reminder.dispatched` / `task.created` / `commitment.proposed`) and `correlation_id` per [D8]. The dual-emit happens INSIDE the existing `_write_one_outbound` orchestration, not via a new helper — minimizes adapter.py diff.

3. **Dual-emit on the inbound sync path** (`_emit_inbound_synced`): when an inbound EKReminder is observed via the poll loop, derive the `list_item.*` event kind from the change semantics — first observation (not in `_seen_inbound` cache) → `list_item.added@v1`; subsequent observation with `completed: false → true` transition → `list_item.completed@v1`; subsequent observation with other field changes → `list_item.updated@v1` (with `field_updates` carrying the changed fields). `list_item.deleted@v1` on inbound is DEFERRED — requires negative-set logic comparing prior vs current sync cycle and complicates the poll's per-item path; ship in a follow-up alongside `list.*` family.

OUT OF SCOPE per parent draft §6:

- **`list.*@v1` family (list-level CRUD)** — defers; `AppleEventKitAdapter.write_capabilities` currently `["create_reminder"]` only.
- **[D22] dual-deployment per-member bridge variant** — follow-up.
- **Deprecating `reminder.list_item.synced@v1`** — dual-emit keeps OLD family live. 47 production consumers preserve byte-for-byte.
- **The `lists` projection (11f)** — consumer-side, not this prompt.
- **`list_item.deleted@v1` emission on inbound** — schema is registered (so consumers can subscribe), but emission only happens from the outbound path's write-error / cancellation; inbound-deletion path is a follow-up.
- **Cross-adapter changes** — Gmail (11a) + Plaid (11r-b) preserved byte-for-byte.

Citations: [§1.2] (event log append-only), [§6.20] (observation wrap on outbound — preserved unchanged from 11r-c), [§7.3] / [D17] (adapters never write projections), [§8.2] / [D6] (no LLM SDK), [D7] (v1 schemas immutable; this prompt ADDS new v1 schemas, does not modify existing), [D8] (causation_id / correlation_id propagation), [D18] (Reminders + Tasks + Notes-checklists emit `list_item.*` taxonomy — this prompt closes that obligation for Reminders), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list — unchanged), memo 05 §5.3 (`list_item.*` canonical event taxonomy), §5.4 (Reminders bidirectional spec), memo 08 §3.4 (Cohort D 11b 4-way split), §5 (canonical builds add real implementations).

[VISION §"Real family"]: Apple Reminders is the highest-vision-value adapter for the family use case. The list-item migration is the first step toward [D22] dual-deployment (per-member bridge variant) which unlocks personal-data-bridge architecture per memo 04. The dual-emit preserves byte-for-byte production behavior for all 47 existing consumers while opening the new schema surface for 11f's lists projection.

## §2 Read first (required)

Per c-band:

**c1 (new schema file + registrations) — 4 files**

- `prompts/11b-i-apple-reminders-list-migration.md` — parent draft (the "what").
- `adminme/events/schemas/reminders.py` (on main) — **READ FILE CONTENTS** per Gate 0 strengthening. The existing `ReminderListItemSyncedV1` field set is the migration source; the new schemas mirror its shape EXCEPT for the additions noted in §1. Note `priority: Literal[0, 1, 5, 9]` (NOT `Literal[1, 5, 9]` — the parent draft erroneously listed three; the existing schema has four including `0`=none).
- `adminme/events/schemas/ingest.py` (on main) — `CalendarEventUpdatedV1`'s `field_updates: dict[str, Any]` shape is the pattern for `ListItemUpdatedV1`.
- `adminme/events/registry.py` (on main) — `registry.register(name, version, model)` API.

**c2 (adapter dual-emit) — 6 files**

- `packs/adapters/reminders/apple-eventkit/adapter.py` (on main, head + class section + `_write_one_outbound` + `_emit_inbound_synced` + `_apply_write_impl` + `_seen_inbound` cache region; use `sed -n` to scope reads — the file is 55.9 KB, do NOT full-read).
- `adminme/events/schemas/lists.py` (on the c1 branch; reference for field names + the `Literal[0,1,5,9]` priority enum).
- `adminme/events/schemas/reminders.py` (the existing emit's field-mapping is the source for the new emit's field-mapping).
- `adminme/lib/adapters/external_state_mirror_base.py` — `WriteOperation` / `WriteResult` shape (already verified at 11r-c authoring time).
- `adminme/lib/observation.py` — `outbound(...)` is invoked in `_apply_write_impl` unchanged; the new emit happens AFTER the `outbound()` call resolves (suppressed-path skip noted in §3 F-3).
- `adminme/adapters/base.py` — `AdapterContext` carries `event_log` / `correlation_id_factory` / `now_factory`; the new emits use these.

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/reminders/` (existing directory from 11r-c, contains `test_apple_eventkit_retrofit.py` — F-3 unique basename precedent).
- `tests/unit/events/schemas/` (existing directory; `test_ingest_schemas.py` precedent for the new `test_lists_schemas.py`).
- `adminme/events/schemas/lists.py` (test target — assertions against the four new models' field sets + `extra="forbid"` enforcement).
- `packs/adapters/reminders/apple-eventkit/adapter.py` (mockable surface for the dual-emit assertions).
- `prompts/_active/11a-gmail-real-outbound.md` (c3 test-file shape precedent: `_FakeEventLog`, `_seed_ctx`, monkeypatching `outbound` for observation-suppress assertions).

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py` (existing from 11r-c c4; F-3 unique-basename precedent for the new `test_apple_eventkit_dual_emit_pack_internal.py`).
- `tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py` (existing from 11r-c c4; extend with dual-emit end-to-end through the supervisor loader).
- `docs/build_log.md` (append the 11b-i entry per the 11a / 11r-c precedent shape).
- `prompts/_active/11a-gmail-real-outbound.md` (c4 BUILD_LOG entry precedent shape: Evidence + Carry-forward sections).

## §3 Reconciliation notes (carry-forward + 11b-i-specific)

- **F-1 (HARD/from canonical 11a's Gate 0 strengthening lesson): READ FILE CONTENTS for any cited schema file before authoring a c1 that registers events.** The c1 of 11b-i is a NEW schema file (`adminme/events/schemas/lists.py`), so name-collision risk is low — none of `list_item.added/updated/completed/deleted` exist on main today. But the c1 author MUST verify by grepping `registry.register("list_item.` across `adminme/events/schemas/*.py` before committing. The cycle #17 c1 collision happened because the Gate skipped this step.
- **F-2 (HARD/from canonical 11a): MAP existing field semantics; do not invent.** The four new v1 schemas mirror `ReminderListItemSyncedV1`'s field NAMES where the semantics carry over (e.g. `member_id`, `list_name`, `title`, `notes`, `due_at`, `priority`, `completed`, `last_modified_at`, `sync_cycle_id`). Renames (`list_identifier` → `list_external_id`, `remind_id` → `item_external_id`) are deliberate: align with the broader `list_item.*` family naming per memo 05 §5.3.
- **F-3 (HARD): observation-suppressed path skips the new emit too.** When `outbound()` returns `OutboundResult(suppressed=True)` on the outbound write, the existing `reminder.list_item.synced@v1` emit is also skipped (per 11r-c's `_apply_write_impl`). The new `list_item.added@v1` emit MUST follow the same rule: skip on suppressed. Consistency invariant — observation mode silences BOTH emits or NEITHER, never one but not the other. Pinned by a c3 test (`test_dual_emit_both_skipped_when_outbound_suppressed`).
- **F-4 (HARD/standing — from 10c-iii + 11r-a + 11r-b + 11r-c + 11a): pack-internal test basename uniqueness.** The new c4 pack-internal test MUST use a unique basename. Proposed: `test_apple_eventkit_dual_emit_pack_internal.py`. Confirmed unique against `test_apple_eventkit_adapter_pack_internal.py` + `test_apple_eventkit_external_state_mirror_retrofit_pack_internal.py` + `test_apple_eventkit_send_pack_internal.py` (canonical 11a c4 precedent — Gmail's pack-internal).
- **F-5 (SOFT/from canonical 11a): adapter.py diff size.** Anti-vision halt: c2 adapter.py diff ≤200 lines added (canonical 11a's c2 was +196). 11b-i's c2 is structurally lighter than 11a's c2 (dual-emit additions inside existing methods, not new methods + new helpers) — expected diff ~80-130 lines added.
- **F-6 (HARD/new for 11b-i): the parent draft erroneously listed priority enum as `Literal[1, 5, 9]`.** The on-main `ReminderListItemSyncedV1` declares `Literal[0, 1, 5, 9]` (EventKit's `0`=none / `1`=high / `5`=medium / `9`=low). The new schemas use the FOUR-value enum to match.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `AppleEventKitAdapter._write_one_outbound` orchestrates writes.** The existing method (post-11r-c) builds a `WriteOperation`, calls `await self.apply_write(operation)`, and on `WriteResult.applied=True` emits `reminder.list_item.synced@v1`. The new dual-emit happens ALONGSIDE that emit (NOT via a new method) — adds ~8-12 lines.
- **A-2: `_emit_inbound_synced` is the inbound emit site.** The existing method emits `reminder.list_item.synced@v1` with `direction="inbound"` per inbound poll cycle. The new dual-emit happens alongside.
- **A-3: `_seen_inbound` cache distinguishes first-observation vs subsequent.** The new inbound dual-emit reads this cache to decide which `list_item.*` event kind to emit. Verify the cache's structure at orientation; it's a `dict[str, ReminderSnapshot]` or similar mapped by `remind_id`.
- **A-4: `ctx.now_factory()` returns a `datetime`.** Both new and existing emits use this for `last_modified_at` / `occurred_at` fields. Existing schema uses `datetime` directly (Pydantic auto-serializes); new schemas mirror.
- **A-5: `registry.register` is idempotent only for IDENTICAL re-registration.** Per `registry.py:69` `RegistryError("duplicate registration: ...")`. Since the four new schemas are NEW names, no collision risk; pre-flight grep confirms.
- **A-6: `priority` enum is `Literal[0, 1, 5, 9]`.** Verified against on-main `reminders.py`. F-6 documents the parent draft's typo.

## §5 Objective

Add four new v1 event schemas at `adminme/events/schemas/lists.py` (`ListItemAddedV1`, `ListItemUpdatedV1`, `ListItemCompletedV1`, `ListItemDeletedV1`) and dual-emit them from the existing `AppleEventKitAdapter` alongside the existing `reminder.list_item.synced@v1` emissions on both outbound and inbound paths. No existing schema or emit is modified.

## §6 Out of scope

Same as parent draft §6:
- `list.*@v1` family (list-level CRUD)
- [D22] dual-deployment per-member bridge variant
- Deprecating `reminder.list_item.synced@v1`
- `lists` projection (11f)
- `list_item.deleted@v1` emission on inbound path
- Cross-adapter changes
- Field additions to existing v1 schemas
- OpenClaw plugin variants

## §7 Deliverables

**c1: `adminme/events/schemas/lists.py` (NEW, ~120 lines)** — four Pydantic v1 models + four `registry.register` calls.

Field set per the canonical mapping:

```python
class ListItemAddedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    list_external_id: str = Field(min_length=1)
    item_external_id: str = Field(min_length=1)
    list_name: str = Field(min_length=1)
    title: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)
    due_at: datetime | None = None
    priority: Literal[0, 1, 5, 9]
    completed: bool
    last_modified_at: datetime
    sync_cycle_id: str = Field(min_length=1)

class ListItemUpdatedV1(BaseModel):
    # same as Added + `field_updates: dict[str, Any]` per CalendarEventUpdatedV1 pattern
    ...

class ListItemCompletedV1(BaseModel):
    # same as Added; `completed=True` always (validated)
    ...

class ListItemDeletedV1(BaseModel):
    model_config = {"extra": "forbid"}
    member_id: str = Field(min_length=1)
    list_external_id: str = Field(min_length=1)
    item_external_id: str = Field(min_length=1)
    deleted_at: datetime
    sync_cycle_id: str = Field(min_length=1)

registry.register("list_item.added", 1, ListItemAddedV1)
registry.register("list_item.updated", 1, ListItemUpdatedV1)
registry.register("list_item.completed", 1, ListItemCompletedV1)
registry.register("list_item.deleted", 1, ListItemDeletedV1)
```

c1 verification: `python -c "from adminme.events.registry import registry; from adminme.events.schemas.lists import *; assert registry.get('list_item.added', 1) is ListItemAddedV1; ... (4 lines)"`.

**c2: `packs/adapters/reminders/apple-eventkit/adapter.py`** (≤200 lines added per F-5; expected ~80-130 lines) — three changes:

1. **Import**: add `from adminme.events.schemas.lists import ListItemAddedV1, ListItemUpdatedV1, ListItemCompletedV1` at the existing imports section. (`ListItemDeletedV1` imported but not used by this prompt's emit paths; flagged with `# noqa: F401` or imported lazily in tests only.)
2. **Outbound dual-emit in `_write_one_outbound`** (after the existing `reminder.list_item.synced` emit, only on `WriteResult.applied=True` non-suppressed path):
   ```python
   if write_result.applied and not write_result.suppressed:
       self._append(ctx=ctx, event_type="reminder.list_item.synced", payload={...existing...})
       # NEW: dual-emit list_item.added@v1
       self._append(ctx=ctx, event_type="list_item.added", payload={
           "member_id": member_id,
           "list_external_id": list_identifier,
           "item_external_id": write_result.external_id,
           ...field mapping per the canonical schema...
       })
   ```
3. **Inbound dual-emit in `_emit_inbound_synced`** (alongside existing emit; derive event kind from `_seen_inbound` cache state):
   ```python
   self._append(ctx=ctx, event_type="reminder.list_item.synced", payload={...existing...})
   # NEW: dual-emit list_item.*@v1 by change kind
   if remind_id not in self._seen_inbound:
       new_event = "list_item.added"
   elif self._seen_inbound[remind_id].completed != reminder.completed and reminder.completed:
       new_event = "list_item.completed"
   else:
       new_event = "list_item.updated"
   self._append(ctx=ctx, event_type=new_event, payload={...field mapping; for "updated" include `field_updates`...})
   ```

All other adapter logic preserved byte-for-byte: `start`, `stop`, `status`, `apply_write` gate, `_apply_write_impl`'s observation wrap, the inbound poll, the never-sync filter, the cursor-advance milestone gating.

**c3: tests (≥18 new + 2 extension)**:

- `tests/unit/events/schemas/test_lists_schemas.py` (NEW, ~6 tests): field-set validation per model; `extra="forbid"` rejection of extras; `Literal[0,1,5,9]` priority enum coverage; registry round-trip for all four.
- `tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py` (NEW, F-4 unique basename, ~12-14 tests): outbound dual-emit happy path (write succeeds → both emits fire with same causation_id); outbound suppressed (observation-on) skips BOTH emits per F-3; inbound dual-emit happy path (first observation → `list_item.added`); inbound dual-emit on completion-flip (`completed: false → true` → `list_item.completed`); inbound dual-emit on field-update (other change → `list_item.updated` with `field_updates`); payload field-mapping fidelity (`MessagingSentV1`-style `model_validate` against the new schemas); priority enum boundary cases; member_id propagation; sync_cycle_id matches between old and new emits for the same inbound cycle.

**c4: pack-internal + integration + BUILD_LOG**:

1. `packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_emit_pack_internal.py` (NEW, F-4 unique basename, ~3 tests):
   - `test_dual_emit_imports_match_lists_schemas` — adapter imports from `adminme.events.schemas.lists`.
   - `test_outbound_dual_emit_payloads_validate_against_on_main_schemas` — both `reminder.list_item.synced@v1` and `list_item.added@v1` payloads validate via `model_validate`.
   - `test_inbound_dual_emit_kinds_derive_from_seen_inbound_cache_state` — pack-internal coverage of the cache-state → event-kind mapping.

2. `tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py` (EXTEND, ~2 new tests):
   - `test_supervisor_loaded_adapter_emits_both_event_families_on_outbound` — full end-to-end through the supervisor loader.
   - `test_supervisor_loaded_adapter_emits_both_event_families_on_inbound` — full end-to-end inbound.

3. `docs/build_log.md` — append the 11b-i entry per the 11a c4 template:
   - **Outcome**: MERGED (placeholder PR # / commit sha — Partner patches in the partner-state-snapshot PR).
   - **Evidence**: test counts; suite tally; `verify_invariants.sh` exit 0; `adminme adapters validate ...` exit 0.
   - **Carry-forward for 11b-i-β (dual-deployment per [D22]) or 11c-i bridge daemon**: dual-emit landed; bridge variant is the next layer.
   - **Schema-immutability note**: `reminder.list_item.synced@v1` unchanged; 47 consumers preserve byte-for-byte; deprecation deferred to a future prompt that migrates consumers first.

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11b-i commit <N>: <imperative summary>` exactly.

- **c1: `phase 11b-i commit 1: register list_item.*@v1 schemas`** — `adminme/events/schemas/lists.py` (NEW, ~120 lines).
- **c2: `phase 11b-i commit 2: AppleEventKitAdapter dual-emit list_item.*@v1`** — `packs/adapters/reminders/apple-eventkit/adapter.py` (≤200 lines added; F-5 anti-vision halt).
- **c3: `phase 11b-i commit 3: framework-level dual-emit + schemas tests`** — `tests/unit/events/schemas/test_lists_schemas.py` + `tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py`.
- **c4: `phase 11b-i commit 4: integration + pack-internal dual-emit tests + BUILD_LOG`** — pack-internal (NEW) + integration extension + BUILD_LOG entry.

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch; PRs open with `base=main` per PR #106 sidecar. Ghost-conflict risk: LOW. c1 file scope (`adminme/events/schemas/lists.py` NEW) is non-overlapping with c2 (`adapter.py`), c3 (new test files), c4 (new pack-internal + extended integration + BUILD_LOG). 11r-c precedent: zero ghost conflicts on similar scope. If a c-band lands dirty, Chrome MCP JS-regex resolution per the established pattern.

Test floor: ≥18 new tests in c3 + ~3 pack-internal in c4 + ~2 integration extensions in c4 + 6 schemas tests in c3 = **≥29 total**.

## §9 Verification

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.lists import ListItemAddedV1, ListItemUpdatedV1, ListItemCompletedV1, ListItemDeletedV1; assert registry.get('list_item.added', 1) is ListItemAddedV1; assert registry.get('list_item.updated', 1) is ListItemUpdatedV1; assert registry.get('list_item.completed', 1) is ListItemCompletedV1; assert registry.get('list_item.deleted', 1) is ListItemDeletedV1; print('all 4 list_item.*@v1 schemas verified registered ✓')"
poetry run mypy adminme/events/schemas/lists.py 2>&1 | tail -5
poetry run ruff check adminme/events/schemas/lists.py 2>&1 | tail -3
```

**c2**:
```bash
poetry run python -c "from packs.adapters.reminders.apple_eventkit.adapter import AppleEventKitAdapter; from adminme.events.schemas.lists import ListItemAddedV1; print('imports ok ✓')"
poetry run mypy adminme/ packs/adapters/reminders/apple-eventkit/ 2>&1 | tail -5
```

**c3**:
```bash
poetry run pytest tests/unit/events/schemas/test_lists_schemas.py tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py -v
# Expected: ≥18 passed
```

**c4**:
```bash
poetry run pytest packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_emit_pack_internal.py tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py -v
poetry run adminme adapters validate packs/adapters/reminders/apple-eventkit
# Expected: exit 0; NO advisory_pending_retrofit; NO errors
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When all four `list_item.*@v1` schemas are registered AND `AppleEventKitAdapter` dual-emits the new family alongside the existing `reminder.list_item.synced@v1` on both outbound and inbound paths AND no projection has been touched (per [§7.3, D17]) AND the existing `reminder.list_item.synced@v1` schema is UNCHANGED on main (per [D7]), c4 is done. Open the PR per the standard four-commit structure. Do NOT proceed to 11b-ii or 11b-i-β (dual-deployment) without operator review.

After merge: Partner runs three-job QC, opens the batched partner-state-snapshot PR (covering post-11a + post-11b-i) via Chrome MCP `file_upload` for the 100KB+ `partner_handoff.md` + `build_log.md`.
