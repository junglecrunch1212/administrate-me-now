# Prompt 11b-iii: Google Calendar central (Cohort D step 2-γ)

**Phase:** L1 / Cohort D step 2-γ — third sub-prompt of the four-way 11b split. First **fresh** Cat-B adapter (vs 11r-c retrofit + 11b-i extension). Parent draft: `prompts/11b-iii-google-calendar-central.md` (merged PR #164).
**Branch:** `claude/11b-iii-c<N>` per c-band.
**Stop condition:** NEW pack `packs/adapters/calendaring/google-calendar/` with `pack.yaml` + `adapter.py` declaring `class GoogleCalendarAdapter(ExternalStateMirrorAdapter)`. `family="calendaring"`. `capabilities=[Cat-B central]`. `write_capabilities=["create_event"]`. Inbound poll emits existing `calendar.event_added@v1` (no new schema). Outbound `_apply_write_impl` for `create_event` calls `events.insert` wrapped via `adminme.lib.observation.outbound(...)`. `adminme adapters validate packs/adapters/calendaring/google-calendar` exits 0.
**Depends-on:** canonical 11a closed (PRs #154-#157); canonical 11b-i closed (PRs #158-#163, main `4ed7b731`); parent draft merged (PR #164, main `a7389a9e`).
**Quality-bar reference:** `prompts/_active/11a-gmail-real-outbound.md` (canonical-build narrow scope; real outbound) + `prompts/_active/11r-c-retrofit-reminders-to-external-state-mirror-adapter.md` (Cat-B precedent). 11b-iii is heavier than both because it builds a fresh pack from scratch (vs 11r-c's retrofit + 11a's stub-replacement).
**Estimated duration:** c1 ~10-15 min (verify) · c2 ~90-120 min (fresh adapter ≤500 lines) · c3 ~60 min (≥15 tests) · c4 ~60 min (pack-internal + integration + BUILD_LOG) · total ~4-4.5 hrs wall / ~$80-110 spend.

---

## §1 Behavioral grounding

11b-iii is the **first fresh Cat-B adapter** under the canonical-build pattern. Three load-bearing items:

1. **Fresh pack structure** — `pack.yaml` declares Cat-B capability + `write_capabilities=["create_event"]` per [UT-27] asymmetric (only what's implemented) + `sharing_model_discriminator: owner_member_id` per memo 05 §2.5 + `dedup_strategy: {kind: external_id}` (Google's stable `event.id`) + `sensitivity_default: normal` + `observation_mode_required: true`. `family: calendaring`. Pack id `adapter:calendaring-google-calendar`.

2. **Google Calendar v3 API surface** — `googleapiclient.discovery.build("calendar", "v3", credentials=...)`. Inbound poll: `service.events().list(calendarId=..., maxResults=250, singleEvents=True, orderBy="startTime", timeMin=...).execute()`. Outbound write: `service.events().insert(calendarId=..., body={...}).execute()` wrapped through `await observation_outbound(...)`. Error classification: `HttpError` taxonomy mirrors Gmail's `_classify_gmail_error` (4xx auth → fatal `auth_failed`; 5xx → transient `network`; 429 → transient `rate_limited`).

3. **Map onto existing `calendar.event_added@v1`** — schema field set is `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` per `adminme/events/schemas/ingest.py:CalendarEventAddedV1`. Google Calendar inbound emits: `source="google_calendar"`, `external_event_id=event["id"]`, `calendar_id=config["calendar_id"]`, `summary=event.get("summary", "")`, `start=event["start"].get("dateTime", event["start"].get("date"))`, `end=event["end"].get("dateTime", event["end"].get("date"))`, `location=event.get("location")`, `attendees=event.get("attendees", [])`, `body=event.get("description")`. NO new schema registered per [D7] + canonical 11a's "MAP DON'T REGISTER".

OUT OF SCOPE per parent draft §"Out of scope":
- `calendar.event_updated@v1` / `calendar.event_deleted@v1` emissions (cache-based diff + negative-set; defer to 11b-iii-β).
- Dual-deployment per [D22] (N/A for Google Calendar shared accounts).
- Other write_capabilities (`update_event`, `delete_event`).
- Push notifications via `events.watch`.
- Real OAuth credential flow (Phase B + bootstrap §10).
- Calendar list discovery.
- Recurring event expansion (Google handles via `singleEvents=True`).
- Cross-adapter changes.

Citations: [§6 invariant 10] (sensitivity floor `normal`), [§6.20] (observation wrap), [§7.3, D17] (no projection writes), [§8.2, D6] (no LLM SDK), [D7] (schemas v1 immutable; this prompt USES existing), [D8] (causation/correlation propagation), [D19] (Cat-B taxonomy for calendar), [PM-27] (constructor injection no-op defaults), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list), [UT-27] (asymmetric write_capabilities). Memo 05 §1.2, §2.3, §2.5, §3.1, §3.2, §3.4. Memo 08 §3.4. [VISION §"Real family"]: Google Calendar is secondary calendaring; 11b-iii establishes the fresh-pack template that 11b-ii Apple Calendar dual + 11b-iv Google Tasks layer on.

## §2 Read first (required)

Per c-band:

**c1 (verify-only no-op) — 3 files**

- `prompts/11b-iii-google-calendar-central.md` (merged parent draft).
- `adminme/events/schemas/ingest.py` (on main) — **READ CONTENTS**: `CalendarEventAddedV1` field set must match c1's verify assertion + c2's emit mapping.
- `adminme/events/registry.py` (on main) — `registry.get(name, version)` API.

**c2 (fresh adapter pack) — 6 files**

- `adminme/lib/adapters/external_state_mirror_base.py` (on main) — Cat-B ABC: `WriteOperation` / `WriteResult` / `ChangeEnvelope` / `WriteCapabilityError` / `apply_write` gate / `_apply_write_impl` + `_on_external_change` abstract methods.
- `adminme/lib/adapters/__init__.py` (on main) — public exports.
- `adminme/lib/observation.py` (on main) — `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier) → OutboundResult`.
- `packs/adapters/messaging/gmail/adapter.py` (on main) — Cat-A precedent (`googleapiclient.discovery.build`, `HttpError` classification, `_emit_error`, `_emit_authenticated`, observation seam slots, `_classify_gmail_error`). Use `sed -n` to scope; the file is large.
- `packs/adapters/reminders/apple-eventkit/adapter.py` (on main, class section + `_apply_write_impl` + `_on_external_change`) — Cat-B precedent.
- `adminme/adapters/base.py` (on main) — `AdapterContext`, `AdapterStatus`, `SensitivityFloor`.

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/messaging/test_gmail_send.py` (on main) — test-file shape precedent (`_FakeEventLog`, `_FakeService`, mocking patterns).
- `tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py` (on main) — Cat-B test precedent.
- `adminme/events/schemas/ingest.py::CalendarEventAddedV1` — field-mapping assertion target.
- `packs/adapters/calendaring/google-calendar/adapter.py` (on the c2 branch).
- `prompts/_active/11b-i-apple-reminders-list-migration.md` — most-recent canonical-build c3 deliverable shape.

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `packs/adapters/messaging/gmail/tests/test_gmail_send_pack_internal.py` (on main) — pack-internal precedent.
- `tests/integration/test_adapter_supervisor_gmail.py` (on main) — integration sibling precedent.
- `docs/build_log.md` (append 11b-iii entry).
- `prompts/_active/11b-i-apple-reminders-list-migration.md` (c4 BUILD_LOG entry shape).

## §3 Reconciliation notes (carry-forward)

- **F-1 (HARD/from cycle #17): READ FILE CONTENTS for any schema file cited in c1**. The c1 verify asserts `registry.get("calendar.event_added", 1) is CalendarEventAddedV1` against the on-main schema. Gate 0 strengthening: grep `registry.register("calendar.event_added"` BEFORE authoring; confirm one canonical registration in `ingest.py` (no plugin shadows).
- **F-2 (HARD/from 11a + 11b-i): MAP existing field semantics; do not invent new schemas.** Google Calendar inbound emits the existing `CalendarEventAddedV1` field set BYTE-FOR-BYTE. No new schemas. No field additions.
- **F-3 (HARD/from 11b-i): observation-suppressed path skips the emit too.** When `outbound()` returns `OutboundResult(suppressed=True)` on `_apply_write_impl`, the adapter returns `WriteResult(applied=False, suppressed=True)` and the supervisor's caller does NOT proceed to emit anything externally-visible. The inbound poll-cycle's `calendar.event_added@v1` emit is independent and NOT gated by observation (inbound observation is a separate concern per [§6.20] — only OUTBOUND side effects suppress).
- **F-4 (HARD/standing): pack-internal test basename uniqueness.** `test_google_calendar_pack_internal.py` is unique against the existing Gmail/Plaid/Reminders pack-internal test basenames (verified).
- **F-5 (SOFT): adapter.py diff size — anti-vision halt at ≤500 lines added.** Fresh Cat-B is heavier than 11r-c's ≤300 retrofit ceiling and 11a/11b-i's ≤200; 11b-iii authors a full new adapter. ≤500 line ceiling accepts fresh-pack scope while preventing scope creep.
- **F-6 (HARD/new): NO empty `__init__.py` in pack-internal tests dir.** Canonical 11a c4 removed an accidental empty `__init__.py` from `packs/adapters/messaging/gmail/tests/` because it tripped pytest package-discovery collision. Do NOT create `packs/adapters/calendaring/google-calendar/tests/__init__.py`. (NB: `tests/unit/adapters/calendaring/__init__.py` IS required as the standard tests-package marker.)

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `CalendarEventAddedV1` field set on main.** Verify `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` matches `adminme/events/schemas/ingest.py:50-60` (approx; depth-read at orientation).
- **A-2: `googleapiclient.discovery.build` + `googleapiclient.errors.HttpError` imports.** Already used by Gmail adapter; same import surface for Calendar.
- **A-3: `service.events().list(...).execute()` chain shape.** Google Calendar API v3 surface; same call style as Gmail's `users().messages()`.
- **A-4: `_apply_write_impl` shape from `external_state_mirror_base.py`.** `async def _apply_write_impl(self, operation: WriteOperation) -> WriteResult`. Already confirmed via Cat-B ABC read at 11b-i authoring.
- **A-5: `observation_outbound` import path.** `from adminme.lib.observation import outbound as observation_outbound` (mirroring 11r-c's pattern); the function returns `OutboundResult(suppressed, result)`.
- **A-6: `_seen_inbound` dedupe cache pattern.** Gmail uses `dict[str, datetime]` keyed by message-id; Google Calendar uses `dict[str, datetime]` keyed by `event["id"]`. Same pattern.

## §5 Objective

Build `packs/adapters/calendaring/google-calendar/` (fresh Cat-B pack) implementing inbound poll + outbound `create_event` for Google Calendar v3 API. Maps onto existing `calendar.event_added@v1` for inbound; observation-wrapped real `_apply_write_impl` for outbound `create_event`. No new schemas.

## §6 Out of scope

Same as parent draft §"Out of scope".

## §7 Deliverables

**c1: VERIFY-ONLY NO-OP** — `adminme/events/schemas/ingest.py` NOT modified.

```python
from adminme.events.registry import registry
from adminme.events.schemas.ingest import CalendarEventAddedV1
assert registry.get("calendar.event_added", 1) is CalendarEventAddedV1
```

Empty commit body documents the no-op. Net diff: 0 lines.

**c2: NEW pack files** (~500 lines target across 4 files; F-5 anti-vision halt at ≤500 lines for `adapter.py` alone):

1. `packs/adapters/calendaring/google-calendar/pack.yaml` (~40 lines): Cat-B manifest matching memo 05 §3.1 + canonical 11a pack.yaml shape.

2. `packs/adapters/calendaring/google-calendar/config.schema.json` (~30 lines): JSON Schema for `service_account_key_ref`, `calendar_id`, `user_email`, `poll_interval_s`.

3. `packs/adapters/calendaring/google-calendar/config.example.yaml` (~10 lines): example minus secrets.

4. `packs/adapters/calendaring/google-calendar/adapter.py` (≤500 lines):
   - Imports + module docstring.
   - `class GoogleCalendarAdapter(ExternalStateMirrorAdapter):` with `family: Literal["calendaring"] = "calendaring"`, `name`, `version`, `sensitivity_floor`, `capabilities`, `write_capabilities=["create_event"]`, `sharing_model_discriminator="owner_member_id"`.
   - `__init__`: config, three optional `observation_*` seam slots (default None), `_service` (None until `start`), `_ctx` (None until `start`), `_seen_inbound: dict[str, datetime] = {}`, `_status: AdapterStatus`.
   - `start(ctx)`: build service, store `_ctx`, emit `adapter.authenticated@v1`, spawn `_poll_loop()` task.
   - `stop()`, `status()`: standard lifecycle.
   - `_poll_loop()`: long-running async loop, calls `_poll_once()` on `poll_interval_s` cadence, catches `HttpError` → `_classify_calendar_error` → `_emit_error` + backoff.
   - `_poll_once()`: `events.list(...)` paginated, for each event not in `_seen_inbound` emit `calendar.event_added@v1` via `self._append(ctx=ctx, event_type="calendar.event_added", payload=...)`, update `_seen_inbound`.
   - `_apply_write_impl(operation)`: `if operation.kind == "create_event":` build event body, call `observation_outbound(...)` with `action_fn` closure invoking `service.events().insert(calendarId=..., body=event_body).execute()`. On success return `WriteResult(applied=True, external_id=response["id"], suppressed=False)`. On suppress return `WriteResult(applied=False, external_id=None, suppressed=True)`. On `HttpError` classify + emit `adapter.error@v1` + raise.
   - `_on_external_change(envelope)`: Phase A no-op satisfying ABC.
   - `_classify_calendar_error(exc)`: mirrors `_classify_gmail_error`.
   - `_emit_error(ctx, severity, error_kind, ...)`: mirrors Gmail's helper.
   - `_emit_authenticated(ctx, ...)`: mirrors Gmail's helper.
   - `_append(ctx, event_type, payload, causation_id=None)`: minimal wrapper around `ctx.event_log.append_sync(...)`.

**c3: `tests/unit/adapters/calendaring/test_google_calendar.py` (NEW, F-4 unique basename, ≥15 tests)** + `tests/unit/adapters/calendaring/__init__.py`:

1. `test_google_calendar_adapter_inherits_external_state_mirror_adapter` — `issubclass(GoogleCalendarAdapter, ExternalStateMirrorAdapter)`.
2. `test_family_class_attribute_is_calendaring_literal` — `family == "calendaring"`.
3. `test_write_capabilities_declared_asymmetrically` — `["create_event"]`; `len == 1`.
4. `test_apply_write_gate_raises_on_undeclared_kind` — `pytest.raises(WriteCapabilityError)` for `kind="update_event"`.
5. `test_apply_write_dispatches_to_impl_on_declared_kind` — `kind="create_event"` reaches `_apply_write_impl` (monkeypatched).
6. `test_apply_write_routes_through_observation_outbound` — assertion that `observation_outbound` is called when seam slots populated.
7. `test_apply_write_observation_suppressed_returns_unapplied` — `OutboundResult(suppressed=True)` → `WriteResult(applied=False, suppressed=True)`.
8. `test_apply_write_direct_call_when_seams_unset_returns_applied` — Phase A direct path returns `WriteResult(applied=True, ...)`.
9. `test_apply_write_4xx_auth_failure_classifies_fatal` — `HttpError(401)` → fatal `auth_failed`.
10. `test_apply_write_5xx_classifies_transient` — `HttpError(503)` → transient `network`.
11. `test_apply_write_429_classifies_rate_limited_transient` — `HttpError(429)` → transient `rate_limited`.
12. `test_poll_once_emits_calendar_event_added_on_first_observation` — `_seen_inbound` empty → emit happens.
13. `test_poll_once_skips_already_seen_event_id` — second cycle on same `event["id"]` → no emit.
14. `test_poll_emit_payload_validates_against_calendar_event_added_v1` — `CalendarEventAddedV1.model_validate(payload)` succeeds; `source == "google_calendar"`.
15. `test_apply_write_create_event_body_shape` — assert `service.events().insert(...)` called with `body={"summary": ..., "start": ..., "end": ..., ...}`.
16. `test_calendar_event_added_v1_registry_lookup_returns_existing_model` — c1 contract.

**c4: pack-internal + integration + BUILD_LOG**:

1. `packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py` (NEW, F-4 unique basename, ≥3 tests). NO `__init__.py` per F-6.
   - `test_pack_yaml_round_trips_through_parse_adapter_manifest` (asserts Cat-B + write_capabilities + sharing_model + dedup_strategy).
   - `test_apply_write_emit_validates_against_on_main_schema` (payload validates byte-for-byte).
   - `test_six_validator_checks_return_zero_errors_and_no_advisory` (validator clean).

2. `tests/integration/test_adapter_google_calendar_supervisor.py` (NEW, ≥3 tests; default-CI sibling):
   - `test_supervisor_loaded_adapter_inherits_external_state_mirror_adapter`.
   - `test_supervisor_write_capabilities_gate_through_loader` (assert `WriteCapabilityError` for unsupported kind).
   - `test_supervisor_inbound_poll_emits_calendar_event_added`.

3. `docs/build_log.md` — append 11b-iii entry per the 11b-i c4 template (Outcome / Evidence / Carry-forward).

## §8 Commit structure

Four commits. Every commit message follows `phase 11b-iii commit <N>: <imperative summary>`.

- **c1: `phase 11b-iii commit 1: verify calendar.event_added@v1 already registered (no-op)`** — 0 production-code lines.
- **c2: `phase 11b-iii commit 2: GoogleCalendarAdapter fresh Cat-B pack`** — pack.yaml + config schema + adapter.py ≤500 lines.
- **c3: `phase 11b-iii commit 3: framework-level Google Calendar tests`** — `tests/unit/adapters/calendaring/test_google_calendar.py` (≥15 tests).
- **c4: `phase 11b-iii commit 4: pack-internal + integration tests + BUILD_LOG`** — pack-internal + integration + BUILD_LOG.

## §8.5 Branch strategy — STACKED

c1 → c2 → c3 → c4 stacked. Each PR `base=main` per PR #106. Ghost-conflict risk: LOW (file scopes non-overlapping: c1 verify-only, c2 new pack dir, c3 new test files under `tests/unit/adapters/calendaring/`, c4 new pack-internal dir + new integration file + BUILD_LOG append).

Test floor: c3 ≥15 + c4 ≥3 pack-internal + ≥3 integration = **≥21 total**.

## §9 Verification

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.ingest import CalendarEventAddedV1; assert registry.get('calendar.event_added', 1) is CalendarEventAddedV1; print('calendar.event_added@v1 verified ✓')"
```

**c2**:
```bash
poetry run python -c "from packs.adapters.calendaring.google_calendar.adapter import GoogleCalendarAdapter; from adminme.lib.adapters import ExternalStateMirrorAdapter; assert issubclass(GoogleCalendarAdapter, ExternalStateMirrorAdapter); assert GoogleCalendarAdapter.write_capabilities == ['create_event']; print('c2 inheritance + ClassVars ✓')"
poetry run mypy adminme/ packs/adapters/calendaring/google-calendar/ 2>&1 | tail -5
```

(Note: import path is `packs.adapters.calendaring.google_calendar` — underscore form per pyproject package convention; verify against existing pack-internal tests at orientation. The directory name is `google-calendar` with hyphen; the importable module name uses underscore.)

**c3**:
```bash
poetry run pytest tests/unit/adapters/calendaring/test_google_calendar.py -v
# Expected: ≥15 passed
```

**c4**:
```bash
poetry run pytest packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py tests/integration/test_adapter_google_calendar_supervisor.py -v
poetry run adminme adapters validate packs/adapters/calendaring/google-calendar
# Expected: exit 0; NO advisory_pending_retrofit; NO errors
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When `adminme adapters validate packs/adapters/calendaring/google-calendar` exits 0, the inbound poll emits `calendar.event_added@v1` on first observation, the outbound `create_event` routes through `outbound()`, and the existing `calendar.event_added@v1` schema on main is UNCHANGED, c4 is done. Open the PR per the standard four-commit structure. Do NOT proceed to 11b-iii-β (update/delete emissions), 11b-ii Apple Calendar dual, or 11b-iv Google Tasks without operator review.

After merge: Partner runs three-job QC. Batched partner-state-snapshot PR covers post-11a + post-11b-i + post-11b-iii via Chrome MCP `file_upload` for the 100KB+ files.
