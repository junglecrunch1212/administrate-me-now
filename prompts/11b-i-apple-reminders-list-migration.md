# Prompt 11b-i: Apple Reminders list-item event-family migration (Cohort D step 2-α)

**Phase:** Phase A — L1 Cohort D step 2 (first of the canonical 11b cohort per `docs/08-architecture-amendment-adapter-rebase.md` §3.4). First sub-prompt of the four-way 11b split: **11b-i Apple Reminders dual-deployment**, scoped narrow for this prompt — schema migration + dual-emit only; the [D22] dual-deployment infrastructure (per-member bridge variant) defers to a follow-up 11b-i sub-prompt or rolls into 11c-i bridge daemon authoring.

**Depends on:** Cohort D step 1 closed (canonical 11a — PRs #154-#157 merged, main `ebfa56ed`). Cohort C closed (11r-a/b/c — Apple Reminders Cat-B retrofit is on main via 11r-c, PRs #145-#148).

**Estimated duration:** ~3-3.5 hours. **NOT a pre-split candidate** at this narrow scope — schema migration to `list_item.*@v1` + dual-emit in the existing AppleEventKitAdapter. The `list.*@v1` schemas + dual-deployment infra are explicitly deferred (Out of scope).

**Stop condition:** Four new v1 schemas registered (`list_item.added@v1`, `list_item.updated@v1`, `list_item.completed@v1`, `list_item.deleted@v1`) at `adminme/events/schemas/lists.py` (NEW file). AppleEventKitAdapter emits the new `list_item.*@v1` events ALONGSIDE the existing `reminder.list_item.synced@v1` events (dual-emit) on both the outbound write path (`_apply_write_impl`) and the inbound sync path (`_emit_inbound_synced`). The existing `reminder.list_item.synced@v1` schema is UNCHANGED per [D7] (47 production consumers preserve byte-for-byte). `adminme adapters validate packs/adapters/reminders/apple-eventkit` exits 0.

---

## Read first

1. `docs/08-architecture-amendment-adapter-rebase.md` §1.3 (Apple Reminders TODO list — items (a)-(e); this prompt closes (b) only; (a) closed via 11r-c; (c)+(d)+(e) deferred), §3.4 (Cohort D 4-way 11b split disposition).
2. `docs/05-architecture-amendment-personal-data-layer.md` §5.3 (`list.*` / `list_item.*` canonical event taxonomy per [D18]), §5.4 (Reminders bidirectional spec).
3. `adminme/events/schemas/ingest.py` (on main, head + registrations at bottom) — pattern reference for new v1 schema registrations. The existing `MessagingReceivedV1` / `MessagingSentV1` shape demonstrates `model_config = {"extra": "forbid"}` + `registry.register(...)` calls at module-load.
4. `adminme/events/schemas/reminders.py` (on main) — the EXISTING `ReminderListItemSyncedV1` schema. **DO NOT MODIFY** per [D7] immutability. The new `list_item.*@v1` events live in a separate file (`lists.py`) so module-level registrations don't entangle.
5. `adminme/events/registry.py` (on main) — `registry.register(name, version, model)` / `registry.get(name, version)` API.
6. `packs/adapters/reminders/apple-eventkit/adapter.py` (on main) — head + class section + `_emit_inbound_synced` + `_apply_write_impl` + `_write_one_outbound`. The dual-emit additions live alongside these methods, not as replacements.
7. `prompts/_active/11a-gmail-real-outbound.md` (on main) — quality-bar reference for the v4 refactor. Canonical-build narrow-scope pattern (verify-only c1; targeted c2; framework tests c3; pack-internal + integration + BUILD_LOG c4).
8. `prompts/_active/11r-c-retrofit-reminders-to-external-state-mirror-adapter.md` (on main) — quality-bar reference for the Cat-B retrofit pattern.

## Operating context

The 11r-c retrofit (Cohort C step 3, PRs #145-#148) brought AppleEventKitAdapter onto the canonical Cat-B `ExternalStateMirrorAdapter` ABC with framework-conformant inheritance + manifest extensions + `_apply_write_impl` observation wrap. The retrofit deliberately deferred the [D18] event-taxonomy migration per memo 08 §5 ("inheritance + manifest only, not a rewrite"). 11b-i closes that deferred work.

What 11b-i adds is the **new `list_item.*@v1` event family** that downstream Cohort D consumers will subscribe to:

- The `lists` projection (11f) — depends on `list_item.*` emissions per memo 08 §3.4.
- Future canonical 11b sub-prompts for Apple Calendar dual / Google Calendar / Google Tasks will follow the same `list.*` / `calendar_event.*` taxonomy pattern.

What 11b-i does NOT change:

- The existing `reminder.list_item.synced@v1` schema stays immutable per [D7]. 47 production consumers (the test count from 11a's reconciliation notes — adjust to the actual on-main consumer count at Executor orientation) keep working byte-for-byte.
- Dual-emit means the adapter emits BOTH the old and new event families from the same source data. Downstream consumers migrate at their own pace; once all consumers have migrated, a future prompt may deprecate `reminder.list_item.synced@v1` per [D7] deprecation procedure.
- The bridge variant per [D22] is NOT shipped here. The adapter remains single-deployment central. The bridge variant ships in a separate 11b-i sub-prompt (or rolls into 11c-i).
- The `list.*@v1` family (list-added / list-updated / list-deleted at the LIST level, not item level) is NOT shipped here. EKReminderList CRUD is a write-capability not currently declared in `AppleEventKitAdapter.write_capabilities` (`["create_reminder"]` only per 11r-c); adding list-level emissions requires a new write_capability declaration that 11r-c explicitly punted on. List-level events come when list-CRUD ships.

## Objective

Add four new v1 event schemas at `adminme/events/schemas/lists.py` (NEW file) and dual-emit them from the existing AppleEventKitAdapter alongside the existing `reminder.list_item.synced@v1` events. The dual-emit:

- On the outbound write path (`_apply_write_impl` → `_write_one_outbound`): after each successful EKReminder save, emit ONE `list_item.added@v1` event AND continue to emit `reminder.list_item.synced@v1` with `direction="outbound"` per the existing pattern.
- On the inbound sync path (`_emit_inbound_synced`): when an inbound EKReminder is observed via the poll loop, emit ONE `list_item.*@v1` event based on the change kind (added / updated / completed / deleted, derived from the existing `reminder` fields like `is_completed` toggling + `_seen_inbound` first-observation vs subsequent-update logic) AND continue to emit `reminder.list_item.synced@v1` with `direction="inbound"`.
- Both event families carry causation/correlation discipline per [D8] — the same `causation_id` (source event id for outbound; the inbound poll cycle id for inbound) propagates to both.

## Out of scope

- **`list.*@v1` family (list-level CRUD)** — EKReminderList add/rename/delete emissions. Defer until `AppleEventKitAdapter.write_capabilities` declares `create_list` / `update_list` / `delete_list` (current declaration is `["create_reminder"]` only). Add to a follow-up prompt.
- **Dual-deployment per [D22]** (per-member bridge variant of AppleEventKitAdapter on bridge Mac Mini) — defer to a follow-up 11b-i sub-prompt or roll into 11c-i bridge daemon authoring.
- **Deprecating `reminder.list_item.synced@v1`** — dual-emit means the OLD event family stays live. Deprecation requires a separate prompt that migrates all 47 consumers off the old schema first.
- **The `lists` projection (11f)** — that's the CONSUMER; this prompt emits the events the consumer reads. No projection writes in this prompt per [§7.3, D17].
- **Schema fields beyond the canonical 11b-i set** — `list_item.added/updated/completed/deleted` carry `{list_external_id, item_external_id, title, is_completed, due_at, priority, member_id, occurred_at}` per memo 05 §5.3. Extra Gmail-specific or Apple-specific fields belong in separate audit events (e.g. the `external.sent` event observation.py already emits).
- **Other adapter modifications** — Gmail (11a) is preserved byte-for-byte; Plaid (11r-b) is preserved byte-for-byte; no other Cat-A/B/C adapter touched.
- **`messaging.sent@v1` field changes** — out of scope per canonical 11a's F-6 lesson (MAP DON'T REGISTER; v1 schemas immutable per [D7]). Same rule applies to `reminder.list_item.synced@v1`.
- **OpenClaw plugin variants** (BlueBubbles, Telegram, Discord) — prompt 12.

## Approach (architectural sketch)

The dual-emit lifts from "single-emit" to "dual-emit" by:

1. **New schema file** `adminme/events/schemas/lists.py`:
   - `ListItemAddedV1(BaseModel)` with `model_config = {"extra": "forbid"}`; fields `{list_external_id: str, item_external_id: str, title: str, is_completed: bool, due_at: str | None, priority: Literal[1,5,9] | None, member_id: str, occurred_at: str}`.
   - `ListItemUpdatedV1` — same shape + `field_updates: dict[str, Any]` per `CalendarEventUpdatedV1` precedent.
   - `ListItemCompletedV1` — same shape; emitted when an existing item flips `is_completed: false → true`.
   - `ListItemDeletedV1` — minimal shape `{list_external_id, item_external_id, member_id, deleted_at}`.
   - `registry.register("list_item.added", 1, ListItemAddedV1)` (+ 3 more) at bottom of module.
2. **Dual-emit in `_apply_write_impl`**: after the existing `reminder.list_item.synced` emit, additionally emit `list_item.added@v1` with field mapping from the EKReminder save result. Same causation_id; same correlation_id; same occurred_at timestamp from `ctx.now_factory()`.
3. **Dual-emit in `_emit_inbound_synced`**: derive the `list_item.*` event kind from the change semantics — first-observation → `list_item.added`; subsequent observation with `is_completed: false → true` → `list_item.completed`; subsequent observation with other field changes → `list_item.updated`; absent from inbound vs prior cycle (would require the never-sync filter's negative-set logic) → `list_item.deleted` (deferred — `list_item.deleted` on inbound is harder than the other three; consider scope-limiting inbound dual-emit to added/updated/completed only and noting deleted as a follow-up if time-budget tightens).
4. **No change to the existing `reminder.list_item.synced@v1` emissions** — both adapter emit sites continue to fire the old event in parallel.

## Deliverables

### New schema file
- `adminme/events/schemas/lists.py` — 4 Pydantic v1 models with `extra="forbid"` + 4 `registry.register` calls at module-load. ~80-100 lines.

### Adapter dual-emit
- `packs/adapters/reminders/apple-eventkit/adapter.py` — dual-emit additions in `_apply_write_impl` (outbound) + `_emit_inbound_synced` (inbound). NO existing emit removed. NO existing field changed. ~80-120 lines added (target ceiling: ≤200 per the F-5 11a precedent).

### Tests
- `tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py` (NEW, F-3 unique basename) — ~12-15 framework tests covering: schema registration; dual-emit happy paths (outbound + inbound, each event kind); field-mapping fidelity (Pydantic `model_validate` against `list_item.*V1` for each emit); causation_id propagation; observation-mode suppression suppresses both old AND new emit (consistency).
- `tests/unit/events/schemas/test_lists_schemas.py` (NEW) — ~6 tests pinning the schema field set, `extra="forbid"` rejection of extras, the `Literal[1,5,9]` priority enum, registry round-trip.
- `packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_emit_pack_internal.py` (NEW, F-3 unique basename, ~3 tests) — pack-internal coverage: dual-emit invariants + payload validation against on-main schemas.
- `tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py` (EXTEND, ~2 new tests) — supervisor-loaded adapter emits both event families end-to-end.

Test floor: ≥18 new tests + ≥2 existing-test extensions.

### Documentation
- `docs/build_log.md` — append the 11b-i entry per the c4 template. Include "old-family preservation" subsection: `reminder.list_item.synced@v1` unchanged on main; all 47 (or actual count) consumers continue to operate; dual-emit gives downstream consumers a migration window.

## Verification (per c-band; full set runs at c4)

```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.lists import ListItemAddedV1, ListItemUpdatedV1, ListItemCompletedV1, ListItemDeletedV1; assert registry.get('list_item.added', 1) is ListItemAddedV1; assert registry.get('list_item.updated', 1) is ListItemUpdatedV1; assert registry.get('list_item.completed', 1) is ListItemCompletedV1; assert registry.get('list_item.deleted', 1) is ListItemDeletedV1; print('all 4 list_item.*@v1 schemas registered')"

poetry run pytest tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py tests/unit/events/schemas/test_lists_schemas.py packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_emit_pack_internal.py tests/integration/test_adapter_apple_eventkit_supervisor_retrofit.py -v

poetry run mypy adminme/ packs/adapters/reminders/apple-eventkit/ 2>&1 | tail -5
poetry run ruff check adminme/ packs/adapters/reminders/apple-eventkit/ 2>&1 | tail -5

poetry run adminme adapters validate packs/adapters/reminders/apple-eventkit
# Expected: exit 0; NO advisory_pending_retrofit; NO errors

bash scripts/verify_invariants.sh
# Expected: exit 0
```

## Stop

When the four new `list_item.*@v1` schemas are registered AND AppleEventKitAdapter dual-emits the new family alongside the existing `reminder.list_item.synced@v1` AND the supervisor-loaded adapter emits both families end-to-end AND no projection has been touched (per [§7.3, D17]), c4 is done.

After merge: Partner runs three-job QC, opens the batched partner-state-snapshot PR (covering both post-11a + post-11b-i). The follow-up 11b-i-β prompt (dual-deployment per [D22]) or 11c-i bridge daemon prompt picks up from this merge.

## What this prompt is NOT

This is the **parent draft** (the "what" — schema migration + dual-emit). It is NOT the v4 refactor (the "how" — per-commit deliverables + verification commands + commit structure). The v4 refactor PR follows after this merges + a pre-authorship gate runs against current main HEAD.

No constitutional doc edits. No PROMPT_SEQUENCE rows touched (11b rows in the sequence file are already correct per memo 08 §4 followups note). No `_active/` v4 refactor file yet.
