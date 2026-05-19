# Prompt 11b-ii-α-2: Apple Reminders dual-deployment (AppleEventKitAdapter + bridge connector-pack)

**Phase:** L1 / Cohort D — second of the two-step 11b-ii-α split per `docs/10-split-memo-11b-ii-alpha.md`. Consumes α-1's bridge runtime Cat-B on-ramp (sinks + `StateMirrorBridgeAdapter` Protocol + loader `STATE_MIRROR_SUBKIND` allow-list + supervisor type widening, all on main `773748f7`). Parent split memo: `docs/04-split-memo-11b-ii.md`.
**Branch:** `claude/11b-ii-alpha-2-c<N>` per c-band (Executor's harness assigns).
**Stop condition:** Apple Reminders dual-deployment shipped: `packs/adapters/reminders/apple-eventkit/` manifest declares `deployment_targets: [central, bridge]`; `config.schema.json` accepts `deployment_target` enum; `AppleEventKitAdapter` reads `deployment_target` from `ctx.config` and branches credential source + `sharing_model_discriminator`; NEW `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` connector-pack scaffolds the bridge-side wrapper that satisfies `StateMirrorBridgeAdapter` Protocol + synthesizes `AdapterContext` from `BridgeContext` + injects `IngestClientBridgeSink` so the internal `AppleEventKitAdapter` emits via the bridge runtime per [§6.19] / memo 04 §2.2. `adminme adapters validate packs/adapters/reminders/apple-eventkit` continues to exit 0. `verify_invariants.sh` exits 0.
**Depends-on:** 11b-ii-α-1 closed (main `773748f7`); 11b-i closed (main `4ed7b731`); 11c-i closed (main `1f88ccec`); 11r-c retrofit closed; existing AppleEventKitAdapter on main.
**Quality-bar reference:** `prompts/_active/11b-i-apple-reminders-list-migration.md` (canonical sibling — same pack, dual-emit precedent) + `prompts/_active/11b-iii-google-calendar-central.md` (fresh Cat-B sibling) + `prompts/_active/11b-ii-alpha-1-bridge-cat-b-onramp.md` (α-1 on-ramp interface reference).
**Estimated duration:** c1 ~25 min (manifest + config schema) · c2 ~70 min (adapter.py deployment_target branching) · c3 ~70 min (bridge connector-pack + framework tests) · c4 ~55 min (integration + pack-internal + BUILD_LOG) · total ~3.5-4 hrs wall.

---

## §1 Behavioral grounding

11b-ii-α-2 closes the Apple Reminders dual-deployment work begun by α-1's on-ramp. Per [D18] (Reminders dual-deployment per memo 05 §5.4) + [D19] (runtime substrate orthogonal to category) + [D22] (Apple Calendar dual-deployment lineage, applied here to Apple Reminders): the AppleEventKitAdapter must run in TWO deployments — **central** (shared assistant Apple ID on the CoS Mac Mini, mirrors household-shared reminders) and **per-member bridge** (each family member's bridge Mac Mini signed into that member's iCloud, mirrors that member's private reminders + owner-scoped to `private:<member_id>`). Both deployments emit the existing `reminder.list_item.synced@v1` + the `list_item.*@v1` family from 11b-i — no new event schemas.

Four load-bearing items:

1. **Manifest declares dual deployment** (`packs/adapters/reminders/apple-eventkit/pack.yaml`). Add top-level `subkind: state-mirror` (so the α-1 bridge loader picks the pack up per `STATE_MIRROR_SUBKIND`) AND a top-level `deployment_targets: [central, bridge]` field per parent memo `04-split-memo-11b-ii.md` §2. Per memo 05 §3.1: each `capabilities[*]` entry's `runtime` field stays `central` for the central-deployment row; α-2 does NOT introduce `runtime: dual` at the capability level (that's an architectural choice the parent memo flagged but doesn't require — the deployment switch lives in the manifest's top-level `deployment_targets` field and the config's `deployment_target` enum, not in the capability list). Backwards-compatibility: existing instances without `deployment_target` in their config default to `"central"`.

2. **Config schema accepts `deployment_target` enum** (`packs/adapters/reminders/apple-eventkit/config.schema.json`). Add `"deployment_target": {"type": "string", "enum": ["central", "bridge"], "default": "central"}` as an optional property. Backwards-compatibility (F-2): the existing required fields (`target_list_identifier`, `member_id`) keep working; central deployments don't need to set `deployment_target` explicitly (default applies). Bridge deployments set `deployment_target: bridge`.

3. **AppleEventKitAdapter reads deployment_target from `ctx.config`** (`packs/adapters/reminders/apple-eventkit/adapter.py`). In `start(ctx: AdapterContext)`: read `deployment_target = ctx.config.get("deployment_target", "central")` once. Branch behaviorally on this:
   - **central path** (existing): credential source is the existing `target_list_identifier` + `member_id` from config; `sharing_model_discriminator` emitted on every event is `"shared_central"` (or whatever the central-side convention is — confirm at orientation per §4 A-1); emit path is `ctx.event_log.append(...)` as today.
   - **bridge path** (NEW): credential source is the bridge-provided per-member Apple ID identity (the bridge wrapper in c3 synthesizes an `AdapterContext` where `ctx.config` carries bridge-side fields including the resolved per-member Apple ID + a `_bridge_sink` reference to the α-1 `IngestClientBridgeSink`); `sharing_model_discriminator` emitted is `"per_member_isolated"`; emit path delegates to `ctx.config["_bridge_sink"].emit(envelope)` (the bridge wrapper injects the sink via the synthesized config). **F-3 (HARD)**: the central code path is byte-for-byte preserved when `deployment_target=="central"`; the bridge branch is purely additive. **F-4 (HARD)**: observation-mode wrap (per [§6.20]) applies to BOTH paths uniformly — `outbound()` is invoked on the same emit-suppression-skip basis from 11b-i + 11r-c (no change to existing F-3 from 11b-i: observation-suppressed path emits NEITHER `reminder.list_item.synced@v1` NOR `list_item.*@v1`).

4. **Bridge connector-pack scaffold** (NEW `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/`). Three files:
   - `__init__.py`: re-exports `AppleEventKitBridgeAdapter`.
   - `pack.yaml`: declares `kind: adapter`, `subkind: state-mirror` (matches α-1's `STATE_MIRROR_SUBKIND` allow-list), `pack.name: reminders-apple-eventkit-bridge`, `entrypoint: adminme_bridge.connector_packs.reminders_apple_eventkit_bridge:AppleEventKitBridgeAdapter`.
   - `wrapper.py`: NEW `class AppleEventKitBridgeAdapter` satisfies α-1's `StateMirrorBridgeAdapter` Protocol structurally — declares `family: ClassVar[Literal["reminders"]] = "reminders"`, plus `async def start(self, *, ctx: BridgeContext)`, `async def stop(self)`, `def status(self) -> AdapterStatus`. The class HOLDS an internal `AppleEventKitAdapter` instance + on `start(ctx: BridgeContext)`: (a) constructs an `IngestClientBridgeSink(ctx.ingest_client)` (or reads `ctx.sink` if injected per α-1 F-3); (b) synthesizes a central `AdapterContext` with `event_log=None` (the bridge path doesn't use it), `config={..., "deployment_target": "bridge", "member_id": ctx.member_id, "_bridge_sink": sink}`, `correlation_id_factory=ctx.correlation_id_factory`, `now_factory=ctx.now_factory`; (c) delegates `await self._internal.start(ctx=synthesized_ctx)`. `stop()` and `status()` forward to the internal adapter. Per memo 04 §2.2: the bridge runtime's central `:3337` ingest router stamps `owner_scope` from Tailscale identity — the adapter sends envelopes with the bridge member's `member_id` declared, but the central router authoritatively stamps `owner_scope = "private:<resolved_member_id>"`. F-5 (HARD): the bridge wrapper does NOT itself implement the EventKit polling logic — all of that lives in the central `AppleEventKitAdapter`; the wrapper is THIN (≤80 LOC) and exists solely to bridge the `BridgeContext` ↔ `AdapterContext` shape gap + inject the sink.

OUT OF SCOPE per `docs/10-split-memo-11b-ii-alpha.md` §2 (α-2 scope) + parent memo `04-split-memo-11b-ii.md` §2:
- **Refactoring central `AppleEventKitAdapter` class hierarchy or making it inherit from multiple base classes** — adapter stays a `ExternalStateMirrorAdapter` subclass (Cat-B ABC). The bridge integration is via composition (wrapper holds adapter instance), not multi-inheritance per [PM-34] (5 ABCs unchanged).
- **New event schemas** — pure dual-deployment; both paths emit existing `reminder.list_item.synced@v1` + `list_item.*@v1` family.
- **New `family` Literal values** — `reminders` already in the Cat-B ABC's Literal.
- **Apple Calendar dual-deployment** — Z1.5 = 11b-ii-β scope.
- **Apple Contacts bridge half** — Z4 = 11e Apple-half scope (reuses α-1's on-ramp).
- **Central `Adapter` Protocol changes** (`adminme/adapters/base.py`) — would require PM-28; not triggered.
- **Bridge daemon main.py / supervisor changes** — α-1 already widened `BridgeSupervisor.adapters: list[BridgeAdapter]`; α-2 adds NO supervisor surface.
- **`BridgeContext.sink` plumbing into `make_context()`** — α-1 left this as a transitional optional field; the wrapper synthesizes the sink locally rather than relying on supervisor injection.
- **iCloud Shared List invitations / lists-auto-seed wizard** — bootstrap §16-§10 scope.

Citations: [D17] (L1-bridge canonical shape), [D18] (Reminders dual-deployment per memo 05 §5.4), [D19] (5-cat taxonomy + runtime orthogonal axis), [D22] (Apple Calendar dual-deployment lineage), [§6.19] (bridge sovereignty), [§6.20] (observation-mode wrap on outbound), [§7.3, D17 corollary 1] (adapters never write projections), [§8.2, D6] (no LLM SDK), [PM-29] (`bridge/` not `adminme/`), [PM-33] (capabilities-as-list — unchanged here), [PM-34] (5 ABCs unchanged — composition not multi-inheritance), [PM-35] (multi-capability — N/A here, single Cat-B). Memo 04 §2.2 (Tailscale-bound identity for bridge emit; central router stamps `owner_scope`). Memo 05 §3.1 (Cat-B `runtime: dual` declaration shape), §5.4 (Reminders bidirectional + dual-deployment spec). `docs/10-split-memo-11b-ii-alpha.md` §2 (α-2 contract).

[VISION §"Real family"]: Apple Reminders dual-deployment is THE Phase A reference implementation for the family-of-four use case. Bridge sovereignty per [§6.19] means each family member's private reminders never leave their bridge except as Tailscale-authenticated owner-scoped events flowing to the central event log. The shared-household reminders run on the central CoS Apple ID. Both surfaces share the same `lists` projection (11f) downstream — household lists + per-member lists side-by-side in the Console (14b). α-2 lands the first concrete dual-deployment adapter that exercises α-1's on-ramp.

## §2 Read first (required)

**c1 (manifest + config schema) — 4 files**

- `docs/10-split-memo-11b-ii-alpha.md` §2 (α-2 scope — canonical contract on main `773748f7`).
- `docs/04-split-memo-11b-ii.md` §2 (parent memo — original α scope).
- `packs/adapters/reminders/apple-eventkit/pack.yaml` (on main) — current manifest shape; α-2 extends.
- `packs/adapters/reminders/apple-eventkit/config.schema.json` (on main) — current schema; α-2 extends.

**c2 (AppleEventKitAdapter dual-deployment branching) — 6 files**

- `packs/adapters/reminders/apple-eventkit/adapter.py` (on main, **READ HEAD + start() + emit-site sections via sed**, file is ~60 KB; do NOT full-read) — central class to extend.
- `adminme/lib/adapters/external_state_mirror_base.py` (on main) — Cat-B ABC; preserved.
- `adminme/adapters/base.py` (on main, **READ AdapterContext lines via sed**) — `AdapterContext` shape that c3's wrapper synthesizes.
- `adminme_bridge.adapters.sinks` (on main from α-1) — `BridgeEventSink` Protocol + `IngestClientBridgeSink` impl that c3's wrapper instantiates.
- `prompts/_active/11b-i-apple-reminders-list-migration.md` (on main) — dual-emit emit-site precedent; α-2's emit branching matches the same observation-wrap discipline (F-4).
- `adminme/lib/observation.py` (on main) — `outbound()` wrap; both deployment paths route through it.

**c3 (bridge connector-pack + framework tests) — 6 files**

- `bridge/adminme_bridge/adapters/base.py` (on main from α-1) — `StateMirrorBridgeAdapter` Protocol + `BridgeContext` + `BridgeAdapter` TypeAlias.
- `bridge/adminme_bridge/adapters/sinks.py` (on main from α-1) — sink impls.
- `bridge/adminme_bridge/packs/__init__.py` (on main from α-1) — `STATE_MIRROR_SUBKIND` constant + `discover_packs()` allow-list (the new connector-pack's manifest matches).
- `packs/adapters/reminders/apple-eventkit/adapter.py` (on c2 branch) — internal adapter the wrapper holds.
- `prompts/_active/11b-ii-alpha-1-bridge-cat-b-onramp.md` (on main) — α-1's interface reference + test-shape precedent for `bridge/tests/unit/`.
- `prompts/_active/11g-home-assistant-adapter.md` (on main) — most-recent canonical c3 test-file shape.

**c4 (integration + pack-internal + BUILD_LOG) — 4 files**

- `tests/integration/test_bridge_state_mirror_onramp.py` (on main from α-1) — integration test rig precedent (ASGITransport + central FastAPI + real EventLog).
- `packs/adapters/reminders/apple-eventkit/tests/` (existing pack-internal test dir from 11r-c + 11b-i) — F-6 + F-7 unique-basename precedent.
- `docs/build_log.md` (on main, **append c4 entry**; file is 376 KB — use Chrome MCP drag-drop if push_files hits inline budget per Partner memory).
- `prompts/_active/11b-ii-alpha-1-bridge-cat-b-onramp.md` (c4 BUILD_LOG entry shape precedent).

## §3 Reconciliation notes (HARD/SOFT findings)

- **F-1 (HARD/standing from cycle #17): READ FILE CONTENTS before authoring c2.** No new event schemas are registered by α-2 — the cycle #17 collision class doesn't fire. But the c2 author MUST verify that `adapter.py`'s existing `start()` + `_write_one_outbound` + `_emit_inbound_synced` emit sites are exactly as 11b-i + 11r-c left them BEFORE adding the deployment_target branch. Grep `ctx.event_log.append` and `_seen_inbound` across `adapter.py` to confirm the existing emit-site shape.
- **F-2 (HARD): Central path byte-for-byte preserved.** When `deployment_target=="central"` (default), the adapter's behavior MUST be byte-for-byte identical to what 11b-i + 11r-c shipped. The dual-emit (legacy `reminder.list_item.synced@v1` + canonical `list_item.*@v1`) per 11b-i continues unchanged. Pinned by c3 regression tests.
- **F-3 (HARD): Bridge wrapper is THIN.** The wrapper at `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/wrapper.py` is ≤80 LOC. It does NOT re-implement EventKit polling, never-sync filter, dual-emit, observation wrap, or any other adapter logic — those all stay in the central `AppleEventKitAdapter`. The wrapper exists SOLELY to (a) satisfy `StateMirrorBridgeAdapter` Protocol structurally (declare `family` ClassVar), (b) synthesize a central `AdapterContext` from a `BridgeContext`, (c) inject the `IngestClientBridgeSink` so the internal adapter's bridge-mode emit path lands at the sink, (d) forward `stop()` / `status()`.
- **F-4 (HARD/standing from 11b-i F-3): Observation-mode suppresses ALL emits uniformly.** The deployment_target branch does NOT change F-3 from 11b-i: when `outbound()` returns `OutboundResult(suppressed=True)`, the adapter emits NEITHER `reminder.list_item.synced@v1` NOR `list_item.*@v1`. Both deployment paths share this rule. Pinned by c3 test (`test_dual_deployment_observation_suppressed_skips_all_emits_uniformly`).
- **F-5 (HARD/standing): test basename uniqueness.** Proposed c3/c4 basenames: `test_apple_eventkit_dual_deployment.py` (c3 framework, in `tests/unit/adapters/reminders/`), `test_apple_eventkit_bridge_wrapper.py` (c3 framework, in `bridge/tests/unit/connector_packs/`), `test_apple_eventkit_dual_deployment_pack_internal.py` (c4 pack-internal, in `packs/adapters/reminders/apple-eventkit/tests/`), `test_adapter_apple_eventkit_dual_deployment_integration.py` (c4 integration). All unique across the repo — verified.
- **F-6 (HARD/from 11a c4): NO empty `__init__.py` in pack-internal test dirs.** `packs/adapters/reminders/apple-eventkit/tests/` directory continues NO `__init__.py` (existing 11b-i + 11r-c convention). Framework test dirs (`tests/unit/adapters/reminders/`, `bridge/tests/unit/connector_packs/`) DO have `__init__.py` — the new `bridge/tests/unit/connector_packs/__init__.py` is NEW.
- **F-7 (SOFT): adapter.py diff size.** c2 adapter.py diff ≤120 lines added. The deployment_target branching is the new logic; emit-site rewiring is additive (NEW bridge path; central path unchanged). 11b-i's c2 was +196; α-2's c2 expected ~80-120.
- **F-8 (HARD/new for α-2): `central_credential_ref` vs `bridge_credential_strategy` config decoupling.** The bridge path's per-member Apple ID identity is NOT in the `config.schema.json` as a config field — it's resolved at runtime via the bridge's `BridgeContext.member_id` (from the bridge daemon's `~/.adminme-bridge/config.yaml`). The pack-level `config.schema.json` only adds the `deployment_target` enum + (optionally) flagging the existing `target_list_identifier` as central-only. c2 author confirms shape at orientation per §4 A-2.
- **F-9 (SOFT/new for α-2): central deployment doesn't load the bridge connector-pack.** The bridge connector-pack at `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` lives in the bridge package's tree, picked up by α-1's `discover_packs()` ONLY when the bridge daemon runs. The central CoS Mac Mini's `adapter_supervisor` reads from `packs/adapters/reminders/apple-eventkit/` directly via the existing central pack registry — no cross-loading. The wrapper class is bridge-runtime-only.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: `sharing_model_discriminator` literal values.** The pack manifest's existing `sharing_model_discriminator: owner_member_id` (from 11r-c) is the schema-row column name on the `lists` projection. The runtime *value* emitted by the central path is `"shared_central"` (or equivalent); bridge path is `"per_member_isolated"`. Confirm by reading memo 05 §2.5 + §5.4 + `lists` projection schema at `adminme/projections/lists/` if it exists yet.
- **A-2: `target_list_identifier` config field per deployment_target.** The existing `target_list_identifier` field in `config.schema.json` is the central deployment's iCloud Reminders list to write to. For bridge deployment, it's the per-member Apple ID's "household" list — c2 author confirms whether the field stays required for both deployments OR conditionally required only for central. Recommendation: keep required for both; bridge config sets it to the per-member-Apple-ID list identifier the operator chooses during bootstrap §16-§10 enrollment.
- **A-3: `member_id` config field semantics in bridge mode.** Central: `member_id` is the household member assigned to operate this central instance (typically the CoS / admin). Bridge: `member_id` is the bridge owner's `private:<member_id>` scope. The wrapper sets it from `ctx.member_id` (the bridge daemon's configured member_id) — overriding the static config value if present. c2 author confirms the override-precedence at orientation.
- **A-4: `AppleEventKitAdapter` constructor stability.** The class's `__init__` signature is NOT changed by α-2 — `deployment_target` is read from `ctx.config` in `start()`, not at construction. This preserves backwards compatibility with the central supervisor's existing `AppleEventKitAdapter()` instantiation. Confirm by reading the head of `adapter.py` at orientation.
- **A-5: `BridgeContext.sink` precedence in the wrapper.** Per α-1 F-3: `BridgeContext.sink: BridgeEventSink | None = None` is optional. The α-2 wrapper SHOULD prefer `ctx.sink` if set, else construct a fresh `IngestClientBridgeSink(ctx.ingest_client)`. This lets future tests inject a fake sink without modifying the wrapper code path.
- **A-6: existing 11b-i + 11r-c tests preserved.** c3 + c4 add NEW tests; the existing `tests/unit/adapters/reminders/test_apple_eventkit_*` test files continue passing unchanged. Run the full suite in c4; assert zero regressions.

## §5 Objective

Ship Apple Reminders dual-deployment per [D18] / [D22] / memo 05 §5.4: the AppleEventKitAdapter reads `deployment_target` from config and branches credential source + sharing_model_discriminator; the bridge connector-pack at `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` wraps the central adapter to satisfy α-1's `StateMirrorBridgeAdapter` Protocol; both deployment paths emit existing `reminder.list_item.synced@v1` + `list_item.*@v1`. No new event schemas. Central deployment behavior preserved byte-for-byte when `deployment_target` defaults to `"central"`.

## §6 Out of scope

(See §1 OUT OF SCOPE list + `docs/10-split-memo-11b-ii-alpha.md` §2 α-2 scope.)

## §7 Deliverables

**c1: pack manifest + config schema extensions (~35 lines net diff)**:

1. `packs/adapters/reminders/apple-eventkit/pack.yaml` (EXTEND, ~6 lines): add top-level `subkind: state-mirror` line; add top-level `deployment_targets: [central, bridge]` list; preserve all existing keys + ordering. Pack version bump optional per [D7].
2. `packs/adapters/reminders/apple-eventkit/config.schema.json` (EXTEND, ~10 lines): add `"deployment_target": {"type": "string", "enum": ["central", "bridge"], "default": "central", "description": "..."}` under `properties`; do NOT add to `required` (default makes it backward-compatible).

c1 verification:
```bash
poetry run adminme adapters validate packs/adapters/reminders/apple-eventkit
# Expected: exit 0; NO advisory_pending_retrofit
poetry run python -c "import json; s = json.load(open('packs/adapters/reminders/apple-eventkit/config.schema.json')); assert s['properties']['deployment_target']['default'] == 'central'; print('config schema deployment_target ok ✓')"
```

**c2: AppleEventKitAdapter dual-deployment branching (~80-120 lines diff in adapter.py)**:

`packs/adapters/reminders/apple-eventkit/adapter.py` (EXTEND):
- In `start(ctx: AdapterContext)`: add `self._deployment_target = ctx.config.get("deployment_target", "central")` as an instance attribute (immutable post-start per [§7.3] / Cat-B ABC contract).
- In `start()` continuation: branch credential resolution:
  - central: existing path unchanged (reads `target_list_identifier` + `member_id` from config; resolves EKEventStore via shared Apple ID grant).
  - bridge: read `member_id` from `ctx.config.get("_bridge_member_id", ctx.config["member_id"])`; the bridge wrapper synthesizes `_bridge_member_id` into the AdapterContext's config so the adapter knows which member's Apple ID it's operating against.
- In `_emit_inbound_synced` + `_write_one_outbound`: branch emit dispatch:
  - central: existing `ctx.event_log.append(envelope, correlation_id=..., causation_id=...)` unchanged.
  - bridge: `await ctx.config["_bridge_sink"].emit(envelope)` — the wrapper injects the sink into the config dict. F-3 invariant from 11b-i preserved: observation-suppressed path skips ALL emits uniformly.
- Add per-emit `sharing_model_discriminator` field to the event payloads: `"shared_central"` (central) or `"per_member_isolated"` (bridge). Per A-1, confirm the literal values at orientation against memo 05 §2.5.
- All other adapter logic preserved byte-for-byte: poll loop, never-sync filter, cursor milestone, observation wrap, dual-emit of legacy + canonical schemas from 11b-i.

c2 verification:
```bash
poetry run mypy packs/adapters/reminders/apple-eventkit/ 2>&1 | tail -5
poetry run python -c "from packs.adapters.reminders.apple_eventkit.adapter import AppleEventKitAdapter; a = AppleEventKitAdapter(); print('adapter constructs ok ✓')"
```

**c3: NEW `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` (≤120 lines across 3 files) + framework tests (≥14 NEW)**:

1. `bridge/adminme_bridge/connector_packs/__init__.py` (NEW, minimal) — package marker.
2. `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/__init__.py` (NEW, re-exports `AppleEventKitBridgeAdapter`).
3. `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/pack.yaml` (NEW, ~15 lines): `kind: adapter`, `subkind: state-mirror`, `pack.name: reminders-apple-eventkit-bridge`, `entrypoint: adminme_bridge.connector_packs.reminders_apple_eventkit_bridge:AppleEventKitBridgeAdapter`, `family: reminders`, `version: 1.0.0`.
4. `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/wrapper.py` (NEW, ≤80 LOC per F-3, ≤120 lines including imports + docstring):
   - `class AppleEventKitBridgeAdapter`:
     - `family: ClassVar[Literal["reminders"]] = "reminders"` (satisfies `StateMirrorBridgeAdapter` Protocol)
     - `__init__(self)`: instantiate internal `AppleEventKitAdapter` lazily on first `start()`.
     - `async def start(self, *, ctx: BridgeContext) -> None`:
       - Sink resolution per A-5: `sink = ctx.sink if ctx.sink is not None else IngestClientBridgeSink(ctx.ingest_client)`.
       - Synthesize `AdapterContext` from `BridgeContext`: `event_log=None`; `config={"deployment_target": "bridge", "member_id": ctx.member_id, "_bridge_member_id": ctx.member_id, "_bridge_sink": sink, "target_list_identifier": <from-bridge-config-or-fixture>, "never_sync_list_substrings": [...], "never_sync_tag_substrings": [...]}`; `correlation_id_factory=ctx.correlation_id_factory`; `now_factory=ctx.now_factory`.
       - Construct internal adapter; await its `start(ctx=synthesized_ctx)`.
     - `async def stop(self) -> None`: forward to internal.
     - `def status(self) -> AdapterStatus`: forward to internal (or return a default if not yet started).

5. Framework tests (≥14 NEW):
   - `tests/unit/adapters/reminders/test_apple_eventkit_dual_deployment.py` (NEW, F-5 unique basename, ~10 tests):
     - `test_deployment_target_defaults_to_central_when_unspecified` (backwards compat).
     - `test_deployment_target_central_uses_existing_credential_source` (F-2 byte-for-byte).
     - `test_deployment_target_bridge_uses_synthesized_member_id_from_config`.
     - `test_central_path_emits_sharing_model_discriminator_shared_central`.
     - `test_bridge_path_emits_sharing_model_discriminator_per_member_isolated`.
     - `test_central_path_uses_event_log_append_for_emit`.
     - `test_bridge_path_uses_injected_sink_for_emit`.
     - `test_dual_deployment_observation_suppressed_skips_all_emits_uniformly` (F-4 pin).
     - `test_dual_emit_of_legacy_plus_canonical_schemas_preserved_in_both_deployments` (F-2 + 11b-i preservation).
     - `test_deployment_target_unknown_value_raises_value_error` (config validation).
   - `bridge/tests/unit/connector_packs/__init__.py` (NEW, empty package marker).
   - `bridge/tests/unit/connector_packs/test_apple_eventkit_bridge_wrapper.py` (NEW, F-5 unique basename, ~4-6 tests):
     - `test_apple_eventkit_bridge_adapter_satisfies_state_mirror_bridge_adapter_protocol` (Protocol pin).
     - `test_wrapper_synthesizes_adapter_context_from_bridge_context_with_deployment_target_bridge`.
     - `test_wrapper_prefers_ctx_sink_when_present_per_a5`.
     - `test_wrapper_falls_back_to_ingest_client_bridge_sink_when_ctx_sink_none_per_a5`.
     - `test_wrapper_forwards_stop_and_status_to_internal_adapter`.

c3 verification:
```bash
poetry run pytest tests/unit/adapters/reminders/test_apple_eventkit_dual_deployment.py bridge/tests/unit/connector_packs/test_apple_eventkit_bridge_wrapper.py -v
# Expected: ≥14 passed; 0 regressions on existing 11b-i + 11r-c tests
```

**c4: integration + pack-internal + BUILD_LOG (≥6 NEW)**:

1. `packs/adapters/reminders/apple-eventkit/tests/test_apple_eventkit_dual_deployment_pack_internal.py` (NEW, F-5 + F-6 unique basename, no `__init__.py` per F-6, ~3 tests):
   - `test_pack_manifest_declares_subkind_state_mirror_and_deployment_targets`.
   - `test_config_schema_accepts_deployment_target_central_and_bridge_with_central_default`.
   - `test_pack_validator_accepts_dual_deployment_pack`.

2. `tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py` (NEW, F-5 unique basename, ~3 tests):
   - `test_bridge_supervisor_loads_apple_eventkit_bridge_connector_pack` — the bridge daemon's `discover_packs()` yields the new `reminders-apple-eventkit-bridge` pack from a tmp_path fixture mimicking `~/.adminme-bridge/packs/`.
   - `test_central_supervisor_loads_apple_eventkit_pack_with_default_deployment_target_central` — regression: central supervisor unchanged.
   - `test_bridge_wrapper_emits_via_ingest_client_sink_to_central_ingest_router_end_to_end` — full end-to-end through bridge supervisor → wrapper → internal adapter → sink → central :3337 router → real EventLog with `owner_scope` stamped per [§6.19] (mirrors α-1's `test_bridge_state_mirror_onramp.py::test_mock_cat_b_adapter_emits_via_ingest_client_sink_end_to_end` shape).

3. `docs/build_log.md` (APPEND) — 11b-ii-α-2 entry per the 11b-ii-α-1 precedent shape just shipped. **Evidence**: test counts; suite tally; mypy + verify_invariants exit 0; validator exit 0 with NO `advisory_pending_retrofit`. **Carry-forward for Z1.5 (11b-ii-β)**: the dual-deployment pattern + bridge connector-pack scaffold are reusable; β extends with a fresh `apple-calendar` pack mirroring this one's shape. **Carry-forward for 11e Apple Contacts bridge half**: same pattern. NOTE: if build_log.md push hits inline MCP token budget (376 KB+ now post-α-1), use Chrome MCP `file_upload` per Partner memory `feedback_no_delegating_mechanical_work.md`.

c4 verification:
```bash
poetry run pytest packs/adapters/reminders/apple-eventkit/tests/ tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py -v
poetry run adminme adapters validate packs/adapters/reminders/apple-eventkit
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0
```

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11b-ii-alpha-2 commit <N>: <imperative summary>` exactly.

- **c1: `phase 11b-ii-alpha-2 commit 1: pack manifest + config schema deployment_target extensions`** — `pack.yaml` + `config.schema.json` (≤35 lines net diff).
- **c2: `phase 11b-ii-alpha-2 commit 2: AppleEventKitAdapter deployment_target branching`** — `adapter.py` (≤120 lines net diff per F-7).
- **c3: `phase 11b-ii-alpha-2 commit 3: bridge connector-pack scaffold + framework tests`** — `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` (NEW) + `tests/unit/adapters/reminders/test_apple_eventkit_dual_deployment.py` (NEW) + `bridge/tests/unit/connector_packs/test_apple_eventkit_bridge_wrapper.py` (NEW).
- **c4: `phase 11b-ii-alpha-2 commit 4: integration + pack-internal tests + BUILD_LOG`** — `packs/.../tests/test_apple_eventkit_dual_deployment_pack_internal.py` (NEW) + `tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py` (NEW) + `docs/build_log.md` (APPEND).

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch; PRs open with `base=main` per the established pattern. Ghost-conflict risk: LOW. c-band file scopes mostly non-overlapping (c1 = pack manifest files; c2 = adapter.py only; c3 = NEW bridge connector-pack + NEW test files; c4 = NEW pack-internal + integration + build_log append). If push_files hits inline budget at c4 (build_log.md grows past ~400 KB), Executor stops + Partner re-fires the c4 step using Chrome MCP `file_upload` for build_log per memory.

Test floor: ≥10 c3 framework (dual-deployment) + ≥4-6 c3 framework (bridge wrapper) + ≥3 c4 pack-internal + ≥3 c4 integration = **≥20 total NEW**.

## §9 Verification

(Per c-band per §7 above. c4 final verification:)

```bash
poetry run pytest tests/unit/adapters/reminders/ bridge/tests/unit/ packs/adapters/reminders/apple-eventkit/tests/ tests/integration/test_adapter_apple_eventkit_dual_deployment_integration.py -v
poetry run adminme adapters validate packs/adapters/reminders/apple-eventkit
poetry run mypy adminme/ bridge/adminme_bridge/ packs/adapters/reminders/apple-eventkit/ 2>&1 | tail -10
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0; zero regressions on existing 11b-i / 11r-c / α-1 tests.
```

## §10 Stop

When `packs/adapters/reminders/apple-eventkit/pack.yaml` declares `subkind: state-mirror` + `deployment_targets: [central, bridge]` AND `config.schema.json` accepts `deployment_target` enum AND `AppleEventKitAdapter` branches on `ctx.config["deployment_target"]` with central path byte-for-byte preserved (F-2) AND `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/AppleEventKitBridgeAdapter` satisfies `StateMirrorBridgeAdapter` Protocol AND the bridge supervisor loads the new connector-pack via α-1's `STATE_MIRROR_SUBKIND` allow-list AND the integration test demonstrates the bridge round-trip with `owner_scope` stamped from Tailscale identity AND existing 11b-i + 11r-c + α-1 tests pass unchanged AND `verify_invariants.sh` exits 0, c4 is done. **Unlocks Z1.5 = 11b-ii-β** (Apple Calendar fresh Cat-B + dual variant) AND Z4 = 11e Apple Contacts bridge half AND any future dual-deployment Cat-B adapter. Do NOT proceed to 11b-ii-β without operator review.

After merge: Partner runs three-job QC, updates `docs/partner_handoff.md` head paragraph + `docs/build_log.md` (c4 evidence already in place from this prompt; head paragraph is the partner-state-snapshot update — push via Chrome MCP `file_upload` if build_log push exceeds inline MCP budget per Partner memory).
