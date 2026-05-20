# Prompt 11b-ii-β-α: Apple Calendar fresh Cat-B central pack (Z1.5)

**Phase:** L1 / Cohort D — first of the two-step 11b-ii-β split per `docs/11-split-memo-11b-ii-beta.md`. Fresh Apple-side Cat-B calendaring adapter, **central deployment only** (dual-deployment is 11b-ii-β-β). Parent split memos: `docs/04-split-memo-11b-ii.md` §2 (β scope) + `docs/11-split-memo-11b-ii-beta.md` §2 (β-α scope).
**Branch:** `claude/11b-ii-beta-alpha-c<N>` per c-band (Executor's harness assigns).
**Stop condition:** NEW pack `packs/adapters/calendaring/apple-calendar/` with `pack.yaml` + `config.schema.json` + `config.example.yaml` + `adapter.py` declaring `class AppleCalendarAdapter(ExternalStateMirrorAdapter)`, `family="calendaring"`, `capabilities=[Cat-B central]`, `write_capabilities=["create_event"]`. Inbound poll over `EKEventStore` (PyObjC) emits the existing `calendar.event_added@v1` (no new schema). Outbound `_apply_write_impl` for `create_event` saves an `EKEvent` wrapped through `adminme.lib.observation.outbound(...)`. `adminme adapters validate packs/adapters/calendaring/apple-calendar` exits 0. `verify_invariants.sh` exits 0.
**Depends-on:** 11b-iii closed (PRs #173-#176, main `04cdee6d` — the `google-calendar` fresh-Cat-B template); 11r-c closed (`packs/adapters/reminders/apple-eventkit/` — the EKEventStore/PyObjC precedent); `docs/11-split-memo-11b-ii-beta.md` merged (PR #223, main `29bea8cd`).
**Quality-bar reference:** `prompts/_active/11b-iii-google-calendar-central.md` (the fresh-Cat-B central template — β-α mirrors it almost exactly, Google Calendar v3 API → Apple EventKit) + `packs/adapters/reminders/apple-eventkit/adapter.py` (the EKEventStore/PyObjC binding precedent — `EKReminder`; β-α uses `EKEvent`).
**Estimated duration:** c1 ~10-15 min (verify) · c2 ~90-120 min (fresh adapter ≤500 lines) · c3 ~60 min (≥15 tests) · c4 ~60 min (pack-internal + integration + BUILD_LOG) · total ~4-4.5 hrs wall.

---

## §1 Behavioral grounding

11b-ii-β-α is the **first fresh Apple-side Cat-B calendaring adapter**. It mirrors 11b-iii (Google Calendar fresh Cat-B central) one-for-one in structure, swapping the Google Calendar v3 API surface for Apple EventKit (`EKEventStore` via PyObjC). It is **central deployment only** — the dual-deployment layer (`deployment_target` branching + bridge connector-pack) is 11b-ii-β-β scope per `docs/11-split-memo-11b-ii-beta.md` §2.

Three load-bearing items:

1. **Fresh pack structure** — `pack.yaml` declares one Cat-B capability with `runtime: central` + `write_capabilities=["create_event"]` per [UT-27] asymmetric (only what is implemented) + `sharing_model_discriminator: owner_member_id` per memo 05 §2.5 + `dedup_strategy: {kind: external_id}` (EKEvent's stable `eventIdentifier`) + `sensitivity_default: normal` + `observation_mode_required: true`. `family: calendaring`. Pack id `adapter:calendaring-apple-calendar`. NO top-level `subkind` / `deployment_targets` fields — those are β-β scope (F-7).

2. **Apple EventKit surface** — `EKEventStore` via PyObjC, the same binding family the Apple Reminders adapter (`packs/adapters/reminders/apple-eventkit/adapter.py`) already uses for `EKReminder`. β-α uses `EKEntityTypeEvent`. Inbound poll: build a date-window predicate (`predicateForEventsWithStartDate_endDate_calendars_`), fetch via `eventsMatchingPredicate_`. Outbound write: `EKEvent.eventWithEventStore_`, set properties, `saveEvent_span_error_(event, EKSpanThisEvent, ...)` routed through `await observation_outbound(...)`. Error classification mirrors the Apple Reminders adapter's EventKit-error taxonomy (access-denied → fatal `auth_failed`; store-save failure → transient).

3. **Map onto existing `calendar.event_added@v1`** — schema field set is `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` per `adminme/events/schemas/ingest.py::CalendarEventAddedV1`. Apple Calendar inbound emits: `source="apple_calendar"`, `external_event_id=event.eventIdentifier()`, `calendar_id=config["calendar_identifier"]`, `summary=event.title() or ""`, `start`/`end` as ISO-8601 from `event.startDate()`/`event.endDate()`, `location=event.location()`, `attendees=[...]` mapped from `EKParticipant` objects (or `[]`), `body=event.notes()`. NO new schema registered per [D7] + the canonical "MAP DON'T REGISTER" rule (11a F-6 / 11b-iii F-2).

OUT OF SCOPE (β-α; see §6): dual-deployment surface (β-β); `calendar.event_updated@v1` / `calendar.event_deleted@v1` emissions; other write_capabilities; EventKit change notifications; real credential / permission-grant flow; new schemas; new `family` Literal values.

Citations: [§6 invariant 10] (sensitivity floor `normal`), [§6.20] (observation wrap on outbound), [§7.3, D17] (adapters never write projections), [§8.2, D6] (no LLM SDK), [D7] (schemas v1 immutable — USE existing), [D8] (causation/correlation propagation), [D19] (Cat-B taxonomy; runtime substrate orthogonal to category), [D22] (Apple Calendar dual-deployment lineage — β-α lands the central half, β-β the dual), [PM-27] (constructor injection with no-op defaults), [PM-34] (5 mutually exclusive ABCs — single-ABC subclass, no multi-inheritance), [PM-35] (capabilities-as-list), [UT-27] (asymmetric write_capabilities). Memo 05 §2.5, §3.1, §6 (Apple Calendar). [VISION §"Real family"]: Apple Calendar is the family-of-four's primary household calendar surface; β-α establishes the central pack that β-β extends to per-member bridges.

## §2 Read first (required)

Per c-band:

**c1 (verify-only no-op) — 3 files**

- `docs/11-split-memo-11b-ii-beta.md` §2 (β-α scope — canonical contract, main `29bea8cd`).
- `adminme/events/schemas/ingest.py` (on main) — **READ CONTENTS**: the `CalendarEventAddedV1` field set must match c1's verify assertion + c2's emit mapping.
- `adminme/events/registry.py` (on main) — the `registry.get(name, version)` API.

**c2 (fresh adapter pack) — 6 files**

- `prompts/_active/11b-iii-google-calendar-central.md` (on main) — the fresh-Cat-B template β-α mirrors structurally.
- `packs/adapters/calendaring/google-calendar/adapter.py` (on main) — fresh-Cat-B `adapter.py` reference shape (poll loop / `_apply_write_impl` / `_classify_*_error` / `_emit_*` helpers). β-α mirrors this, EventKit-substituted.
- `packs/adapters/reminders/apple-eventkit/adapter.py` (on main, **READ HEAD + the EKEventStore sections via `sed`**; the file is large — do NOT full-read) — the EKEventStore/PyObjC binding precedent (`EKReminder`; β-α uses `EKEvent`).
- `adminme/lib/adapters/external_state_mirror_base.py` (on main) — the Cat-B ABC: `ExternalStateMirrorAdapter`, `WriteOperation` / `WriteResult` / `WriteCapabilityError` / `ChangeEnvelope`, the `apply_write` gate, `_apply_write_impl` + `_on_external_change` abstract methods.
- `adminme/lib/observation.py` (on main) — `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier) -> OutboundResult`.
- `adminme/adapters/base.py` (on main) — `AdapterContext`, `AdapterStatus`, `SensitivityFloor`.

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/calendaring/test_google_calendar.py` (on main) — the Cat-B calendaring test-file shape precedent (`_FakeEventLog`, fake-service patterns). `tests/unit/adapters/calendaring/__init__.py` already exists (11b-iii) — do NOT recreate it.
- `tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py` (on main) — EKEventStore-fake test precedent.
- `adminme/events/schemas/ingest.py` :: `CalendarEventAddedV1` — the field-mapping assertion target.
- `packs/adapters/calendaring/apple-calendar/adapter.py` (on the c2 branch) — the class under test.
- `prompts/_active/11b-iii-google-calendar-central.md` §7 c3 — the most-recent fresh-Cat-B c3 deliverable shape.

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py` (on main) — pack-internal precedent.
- `tests/integration/test_adapter_google_calendar_supervisor.py` (on main) — integration sibling precedent.
- `docs/build_log.md` (on main, **append the β-α entry**; the file is ~400 KB — if `push_files` hits the inline budget, the Executor stops and Partner re-fires the c4 step via Chrome MCP `file_upload` per Partner memory).
- `prompts/_active/11b-iii-google-calendar-central.md` §7 c4 — the BUILD_LOG entry shape precedent.

## §3 Reconciliation notes (HARD/SOFT findings)

- **F-1 (HARD/standing from cycle #17): READ FILE CONTENTS for the schema file cited in c1.** The c1 verify asserts `registry.get("calendar.event_added", 1) is CalendarEventAddedV1` against the on-main schema. Grep `registry.register("calendar.event_added"` in `ingest.py` BEFORE authoring c2 — confirm exactly one canonical registration (no plugin shadow).
- **F-2 (HARD/from 11a + 11b-iii): MAP existing field semantics; do not invent schemas.** Apple Calendar inbound emits the existing `CalendarEventAddedV1` field set BYTE-FOR-BYTE. No new schemas, no field additions. The schema is `model_config = {"extra": "forbid"}` — an extra payload key fails validation.
- **F-3 (HARD/from 11b-i + 11b-iii): the observation-suppressed outbound path skips the emit too.** When `observation_outbound(...)` returns `OutboundResult(suppressed=True)` inside `_apply_write_impl`, the adapter returns `WriteResult(applied=False, suppressed=True)` and emits NOTHING externally-visible. The inbound poll-cycle `calendar.event_added@v1` emit is independent and is NOT observation-gated (only OUTBOUND side effects suppress per [§6.20]).
- **F-4 (HARD/standing): test basename uniqueness.** Proposed basenames `test_apple_calendar.py` (c3 framework, in `tests/unit/adapters/calendaring/`), `test_apple_calendar_pack_internal.py` (c4 pack-internal), `test_adapter_apple_calendar_supervisor.py` (c4 integration) — all unique against existing Google Calendar / Apple Reminders test basenames. Confirm at orientation.
- **F-5 (SOFT): `adapter.py` diff size — anti-vision halt at ≤500 lines.** Fresh Cat-B is a full new adapter; ≤500-line ceiling per 11b-iii F-5. The EKEventStore PyObjC inbound surface may run heavier than `googleapiclient`; if `adapter.py` would exceed 500 lines, STOP and report (the `docs/11-split-memo-11b-ii-beta.md` §4 watch flag).
- **F-6 (HARD/from 11a c4): NO empty `__init__.py` in the pack-internal tests dir.** Do NOT create `packs/adapters/calendaring/apple-calendar/tests/__init__.py`. (`tests/unit/adapters/calendaring/__init__.py` already exists from 11b-iii — do NOT recreate it.)
- **F-7 (HARD/β-α central-only): NO dual-deployment surface.** β-α does NOT add `deployment_target` config, top-level `subkind` / `deployment_targets` manifest fields, any `_bridge_sink`, or any bridge connector-pack. The adapter's emit path is `ctx.event_log.append_sync(...)` only. Dual-deployment is 11b-ii-β-β; introducing any of it here is a scope extension across the §6 out-of-scope line.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `CalendarEventAddedV1` field set on main.** Verify `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` against `adminme/events/schemas/ingest.py` (depth-read at orientation).
- **A-2: EKEventStore PyObjC import + access surface.** The Apple Reminders adapter at `packs/adapters/reminders/apple-eventkit/adapter.py` already imports the EventKit binding for `EKReminder`; β-α uses the same store with `EKEntityTypeEvent`. Confirm the import path + the `EKEvent` accessor names (`eventIdentifier`, `title`, `startDate`, `endDate`, `location`, `notes`, `attendees`) at orientation against the Apple Reminders precedent.
- **A-3: predicate-query chain.** `predicateForEventsWithStartDate_endDate_calendars_` + `eventsMatchingPredicate_`. Confirm against the Apple Reminders adapter's analogous `predicateForReminders*` pattern.
- **A-4: `_apply_write_impl` shape from the Cat-B ABC.** `async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult`. Already confirmed via the Cat-B ABC; `apply_write` gates on `write_capabilities` membership before dispatch.
- **A-5: `observation_outbound` import.** `from adminme.lib.observation import outbound as observation_outbound`; returns `OutboundResult(suppressed, result)`. Mirror 11b-iii's seam-slot pattern — three optional `observation_*` slots default `None`; a Phase-A direct path runs when the slots are unset.
- **A-6: `_seen_inbound` dedupe cache.** `dict[str, datetime]` keyed by `EKEvent.eventIdentifier()`. Same pattern as Google Calendar's `event["id"]` cache.

## §5 Objective

Build `packs/adapters/calendaring/apple-calendar/` (fresh Cat-B central pack) implementing an inbound `EKEventStore` poll + an outbound `create_event` for Apple Calendar via EventKit/PyObjC. Maps onto the existing `calendar.event_added@v1` for inbound; observation-wrapped real `_apply_write_impl` for outbound `create_event`. Central deployment only. No new schemas.

## §6 Out of scope

- Dual-deployment: the `deployment_target` config enum, the top-level `subkind` / `deployment_targets` manifest fields, the `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` connector-pack — ALL 11b-ii-β-β scope per `docs/11-split-memo-11b-ii-beta.md` §2.
- `calendar.event_updated@v1` / `calendar.event_deleted@v1` emissions.
- `update_event` / `delete_event` write capabilities.
- EventKit change-notification subscriptions.
- Real iCloud credential / EKEventStore permission-grant flow (Phase B + bootstrap §10).
- New event schemas; new `family` Literal values.
- Any cross-adapter or cross-pack changes.

## §7 Deliverables

**c1: VERIFY-ONLY NO-OP** — `adminme/events/schemas/ingest.py` NOT modified.

```python
from adminme.events.registry import registry
from adminme.events.schemas.ingest import CalendarEventAddedV1
assert registry.get("calendar.event_added", 1) is CalendarEventAddedV1
```

The commit body documents the no-op. Net production-code diff: 0 lines.

**c2: NEW pack files** (`adapter.py` ≤500 lines per F-5):

1. `packs/adapters/calendaring/apple-calendar/pack.yaml` (~40 lines): Cat-B manifest mirroring `packs/adapters/calendaring/google-calendar/pack.yaml` — `id: adapter:calendaring-apple-calendar`, `family: calendaring`, `capabilities: [{kind: Cat-B, runtime: central, event_families: [calendar.event_added]}]`, `write_capabilities: [create_event]`, `sharing_model_discriminator: owner_member_id`, `dedup_strategy: {kind: external_id}`, `sensitivity_default: normal`, `observation_mode_required: true`, `sensitivity_floor: normal`. NO top-level `subkind` / `deployment_targets` (F-7).
2. `packs/adapters/calendaring/apple-calendar/config.schema.json` (~30 lines): JSON Schema for `calendar_identifier`, `member_id`, `poll_window_days`, `poll_interval_s`.
3. `packs/adapters/calendaring/apple-calendar/config.example.yaml` (~10 lines): example minus secrets.
4. `packs/adapters/calendaring/apple-calendar/adapter.py` (≤500 lines):
   - `class AppleCalendarAdapter(ExternalStateMirrorAdapter)` with `family: ClassVar[Literal["calendaring"]] = "calendaring"`, `name`, `version`, `sensitivity_floor = "normal"`, `capabilities`, `write_capabilities = ["create_event"]`, `sharing_model_discriminator = "owner_member_id"`.
   - `__init__`: config, three optional `observation_*` seam slots (default `None`), `_store` (EKEventStore, `None` until `start`), `_ctx` (`None` until `start`), `_seen_inbound: dict[str, datetime] = {}`, `_status: AdapterStatus`. A constructor-injectable `ek_event_store_factory` per [PM-27] (no-op default builds the real store) so tests inject a fake.
   - `start(ctx)`: build/authorize the EKEventStore, store `_ctx`, emit `adapter.authenticated@v1`, spawn `_poll_loop()`.
   - `stop()`, `status()`: standard lifecycle.
   - `_poll_loop()` / `_poll_once()`: window-predicate query; for each `EKEvent` whose `eventIdentifier()` is not in `_seen_inbound`, emit `calendar.event_added@v1` via `self._append(...)`; update `_seen_inbound`.
   - `_apply_write_impl(operation)`: `if operation.kind == "create_event":` build the `EKEvent`, route the `saveEvent_span_error_` call through `observation_outbound(...)`. Success → `WriteResult(applied=True, external_id=event.eventIdentifier(), suppressed=False)`; suppressed → `WriteResult(applied=False, suppressed=True)`; EventKit error → classify + emit `adapter.error@v1` + raise.
   - `_on_external_change(envelope)`: Phase-A no-op satisfying the ABC.
   - `_classify_eventkit_error`, `_emit_error`, `_emit_authenticated`, `_append(ctx, event_type, payload, causation_id=None)` — mirror the Apple Reminders adapter's helpers.

c2 verification:
```bash
poetry run python -c "from packs.adapters.calendaring.apple_calendar.adapter import AppleCalendarAdapter; from adminme.lib.adapters import ExternalStateMirrorAdapter; assert issubclass(AppleCalendarAdapter, ExternalStateMirrorAdapter); assert AppleCalendarAdapter.write_capabilities == ['create_event']; print('c2 ok')"
poetry run mypy adminme/ packs/adapters/calendaring/apple-calendar/ 2>&1 | tail -5
```
(The directory name `apple-calendar` is hyphenated; the importable module is `packs.adapters.calendaring.apple_calendar` with an underscore — confirm against the `google_calendar` precedent at orientation.)

**c3: `tests/unit/adapters/calendaring/test_apple_calendar.py` (NEW, F-4 unique basename, ≥15 tests).** Do NOT create `tests/unit/adapters/calendaring/__init__.py` (it exists). The test set mirrors 11b-iii §7 c3: inheritance from `ExternalStateMirrorAdapter`; `family` ClassVar is `"calendaring"`; asymmetric `write_capabilities`; the `apply_write` gate raises `WriteCapabilityError` on an undeclared kind; dispatch reaches `_apply_write_impl` on a declared kind; the outbound path routes through `observation_outbound`; a suppressed outbound returns `WriteResult(applied=False, suppressed=True)`; the direct (seams-unset) path returns `applied=True`; EventKit-error classification (access-denied fatal / store-failure transient); the poll emits `calendar.event_added` on first observation; the poll skips an already-seen `eventIdentifier`; the emit payload validates against `CalendarEventAddedV1` with `source == "apple_calendar"`; the `create_event` EKEvent body shape is asserted; the c1 registry-lookup contract holds.

**c4: pack-internal + integration + BUILD_LOG**:

1. `packs/adapters/calendaring/apple-calendar/tests/test_apple_calendar_pack_internal.py` (NEW, F-4 unique basename, ≥3 tests; NO `__init__.py` per F-6): `pack.yaml` round-trips through the manifest parser (Cat-B + `write_capabilities` + `sharing_model_discriminator` + `dedup_strategy`; and asserts NO top-level `subkind`/`deployment_targets` per F-7); the emit payload validates byte-for-byte against the on-main schema; the 11-β six-check validator returns zero errors and no `advisory_pending_retrofit`.
2. `tests/integration/test_adapter_apple_calendar_supervisor.py` (NEW, ≥3 tests; default-CI sibling): the supervisor-loaded adapter inherits `ExternalStateMirrorAdapter`; the `write_capabilities` gate raises `WriteCapabilityError` for an unsupported kind through the loader; the inbound poll emits `calendar.event_added@v1`.
3. `docs/build_log.md` — append the 11b-ii-β-α entry per the 11b-iii c4 BUILD_LOG template (Refactored / Session merged / Outcome / Evidence / Carry-forward for 11b-ii-β-β).

c4 verification:
```bash
poetry run pytest tests/unit/adapters/calendaring/test_apple_calendar.py packs/adapters/calendaring/apple-calendar/tests/ tests/integration/test_adapter_apple_calendar_supervisor.py -v
poetry run adminme adapters validate packs/adapters/calendaring/apple-calendar
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0; NO advisory_pending_retrofit; NO errors.
```

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11b-ii-beta-alpha commit <N>: <imperative summary>` exactly.

- **c1: `phase 11b-ii-beta-alpha commit 1: verify calendar.event_added@v1 already registered (no-op)`** — 0 production-code lines.
- **c2: `phase 11b-ii-beta-alpha commit 2: AppleCalendarAdapter fresh Cat-B central pack`** — `pack.yaml` + `config.schema.json` + `config.example.yaml` + `adapter.py` (≤500 lines).
- **c3: `phase 11b-ii-beta-alpha commit 3: framework-level Apple Calendar tests`** — `tests/unit/adapters/calendaring/test_apple_calendar.py` (≥15 tests).
- **c4: `phase 11b-ii-beta-alpha commit 4: pack-internal + integration tests + BUILD_LOG`** — pack-internal + integration + `docs/build_log.md` append.

## §8.5 Branch strategy — STACKED

c1 → c2 → c3 → c4 stacked; each PR opens with `base=main`. Ghost-conflict risk: LOW — file scopes are non-overlapping (c1 verify-only; c2 a new pack directory; c3 a new test file under the already-existing `tests/unit/adapters/calendaring/`; c4 a new pack-internal directory + a new integration file + a `build_log.md` append). If `push_files` hits the inline budget at c4 (`build_log.md` is ~400 KB), the Executor stops and Partner re-fires the c4 step via Chrome MCP `file_upload` per Partner memory.

Test floor: c3 ≥15 + c4 ≥3 pack-internal + ≥3 integration = **≥21 total NEW**.

## §9 Verification

(Per c-band per §7. c4 final:)
```bash
poetry run pytest tests/unit/adapters/calendaring/ packs/adapters/calendaring/apple-calendar/tests/ tests/integration/test_adapter_apple_calendar_supervisor.py -v
poetry run adminme adapters validate packs/adapters/calendaring/apple-calendar
poetry run mypy adminme/ packs/adapters/calendaring/apple-calendar/ 2>&1 | tail -10
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0; zero regressions on existing google-calendar / apple-eventkit tests.
```

## §10 Stop

When `adminme adapters validate packs/adapters/calendaring/apple-calendar` exits 0, the inbound EKEventStore poll emits `calendar.event_added@v1` on first observation, the outbound `create_event` routes through `observation_outbound(...)`, the existing `calendar.event_added@v1` schema on main is UNCHANGED, and NO dual-deployment surface was introduced (F-7), c4 is done. Open each PR with `base=main` per the stacked strategy. **Unlocks Z1.5-bis = 11b-ii-β-β** (Apple Calendar dual-deployment). Do NOT proceed to 11b-ii-β-β without operator review.

After merge: Partner runs the three-job QC.
