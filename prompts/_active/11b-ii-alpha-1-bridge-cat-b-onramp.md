# Prompt 11b-ii-α-1: Bridge runtime Cat-B on-ramp

**Phase:** L1-bridge infrastructure / Cohort D 11b-ii-α-1. First of the two-step 11b-ii-α split per `docs/10-split-memo-11b-ii-alpha.md`. Parent split memo: `docs/04-split-memo-11b-ii.md`. Parent draft: none (infrastructure refactor; no prompts/11b-ii-α-1-*.md predecessor).
**Branch:** `claude/11b-ii-alpha-1-c<N>` per c-band (Executor's harness assigns).
**Stop condition:** `bridge/adminme_bridge/` extended to load + supervise + emit-from Cat-B state-mirror adapter packs alongside the existing Cat-D knowledge-source packs. Specifically: event-sink abstraction in `bridge/adminme_bridge/adapters/sinks.py` (NEW); `discover_packs()` accepts state-mirror packs; sibling `StateMirrorBridgeAdapter` Protocol (or generalized parent) at `bridge/adminme_bridge/adapters/base.py`; `BridgeSupervisor.adapters` type widened. **No reminders-pack changes.** **No `AppleEventKitAdapter` changes.** **No connector-pack scaffolds.** All existing 11c-i tests continue passing.
**Depends-on:** 11c-i bridge daemon shell closed (main `1f88ccec`); split memo merged (main `da6a55c8`).
**Quality-bar reference:** `prompts/_active/11c-i-bridge-daemon-and-ingest.md` (bridge daemon shape) + `prompts/_active/11g-home-assistant-adapter.md` (most-recent canonical c1-c4 shape). Transplant the c1-c4 cadence + §3 HARD findings structure.
**Estimated duration:** c1 ~30 min · c2 ~75 min · c3 ~60 min · c4 ~45 min · total ~2.5-3 hrs wall / ~$50-80 spend.

---

## §1 Behavioral grounding

11b-ii-α-1 closes the bridge-runtime architectural gap surfaced by `docs/10-split-memo-11b-ii-alpha.md` §1: the bridge daemon shipped Cat-D-only (11c-i `main.py` docstring: *"Mirrors `adminme/daemons/adapter_supervisor.py` but Cat-D-only and HTTP-emit"*), but [D19] establishes that runtime substrate (central / bridge / dual-deployment) is **orthogonal** to category. Cat-B `ExternalStateMirrorAdapter` packs can architecturally run on a bridge; the runtime infrastructure must support it. This prompt builds that on-ramp.

Four load-bearing items:

1. **Event-sink abstraction** (NEW `bridge/adminme_bridge/adapters/sinks.py`). Bridge adapters emit envelopes; today the only emission path is `IngestClient.submit(envelope)`. The on-ramp introduces a `BridgeEventSink` Protocol abstracting over emit pathways, with two implementations: `IngestClientBridgeSink` (production path; wraps existing `IngestClient.submit`) and `EventLogBridgeSink` (test + central-deployment-fallback path; wraps central `EventLog.append`). `BridgeContext` gains a `sink: BridgeEventSink` field. Existing `BridgeContext.ingest_client` field is preserved for 11c-i backward compatibility; deprecation is out-of-scope per §6.

2. **Sibling `StateMirrorBridgeAdapter` Protocol** (extends `bridge/adminme_bridge/adapters/base.py`). Adds a structural sibling to `KnowledgeAdapter`. Both Protocols share `start(ctx: BridgeContext)`, `stop()`, `status() -> AdapterStatus`. They differ in their type discriminator class attribute: `KnowledgeAdapter.subkind: ClassVar[str]` (unchanged); `StateMirrorBridgeAdapter.family: ClassVar[str]` (mirrors central `ExternalStateMirrorAdapter.family`). A new `BridgeAdapter` TypeAlias (Union) is exported for the supervisor's type annotation. Choice between sibling-Protocol vs generalized-parent-Protocol shape is the c2 author's call per §4 A-1.

3. **Loader generalization** (`bridge/adminme_bridge/packs/__init__.py`). `discover_packs()` today filters strictly by `subkind: knowledge-source`. Extend the filter to also accept Cat-B state-mirror packs. Mechanism: add `STATE_MIRROR_SUBKIND = "state-mirror"` constant + multi-subkind allow-list, OR inspect `capabilities[*].kind == "Cat-B"` + `runtime in {dual, bridge}` per [D19] (c2 author's call per §4 A-2). Manifest-malformed errors continue to raise `PackLoadError`. Unknown subkinds continue to be silently skipped (existing 11c-i contract).

4. **Supervisor type broadening** (`bridge/adminme_bridge/main.py`). `BridgeSupervisor.adapters: list[KnowledgeAdapter]` → `list[BridgeAdapter]` (the Union exported in (2)). Loop dispatch unchanged — both Protocols' `start(ctx)` signature matches. Mock Cat-B adapter satisfying `StateMirrorBridgeAdapter` end-to-end through the supervisor → emit → sink → consumed.

OUT OF SCOPE per `docs/10-split-memo-11b-ii-alpha.md` §2 (11b-ii-α-1 scope):
- **`packs/adapters/reminders/apple-eventkit/` changes** — α-2 scope.
- **`AppleEventKitAdapter` code changes** — α-2 scope.
- **Bridge-side connector-pack scaffolds** (`bridge/adminme_bridge/connector_packs/`) — α-2 scope.
- **New event schemas** — pure infrastructure.
- **New `family` Literal values** — pure infrastructure.
- **Pre-cycle sidecar Cat-B `family` Literal extension** (parent memo §4 watch flag for α-2, not α-1).
- **Central `Adapter` Protocol changes** (`adminme/adapters/base.py`) — would require PM-28 amendment per α-1 §4 watch flag in `docs/10-split-memo-11b-ii-alpha.md` §4.
- **Bridge daemon docstring "Cat-D-only"** — leave existing docstring; the new sibling Protocol + loader extension widens behavior without contradicting the docstring (Cat-D remains supported).
- **`ingest_client` field removal from `BridgeContext`** — backward-compatible additive change only.
- **`IngestClient` interface changes** — wrap via sink, don't modify.
- **Cross-pack changes** — bridge daemon files only.

Citations: [D17] (L1-bridge canonical shape), [D18] (Reminders dual-deployment per memo 05 §5.4), [D19] (5-cat taxonomy + runtime orthogonal axis), [§6.19] (bridge sovereignty), [§7.3, D17 corollary 1] (adapters never write projections), [§8.2, D6] (no LLM SDK), [PM-29] (`bridge/` not `adminme/`), [PM-33] (capabilities-as-list), [PM-34] (5 ABCs unchanged), [PM-35] (multi-capability). Memo 04 §2.2 (Tailscale-bound identity for bridge emit). Memo 05 §3.1 (Cat-B `runtime: dual` declaration), §5.4 (Reminders bidirectional). `docs/10-split-memo-11b-ii-alpha.md` §1-§2 (this prompt's contract).

[VISION §"Real family"]: bridge sovereignty is identity-first privacy at the physical layer. The on-ramp is the substrate that lets Apple Reminders dual-deployment ship — and downstream of that, Apple Calendar dual (11b-ii-β) and Apple Contacts per-member (11e Apple-half). Architecturally clean here; clean for every dual-deployment Cat-B adapter forever after.

## §2 Read first (required)

**c1 (event-sink abstraction) — 5 files**

- `docs/10-split-memo-11b-ii-alpha.md` — canonical scope contract (just merged at `da6a55c8`).
- `bridge/adminme_bridge/adapters/base.py` (on main) — existing `BridgeContext` + `KnowledgeAdapter` Protocol shape; sinks live alongside.
- `bridge/adminme_bridge/ingest_client.py` (on main) — `IngestClient.submit(envelope) -> SubmitResult` signature; sink wraps this.
- `adminme/events/envelope.py` (on main) — `EventEnvelope` Pydantic shape; sink's `emit(envelope)` consumes this.
- `adminme/events/log.py` (on main; **READ HEAD ONLY via sed `1,80p`**, file is 20 KB) — `EventLog.append` signature for `EventLogBridgeSink` impl. Verify async + correlation_id/causation_id kwargs per [D8 addition 2].

**c2 (Protocol broadening + loader + supervisor) — 6 files**

- `bridge/adminme_bridge/adapters/base.py` (on c1 branch with sink imports) — extend with `StateMirrorBridgeAdapter` Protocol + `BridgeAdapter` TypeAlias.
- `bridge/adminme_bridge/adapters/__init__.py` (on main) — re-exports; update with new symbols.
- `bridge/adminme_bridge/packs/__init__.py` (on main) — `discover_packs()` filter generalization.
- `bridge/adminme_bridge/main.py` (on main) — `BridgeSupervisor.adapters` type widening.
- `adminme/lib/adapters/external_state_mirror_base.py` (on main, **READ HEAD via sed `1,110p`**) — central Cat-B ABC `family: ClassVar[Literal[...]]` discriminator (the bridge sibling Protocol mirrors this).
- `docs/05-architecture-amendment-personal-data-layer.md` (on main, **READ §3.1 + §5.4 ONLY via sed**, file is 122 KB) — architectural canon for the Protocol shape choice.

**c3 (framework tests) — 5 files**

- `bridge/tests/unit/test_adapters_base.py` (on main) — existing tests; extend with new Protocol coverage.
- `bridge/tests/unit/test_ingest_client.py` (on main) — IngestClient mocking pattern precedent.
- `prompts/_active/11g-home-assistant-adapter.md` (on main) — c3 test-file shape precedent (`_FakeEventLog`, `_seed_ctx`, monkeypatching). `bridge/tests/unit/` mirrors `tests/unit/` shape.
- `bridge/adminme_bridge/adapters/sinks.py` (on c1 branch) — sink test target.
- `adminme/events/envelope.py` — `EventEnvelope` construction in test fixtures.

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `tests/integration/test_bridge_ingest_roundtrip.py` (on main, from 11c-i) — integration test precedent for the bridge supervisor end-to-end shape.
- `bridge/tests/unit/test_main.py` (on main, **EXTEND** with supervisor-Cat-B wire-up coverage).
- `docs/build_log.md` (append the 11b-ii-α-1 entry per the 11g / 11c-i precedent shape).
- `prompts/_active/11g-home-assistant-adapter.md` (c4 BUILD_LOG entry shape).

## §3 Reconciliation notes (HARD/SOFT findings)

- **F-1 (HARD/standing from cycle #17 memory): READ FILE CONTENTS before c1.** No new event schemas registered by this prompt, so the cycle #17 collision class doesn't fire. But the c1 author must still grep `bridge/adminme_bridge/adapters/sinks.py` does NOT exist on main before creating it (path-exists pre-check). Confirmed at authoring: dir listing returned no `sinks.py`.
- **F-2 (HARD/standing): No central Adapter Protocol changes.** `adminme/adapters/base.py` is READ-ONLY for cross-reference. If the c2 author finds the chosen Protocol shape requires unifying `AdapterContext` + `BridgeContext` under a common parent type, **HALT** and open `<NEEDS_OPERATOR_DECISION reason="alpha_1_requires_central_protocol_amendment"/>` per `docs/10-split-memo-11b-ii-alpha.md` §4 α-1 watch flag. That's PM-28, not a refactor.
- **F-3 (HARD/standing): BridgeContext is `@dataclass(frozen=True)`.** Adding `sink: BridgeEventSink` field is an additive change to a frozen dataclass — safe for instantiation, but any caller that previously called `BridgeContext(member_id=..., ingest_client=..., correlation_id_factory=..., now_factory=...)` (positional kwargs) breaks if `sink` is added before `now_factory` in field order. ADD `sink` at the END of the field list to preserve positional-arg compatibility AND make it OPTIONAL with default `None` for the 11c-i transitional period. Pinned by c3 test (`test_bridge_context_construction_with_optional_sink`).
- **F-4 (HARD/standing): test basename uniqueness.** Proposed: `test_sinks.py`, `test_state_mirror_protocol.py`, `test_packs_loader_state_mirror.py`, `test_supervisor_state_mirror.py` — all unique across `bridge/tests/unit/` (verified — no `sinks` / `state_mirror` / `packs_loader` prefixes exist).
- **F-5 (SOFT): per-file ceilings.** `sinks.py` ≤120 lines. `adapters/base.py` net diff ≤80 lines added (StateMirrorBridgeAdapter Protocol + TypeAlias + re-exports). `packs/__init__.py` net diff ≤40 lines added. `main.py` net diff ≤20 lines (type-annotation-only).
- **F-6 (HARD/from 11a c4): NO empty `__init__.py` in pack-internal test dirs.** This prompt's test dirs are framework-level (`bridge/tests/unit/`) — `__init__.py` IS required (already on main from 11c-i; no change). F-6 does not fire.
- **F-7 (SOFT/new for α-1): `BridgeEventSink` Protocol's `emit` return type.** `IngestClient.submit` returns `SubmitResult(ok, event_id, status_code, error)`. `EventLog.append` returns `event_id: str`. The sink's `emit(envelope) -> EmitResult` unifies these into a `@dataclass(frozen=True) class EmitResult(ok: bool, event_id: str | None, error: str | None)`. Status-code is dropped at the sink boundary (impl-internal concern for `IngestClientBridgeSink`). c2 author's discretion to add fields if a downstream test surfaces a missing one.
- **F-8 (HARD/new for α-1): `EventLogBridgeSink` is NOT a substitute for the central event-log path in production.** It is for tests + central-deployment-fallback only. Production bridge path is always `IngestClientBridgeSink` per [§6.19] / memo 04 §2.2 (events go through the central :3337 ingest router which stamps `owner_scope` from Tailscale identity). Bridges never write directly to a local event log; the bridge daemon's role per [§6.19] is to ingest from member-Apple-ID surfaces and forward via HTTPS. Docstring on `EventLogBridgeSink` makes this explicit.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: Protocol shape choice.** Sibling Protocols (`KnowledgeAdapter` + new `StateMirrorBridgeAdapter`, both sharing structural method set) vs. generalized parent (`_BridgeAdapterBase` + `KnowledgeAdapter(_BridgeAdapterBase, Protocol)` + `StateMirrorBridgeAdapter(_BridgeAdapterBase, Protocol)`). Recommendation: **siblings + `BridgeAdapter` TypeAlias = Union[…]**. Cleaner; less rope to refactor 11c-i tests. c2 author confirms at orientation.
- **A-2: Loader filter mechanism.** Multi-subkind allow-list vs. capability inspection. Recommendation: **multi-subkind allow-list** with `KNOWLEDGE_SOURCE_SUBKIND` (existing) + `STATE_MIRROR_SUBKIND = "state-mirror"` (new). Reminders pack manifest doesn't get `subkind: state-mirror` in α-1 (that's α-2's scope); α-1's loader test fixtures use synthetic test manifests with the new subkind. c2 author confirms at orientation.
- **A-3: `EventLog.append` signature.** Per [D8 addition 2] + [D14]: `async def append(envelope: EventEnvelope, *, correlation_id: str | None = None, causation_id: str | None = None) -> str` (returns `event_id`). Verify by reading head of `adminme/events/log.py` via `sed -n '1,80p'`.
- **A-4: `IngestClient.submit` returns `SubmitResult` (ok, event_id, status_code, error).** Verified at α pre-auth (frozen dataclass at `bridge/adminme_bridge/ingest_client.py` line 22-27).
- **A-5: `BridgeContext` is frozen dataclass + field order matters.** F-3 above: add `sink` at the END with default `None`.
- **A-6: Existing 11c-i tests continue passing.** Run the full `bridge/tests/unit/` + `tests/unit/products/bridge/` suite in c4; assert zero regressions.

## §5 Objective

Extend the bridge daemon at `bridge/adminme_bridge/` to support Cat-B state-mirror adapter packs as a first-class runtime target alongside the existing Cat-D knowledge-source packs. Introduces an event-sink abstraction (sinks.py), a sibling `StateMirrorBridgeAdapter` Protocol, a generalized pack loader, and a widened supervisor adapter-list type. No reminders-pack changes; no `AppleEventKitAdapter` changes; no new event schemas.

## §6 Out of scope

(See §1 OUT OF SCOPE list + `docs/10-split-memo-11b-ii-alpha.md` §2 11b-ii-α-1 scope.)

## §7 Deliverables

**c1: NEW `bridge/adminme_bridge/adapters/sinks.py` (≤120 lines) + extend `BridgeContext`**:

1. `bridge/adminme_bridge/adapters/sinks.py` (NEW):
   - `EmitResult` frozen dataclass: `{ok: bool, event_id: str | None, error: str | None}`.
   - `@runtime_checkable class BridgeEventSink(Protocol)`: `async def emit(self, envelope: EventEnvelope) -> EmitResult`.
   - `class IngestClientBridgeSink`: wraps `IngestClient.submit`; maps `SubmitResult → EmitResult`.
   - `class EventLogBridgeSink`: wraps `EventLog.append`; passes through `envelope.correlation_id` / `envelope.causation_id` as kwargs per [D8].
   - Docstrings cite [§6.19] / memo 04 §2.2 / F-8.

2. `bridge/adminme_bridge/adapters/base.py` (EXTEND, ~10 lines added):
   - Add `sink: BridgeEventSink | None = None` to `BridgeContext` (END of field list per F-3).
   - Update `__all__` if needed (no new top-level exports — sinks export via their own module).

3. `bridge/adminme_bridge/adapters/__init__.py` (EXTEND):
   - Re-export `BridgeEventSink, EventLogBridgeSink, IngestClientBridgeSink, EmitResult` from `sinks.py`.

c1 verification:
```bash
poetry run python -c "from adminme_bridge.adapters import BridgeEventSink, EventLogBridgeSink, IngestClientBridgeSink, EmitResult; print('sinks imports ok ✓')"
poetry run mypy bridge/adminme_bridge/adapters/ 2>&1 | tail -5
```

**c2: Sibling Protocol + loader + supervisor type (~70-100 lines net diff across 3 files)**:

1. `bridge/adminme_bridge/adapters/base.py` (EXTEND, ~30-40 lines added):
   - `@runtime_checkable class StateMirrorBridgeAdapter(Protocol)`: `family: ClassVar[str]`, `async start(self, *, ctx: BridgeContext)`, `async stop(self)`, `def status(self) -> AdapterStatus`.
   - `BridgeAdapter = KnowledgeAdapter | StateMirrorBridgeAdapter` (TypeAlias).
   - Update `__all__` += `["StateMirrorBridgeAdapter", "BridgeAdapter"]`.

2. `bridge/adminme_bridge/packs/__init__.py` (EXTEND, ~20-30 lines):
   - Add `STATE_MIRROR_SUBKIND = "state-mirror"` constant.
   - Refactor `discover_packs()` filter: accept manifest if `kind == "adapter"` AND `subkind in {KNOWLEDGE_SOURCE_SUBKIND, STATE_MIRROR_SUBKIND}`. Unknown subkinds continue silent-skip.
   - Update `__all__` += `["STATE_MIRROR_SUBKIND"]`.

3. `bridge/adminme_bridge/main.py` (EXTEND, ≤10 lines net diff):
   - `BridgeSupervisor.adapters: list[KnowledgeAdapter]` → `list[BridgeAdapter]` (import the TypeAlias).
   - Update `adapters` property type.
   - Update `__init__` param type.
   - No logic changes.

c2 verification:
```bash
poetry run python -c "from adminme_bridge.adapters.base import StateMirrorBridgeAdapter, BridgeAdapter; from adminme_bridge.packs import STATE_MIRROR_SUBKIND, KNOWLEDGE_SOURCE_SUBKIND; print('c2 imports ok ✓')"
poetry run mypy bridge/adminme_bridge/ 2>&1 | tail -5
```

**c3: Framework tests (≥10 NEW + extensions to 2 existing)**:

- `bridge/tests/unit/test_sinks.py` (NEW, F-4 unique basename, ~5 tests):
  - `test_emit_result_is_frozen_dataclass`.
  - `test_ingest_client_bridge_sink_maps_submit_result_to_emit_result_ok_case`.
  - `test_ingest_client_bridge_sink_maps_submit_result_to_emit_result_error_case`.
  - `test_event_log_bridge_sink_passes_correlation_and_causation_kwargs_per_d8`.
  - `test_bridge_event_sink_protocol_runtime_check_both_impls`.

- `bridge/tests/unit/test_state_mirror_protocol.py` (NEW, F-4 unique, ~4 tests):
  - `test_state_mirror_bridge_adapter_protocol_requires_family_classvar`.
  - `test_knowledge_adapter_protocol_unchanged_subkind_still_required`.
  - `test_bridge_adapter_typealias_accepts_both_protocols`.
  - `test_mock_cat_b_class_satisfies_state_mirror_bridge_adapter_isinstance_check`.

- `bridge/tests/unit/test_packs_loader_state_mirror.py` (NEW, F-4 unique, ~5 tests):
  - `test_discover_packs_unchanged_yields_knowledge_source_packs`.
  - `test_discover_packs_yields_state_mirror_packs`.
  - `test_discover_packs_silently_skips_unknown_subkind`.
  - `test_discover_packs_raises_pack_load_error_on_malformed_state_mirror_manifest`.
  - `test_discover_packs_constants_match_module_exports`.

- `bridge/tests/unit/test_adapters_base.py` (EXTEND, ~2 tests):
  - `test_bridge_context_construction_with_optional_sink` (F-3 — `sink` is optional + defaults to None).
  - `test_bridge_context_field_order_preserves_positional_arg_compatibility` (F-3 pin).

c3 verification:
```bash
poetry run pytest bridge/tests/unit/test_sinks.py bridge/tests/unit/test_state_mirror_protocol.py bridge/tests/unit/test_packs_loader_state_mirror.py bridge/tests/unit/test_adapters_base.py -v
# Expected: ≥12 new passed; 0 regressions on existing 11c-i tests.
```

**c4: integration + supervisor wire-up + BUILD_LOG**:

1. `bridge/tests/unit/test_main.py` (EXTEND, ~2 new tests):
   - `test_bridge_supervisor_accepts_mock_state_mirror_adapter` (A-6 invariant — existing Cat-D path continues working).
   - `test_bridge_supervisor_start_dispatches_to_both_cat_d_and_cat_b_adapters_in_one_run`.

2. `tests/integration/test_bridge_state_mirror_onramp.py` (NEW, F-4 unique, ~2 tests):
   - `test_mock_cat_b_adapter_emits_via_ingest_client_sink_end_to_end` — full bridge supervisor → mock adapter `start` → emit via `IngestClientBridgeSink` → mock central `:3337` receiver records envelope.
   - `test_mock_cat_b_adapter_emits_via_event_log_sink_for_test_fixture` — central-deployment-fallback path; `EventLogBridgeSink` wired to in-memory `EventLog`; envelope appended with correlation_id passed through per [D8].

3. `docs/build_log.md` — 11b-ii-α-1 entry per the 11g / 11c-i precedent. **Evidence section**: test counts ≥12 + ≥2 extensions + ≥2 integration; mypy clean; existing 11c-i suite unchanged. **Carry-forward**: bridge-runtime Cat-B on-ramp landed; **11b-ii-α-2 (AppleEventKitAdapter dual-deployment) is now unblocked** — it consumes `BridgeEventSink` + `StateMirrorBridgeAdapter` + the loader's new `state-mirror` subkind. Reusable by 11b-ii-β + 11e Apple Contacts + any future dual-deployment Cat-B adapter.

c4 verification:
```bash
poetry run pytest bridge/tests/unit/ tests/integration/test_bridge_state_mirror_onramp.py -v
cd bridge && poetry run pytest tests/unit/ -v && cd ..
poetry run pytest tests/unit/products/bridge/ -v  # existing 11c-i central tests; expect 0 regressions
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §8 Commit structure

Four commits exactly. Every commit message follows `phase 11b-ii-alpha-1 commit <N>: <imperative summary>` exactly.

- **c1: `phase 11b-ii-alpha-1 commit 1: BridgeEventSink Protocol + EventLogBridgeSink + IngestClientBridgeSink`** — `bridge/adminme_bridge/adapters/sinks.py` (NEW, ≤120 lines) + `base.py` BridgeContext `sink` field + `__init__.py` re-exports.
- **c2: `phase 11b-ii-alpha-1 commit 2: StateMirrorBridgeAdapter Protocol + loader subkind allow-list + supervisor type widening`** — `bridge/adminme_bridge/adapters/base.py` + `packs/__init__.py` + `main.py`.
- **c3: `phase 11b-ii-alpha-1 commit 3: framework-level sinks + Protocol + loader tests`** — `bridge/tests/unit/test_sinks.py` + `test_state_mirror_protocol.py` + `test_packs_loader_state_mirror.py` + extensions to `test_adapters_base.py`.
- **c4: `phase 11b-ii-alpha-1 commit 4: integration test + supervisor wire-up + BUILD_LOG`** — `tests/integration/test_bridge_state_mirror_onramp.py` + extensions to `bridge/tests/unit/test_main.py` + `docs/build_log.md`.

## §8.5 Branch strategy — STACKED

Each c-band commits onto the prior c-band's branch; PRs open with `base=main` per the established pattern (PR #106 sidecar precedent). Ghost-conflict risk: LOW. c-band file scopes mostly non-overlapping (c1 = sinks.py new + base.py BridgeContext field; c2 = base.py Protocol/TypeAlias + packs/__init__.py + main.py; c3 = new test files + small extension; c4 = new integration test + test_main.py extension + build_log.md append). c1 and c2 BOTH touch `bridge/adminme_bridge/adapters/base.py` — c2 is stacked on c1, so c2's edits land cleanly atop c1's.

Test floor: ≥12 NEW c3 + ≥2 extensions (test_adapters_base.py) + ≥2 extensions (test_main.py) + ≥2 NEW integration = **≥18 total NEW + 4 extensions**.

## §9 Verification

(Per c-band per §7 above. c4 final verification:)

```bash
poetry run pytest bridge/tests/unit/ tests/integration/test_bridge_state_mirror_onramp.py tests/unit/products/bridge/ -v
cd bridge && poetry run pytest tests/unit/ -v && cd ..
poetry run mypy bridge/adminme_bridge/ adminme/ 2>&1 | tail -10
bash scripts/verify_invariants.sh
# Expected: all pass; exit 0; zero regressions on existing 11c-i tests.
```

## §10 Stop

When `bridge/adminme_bridge/adapters/sinks.py` exists with the three exports + `StateMirrorBridgeAdapter` Protocol on main + `discover_packs()` yields state-mirror packs + `BridgeSupervisor.adapters: list[BridgeAdapter]` accepts a mock Cat-B adapter end-to-end + existing 11c-i tests pass unchanged + `verify_invariants.sh` exits 0, c4 is done. **Unlocks 11b-ii-α-2 (AppleEventKitAdapter dual-deployment + bridge connector-pack), 11b-ii-β (Apple Calendar fresh Cat-B + dual), 11e Apple Contacts bridge half, and any future dual-deployment Cat-B adapter.** Do NOT proceed to 11b-ii-α-2 without operator review.

After merge: Partner runs three-job QC. Updates `docs/partner_handoff.md` head paragraph + `docs/build_log.md` (c4 evidence already in place from this prompt; head paragraph is the partner-state-snapshot update).
