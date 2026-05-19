# 10-split-memo-11b-ii-alpha.md — Secondary Tier C split memo for 11b-ii-α (Apple Reminders dual-deployment)

**Authored:** 2026-05-19 by Partner during the Phase A closeout multi-Z drive session, immediately after the Z1 pre-authorship gate against `docs/04-split-memo-11b-ii.md` §2 surfaced four Gate 4 contract misses against on-main bridge architecture.

**Scope:** Secondary split of **11b-ii-α** (already split off from 11b-ii per `docs/04-split-memo-11b-ii.md` on 2026-05-18) into **11b-ii-α-1** + **11b-ii-α-2** per PM-23 secondary-split lineage. The parent memo `04-split-memo-11b-ii.md` §4 explicitly flagged α as a secondary-split candidate; that watch flag fired during pre-auth.

**Status:** Disposition decision. **No code touched.** Output is this memo + (operator-merge-then) the next Partner session's first refactor (α-1). The α-1 and α-2 v4 refactor prompts follow in separate Partner sessions (Type 3 refactor-only).

**Triggering session:** Z1 pre-authorship gate run on 2026-05-19 against `daecbc24`. The gate's verification table found all 17 cited paths exist on main (Gate 2 PASS), but the contracts table found four cross-cutting `✗` findings against the parent split memo §2 α-scope description.

---

## §1 Why secondary-split

The parent split memo at `docs/04-split-memo-11b-ii.md` §2 describes 11b-ii-α as:

> "Extend the existing `packs/adapters/reminders/apple-eventkit/` pack to support **dual deployment** (central + per-member bridge) per [D22] / memo 05 §5.4 / [§6.19] bridge sovereignty. **No new pack.** Code-level changes are mostly manifest + small adapter-config differences keyed off `deployment_target`."

The scope-shape items it enumerated:

- `pack.yaml`: add `deployment_targets: [central, bridge]`
- `config.schema.json`: add `deployment_target: enum["central", "bridge"]`
- `adapter.py`: minor (~50-100 lines added); read `ctx.deployment_target` and branch credentials + sharing_model
- `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/`: NEW small `__init__.py` declaring the bridge-side load path
- The bridge daemon "imports the same `AppleEventKitAdapter` class but instantiates it with `deployment_target='bridge'`"
- Estimated envelope: ~200-260 prompt lines / ~14-18 KB; ~120-180 LOC added; ≥11 tests; ~2.5-3 hrs wall

The pre-authorship Gate 4 contracts table run on 2026-05-19 (`daecbc24`) surfaced four cross-cutting on-main contract gaps that materially change the scope shape:

### Contract miss #1 — `BridgeContext` lacks an `event_log` analog

`bridge/adminme_bridge/main.py::BridgeSupervisor.make_context()` returns a `BridgeContext` containing `{member_id, ingest_client, correlation_id_factory, now_factory}`. There is **no `event_log`** field on `BridgeContext` (`bridge/adminme_bridge/adapters/base.py` lines 35-48). The central `AdapterContext` (`adminme/adapters/base.py` lines 50-62) has `{event_log: EventLog, config: dict, correlation_id_factory, now_factory}`. The two context types are not interchangeable.

`AppleEventKitAdapter` today (post-11r-c retrofit, post-11b-i dual-emit) emits via `event_log.append(envelope)` on both outbound `_write_one_outbound` and inbound `_emit_inbound_synced` (per `prompts/_active/11b-i-apple-reminders-list-migration.md` §1 items 2-3). For the bridge deployment target to land emits on the central `:3337` ingest endpoint (per [§6.19] / memo 04 §2.2), the adapter's emit sites must dispatch to `IngestClient.submit(envelope)` instead of `event_log.append(envelope)`. This requires introducing an **event-sink abstraction** (Protocol or dual-method) over the two emit pathways AND rewiring every emit site in the adapter. The parent memo's "~50-100 lines added" estimate excludes this surgery; realistic estimate is ~150-220 LOC including the abstraction + emit-site rewiring + the deployment-target branching.

### Contract miss #2 — bridge `discover_packs()` filter rejects state-mirror packs

`bridge/adminme_bridge/packs/__init__.py::discover_packs()` walks `<packs_dir>/*/pack.yaml` and filters strictly:

```python
if data.get("kind") != KNOWLEDGE_SOURCE_KIND or data.get("subkind") != KNOWLEDGE_SOURCE_SUBKIND:
    continue
```

where `KNOWLEDGE_SOURCE_KIND = "adapter"` and `KNOWLEDGE_SOURCE_SUBKIND = "knowledge-source"`.

The reminders pack at `packs/adapters/reminders/apple-eventkit/pack.yaml` declares `kind: adapter` but has **no `subkind` field at all** (it carries `family: reminders` and `capabilities: [{kind: Cat-B, runtime: central, …}]`). Today the bridge loader silently skips it. The parent memo's "One small `__init__.py` declaring the bridge-side load path" does not change this filter.

To make the bridge loader pick up state-mirror packs, α must either:
- Extend the discriminator to a multi-subkind allow-list (e.g., `{"knowledge-source", "state-mirror"}`) AND add `subkind: state-mirror` to the reminders pack manifest, OR
- Introduce a parallel state-mirror loader in `bridge/adminme_bridge/state_mirrors/` (or similar) that filters by `kind: adapter, capabilities[*].kind: Cat-B, capabilities[*].runtime: dual` (or `bridge`), OR
- Drop the subkind filter and broaden by `family` + `runtime` (semantic alignment with [D19] axes).

Each option has trade-offs; the choice is a real architectural decision, not a manifest tweak.

### Contract miss #3 — `KnowledgeAdapter` Protocol's `subkind` ClassVar disqualifies Cat-B

`bridge/adminme_bridge/adapters/base.py::KnowledgeAdapter` is a `@runtime_checkable` Protocol declaring:

```python
class KnowledgeAdapter(Protocol):
    subkind: ClassVar[str]
    async def start(self, *, ctx: BridgeContext) -> None: ...
    async def stop(self) -> None: ...
    def status(self) -> AdapterStatus: ...
```

`ExternalStateMirrorAdapter` (per `adminme/lib/adapters/external_state_mirror_base.py` lines 99-105) declares `family: ClassVar[Literal["reminders", "calendaring", "contacts", "tasks"]]` but NOT `subkind`. A runtime `isinstance(apple_adapter, KnowledgeAdapter)` check returns False today. The `start(ctx)` signature also fails — `ExternalStateMirrorAdapter.start` takes `ctx: AdapterContext`, not `BridgeContext`.

The parent memo's framing of "the same `AppleEventKitAdapter` class" satisfying the bridge supervisor is structurally false. Either:
- Introduce a sibling `StateMirrorBridgeAdapter` Protocol (with `family` ClassVar + `BridgeContext` start signature) and broaden `BridgeSupervisor.adapters: list[KnowledgeAdapter | StateMirrorBridgeAdapter]`, OR
- Generalize `KnowledgeAdapter` Protocol into a `BridgeAdapter` base Protocol with two specializations, OR
- Have the adapter class implement BOTH the central `Adapter` Protocol AND the bridge `KnowledgeAdapter` Protocol (with a `subkind` class attribute), accepting a unified context shape.

Again — architectural choice, not a config tweak.

### Contract miss #4 — `BridgeSupervisor.adapters` type narrows out Cat-B

`BridgeSupervisor.__init__` accepts `adapters: list[KnowledgeAdapter]` (`bridge/adminme_bridge/main.py` lines 35-43). Even if a Cat-B adapter satisfied the Protocol structurally, the supervisor's type annotation excludes it. mypy would reject the wire-up. The supervisor's `start()` loop iterates `self._adapters` and dispatches `adapter.start(ctx=self.make_context())` — `make_context()` returns `BridgeContext`, which the Cat-B `start(ctx: AdapterContext)` signature rejects.

### Net effect

The parent memo's α-scope "Light: same adapter shape as 11b-iii / 11b-iv siblings; two new `family` values" framing is **correct at the architectural-category level** — both deployment targets are Cat-B, both emit `list_item.*@v1` per [D18], both fit the canonical pattern. The parent memo correctly identified the architectural cleanness.

What the parent memo **underestimated** is the **bridge-runtime extension cost**. The bridge daemon (per `prompts/_active/11c-i-bridge-daemon-and-ingest.md`) shipped Cat-D-only — `bridge/adminme_bridge/main.py` line 1 docstring: *"Mirrors `adminme/daemons/adapter_supervisor.py` but Cat-D-only and HTTP-emit."* 11c-i also explicitly defers Apple Reminders bridge half to 11b-ii (11c-i §"Out of scope": "Apple Reminders / Apple Calendar / Apple Contacts bridge halves — 11e / 11b-ii."). The architecture asks 11b-ii to **introduce Cat-B support to the bridge runtime**, not just to write a Cat-B adapter that happens to run there.

Realistic combined envelope for α-as-parent-memo-described:

| Subpart | Estimated prompt lines | Estimated LOC | Estimated tests | §2.9 fit |
|---|---|---|---|---|
| Bridge runtime Cat-B on-ramp (event-sink abstraction + loader filter + Protocol generalization + supervisor type broadening) | ~180-220 | ~150-200 across 4-5 files | ~10-14 framework tests | comfortably ≤350 prompt lines, fits |
| AppleEventKitAdapter dual-deployment (manifest + config schema + adapter.py emit-site rewiring + connector-pack scaffold + bridge-side credential plumbing) | ~250-300 | ~150-220 | ~12-18 framework + ~3-4 pack-internal + ~2-3 integration | at upper edge of §2.9 |
| **Combined as single 11b-ii-α** | **~430-520** | **~300-420** | **~27-39** | **~30-50% over §2.9 ceiling — anti-vision halt at authoring** |

Per PM-23 (secondary splits permitted on discovery of envelope blowout at orientation) + the parent memo's own §4 watch flag "11b-ii-α secondary-split candidate", **the structural fix is to split α into α-1 (bridge runtime Cat-B on-ramp; foundation; lighter) + α-2 (AppleEventKitAdapter dual-deployment; depends on α-1; mirrors 11b-iii in shape modulo the dual-deployment branching)**.

This split also has architectural payoff: **α-1's on-ramp is reusable**. 11b-ii-β (Apple Calendar fresh Cat-B + dual variant), 11e (Apple Contacts bridge per-member per [D23]), and any future dual-deployment Cat-B adapter all consume α-1's infrastructure. Bundling the on-ramp into α-1 means it gets one focused review pass instead of being smeared across every Cat-B-bridge prompt.

---

## §2 Disposition

**Split 11b-ii-α into 11b-ii-α-1 + 11b-ii-α-2.** Dependency-correct ordering: α-1 before α-2.

### 11b-ii-α-1 — Bridge runtime Cat-B on-ramp

**Scope:** Introduce the bridge-runtime infrastructure required to load + supervise + emit-from Cat-B `ExternalStateMirrorAdapter` packs alongside the existing Cat-D `KnowledgeAdapter` packs. **No reminders-pack changes.** **No AppleEventKitAdapter changes.** **No connector-pack scaffolds.** Just the on-ramp.

**Key deliverables (sketch; final wording at α-1 v4 refactor time):**

- **Event-sink abstraction.** Introduce a `BridgeEventSink` Protocol (or equivalent) in `bridge/adminme_bridge/adapters/base.py` (or a new sibling module) that abstracts over the two emit pathways. Two implementations: `EventLogBridgeSink` (wraps central `EventLog`, used in tests + central-deployment fallback) and `IngestClientBridgeSink` (wraps `IngestClient.submit` for the bridge runtime path). `BridgeContext` gains a `sink: BridgeEventSink` field (replacing or augmenting `ingest_client` depending on the chosen shape). Test floor: ≥4 framework tests covering both sink implementations + Protocol satisfaction.
- **Loader generalization.** Extend `bridge/adminme_bridge/packs/__init__.py::discover_packs()` to filter by a subkind allow-list (or by a `bridge_compatible: true` manifest field, or by capability inspection — α-1's authoring session picks the shape after reading memo 05 §3.1 + §5.4 + [D19]). Add `subkind: state-mirror` (or equivalent) to a new `KNOWLEDGE_SOURCE_KIND` constant family. Test floor: ≥4 framework tests covering existing knowledge-source discovery (unchanged) + new state-mirror discovery + rejection of unknown subkinds + manifest-malformed errors.
- **Protocol broadening or sibling.** Either generalize `KnowledgeAdapter` into a `BridgeAdapter` base Protocol with `KnowledgeBridgeAdapter` + `StateMirrorBridgeAdapter` specializations, OR introduce a sibling `StateMirrorBridgeAdapter` Protocol with the appropriate ClassVar (`family` not `subkind`) + `BridgeContext` start signature. The α-1 author picks the cleaner shape and documents the choice. Test floor: ≥3 framework tests covering Protocol runtime-check satisfaction.
- **Supervisor type broadening.** `BridgeSupervisor.adapters` type annotation widened (Union Protocol or generic). `BridgeSupervisor.start()` loop dispatches to whichever start signature the adapter satisfies. Test floor: ≥3 framework tests covering supervisor wire-up of a mock Cat-D adapter (existing behavior preserved) AND a mock Cat-B adapter (new path).
- **No new event schemas.** Pure infrastructure.
- **No new `family` Literal values.** Pure infrastructure.
- **No `pack.yaml` / `config.schema.json` changes on the reminders pack.** That's α-2's scope.
- **No `AppleEventKitAdapter` code changes.** That's α-2's scope.

**Estimated envelope:**

- v4 prompt: ~200-260 lines / ~14-18 KB.
- LOC: ~120-180 added across `bridge/adminme_bridge/{adapters/base.py, packs/__init__.py, main.py}` + possibly a new `bridge/adminme_bridge/adapters/sinks.py` (or similar).
- Tests: ≥10 framework + ≥2 integration (mock Cat-B adapter end-to-end through the bridge supervisor → emit → sink → consumed).
- Wall time: ~2.5-3 hours / ~$50-80 spend.

**Dependencies:** 11c-i bridge daemon shell closed (main `1f88ccec`); central `ExternalStateMirrorAdapter` ABC on main (no changes); central `Adapter` Protocol on main (no changes).

**Unlocks:** the bridge-runtime Cat-B template that 11b-ii-α-2, 11b-ii-β, 11e (Apple Contacts), and any future bridge-deployed Cat-B adapter reuse.

**§2.9 self-check (predicted):** PASS comfortably. Single-cycle judgment: well within Executor's single-session envelope. Files-to-read per commit: ≤6 (bridge daemon files only; no reminders code).

### 11b-ii-α-2 — AppleEventKitAdapter dual-deployment + bridge connector-pack

**Scope:** Extend the existing `packs/adapters/reminders/apple-eventkit/` pack and `adapter.py` to support dual deployment (central + per-member bridge) per [D18] / [D22] / memo 05 §5.4 / [§6.19]. **Depends on α-1's on-ramp.** **No new pack** (still per parent memo `04-split-memo-11b-ii.md` §2).

**Key deliverables (sketch; final wording at α-2 v4 refactor time, after α-1 lands):**

- `packs/adapters/reminders/apple-eventkit/pack.yaml` — extend `capabilities` with `runtime: dual` (per memo 05 §3.1) AND add a top-level `deployment_targets: [central, bridge]` field per parent memo. Add `subkind: state-mirror` (or whatever α-1 lands on) so the bridge loader picks the pack up.
- `packs/adapters/reminders/apple-eventkit/config.schema.json` — extend with `deployment_target: enum["central", "bridge"]` (default `central`) per parent memo. Plus any per-deployment-target nested config (e.g., `central_credential_ref` vs `bridge_credential_strategy: tailscale_bound`).
- `packs/adapters/reminders/apple-eventkit/adapter.py` — emit-site rewiring per α-1's event-sink abstraction. Deployment-target-aware credential source: central path reads from existing config; bridge path receives per-member Apple ID credentials via `BridgeContext` per memo 04 §2.2. `sharing_model_discriminator` branches: `shared_central` for central, `per_member_isolated` for bridge. `owner_scope` stamping for bridge variant uses Tailscale-bound identity (resolved by the central `:3337` ingest router per 11c-i F-3) — adapter sends an unstamped envelope; central router stamps. Emit-site changes happen at `_write_one_outbound`, `_emit_inbound_synced`, `_apply_write_impl`. Preserve all other behavior byte-for-byte (poll loop, never-sync filter, cursor milestone, observation wrap, dual-emit of legacy + canonical schemas from 11b-i).
- `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` — NEW bridge-side connector-pack scaffold. Minimal `__init__.py` plus a small `pack.yaml` (or equivalent) declaring the bridge-side load entrypoint per α-1's loader contract. Imports `AppleEventKitAdapter` from `packs.adapters.reminders.apple_eventkit.adapter` and instantiates with `deployment_target="bridge"` + bridge-side BridgeContext wiring.
- **No new event schemas.** Both deployment targets emit existing `reminder.list_item.synced@v1` + `list_item.*@v1` family from 11b-i.
- **No new `family` Literal values** (still — per parent memo).

**Estimated envelope:**

- v4 prompt: ~280-340 lines / ~16-20 KB.
- LOC: ~150-220 added.
- Tests: ≥14 framework (deployment_target dispatch; bridge credential injection; sharing_model emission distinct between central + bridge; event-sink dispatch by deployment target) + ≥4 pack-internal + ≥3 integration (bridge daemon end-to-end round-trip).
- Wall time: ~3.5-4 hours / ~$70-100 spend.

**Dependencies:** α-1 closed; 11b-i closed (main `4ed7b731`); existing `packs/adapters/reminders/apple-eventkit/` Cat-B retrofit on main.

**Unlocks:** Apple-side Cat-B parity (central + bridge) with Google-side. Closes the parent memo `04-split-memo-11b-ii.md` α scope. Together with 11b-ii-β (still next in queue after α-2), completes 11b-ii.

**§2.9 self-check (predicted):** PASS at the upper edge of §2.9. Files-to-read per commit: ≤6 (adapter.py + sink Protocol from α-1 + pack manifest + config schema + bridge connector-pack scaffold).

---

## §3 Sequence-update implication

The closeout brief at `docs/SESSION-HANDOFF-2026-05-18-phase-a-closeout.md` §3 + parent split memo `04-split-memo-11b-ii.md` §5 shift the Z1-Z10 sequence by one cycle. This memo's secondary split shifts it again by one more cycle:

- **Z1: 11b-ii-α-1** (bridge runtime Cat-B on-ramp) — NEW per this memo.
- **Z1-bis: 11b-ii-α-2** (AppleEventKitAdapter dual-deployment + connector-pack) — NEW per this memo.
- **Z1.5: 11b-ii-β** (Apple Calendar fresh Cat-B + dual variant) — unchanged from parent memo (Apple Calendar fresh Cat-B + dual; reuses α-1's bridge on-ramp).
- **Z2: 11c-ii** (Cat-D scaffolding) — unchanged.
- **Z3: 11d** (member_knowledge projection) — quarter-mark, unchanged.
- **Z4: 11e** (Contacts) — unchanged; Apple Contacts half also reuses α-1's bridge on-ramp.
- **Z5: 12** (OpenClaw plugin adapters) — unchanged.
- **Z6: 14a + 14b + 14c** (Console) — quarter-mark, unchanged.
- **Z7: 15 + 15.5** — unchanged.
- **Z8: 16** (Bootstrap wizard, Tier C split) — unchanged.
- **Z9: 17** (CLI) — unchanged.
- **Z10: 18** (integration test → Phase A codebase complete) — unchanged.

**Total Phase A cycles to bootstrap-ready:** ~14-16 (was ~13-15 in parent memo, +1 for this secondary split).

The cost of the secondary split is +1 cycle on the Z1 chunk; the payoff is that α-1's bridge Cat-B on-ramp is built once and reused by β / 11e Apple-half / any future dual-deployment Cat-B adapter, instead of being re-derived under time pressure each time.

**Minimal-scope `prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation:** append after the parent-memo annotation already there (per PR #199):

```
**11b-ii-α secondary split (updated 2026-05-19):** Pre-authorship gate
against `docs/04-split-memo-11b-ii.md` §2 on 2026-05-19 (main `daecbc24`)
surfaced four cross-cutting bridge-runtime contract misses (BridgeContext
lacks event_log abstraction; bridge `discover_packs()` filters reject
state-mirror packs; `KnowledgeAdapter` Protocol's `subkind` ClassVar
disqualifies Cat-B; `BridgeSupervisor.adapters` type narrows out Cat-B).
11b-ii-α is pre-split into **11b-ii-α-1** (bridge runtime Cat-B on-ramp;
infrastructure only) and **11b-ii-α-2** (AppleEventKitAdapter
dual-deployment + connector-pack; depends on α-1) per
`docs/10-split-memo-11b-ii-alpha.md`. α-1's on-ramp is reusable by
11b-ii-β + 11e Apple Contacts + any future dual-deployment Cat-B
adapter. Total cycles to bootstrap-ready: ~14-16.
```

Lands together with this memo in the same `partner/10-split-memo-11b-ii-alpha` PR per PM-22 infrastructure-PR pattern.

---

## §4 Secondary-secondary-split watch flags (defensive)

Per PM-23 lineage and the lessons of the parent memo §4:

- **α-1 secondary-split candidate:** if the α-1 author at v4 refactor time discovers that the chosen Protocol-broadening or sibling-Protocol shape requires touching the central `Adapter` Protocol at `adminme/adapters/base.py` (e.g., to unify `AdapterContext` and `BridgeContext` under a common parent type), that's a constitutional ABC touch. Per [PM-34] (5 ABCs only) the touch may require a PM-28 architecture amendment — not a refactor. Watch for this; if the chosen shape requires central-Adapter-Protocol surgery, halt α-1 authoring and open `<NEEDS_OPERATOR_DECISION reason="alpha_1_requires_central_protocol_amendment"/>`.
- **α-2 secondary-split candidate:** if EKEventStore PyObjC's per-member-Apple-ID credential surface introduces a heavier-than-anticipated platform-coupling layer (compared to the central path which uses the supervisor's process-level Apple ID), the credential-injection logic alone may exceed 100 LOC and warrant splitting α-2 into α-2-a (manifest + config schema + central-path no-op verification) + α-2-b (bridge-path credential injection + emit-site rewiring + connector-pack). Watch the §2.9 self-check at v4 refactor.

Both flags are **defensive**. The default pre-authorship gate at α-1 / α-2 refactor time will catch envelope blowouts.

---

## §5 Operator follow-up

After this memo merges + `prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation lands:

1. **Workspace sync (still pending from parent memo §5.1).** Fetch live main's `prompts/PROMPT_SEQUENCE.md` to the workspace pinned copy. Closes both the original 2026-05-18 workspace-staleness signal AND this memo's update.

2. **Next Partner session opens with α-1 refactor.** The Type 3 refactor session runs the pre-authorship gate against main for α-1's cited paths (bridge daemon files; central `Adapter` Protocol; the Cat-B ABC; the canonical Cat-D `KnowledgeAdapter` Protocol). Choice of Protocol shape (broadened single base / sibling Protocols / unified context) is the α-1 author's decision after reading memo 05 §3.1 + §5.4 + [D19] + [PM-34].

3. **α-2 refactor follows α-1's merge.** Reads α-1's shipped Protocol shape + event-sink abstraction as canonical reference. Writes against the new bridge-loader subkind allow-list (or equivalent).

4. **Phase A cycle estimate updates to ~14-16 from ~13-15.**

---

## §6 What this memo is NOT

- **Not a v4 refactor prompt.** This memo declares the secondary split disposition only. The 11b-ii-α-1 and 11b-ii-α-2 v4 refactored prompts follow in separate Partner sessions.
- **Not a constitutional doc edit.** No SYSTEM_INVARIANTS / DECISIONS / architecture-summary / BUILD.md / CONSOLE_PATTERNS / REFERENCE_EXAMPLES / DIAGRAMS touched. PM-28 amendment cycle does NOT apply at memo time. (Note α-1's secondary-split watch flag above: IF α-1's chosen shape requires central Protocol surgery, THAT would require PM-28 — but the disposition here is to avoid that path.)
- **Not a cohort-row refactor of PROMPT_SEQUENCE.md.** Only the Rebase notes section gets an additional tracking annotation.
- **Not a BUILD_LOG Evidence section.** PM-22 infrastructure PR — sequence/split-memo prep is not a build prompt.
- **Not a supersession of the parent memo.** `docs/04-split-memo-11b-ii.md` remains canonical for the 11b-ii cohort-level split into α + β; this memo only further splits α. β's contract is unchanged.

---

*Authored by Partner during the 2026-05-19 Phase A closeout multi-Z drive session, after pre-authorship Gate 4 against `docs/04-split-memo-11b-ii.md` §2 produced four cross-cutting contract misses against `daecbc24`. Lands together with the `prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation in a single `partner/10-split-memo-11b-ii-alpha` PR per PM-22.*
