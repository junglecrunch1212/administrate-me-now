# Prompt 11g: Home Assistant Cat-C+E multi-capability adapter

**Phase:** L1 / Cohort D — per `prompts/PROMPT_SEQUENCE.md` row 122 + [D24] + memo 05 §8. First **Cat-E reference implementation** of `OutboundActionAdapter` (5th ABC introduced in 11-α but never demonstrated). Multi-capability per [PM-35]: declares BOTH Cat-C state-read seam AND Cat-E service-call seam.

**Depends on:** Cohort B closed (5 ABCs including `OutboundActionAdapter`); canonical 11a/11b-i/11b-iii/11b-iv/11f/11c-i all on main.

**Estimated duration:** ~3-4 hours. Single prompt per PROMPT_SEQUENCE.md row 122.

**Stop condition:** NEW pack `packs/adapters/automation/home-assistant/` declaring `class HomeAssistantAdapter(OutboundActionAdapter)` with `family="home_automation"`, multi-capability `capabilities=[Cat-C iot state-read, Cat-E home_automation service-call]`. Inbound path polls `/api/states`, emits `ha.state_changed@v1` on diffs. Outbound `execute_action` for `kind="ha.service_call"` calls `POST /api/services/<domain>/<service>` wrapped via `adminme.lib.observation.outbound` per [§6.20]; emits `action.executed@v1` on success or `action.failed@v1` on classified error. Three NEW v1 schemas at `adminme/events/schemas/automation.py`. `adminme adapters validate packs/adapters/automation/home-assistant` exits 0.

---

## Read first

1. `prompts/PROMPT_SEQUENCE.md` row 122 — "11g `11g-home-assistant-adapter.md` — Multi-capability Cat-C + Cat-E reference implementation per [D24] + memo §8."
2. `docs/DECISIONS.md` [D24] (Home Assistant multi-capability adapter, full bidirectional with observation-mode integration on Cat-E half).
3. `docs/05-architecture-amendment-personal-data-layer.md` §8 (Home Assistant spec) + §3.2 (multi-capability via capabilities-list per [PM-35]).
4. `adminme/lib/adapters/outbound_action_base.py` (on main) — Cat-E ABC: `ActionRequest`, `ActionResult`, `execute_action` abstract method, `observation_mode_required: ClassVar[bool] = True` (cannot opt out).
5. `adminme/lib/adapters/inbound_data_base.py` (on main) — Cat-C ABC (NOT inherited by HA — Cat-E inherited; Cat-C is declared via the `capabilities: list` entry per [PM-35] multi-capability).
6. `adminme/lib/observation.py` (on main) — `outbound(...)` signature.
7. `packs/adapters/calendaring/google-calendar/adapter.py` (on main) — fresh-Cat-B pack precedent (transplants structurally: lazy SDK import, observation seam slots, `_classify_*_error`, `_emit_authenticated`, `_emit_error`, `_append`).
8. `packs/adapters/financial/plaid/adapter.py` (on main) — Cat-C precedent for read-only polling shape (state-list semantics).

## Operating context

11g is the first canonical Cat-E demo. Three load-bearing items:

1. **Multi-capability declaration per [PM-35]** — pack.yaml + ClassVar declare TWO capability blocks: Cat-C `iot` state-read + Cat-E `home_automation` service-call. The class INHERITS `OutboundActionAdapter` (Cat-E ABC) because Cat-E carries the heavier abstract surface (`execute_action`) and the `observation_mode_required: True` constraint. The Cat-C inbound path runs as an internal method (`_poll_state_loop` → emits `ha.state_changed@v1`) without going through any Cat-C ABC method — Cat-C's ABC contract is `start/stop/status` only, satisfied by the class's own lifecycle.

2. **Cat-C state-read seam** — poll `/api/states` every `poll_interval_s` (default 30). For each state entry, compare against `_seen_states[entity_id]` cache (last `last_changed` timestamp). On diff (or first observation), emit `ha.state_changed@v1` with `{entity_id, state, attributes, last_changed, last_updated, source="home_assistant"}`. Dedupe gate per [F-3 standing] keeps emissions load-bearing. `observation_mode_required: false` for this path (reading state is not a side effect per memo §8); the manifest's `observation_mode_required: true` applies to the Cat-E path only — surface this via the Cat-C `capabilities` entry's `observation_mode_required: false` override per [PM-35].

3. **Cat-E service-call seam** — `execute_action(request: ActionRequest)` accepts `kind="ha.service_call"` with payload `{domain, service, service_data}`. Builds the HA REST call to `POST /api/services/<domain>/<service>` with `service_data` as the JSON body. Wraps via `observation_outbound(session, "ha.service_call", payload, action_fn, manager, event_log, target_channel="home_assistant", target_identifier=f"{domain}.{service}")` per [§6.20]. On non-suppressed success: emit `action.executed@v1` + return `ActionResult(executed=True, external_id=<some_ha_correlation>, suppressed=False)`. On suppress: emit `observation.suppressed@v1` (already emitted by `outbound()` itself) + return `ActionResult(executed=False, external_id=None, suppressed=True)`. On `HttpError`: classify per `_classify_ha_error` (401/403 fatal, 429 transient rate_limited, 5xx transient network) → emit `action.failed@v1` + re-raise for supervisor backoff.

OUT OF SCOPE:

- **WebSocket subscription path** — Phase A polls `/api/states`; WebSocket optimization deferred to Phase B (memo §8 lists both options).
- **HA `action.requested` event consumer wiring** — 11g implements `execute_action`; the L2 mechanism that routes `action.requested@v1` events to Cat-E adapters lives in the supervisor (already on main from 11-γ) but the per-event-type dispatch logic is a follow-up if needed.
- **Bridge variant** — Home Assistant runs central-only per [D24]; no bridge variant.
- **Long-lived access token rotation** — operator-side via 1Password; bootstrap §8 (16) handles initial pairing.
- **Webhook trigger from HA → AdministrateMe** — out of scope; one-way (AdministrateMe → HA) only.
- **Real HA endpoint authentication** — Phase A uses `@pytest.mark.requires_live_services` + mocked HTTP client.
- **Cross-adapter changes** — all prior canonical builds + retrofits preserved byte-for-byte.

Citations: [D24] (HA multi-capability spec), [D19] (5-cat taxonomy), [PM-34] (5 ABCs unchanged; HA inherits ONE), [PM-35] (capabilities-as-list — multi-capability via list entries), [UT-27] N/A (Cat-E doesn't have write_capabilities — see Cat-E ABC; the per-operation gate concept lives on Cat-B only), [D7] (3 NEW v1 schemas additive), [D8] (correlation propagation through `ActionRequest.correlation_id` → emitted events), [§6.20] observation wrap on Cat-E `execute_action`, [§7.3, D17] (adapter never writes projection), [§8.2, D6] (no LLM SDK). Memo 05 §8, §3.2. [VISION §"Real family"]: HA is the family's automation surface — turning on lights, dispatching scenes, reading occupancy / climate state. 11g lands the canonical "read state + dispatch action" pattern that later prompts wire into Today / Scoreboard / household-automation flows.

## Objective

Build the canonical Home Assistant adapter at `packs/adapters/automation/home-assistant/`: multi-capability Cat-C state-read seam + Cat-E service-call seam. Three NEW v1 schemas. Inherits `OutboundActionAdapter`. First Cat-E reference impl on main.

## Out of scope

(See above.)

## Approach (architectural sketch)

Pattern transplants from 11b-iii Google Calendar fresh-pack structurally:
- `packs/adapters/automation/home-assistant/pack.yaml` (~50 lines): two capability entries (Cat-C iot + Cat-E home_automation); pack-level `observation_mode_required: true` (Cat-E governance); Cat-C entry's `observation_mode_required: false` override per [PM-35] capability-list shape.
- `config.schema.json` + `config.example.yaml` (~40 lines combined): `base_url` (e.g. `http://homeassistant.local:8123`), `access_token_ref` (1Password reference), `poll_interval_s`.
- `adapter.py` (≤500 lines): `class HomeAssistantAdapter(OutboundActionAdapter)`. ClassVars per [PM-34]; `__init__` with three observation-seam slots; `start(ctx)` builds httpx client + emits `adapter.authenticated@v1` + spawns `_poll_state_loop`; `_poll_state_loop` async; `_poll_states_once` GETs `/api/states` + diffs against `_seen_states` + emits `ha.state_changed@v1`; `execute_action(request)` body per §1 item 3; `_classify_ha_error`; `_emit_authenticated` / `_emit_error` / `_append` helpers (transplant from Google Calendar adapter).
- `adminme/events/schemas/automation.py` (NEW, ~80 lines): `HaStateChangedV1`, `ActionExecutedV1`, `ActionFailedV1` + 3 registry registrations.

## Deliverables

(c1 schemas; c2 fresh pack; c3 framework tests ≥15; c4 pack-internal + integration + BUILD_LOG.)

`docs/build_log.md` — append 11g entry; note **"First canonical Cat-E reference implementation; introduces multi-capability declaration per [PM-35]."**

## Verification

```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.automation import HaStateChangedV1, ActionExecutedV1, ActionFailedV1; assert registry.get('ha.state_changed', 1) is HaStateChangedV1; assert registry.get('action.executed', 1) is ActionExecutedV1; assert registry.get('action.failed', 1) is ActionFailedV1; print('3 schemas verified ✓')"
poetry run pytest tests/unit/adapters/automation/ packs/adapters/automation/home-assistant/tests/ tests/integration/test_adapter_home_assistant_supervisor.py -v
poetry run adminme adapters validate packs/adapters/automation/home-assistant
# Expected: exit 0; NO advisory_pending_retrofit; NO errors
bash scripts/verify_invariants.sh
```

## Stop

When the Home Assistant pack passes validator + tests + Cat-C poll emits `ha.state_changed@v1` + Cat-E `execute_action` routes via `outbound()` + observation suppression returns `ActionResult(executed=False, suppressed=True)`, c4 is done.

## What this PR is NOT

Parent draft only. v4 refactor follows in `partner/refactor-11g`.
