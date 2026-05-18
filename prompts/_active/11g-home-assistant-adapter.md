# Prompt 11g: Home Assistant Cat-C+E multi-capability adapter

**Phase:** L1 / Cohort D — first Cat-E reference implementation. Parent draft: `prompts/11g-home-assistant-adapter.md` (merged PR #189).
**Branch:** `claude/11g-c<N>` per c-band.
**Stop condition:** NEW pack `packs/adapters/automation/home-assistant/` declaring `class HomeAssistantAdapter(OutboundActionAdapter)` with `family="home_automation"`, multi-capability `capabilities=[Cat-C iot, Cat-E home_automation]`. Inbound `_poll_states` emits `ha.state_changed@v1`. Outbound `execute_action(kind="ha.service_call")` wraps `POST /api/services/<domain>/<service>` via `observation_outbound` per [§6.20]; emits `action.executed@v1` on success or `action.failed@v1` on classified error. 3 NEW v1 schemas. `adminme adapters validate ...` exits 0.
**Depends-on:** all prior canonical builds on main; parent draft merged (PR #189, main `c9ad20e4`).
**Quality-bar reference:** `packs/adapters/calendaring/google-calendar/adapter.py` (fresh-pack structural precedent) + `adminme/lib/adapters/outbound_action_base.py` (Cat-E ABC). Transplant the lazy-import + observation-seam-slots + `_classify_*_error` + `_emit_*` helpers from Google Calendar; add Cat-E `execute_action` per OutboundActionAdapter contract.
**Estimated duration:** c1 ~20 min · c2 ~90 min · c3 ~60 min · c4 ~60 min · total ~3.5-4 hrs.

---

## §1 Behavioral grounding

11g lands the first canonical Cat-E reference implementation. Multi-capability per [PM-35]: declares BOTH Cat-C and Cat-E in `capabilities: list` but inherits **one** ABC (Cat-E `OutboundActionAdapter`) per [PM-34].

Three load-bearing items:

1. **Multi-capability declaration** — pack.yaml `capabilities:` list contains TWO entries:
   - `{kind: Cat-C, runtime: central, event_families: [ha.state_changed], observation_mode_required: false}` (Cat-C reads are not side effects per memo §8).
   - `{kind: Cat-E, runtime: central, event_families: [action.executed, action.failed], observation_mode_required: true}` (Cat-E writes ARE side effects; observation wraps `execute_action`).
   - The pack-level `observation_mode_required: true` (top-level manifest field) reflects the Cat-E gate; the per-capability `observation_mode_required: false` override on the Cat-C entry is per-capability per [PM-35].
   - Class `capabilities: ClassVar[list[Any]]` mirrors the manifest.

2. **Cat-C state-read seam** — `_poll_state_loop` long-running async + `_poll_states_once`:
   - GET `<base_url>/api/states` with `Authorization: Bearer <access_token>` header.
   - Returns `list[StateDict]` where each `StateDict` has `{entity_id, state, attributes, last_changed, last_updated, ...}`.
   - For each state: lookup `_seen_states.get(entity_id)`; on first observation OR `last_changed` differs from cached → emit `ha.state_changed@v1` payload mapping; update `_seen_states[entity_id] = state["last_changed"]`.
   - Inbound emit's `correlation_id` from `ctx.correlation_id_factory()`; `causation_id` is None (poll-driven, not event-caused).
   - `_classify_ha_error` (mirror Google Calendar's) handles 401/403 → fatal `auth_failed`, 429 → transient `rate_limited`, 5xx → transient `network`.

3. **Cat-E service-call seam** — `execute_action(request: ActionRequest) -> ActionResult`:
   - Defense-in-depth: `if request.kind != "ha.service_call": raise WriteCapabilityError`-equivalent (HA uses ActionRequest's `kind` field; Cat-E doesn't have a write-capabilities gate, so raise `ValueError` instead).
   - Extract `domain = request.payload["domain"]`, `service = request.payload["service"]`, `service_data = request.payload.get("service_data", {})`.
   - Build `_do_service_call()` closure: `httpx.post(f"{base_url}/api/services/{domain}/{service}", json=service_data, headers={"Authorization": f"Bearer {token}"})`.
   - If observation seam slots populated → wrap via `await observation_outbound(session, "ha.service_call", {"domain": domain, "service": service, "service_data": service_data}, _do_service_call, manager=..., event_log=..., target_channel="home_assistant", target_identifier=f"{domain}.{service}")`. On `outbound_result.suppressed=True` → return `ActionResult(executed=False, external_id=None, suppressed=True)`. Otherwise emit `action.executed@v1` with `correlation_id=request.correlation_id` → return `ActionResult(executed=True, external_id=<ha-correlation-or-None>, suppressed=False)`.
   - If seam slots unset (Phase A) → call `_do_service_call()` directly + emit `action.executed@v1` + return `ActionResult(executed=True, ...)`.
   - On `httpx.HTTPStatusError` or generic exception → classify via `_classify_ha_error` → emit `action.failed@v1` → raise for supervisor backoff. **F-3 invariant**: on suppression NEITHER `action.executed` NOR `action.failed` is emitted (only `observation.suppressed` emitted by `outbound()` itself).

OUT OF SCOPE (parent draft §"Operating context"):
- WebSocket subscription path (Phase B optimization).
- L2 routing of `action.requested@v1` events to Cat-E adapters (supervisor wires this; this prompt implements `execute_action` only).
- Bridge variant.
- Real auth (mocked + `requires_live_services` gate).
- Webhook from HA → AdministrateMe (one-way only).
- Cross-adapter changes.

Citations: [D24] (HA multi-capability), [D19] (5-cat taxonomy), [PM-34] (inherit one ABC), [PM-35] (capabilities-as-list multi-capability), [§6.20] observation wrap on Cat-E, [§7.3, D17] (no projection writes), [§8.2, D6] (no LLM SDK), [D7] (3 additive v1 schemas), [D8] (correlation propagation). Memo 05 §8, §3.2.

## §2 Read first

**c1 (3 NEW v1 schemas) — 3 files**
- `prompts/11g-home-assistant-adapter.md` (merged parent).
- `adminme/events/schemas/lists.py` (on main) — most-recent additive-schemas-module precedent for `automation.py` shape.
- `adminme/events/registry.py` — registration API.

**c2 (fresh pack) — 6 files**
- `adminme/lib/adapters/outbound_action_base.py` (on main) — Cat-E ABC contract: `ActionRequest` / `ActionResult` / `execute_action` abstract / `observation_mode_required: ClassVar[bool] = True`.
- `adminme/lib/observation.py` — `outbound(...)` signature.
- `packs/adapters/calendaring/google-calendar/adapter.py` (on main) — fresh-pack precedent. **Heavy transplant**: lazy import (no `googleapiclient` here — use `httpx` instead per memo §8), observation seam slots, `_classify_*_error`, `_emit_authenticated/_emit_error/_append`.
- `packs/adapters/calendaring/google-calendar/pack.yaml` — pack.yaml shape precedent (single-capability); 11g extends to multi-capability per [PM-35].
- `adminme/adapters/base.py` — `AdapterContext` shape.
- `adminme/events/schemas/automation.py` (on c1 branch) — reference for imports in adapter.py.

**c3 (framework tests) — 5 files**
- `tests/unit/adapters/calendaring/test_google_calendar.py` (on main) — fresh-pack test precedent (≥15 tests pattern).
- `adminme/events/schemas/automation.py` — assertion targets for emit-payload validation.
- `packs/adapters/automation/home-assistant/adapter.py` (on c2 branch).
- `adminme/lib/observation.py` — `OutboundResult` import for suppress-path test.
- `prompts/_active/11b-iv-google-tasks-central.md` — c3 deliverable shape (most-recent fresh-Cat-B precedent).

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**
- `packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py` (on main) — pack-internal precedent.
- `tests/integration/test_adapter_google_calendar_supervisor.py` (on main) — integration precedent.
- `docs/build_log.md` (append).
- `prompts/_active/11b-iv-google-tasks-central.md` (c4 BUILD_LOG shape).

## §3 Reconciliation notes

- **F-1 (HARD/standing): READ FILE CONTENTS for schemas in c1.** Grep `registry.register("ha.state_changed"` / `action.executed` / `action.failed` across `adminme/events/schemas/*.py` before authoring; confirm no prior shadow.
- **F-2 (HARD/standing): no LLM SDK.** Pack uses `httpx` only; no `googleapiclient` (Google Calendar uses it for Gmail/Calendar but HA is a REST API).
- **F-3 (HARD/standing): observation-suppressed path emits NEITHER `action.executed` NOR `action.failed`.** `outbound()` itself emits `observation.suppressed@v1`. Pinned by c3 test.
- **F-4 (HARD/standing): pack-internal basename uniqueness.** `test_home_assistant_pack_internal.py` unique against all prior basenames (verified — `home_assistant` prefix new).
- **F-5 (SOFT): adapter.py diff ≤500 lines** (matches 11b-iii/iv fresh-pack ceiling).
- **F-6 (HARD/from 11a c4): NO empty `__init__.py` in pack-internal tests dir.** `packs/adapters/automation/home-assistant/tests/` directory carries NO `__init__.py`. `tests/unit/adapters/automation/__init__.py` IS required (framework-test package marker).
- **F-7 (HARD/new for 11g): Multi-capability per [PM-35].** Pack.yaml + ClassVar declare TWO capability entries. Per-capability `observation_mode_required` override on the Cat-C entry (`false`) overrides the pack-level Cat-E `true`. The validator's `observation_mode_wrapping` AST-walk only checks Cat-E paths (where the wrap is required) — the Cat-C `_poll_states_once` path does NOT route through `outbound()` (correct; reading state is not a side effect per memo §8).
- **F-8 (SOFT/new): `_classify_ha_error` duck-types `httpx.HTTPStatusError`.** Same pattern as Google Calendar's HttpError duck-typing — read `.response.status_code` if present. Includes `httpx.ConnectError` / `httpx.TimeoutException` as transient `network`.

## §4 Assumptions to verify

- **A-1: `OutboundActionAdapter.observation_mode_required` is `ClassVar[bool] = True`** with a comment saying it cannot be opted out. Verified.
- **A-2: `ActionRequest` field set** — `{kind, target_external_id, payload, correlation_id}` per the ABC source.
- **A-3: `httpx` is in `pyproject.toml`** — verified at canonical-11a authoring (Gmail uses httpx as a transitive dep of `google-api-python-client`; HA uses it directly). Confirm at orientation.
- **A-4: `Tailscale-User-Login`-style identity NOT relevant for HA** — HA auth is the long-lived access token in the `Authorization: Bearer` header. Operator-side config carries the token reference; bootstrap §8 (16) writes the reference.
- **A-5: HA returns `/api/states` as JSON list** of state dicts. Use a typed `StateDict = dict[str, Any]` (no strict Pydantic model needed for inbound — schema validation happens at emit boundary against `HaStateChangedV1`).
- **A-6: HA REST returns 200 on successful service call** — `POST /api/services/<domain>/<service>` returns 200 + an array of changed-state dicts; the adapter doesn't need to parse the response body for the canonical flow (just confirms 2xx → executed).

## §5 Objective

Build `packs/adapters/automation/home-assistant/` (fresh multi-capability Cat-C+E pack). Inherits `OutboundActionAdapter`. Cat-C inbound poll emits `ha.state_changed@v1`. Cat-E outbound `execute_action` for `kind="ha.service_call"` wraps HA REST via `observation_outbound`. 3 new v1 schemas.

## §6 Out of scope

(See parent draft.)

## §7 Deliverables

**c1: NEW `adminme/events/schemas/automation.py` (~85 lines)** — three Pydantic v1 models:

```python
class HaStateChangedV1(BaseModel):
    model_config = {"extra": "forbid"}
    entity_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)
    last_changed: datetime
    last_updated: datetime
    source: Literal["home_assistant"]

class ActionExecutedV1(BaseModel):
    model_config = {"extra": "forbid"}
    action_kind: str = Field(min_length=1)
    target_external_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    external_id: str | None = None
    executed_at: datetime
    correlation_id: str | None = None

class ActionFailedV1(BaseModel):
    model_config = {"extra": "forbid"}
    action_kind: str = Field(min_length=1)
    target_external_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error_message: str = Field(min_length=1, max_length=2000)
    severity: Literal["fatal", "transient"]
    error_kind: str = Field(min_length=1)
    failed_at: datetime
    correlation_id: str | None = None

registry.register("ha.state_changed", 1, HaStateChangedV1)
registry.register("action.executed", 1, ActionExecutedV1)
registry.register("action.failed", 1, ActionFailedV1)
```

**c2: NEW pack at `packs/adapters/automation/home-assistant/` (~500 lines)**:
1. `pack.yaml` (~55 lines) — multi-capability per [PM-35].
2. `config.schema.json` + `config.example.yaml` (~40 combined).
3. `adapter.py` (≤500 lines) — `class HomeAssistantAdapter(OutboundActionAdapter)`:
   - ClassVars: `name`, `family: Literal["home_automation"]="home_automation"`, `version`, `sensitivity_floor="normal"`, `capabilities` (2 entries), `observation_mode_required=True` (Cat-E default).
   - `__init__`: config + 3 obs seam slots + `_seen_states: dict[str, str]` + `_client: httpx.AsyncClient | None`.
   - `start(ctx)`: build `httpx.AsyncClient` with bearer auth; emit `adapter.authenticated@v1`; spawn `_poll_state_loop`.
   - `stop()`: close client.
   - `status()`: lifecycle.
   - `_poll_state_loop` / `_poll_states_once`: poll + dedupe + emit.
   - `_emit_state_changed(ctx, state)`: map HA state → `HaStateChangedV1` payload.
   - `execute_action(request)`: Cat-E body per §1 item 3.
   - `_classify_ha_error(exc)`: 401/403→fatal auth_failed; 429→transient rate_limited; 5xx→transient network; httpx connect/timeout→transient network.
   - `_emit_authenticated/_emit_error/_append`: transplanted helpers.

**c3: `tests/unit/adapters/automation/test_home_assistant.py` (NEW, F-4 unique, ≥15 tests)** + `tests/unit/adapters/automation/__init__.py`:

1. `test_home_assistant_adapter_inherits_outbound_action_adapter`.
2. `test_family_class_attribute_is_home_automation_literal`.
3. `test_capabilities_contains_cat_c_and_cat_e_entries` (multi-capability per [PM-35]).
4. `test_observation_mode_required_is_true_at_pack_level`.
5. `test_cat_c_capability_overrides_observation_mode_required_false`.
6. `test_poll_states_once_emits_ha_state_changed_on_first_observation`.
7. `test_poll_states_once_skips_seen_entity_with_unchanged_last_changed`.
8. `test_poll_states_emit_payload_validates_against_ha_state_changed_v1` (`model_validate`).
9. `test_execute_action_dispatches_to_ha_service_call_kind`.
10. `test_execute_action_routes_through_observation_outbound_when_seams_populated`.
11. `test_execute_action_observation_suppressed_returns_unapplied` (F-3 — NEITHER `action.executed` NOR `action.failed` emitted).
12. `test_execute_action_direct_call_when_seams_unset_returns_applied`.
13. `test_execute_action_4xx_auth_failure_classifies_fatal_emits_action_failed`.
14. `test_execute_action_5xx_classifies_transient_emits_action_failed`.
15. `test_execute_action_429_classifies_rate_limited_transient`.
16. `test_execute_action_undeclared_kind_raises_value_error`.
17. `test_three_v1_schemas_registry_lookup_returns_models` (c1 contract).

**c4: pack-internal + integration + BUILD_LOG**:
1. `packs/adapters/automation/home-assistant/tests/test_home_assistant_pack_internal.py` (NEW, F-4 unique, ≥3 tests; NO `__init__.py` per F-6).
2. `tests/integration/test_adapter_home_assistant_supervisor.py` (NEW, ≥3 tests).
3. `docs/build_log.md` — 11g entry. Mark **"First canonical Cat-E reference implementation."**

## §8 Commit structure

- **c1: `phase 11g commit 1: register ha.state_changed, action.executed, action.failed v1 schemas`**.
- **c2: `phase 11g commit 2: HomeAssistantAdapter multi-capability Cat-C+E pack`**.
- **c3: `phase 11g commit 3: framework-level Home Assistant tests`**.
- **c4: `phase 11g commit 4: pack-internal + integration tests + BUILD_LOG`**.

## §8.5 Branch strategy — STACKED

Non-overlapping c-band file scopes. Zero ghost conflicts expected.

Test floor: ≥17 framework + ≥3 pack-internal + ≥3 integration = **≥23 total**.

## §9 Verification

(Per c-band; full set at c4 per §"Verification" in parent draft.)

## §10 Stop

When pack passes validator + tests + Cat-C poll emits `ha.state_changed@v1` + Cat-E `execute_action` routes via `outbound()` + observation suppression returns `ActionResult(executed=False, suppressed=True)` with neither action.executed nor action.failed emitted, c4 is done. First Cat-E demo on main.
