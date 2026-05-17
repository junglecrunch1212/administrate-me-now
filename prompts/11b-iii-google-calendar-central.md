# Prompt 11b-iii: Google Calendar central (Cohort D step 2-γ)

**Phase:** L1 / Cohort D step 2-γ — third sub-prompt of the four-way 11b split per `docs/08-architecture-amendment-adapter-rebase.md` §3.4. First **fresh** Cat-B adapter (vs 11r-c's retrofit + 11b-i's incremental extension).

**Depends on:** Cohort B closed; Cohort C closed; canonical 11a closed (PRs #154-#157); canonical 11b-i closed (PRs #160-#163, main `4ed7b7311`). All three shipped v1 Cat-A/B/C packs framework-conformant.

**Estimated duration:** ~3-4 hours. **Narrow scope** for a fresh adapter: inbound poll (first-observation emission only) + outbound `create_event` via `events.insert`. Updated/deleted inbound emissions deferred (require cache + negative-set logic, analogous to 11b-i's deferred `list_item.deleted@v1` inbound).

**Stop condition:** NEW pack `packs/adapters/calendaring/google-calendar/` with `pack.yaml` + `adapter.py` declaring `class GoogleCalendarAdapter(ExternalStateMirrorAdapter)` with `family="calendaring"` + `capabilities=[Cat-B central]` + `write_capabilities=["create_event"]`. Inbound poll emits the EXISTING `calendar.event_added@v1` schema (registered phase 04-3 in `adminme/events/schemas/ingest.py`); outbound `_apply_write_impl(WriteOperation(kind="create_event", ...))` calls `service.events().insert(...)` wrapped via `adminme.lib.observation.outbound(...)`. `adminme adapters validate packs/adapters/calendaring/google-calendar` exits 0.

---

## Read first

1. `docs/08-architecture-amendment-adapter-rebase.md` §3.4 — Cohort D 4-way 11b split.
2. `docs/05-architecture-amendment-personal-data-layer.md` §1.2 (Cat-B contract), §2.3 (asymmetric write_capabilities), §2.5 (sharing_model_discriminator), §3.1 (manifest extension fields).
3. `adminme/events/schemas/ingest.py` (on main) — **READ CONTENTS** per Gate 0 strengthening. The existing `CalendarEventAddedV1` field set is `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` — Google Calendar inbound MUST map to these exact field names.
4. `adminme/lib/adapters/external_state_mirror_base.py` (on main) — Cat-B ABC contract (`WriteOperation`, `WriteResult`, `ChangeEnvelope`, `WriteCapabilityError`, `apply_write` gate, `_apply_write_impl` abstract method).
5. `adminme/lib/observation.py` (on main) — `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier) → OutboundResult` per [§6.20].
6. `packs/adapters/messaging/gmail/pack.yaml` + `packs/adapters/messaging/gmail/adapter.py` — Cat-A precedent for pack structure (config.schema.json, config.example.yaml, `googleapiclient.discovery.build`, `googleapiclient.errors.HttpError`, `_classify_*_error`, `_emit_error`).
7. `packs/adapters/reminders/apple-eventkit/adapter.py` (on main, class head + `_apply_write_impl` + `_on_external_change` regions only; the file is 55.9KB) — Cat-B precedent for the `apply_write` + observation-wrap pattern.
8. `prompts/_active/11a-gmail-real-outbound.md` (on main) — canonical-build narrow-scope precedent for fresh outbound implementation.
9. `prompts/_active/11b-i-apple-reminders-list-migration.md` (on main) — most-recent quality-bar reference; F-3 observation-suppress invariant; F-4 unique-basename rule.

## Operating context

This is the **first fresh Cat-B adapter** built under the canonical-build pattern (vs 11r-c's retrofit and 11b-i's extension). Three load-bearing items 11b-iii establishes:

1. **Fresh Cat-B pack structure** — `pack.yaml` declares the Cat-B capability block + `write_capabilities=["create_event"]` + `sharing_model_discriminator: owner_member_id` + `dedup_strategy: {kind: external_id}` + `sensitivity_default: normal` per memo 05 §3.1. NO existing pack to retrofit.

2. **Google Calendar API surface bound to Cat-B ABC** — inbound poll uses `service.events().list(calendarId=..., maxResults=..., singleEvents=true, orderBy="startTime", timeMin=...).execute()`. Outbound write uses `service.events().insert(calendarId=..., body={...}).execute()` wrapped through `adminme.lib.observation.outbound(...)` per [§6.20].

3. **Map onto existing `calendar.event_*@v1` schemas** — `calendar.event_added@v1` is the inbound emit on first observation (`event.id` not in `_seen_inbound` cache); the schema's field set is `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` with `source="google_calendar"`. Per canonical 11a's "MAP DON'T REGISTER" lesson + [D7] immutability, NO new schema is registered. `calendar.event_updated@v1` and `calendar.event_deleted@v1` emissions are deferred (require cache-based diff + negative-set across cycles).

OUT OF SCOPE per memo 08 §3.4:

- **`calendar.event_updated@v1` and `calendar.event_deleted@v1` emissions** — defer to a follow-up 11b-iii-β prompt. Both require `_inbound_snapshots`-style cache + diff logic; 11b-i's pattern transplants directly but the scope is meaningful.
- **Dual-deployment per [D22]** — Google Calendar is single-deployment central; per-member is N/A (Google Calendar is shared family/team account).
- **`update_event` / `delete_event` write capabilities** — declaring without implementation fails `write_capabilities_check` per [UT-27]. Only `create_event` ships.
- **Push notifications via `events.watch`** — Phase A uses poll; webhook is Phase B.
- **OAuth credential flow** — Phase A uses `requires_live_services`-gated tests + mocked `service` factory. Real OAuth wires in at bootstrap §10 (prompt 16).
- **Calendar list discovery** (`calendarList.list`) — Phase A configures a single `calendar_id` per pack instance.
- **Recurring event expansion** — Phase A passes `singleEvents=true` to let Google's API expand recurrence; storing recurrence rules is out of scope.
- **Cross-adapter changes** — Gmail (11a) + Plaid (11r-b) + Apple Reminders (11r-c + 11b-i) preserved byte-for-byte.

Citations: [§6 invariant 10] (sensitivity floor `normal`), [§6.13-§6.20] (observation final outbound filter), [§7.3, D17] (no projection writes), [§8.2, D6] (no LLM SDK), [D7] (schemas v1 immutable; this prompt USES existing v1), [D8] (causation/correlation propagation), [D19] (Cat-B taxonomy for calendar), [PM-27] (constructor injection with no-op defaults for observation seams), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list), [UT-27] (asymmetric write_capabilities — declare only what's implemented). Memo 05 §1.2, §2.3, §2.5, §3.1, §3.2, §3.4. Memo 08 §3.4.

[VISION §"Real family"]: Google Calendar is the family's secondary calendaring surface (Apple Calendar is primary per the Apple-first stack noted in memo 04). 11b-iii establishes the fresh Cat-B build pattern; subsequent 11b-ii Apple Calendar dual + 11b-iv Google Tasks layer the dual-deployment + ListItem patterns on this foundation.

## Objective

Build a fresh Cat-B `GoogleCalendarAdapter` at `packs/adapters/calendaring/google-calendar/` that inbound-polls Google Calendar's `events.list` and outbound-writes via `events.insert`. Inbound emits `calendar.event_added@v1` per first-observation; outbound writes via `apply_write(WriteOperation(kind="create_event", ...))` → `_apply_write_impl` → observation-wrapped Google Calendar API call. No new schemas registered.

## Out of scope

(See §"Operating context" above — same list.)

## Approach (architectural sketch)

The fresh pack ships:

1. **`pack.yaml`** — Cat-B manifest with `capabilities`, `write_capabilities=["create_event"]`, `sharing_model_discriminator: owner_member_id`, `dedup_strategy: {kind: external_id}`, `sensitivity_default: normal`, `observation_mode_required: true`. `family: calendaring`. `events_emitted: [calendar.event_added, adapter.error, adapter.authenticated, adapter.cursor_advanced]`.

2. **`config.schema.json` + `config.example.yaml`** — minimal config carrying `service_account_key_ref`, `calendar_id`, `user_email`, `poll_interval_s`.

3. **`adapter.py`** — `class GoogleCalendarAdapter(ExternalStateMirrorAdapter)`:
   - ClassVars: `name`, `family: Literal["calendaring"] = "calendaring"`, `version`, `sensitivity_floor: SensitivityFloor = "normal"`, `capabilities`, `write_capabilities=["create_event"]`, `sharing_model_discriminator="owner_member_id"`.
   - `__init__`: stores `config`, three optional `observation_*` seam slots defaulting to `None`, `_service` (set in `start`), `_ctx`, `_seen_inbound: dict[str, datetime]` cache, `_status: AdapterStatus`.
   - `start(ctx)`: build `googleapiclient.discovery.build("calendar", "v3", credentials=...)`; emit `adapter.authenticated@v1`; spawn poll task.
   - `stop()`, `status()`: standard lifecycle.
   - `_poll_loop()`: every `poll_interval_s` call `_poll_once()`; on `HttpError` classify + emit `adapter.error@v1` + backoff.
   - `_poll_once()`: `service.events().list(calendarId=..., maxResults=250, singleEvents=true, orderBy="startTime", timeMin=...).execute()`; for each event not in `_seen_inbound`, emit `calendar.event_added@v1` mapped to the existing schema field set; update `_seen_inbound`.
   - `_apply_write_impl(operation)`: for `kind="create_event"`, build Google event body from `operation.payload`, call `service.events().insert(calendarId=..., body=event_body).execute()` wrapped through `await observation_outbound(session, "calendar.event_create", payload, action_fn, manager, event_log, target_channel="google_calendar", target_identifier=calendar_id)`. On success return `WriteResult(applied=True, external_id=event["id"], suppressed=False)`. On suppress return `WriteResult(applied=False, external_id=None, suppressed=True)`.
   - `_on_external_change(envelope)`: thin wrapper (Phase A no-op; production poll path is unchanged).

## Deliverables

### NEW pack files
- `packs/adapters/calendaring/google-calendar/pack.yaml` (~40 lines)
- `packs/adapters/calendaring/google-calendar/config.schema.json` (~30 lines)
- `packs/adapters/calendaring/google-calendar/config.example.yaml` (~10 lines)
- `packs/adapters/calendaring/google-calendar/adapter.py` (target: ≤500 lines for the fresh adapter; F-5 anti-vision halt — fresh Cat-B is heavier than 11r-c's ≤300 retrofit ceiling but narrower than Gmail's full historic build).

### Tests
- `tests/unit/adapters/calendaring/__init__.py` (NEW dir; standard package marker).
- `tests/unit/adapters/calendaring/test_google_calendar.py` (NEW, F-4 unique basename, ~15 tests).
- `packs/adapters/calendaring/google-calendar/tests/__init__.py` AVOIDED (per the canonical 11a c4 lesson — empty `__init__.py` in pack-internal test dirs causes pytest package-discovery collision).
- `packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py` (NEW, F-4 unique basename, ~3 tests).
- `tests/integration/test_adapter_google_calendar_supervisor.py` (NEW, ~3 tests; default-CI sibling).

### Documentation
- `docs/build_log.md` — append the 11b-iii entry per the canonical 11a c4 template.

## Verification

```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.ingest import CalendarEventAddedV1; assert registry.get('calendar.event_added', 1) is CalendarEventAddedV1; print('calendar.event_added@v1 verified registered ✓')"

poetry run pytest tests/unit/adapters/calendaring/test_google_calendar.py packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py tests/integration/test_adapter_google_calendar_supervisor.py -v

poetry run mypy adminme/ packs/adapters/calendaring/google-calendar/ 2>&1 | tail -5
poetry run ruff check packs/adapters/calendaring/google-calendar/ 2>&1 | tail -3

poetry run adminme adapters validate packs/adapters/calendaring/google-calendar
# Expected: exit 0; NO advisory_pending_retrofit; NO errors

bash scripts/verify_invariants.sh
# Expected: exit 0
```

## Stop

When the Google Calendar pack passes validator + tests + the inbound poll emits `calendar.event_added@v1` on first observation + the outbound `create_event` calls `events.insert` wrapped through `outbound()`, c4 is done. Do NOT proceed to 11b-iii-β (updated/deleted emissions), 11b-ii (Apple Calendar dual), or 11b-iv (Google Tasks) without operator review.

## What this PR is NOT

This is the parent draft (the "what"). The v4 refactor (the "how" — per-commit deliverables + verification + commit structure) follows in `partner/refactor-11b-iii` after this merges + pre-authorship gate runs against current main HEAD.

No constitutional doc edits. No PROMPT_SEQUENCE rows touched. No `_active/` v4 refactor file yet.
