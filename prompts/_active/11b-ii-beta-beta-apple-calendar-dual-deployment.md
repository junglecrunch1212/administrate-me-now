# Prompt 11b-ii-β-β: Apple Calendar dual-deployment (AppleCalendarAdapter + bridge connector-pack)

**Phase:** L1 / Cohort D — second of the two-step 11b-ii-β split per `docs/11-split-memo-11b-ii-beta.md`. Adds dual-deployment to the central Apple Calendar Cat-B pack 11b-ii-β-α shipped. Mirrors 11b-ii-α-2 (Apple Reminders dual-deployment) one-for-one. Parent split memos: `docs/04-split-memo-11b-ii.md` §2, `docs/11-split-memo-11b-ii-beta.md` §2.
**Branch:** `claude/11b-ii-beta-beta-c<N>` per c-band (Executor's harness assigns).
**Stop condition:** `packs/adapters/calendaring/apple-calendar/pack.yaml` declares top-level `subkind: state-mirror` + `deployment_targets: [central, bridge]`; `config.schema.json` accepts a `deployment_target` enum; `AppleCalendarAdapter` reads `deployment_target` from `ctx.config` and branches credential source + emit path (central byte-for-byte preserved per F-2; bridge emits via a wrapper-injected `IngestClientBridgeSink`); NEW `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` connector-pack scaffolds a THIN `AppleCalendarBridgeAdapter` satisfying α-1's `StateMirrorBridgeAdapter` Protocol. `adminme adapters validate packs/adapters/calendaring/apple-calendar` exits 0. `verify_invariants.sh` exits 0.
**Depends-on:** 11b-ii-β-α closed (main `4e052412` — the central `apple-calendar` pack); 11b-ii-α-2 closed (main `0de63b19` — the dual-deployment template); 11b-ii-α-1 closed (the bridge runtime Cat-B on-ramp); `docs/11-split-memo-11b-ii-beta.md` merged (PR #223, main `29bea8cd`).
**Quality-bar reference:** `prompts/_active/11b-ii-alpha-2-apple-eventkit-dual-deployment.md` (the dual-deployment template — β-β mirrors it 1:1, `apple-eventkit` → `apple-calendar`) + `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/wrapper.py` (the connector-pack wrapper template).
**Estimated duration:** c1 ~20 min (manifest + config schema) · c2 ~65 min (adapter deployment_target branching) · c3 ~70 min (connector-pack + framework tests) · c4 ~55 min (integration + pack-internal + BUILD_LOG) · total ~3.5-4 hrs wall.

---

## §1 Behavioral grounding

11b-ii-β-β closes the Apple Calendar dual-deployment work begun by 11b-ii-β-α's central pack. Per [D18] / [D19] / [D22] / memo 04 §2.2: the `AppleCalendarAdapter` must run in TWO deployments — **central** (the shared assistant Apple ID on the CoS Mac Mini, mirrors the household calendar) and **per-member bridge** (each family member's bridge Mac Mini signed into that member's iCloud, owner-scoped to `private:<member_id>`). Both deployments emit the existing `calendar.event_added@v1` — no new event schemas. This is the Apple-Calendar twin of 11b-ii-α-2 (Apple Reminders dual-deployment); it reuses α-1's bridge runtime Cat-B on-ramp and follows the α-2 pattern 1:1.

Four load-bearing items (mirroring 11b-ii-α-2 §1):

1. **Manifest declares dual deployment** (`packs/adapters/calendaring/apple-calendar/pack.yaml`). Add a top-level `subkind: state-mirror` line (so α-1's bridge loader `STATE_MIRROR_SUBKIND` allow-list picks the pack up) AND a top-level `deployment_targets: [central, bridge]` field. The `capabilities[*].runtime` row stays `central` (the deployment switch lives in the top-level field + the config enum, not the capability list). Backwards-compatibility: existing β-α instances whose config omits `deployment_target` default to `"central"`.

2. **Config schema accepts `deployment_target` enum** (`packs/adapters/calendaring/apple-calendar/config.schema.json`). Add `"deployment_target": {"type": "string", "enum": ["central", "bridge"], "default": "central"}` as an OPTIONAL property — NOT added to `required` (F-2 backwards-compat; β-α's existing `calendar_identifier` + `member_id` required fields are unchanged).

3. **`AppleCalendarAdapter` reads `deployment_target` from `ctx.config`** (`packs/adapters/calendaring/apple-calendar/adapter.py`). In `start()`: `deployment_target = ctx.config.get("deployment_target", "central")`; reject any value not in `{"central", "bridge"}` with a typed `ValueError`. Branch:
   - **central path** (existing): byte-for-byte the β-α behavior — emit via `ctx.event_log.append_sync(...)` through the existing `_append`; `sharing_model` value `"shared_central"`.
   - **bridge path** (NEW): the bridge wrapper synthesizes `ctx.config` carrying `_bridge_member_id` + `_bridge_sink` (an α-1 `BridgeEventSink`); the emit path dispatches the envelope to `ctx.config["_bridge_sink"].emit(envelope)`; `sharing_model` value `"per_member_isolated"`.
   F-2 (HARD): the central code path is byte-for-byte preserved when `deployment_target=="central"`. F-4 (HARD): the observation-mode wrap in `_apply_write_impl` is unchanged and applies uniformly — a suppressed outbound emits NEITHER the save NOR any event on BOTH deployment paths.

4. **Bridge connector-pack scaffold** (NEW `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/`). Three files mirroring α-2's `reminders_apple_eventkit_bridge/`:
   - `__init__.py`: re-exports `AppleCalendarBridgeAdapter`.
   - `pack.yaml`: `kind: adapter`, `subkind: state-mirror`, `family: calendaring`, `pack.name: calendaring-apple-calendar-bridge`, `entrypoint: adminme_bridge.connector_packs.calendaring_apple_calendar_bridge:AppleCalendarBridgeAdapter`.
   - `wrapper.py`: NEW `class AppleCalendarBridgeAdapter` satisfying α-1's `StateMirrorBridgeAdapter` Protocol structurally — `family: ClassVar[Literal["calendaring"]] = "calendaring"`, plus `async start(*, ctx: BridgeContext)` / `async stop()` / `status() -> AdapterStatus`. It HOLDS an internal `AppleCalendarAdapter`, loaded by file path (the `apple-calendar` dir is hyphenated, not dotted-importable); on `start(ctx)` it (a) resolves the sink (`ctx.sink` if set else `IngestClientBridgeSink(ctx.ingest_client)`), (b) synthesizes a central `AdapterContext` with `event_log=None`, `config={..., "deployment_target": "bridge", "_bridge_member_id": ctx.member_id, "_bridge_sink": sink}`, (c) delegates to the internal adapter's `start`. F-3 (HARD): the wrapper is THIN (≤80 LOC) — it re-implements NO EKEventStore poll / observation / adapter logic; composition not multi-inheritance per [PM-34].

OUT OF SCOPE (§6): new event schemas; new `family` Literal values; central `Adapter` Protocol changes; bridge daemon / `BridgeSupervisor` changes (α-1 already widened `BridgeSupervisor.adapters: list[BridgeAdapter]`); `calendar.event_updated` / `calendar.event_deleted` emissions; `update_event` / `delete_event` write_capabilities; the bootstrap §16-§10 iCloud-enrollment wizard.

Citations: [D18] (Reminders/Calendar dual-deployment per memo 05 §5.4), [D19] (5-cat taxonomy; runtime substrate orthogonal), [D22] (Apple Calendar dual-deployment lineage), [§6.19] (bridge sovereignty), [§6.20] (observation wrap on outbound), [§7.3, D17] (adapters never write projections), [§8.2, D6] (no LLM SDK), [PM-29] (`bridge/` not `adminme/`), [PM-34] (5 ABCs — composition not multi-inheritance), [UT-27] (asymmetric write_capabilities — unchanged from β-α). Memo 04 §2.2 (Tailscale-bound bridge identity; the central `:3337` router stamps `owner_scope`). `docs/11-split-memo-11b-ii-beta.md` §2 (β-β contract). [VISION §"Real family"]: Apple Calendar dual-deployment puts each family member's private calendar on their own bridge while the shared household calendar runs central — the sovereignty model 11b-ii-α-2 established for Reminders, now for Calendars.

## §2 Read first (required)

**c1 (manifest + config schema) — 4 files**

- `docs/11-split-memo-11b-ii-beta.md` §2 (β-β scope — canonical contract, main `29bea8cd`).
- `prompts/_active/11b-ii-alpha-2-apple-eventkit-dual-deployment.md` §1 + §7 c1 (the manifest + config-schema extension template).
- `packs/adapters/calendaring/apple-calendar/pack.yaml` (on main from β-α) — current manifest; β-β extends.
- `packs/adapters/calendaring/apple-calendar/config.schema.json` (on main from β-α) — current schema; β-β extends.

**c2 (adapter deployment_target branching) — 6 files**

- `packs/adapters/calendaring/apple-calendar/adapter.py` (on main from β-α — **READ FULL**; the central class to extend).
- `packs/adapters/reminders/apple-eventkit/adapter.py` (on main, **READ the dual-deployment sections via `sed`** — the file is large) — α-2's `deployment_target` branching + `_append_via_bridge_sink` pattern.
- `bridge/adminme_bridge/adapters/sinks.py` (on main from α-1) — `BridgeEventSink` Protocol + `IngestClientBridgeSink`.
- `adminme/adapters/base.py` (on main) — `AdapterContext`.
- `adminme/events/envelope.py` (on main) — `EventEnvelope`; the bridge emit path constructs one.
- `prompts/_active/11b-ii-alpha-2-apple-eventkit-dual-deployment.md` §1 + §3 (the F-2 / F-3 / F-4 discipline).

**c3 (bridge connector-pack + framework tests) — 6 files**

- `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/wrapper.py` (on main from α-2) — the THIN wrapper template; β-β mirrors it.
- `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/pack.yaml` (on main from α-2) — connector-pack manifest template.
- `bridge/adminme_bridge/adapters/base.py` (on main from α-1) — `StateMirrorBridgeAdapter` Protocol + `BridgeContext` + `AdapterStatus`.
- `packs/adapters/calendaring/apple-calendar/adapter.py` (on the c2 branch) — the internal adapter the wrapper holds.
- `bridge/tests/unit/connector_packs/test_apple_eventkit_bridge_wrapper.py` (on main from α-2) — wrapper-test precedent.
- `tests/unit/adapters/reminders/test_apple_eventkit_dual_deployment.py` (on main from α-2) — dual-deployment framework-test precedent.

**c4 (integration + pack-internal + BUILD_LOG) — 4 files**

- `tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py` (on main from α-2) — integration rig precedent (`BridgeSupervisor` + `IngestClient` + `httpx.ASGITransport` + central FastAPI ingest router).
- `packs/adapters/calendaring/apple-calendar/tests/` (on main from β-α) — F-6 no-`__init__.py` pack-internal dir.
- `docs/build_log.md` (on main, **append the c4 entry**; the file is ~400 KB — if `push_files` exceeds the inline budget, the Executor stops and Partner re-fires the c4 step via Chrome MCP `file_upload` per Partner memory).
- `prompts/_active/11b-ii-alpha-2-apple-eventkit-dual-deployment.md` §7 c4 (BUILD_LOG entry shape).

## §3 Reconciliation notes (HARD/SOFT findings)

- **F-1 (HARD/standing from cycle #17): READ β-α's `adapter.py` FULL before authoring c2.** No new schemas are registered. Confirm the `_append` chokepoint + `_emit_calendar_event_added` + `_emit_authenticated` / `_emit_error` emit sites are exactly as β-α (PR #226) left them BEFORE adding the `deployment_target` branch.
- **F-2 (HARD): central path byte-for-byte preserved.** `deployment_target=="central"` (default) → β-α behavior identical: same `_append` → `ctx.event_log.append_sync`, same poll loop, same observation wrap. Pinned by c3 regression tests.
- **F-3 (HARD): the bridge wrapper is THIN (≤80 LOC).** `AppleCalendarBridgeAdapter` re-implements NO EKEventStore poll, dedupe, observation wrap, or adapter logic — all of that stays in the central `AppleCalendarAdapter`. The wrapper SOLELY: (a) satisfies `StateMirrorBridgeAdapter` Protocol (declare the `family` ClassVar), (b) synthesizes a central `AdapterContext` from a `BridgeContext`, (c) injects the `IngestClientBridgeSink`, (d) forwards `stop()` / `status()`.
- **F-4 (HARD/standing): observation-mode suppression uniform across both deployments.** The `_apply_write_impl` observation wrap is unchanged — a suppressed outbound emits NEITHER the EKEvent save NOR `calendar.event_added` NOR `adapter.error`, on BOTH central and bridge. Pinned by a c3 test.
- **F-5 (HARD/new): the bridge emit path MUST retain emit-Task references.** When the adapter dispatches a bridge emit from a sync call site via `loop.create_task(...)`, it MUST hold a reference to the returned `Task` — keep a `set[asyncio.Task]` on the instance, add on create, discard in a done-callback. An unreferenced fire-and-forget task can be garbage-collected mid-flight and silently drop the emit. **Do NOT copy the unreferenced-`create_task` pattern from α-2's `_append_via_bridge_sink`** — that is a known defect carried in the 11b-ii-α-2 + 11b-ii-β-α QC findings; β-β writes this emit path fresh and writes it correctly.
- **F-6 (HARD/standing): test basename uniqueness + no pack-internal `__init__.py`.** Proposed basenames `test_apple_calendar_dual_deployment.py` (c3 framework), `test_apple_calendar_bridge_wrapper.py` (c3 framework), `test_apple_calendar_dual_deployment_pack_internal.py` (c4 pack-internal), `test_adapter_apple_calendar_dual_deployment_integration.py` (c4 integration) — all unique across the repo. NO `__init__.py` in `packs/adapters/calendaring/apple-calendar/tests/` (the dir already exists from β-α without one). `bridge/tests/unit/connector_packs/__init__.py` already exists from α-2 — do NOT recreate it.
- **F-7 (SOFT): c2 `adapter.py` diff ≤120 lines added.** β-α's `adapter.py` is 499 lines; the dual-deployment extension legitimately pushes the file past 500 — that is EXPECTED (the ≤500 ceiling was β-α's fresh-pack ceiling, not a β-β cap). Keep the c2 *diff* itself ≤120 lines.
- **F-8 (SOFT/opportunistic): `_poll_loop` `CancelledError` hardening.** While editing `adapter.py` for c2, the Executor MAY also re-raise `asyncio.CancelledError` in `_poll_loop`'s `except BaseException` branch (β-α QC Job 2 finding — currently a mid-poll cancellation is swallowed for one cycle and emits a spurious `adapter.error`). One-line in-scope hardening of the adapter being made dual-deployment. If skipped, it carries forward to a sidecar.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `AppleCalendarAdapter` constructor stability.** `deployment_target` is read in `start()` from `ctx.config`, NOT at construction; β-α's `__init__` signature (`config`, `ek_event_store_factory`, `max_poll_cycles`, three `observation_*` slots) is unchanged.
- **A-2: `sharing_model` value placement.** Central `"shared_central"` / bridge `"per_member_isolated"`, surfaced as an adapter INSTANCE ATTRIBUTE — NOT an event payload field (the `calendar.event_added@v1` schema is `extra="forbid"` and β-β registers no schema; this mirrors α-2's resolution and β-α's existing `sharing_model_discriminator` ClassVar).
- **A-3: bridge `member_id`.** The wrapper forces the internal adapter's `member_id` to `ctx.member_id` (the bridge daemon's configured per-member identity) per [§6.19] / memo 04 §2.2.
- **A-4: sink imports.** `IngestClientBridgeSink` / `BridgeEventSink` import from `adminme_bridge.adapters.sinks` (α-1).
- **A-5: `BridgeContext.sink` precedence.** The wrapper prefers `ctx.sink` if set, else builds `IngestClientBridgeSink(ctx.ingest_client)` (per α-1 F-3 — mirrors α-2's A-5).

## §5 Objective

Ship Apple Calendar dual-deployment per [D18] / [D22] / memo 04 §2.2: the `AppleCalendarAdapter` reads `deployment_target` from config and branches credential source + emit path; the bridge connector-pack at `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` wraps the central adapter to satisfy α-1's `StateMirrorBridgeAdapter` Protocol; both deployment paths emit the existing `calendar.event_added@v1`. No new event schemas. Central deployment behavior preserved byte-for-byte when `deployment_target` defaults to `"central"`.

## §6 Out of scope

- New event schemas; new `family` Literal values.
- Central `Adapter` Protocol changes (`adminme/adapters/base.py`) — would require PM-28; not triggered.
- Bridge daemon / `BridgeSupervisor` changes — α-1 already widened `BridgeSupervisor.adapters`; β-β adds NO supervisor surface.
- `calendar.event_updated@v1` / `calendar.event_deleted@v1` emissions.
- `update_event` / `delete_event` write capabilities.
- The bootstrap §16-§10 iCloud-enrollment wizard.
- Any cross-adapter or cross-pack changes beyond the `apple-calendar` pack + its new bridge connector-pack.

## §7 Deliverables

**c1: pack manifest + config schema extensions (~35 lines net diff)**:

1. `packs/adapters/calendaring/apple-calendar/pack.yaml` (EXTEND): add a top-level `subkind: state-mirror` line + a top-level `deployment_targets: [central, bridge]` list; preserve all existing keys + ordering.
2. `packs/adapters/calendaring/apple-calendar/config.schema.json` (EXTEND): add `"deployment_target": {"type": "string", "enum": ["central", "bridge"], "default": "central", "description": "..."}` under `properties`; do NOT add it to `required`.

c1 verification:
```bash
poetry run adminme adapters validate packs/adapters/calendaring/apple-calendar
poetry run python -c "import json; s=json.load(open('packs/adapters/calendaring/apple-calendar/config.schema.json')); assert s['properties']['deployment_target']['default']=='central'; print('c1 ok')"
```

**c2: `AppleCalendarAdapter` deployment_target branching (~80-120 lines diff, F-7)**:

`packs/adapters/calendaring/apple-calendar/adapter.py` (EXTEND):
- `__init__`: add `self._deployment_target`, `self._sharing_model_value`, `self._bridge_sink` instance attributes (defaults preserve the central path per F-2); add a `set[asyncio.Task]` for emit-task references per F-5.
- `start()`: read `deployment_target = ctx.config.get("deployment_target", "central")`; reject unknown values with `ValueError`; set `_sharing_model_value` (`"shared_central"` / `"per_member_isolated"`); on the bridge branch read `_bridge_member_id` + the required `_bridge_sink` from `ctx.config`.
- emit path: branch in `_append` (the existing chokepoint) — `deployment_target=="bridge"` dispatches through a NEW `_append_via_bridge_sink` helper that builds an `EventEnvelope` and dispatches `sink.emit(envelope)`; per F-5 the helper retains the `loop.create_task(...)` Task in the instance `set` with a done-callback discard. Central path unchanged (F-2).
- All other adapter logic (poll loop, dedupe, observation wrap, `_emit_*` helpers) preserved byte-for-byte.

c2 verification:
```bash
poetry run mypy packs/adapters/calendaring/apple-calendar/ 2>&1 | tail -5
poetry run python -c "from packs.adapters.calendaring.apple_calendar.adapter import AppleCalendarAdapter; print('c2 ok')"
```

**c3: NEW `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` (3 files, ≤120 lines) + framework tests (≥14 NEW)**:

1. `__init__.py` (re-exports `AppleCalendarBridgeAdapter`).
2. `pack.yaml` (`kind: adapter`, `subkind: state-mirror`, `family: calendaring`, `pack.name: calendaring-apple-calendar-bridge`, `entrypoint: adminme_bridge.connector_packs.calendaring_apple_calendar_bridge:AppleCalendarBridgeAdapter`, `version: 1.0.0`).
3. `wrapper.py` (`AppleCalendarBridgeAdapter`, ≤80 LOC per F-3 — mirrors α-2's `AppleEventKitBridgeAdapter`).
4. Framework tests (≥14 NEW): `tests/unit/adapters/calendaring/test_apple_calendar_dual_deployment.py` (≥10 — deployment_target default/branch, central vs bridge emit dispatch, F-2 central preservation, F-4 observation suppression uniform across deployments, F-5 emit-Task retained, unknown deployment_target raises) + `bridge/tests/unit/connector_packs/test_apple_calendar_bridge_wrapper.py` (≥4 — Protocol satisfaction, AdapterContext synthesis, A-5 sink precedence, stop/status forwarding).

c3 verification:
```bash
poetry run pytest tests/unit/adapters/calendaring/test_apple_calendar_dual_deployment.py bridge/tests/unit/connector_packs/test_apple_calendar_bridge_wrapper.py -v
# Expected: ≥14 passed; 0 regressions on β-α's existing apple-calendar tests
```

**c4: integration + pack-internal + BUILD_LOG (≥6 NEW)**:

1. `packs/adapters/calendaring/apple-calendar/tests/test_apple_calendar_dual_deployment_pack_internal.py` (≥3, no `__init__.py` per F-6 — manifest declares `subkind` + `deployment_targets`; config schema accepts the enum with central default; validator still exits clean with no `advisory_pending_retrofit`).
2. `tests/integration/test_adapter_apple_calendar_dual_deployment_integration.py` (≥3 — bridge supervisor loads the new connector-pack; central supervisor regression default-`central`; end-to-end bridge wrapper → `IngestClientBridgeSink` → central `:3337` router → `EventLog` with `owner_scope` stamped per [§6.19]).
3. `docs/build_log.md` (APPEND) — the 11b-ii-β-β entry per the α-2 c4 BUILD_LOG shape (Refactored / Session merged / Outcome / Evidence / Carry-forward — closes 11b-ii-β and the 11b-ii cohort).

c4 verification:
```bash
poetry run pytest tests/unit/adapters/calendaring/ packs/adapters/calendaring/apple-calendar/tests/ tests/integration/test_adapter_apple_calendar_dual_deployment_integration.py -v
poetry run adminme adapters validate packs/adapters/calendaring/apple-calendar
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0; zero regressions on β-α / α-2 / α-1 tests.
```

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11b-ii-beta-beta commit <N>: <imperative summary>` exactly.

- **c1: `phase 11b-ii-beta-beta commit 1: pack manifest + config schema deployment_target extensions`** — `pack.yaml` + `config.schema.json` (≤35 lines net diff).
- **c2: `phase 11b-ii-beta-beta commit 2: AppleCalendarAdapter deployment_target branching`** — `adapter.py` (≤120 lines net diff per F-7).
- **c3: `phase 11b-ii-beta-beta commit 3: bridge connector-pack scaffold + framework tests`** — `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` (NEW) + the two NEW framework test files.
- **c4: `phase 11b-ii-beta-beta commit 4: integration + pack-internal tests + BUILD_LOG`** — NEW pack-internal + integration test files + `docs/build_log.md` append.

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch; PRs open with `base=main`. Ghost-conflict risk: LOW — c-band file scopes are non-overlapping (c1 = pack manifest files; c2 = `adapter.py` only; c3 = NEW bridge connector-pack + NEW test files; c4 = NEW pack-internal + integration + `build_log.md` append). If `push_files` hits the inline budget at c4 (`build_log.md` is ~400 KB), the Executor stops and Partner re-fires the c4 step via Chrome MCP `file_upload` per Partner memory.

Test floor: ≥10 c3 framework (dual-deployment) + ≥4 c3 framework (bridge wrapper) + ≥3 c4 pack-internal + ≥3 c4 integration = **≥20 total NEW**.

## §9 Verification

(Per c-band per §7. c4 final:)
```bash
poetry run pytest tests/unit/adapters/calendaring/ bridge/tests/unit/ packs/adapters/calendaring/apple-calendar/tests/ tests/integration/test_adapter_apple_calendar_dual_deployment_integration.py -v
poetry run adminme adapters validate packs/adapters/calendaring/apple-calendar
poetry run mypy adminme/ bridge/adminme_bridge/ packs/adapters/calendaring/apple-calendar/ 2>&1 | tail -10
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0; zero regressions on existing β-α / α-2 / α-1 tests.
```

## §10 Stop

When `packs/adapters/calendaring/apple-calendar/pack.yaml` declares `subkind: state-mirror` + `deployment_targets: [central, bridge]` AND `config.schema.json` accepts the `deployment_target` enum AND `AppleCalendarAdapter` branches on `ctx.config["deployment_target"]` with the central path byte-for-byte preserved (F-2) AND `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/AppleCalendarBridgeAdapter` satisfies the `StateMirrorBridgeAdapter` Protocol AND the bridge supervisor loads the new connector-pack AND the integration test demonstrates the bridge round-trip with `owner_scope` stamped from Tailscale identity AND existing β-α / α-2 / α-1 tests pass unchanged AND `verify_invariants.sh` exits 0, c4 is done. Open each PR with `base=main` per the stacked strategy. **This closes 11b-ii-β and the entire 11b-ii cohort.** Do NOT proceed to Z2 = 11c-ii without operator review.

After merge: Partner runs the three-job QC.
