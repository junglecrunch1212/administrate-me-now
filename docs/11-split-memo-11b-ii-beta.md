# 11-split-memo-11b-ii-beta.md — Tier C split memo for 11b-ii-β (Apple Calendar fresh Cat-B + dual-deployment)

**Authored:** 2026-05-20 by Partner during the Phase A closeout multi-Z drive session, immediately after the Z1.5 pre-authorship gate against `docs/04-split-memo-11b-ii.md` §2 (β scope) PASSED on paths + contracts but the §2.9 pre-split disposition surfaced a single-cycle envelope blowout.

**Scope:** Secondary split of **11b-ii-β** (itself split off from 11b-ii per `docs/04-split-memo-11b-ii.md` on 2026-05-18) into **11b-ii-β-α** + **11b-ii-β-β** per PM-23 secondary-split lineage. Both `docs/04-split-memo-11b-ii.md` §4 and `docs/10-split-memo-11b-ii-alpha.md` §4 explicitly flagged β as a secondary-split candidate; this memo records the disposition.

**Status:** Disposition decision. **No code touched.** Output is this memo. The 11b-ii-β-α and 11b-ii-β-β v4 refactor prompts follow in separate Partner sessions (Type 3 refactor-only).

**Triggering session:** Z1.5 pre-authorship gate, run 2026-05-20 against main `8b256840`. Gate 0 PASS (PROMPT_SEQUENCE surface-sync — α-side annotation lag is not β-scope-affecting); all 22 cited paths verified on main (Gate 2 PASS); all cross-cutting contracts confirmed against on-main signatures (Gate 4 PASS). The §2.9 pre-split disposition analysis — run before authoring per PM-23 — found 11b-ii-β as a single prompt ~1.6-1.8x the empirical single-cycle envelope.

---

## §1 Why split

`docs/04-split-memo-11b-ii.md` §2 sized 11b-ii-β at "~320-360 lines / ~18-22 KB ... at the §2.9 upper edge but inside." That estimate predates two facts that have since landed:

1. **The 10-split-memo's bridge-runtime discovery.** `docs/10-split-memo-11b-ii-alpha.md` established that dual-deployment of a Cat-B adapter is not a manifest tweak — it is a full sub-prompt's worth of scope. **11b-ii-α-2** (Apple Reminders dual-deployment of an *existing* pack) shipped as a **237-line / 33.5 KB** v4 prompt on its own (Partner refactor PR #210), with its own four-commit cycle (PRs #212 / #214 / #220 / #221, main `0de63b19`).

2. **β bundles a fresh-pack build AND a dual-deployment layer.** 11b-ii-β must do everything 11b-iii (Google Calendar fresh Cat-B central) did — a fresh `packs/adapters/calendaring/apple-calendar/` pack, `adapter.py` ≤500 lines, config schema + example, ≥15 framework + ≥3 pack-internal + ≥3 integration tests, a ~4-4.5 hr cycle — **plus** everything 11b-ii-α-2 did (deployment_target branching + a bridge connector-pack + ≥14 dual-deployment tests, a ~3.5-4 hr cycle).

| Subpart | Est. v4 prompt lines | Est. cycle | §2.9 fit |
|---|---|---|---|
| Apple Calendar fresh Cat-B central (new pack, mirrors 11b-iii / `google-calendar`) | ~300-340 | ~4-4.5 hr | at the §2.9 upper edge — fits alone |
| Apple Calendar dual-deployment (deployment_target branching + bridge connector-pack, mirrors 11b-ii-α-2) | ~230-280 | ~3.5-4 hr | fits alone |
| **Combined as single 11b-ii-β** | **~530-620** | **~7.5-8.5 hr** | **~1.6-1.8x the §2.9 ≤350-line ceiling; fails single-cycle judgment — anti-vision halt at authoring** |

The 04-memo's "fits, upper edge" framing was correct *at the architectural-category level* — β introduces no new ABCs and no new abstractions; both the fresh pack and the dual variant slot into existing canonical patterns. But "shape" and "size" are different envelopes: the shape is clean, the size is ~2x a single cycle. Per PM-23 (secondary splits permitted on discovery of envelope blowout at orientation) + the operator's standing pre-split discipline + the explicit §4 watch flags in both parent memos, **11b-ii-β is a Tier C pre-split before refactor.**

---

## §2 Disposition

**Split 11b-ii-β into 11b-ii-β-α + 11b-ii-β-β.** Dependency-correct ordering: β-α before β-β.

### 11b-ii-β-α — Apple Calendar fresh Cat-B central pack

**Scope:** NEW pack `packs/adapters/calendaring/apple-calendar/` mirroring the canonical fresh-Cat-B pattern established by `packs/adapters/calendaring/google-calendar/` (11b-iii). **Central deployment only** — `capabilities[*].runtime: central`; NO top-level `subkind` / `deployment_targets` fields (those are β-β's scope). No bridge connector-pack.

**Key deliverables (sketch; final wording at β-α v4 refactor time):**

- `packs/adapters/calendaring/apple-calendar/pack.yaml` — Cat-B manifest: `family: calendaring` (existing Literal value — no extension), `capabilities: [{kind: Cat-B, runtime: central, event_families: [calendar.event_added]}]`, `write_capabilities: [create_event]` per [UT-27] asymmetric, `sharing_model_discriminator: owner_member_id` per memo 05 §2.5, `dedup_strategy: {kind: external_id}` keyed on `EKEvent.calendarItemIdentifier`, `sensitivity_default: normal`, `observation_mode_required: true`, `sensitivity_floor: normal` per [§6 invariant 10].
- `packs/adapters/calendaring/apple-calendar/config.schema.json` + `config.example.yaml` — pack config (EventKit calendar identifier, `member_id`, `poll_interval_s`).
- `packs/adapters/calendaring/apple-calendar/adapter.py` — ≤500 lines (F-5 ceiling). `class AppleCalendarAdapter(ExternalStateMirrorAdapter)` with `family: ClassVar[Literal["calendaring"]] = "calendaring"`. Inbound: poll `EKEventStore` via PyObjC for events, emit existing `calendar.event_added@v1` (MAP DON'T REGISTER per [D7] / 11a F-6 — field set `{source, external_event_id, calendar_id, summary, start, end, location, attendees, body}` byte-for-byte). Outbound `_apply_write_impl` for `create_event` saves an `EKEvent` wrapped through `adminme.lib.observation.outbound` per [§6.20] (HARD: observation-suppressed path emits NEITHER `calendar.event_added` NOR `adapter.error`). `_on_external_change` Phase-A no-op satisfying the ABC.
- Tests: ≥15 framework (`tests/unit/adapters/calendaring/test_apple_calendar.py`, F-5 unique basename) + ≥3 pack-internal + ≥3 integration. Same shape as 11b-iii's test floor.
- **No new event schemas.** Maps onto existing `calendar.event_added@v1`.

**Estimated envelope:** v4 prompt ~300-340 lines / ~18-22 KB; adapter LOC ~400-500; ~21-25 tests; ~4-4.5 hr wall. §2.9 self-check (predicted): PASS at the upper edge — directly mirrors 11b-iii's proven envelope.

**Dependencies:** 11b-iii closed (`google-calendar` quality-bar reference, main `04cdee6d`); central `ExternalStateMirrorAdapter` ABC on main (no changes); existing `calendar.event_added@v1` schema on main.

**Unlocks:** the central Apple Calendar pack that 11b-ii-β-β extends with dual-deployment.

### 11b-ii-β-β — Apple Calendar dual-deployment

**Scope:** Extend β-α's `packs/adapters/calendaring/apple-calendar/` pack with dual deployment (central + per-member bridge) per [D18] / [D22] / memo 04 §2.2 / [§6.19], mirroring 11b-ii-α-2 1:1. **Depends on β-α** (the pack must exist) + 11b-ii-α-2 (the dual-deployment template) + 11b-ii-α-1 (the bridge runtime Cat-B on-ramp). **No new pack.**

**Key deliverables (sketch; final wording at β-β v4 refactor time, after β-α lands):**

- `packs/adapters/calendaring/apple-calendar/pack.yaml` — add top-level `subkind: state-mirror` (matches α-1's `STATE_MIRROR_SUBKIND` loader allow-list) + top-level `deployment_targets: [central, bridge]`.
- `packs/adapters/calendaring/apple-calendar/config.schema.json` — add `deployment_target` enum `["central", "bridge"]` (default `central`, optional — F-2 backwards-compat).
- `packs/adapters/calendaring/apple-calendar/adapter.py` — add `deployment_target` branching to `AppleCalendarAdapter`: central path byte-for-byte preserved (F-2); bridge path emits via a wrapper-injected `_bridge_sink` (the α-2 `_append_via_bridge_sink` pattern); `sharing_model` value branches `shared_central` / `per_member_isolated`.
- NEW `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` — THIN `AppleCalendarBridgeAdapter` wrapper mirroring α-2's `AppleEventKitBridgeAdapter`: satisfies α-1's `StateMirrorBridgeAdapter` Protocol (`family: ClassVar[Literal["calendaring"]]`), synthesizes a central `AdapterContext` from a `BridgeContext`, injects an `IngestClientBridgeSink`. Composition not multi-inheritance per [PM-34].
- Tests: ≥14 framework + ≥3 pack-internal + ≥3 integration.
- **No new event schemas. No new `family` Literal values.**

**Estimated envelope:** v4 prompt ~230-280 lines / ~16-20 KB; LOC ~150-220 added; ~20-24 tests; ~3.5-4 hr wall. §2.9 self-check (predicted): PASS comfortably — pure extension following the α-2 template, and lighter than α-2 itself because α-1's on-ramp is already on main.

**Dependencies:** β-α closed; 11b-ii-α-2 closed (dual-deployment template, main `0de63b19`); 11b-ii-α-1 closed (bridge on-ramp).

**Unlocks:** Apple-side Cat-B calendaring parity (central + bridge) with the Google side. Together with β-α, completes 11b-ii-β and the 11b-ii cohort.

### Why this seam

Each half maps **1:1 onto a known-good, recently-merged quality-bar reference**: β-α ≅ 11b-iii (fresh Cat-B central), β-β ≅ 11b-ii-α-2 (dual-deployment of an existing Cat-B pack). `docs/10-split-memo-11b-ii-alpha.md` §4's watch flag suggested an *inbound-poll-only / outbound+dual* seam; this memo instead splits at the **central-pack / dual-deployment** seam because: (a) each resulting sub-prompt has an exact on-main precedent, which is the lowest-risk authoring path and lets each pre-authorship gate cite a concrete sibling; (b) it matches the established Apple-side rhythm — 11b-ii-α already decomposed as "build the central capability (α-2's adapter), then the dual runtime support" — keeping the Apple Reminders and Apple Calendar sub-sequences structurally parallel. An inbound-poll-only β-α (the 10-memo's suggested shape) has no exact precedent and would defer the entire outbound + observation-wrap surface into a heavier β-β.

---

## §3 Sequence-update implication

The closeout brief `docs/SESSION-HANDOFF-2026-05-18-phase-a-closeout.md` §3 + `docs/04-split-memo-11b-ii.md` §5 + `docs/10-split-memo-11b-ii-alpha.md` §3 shift the Z-sequence; this memo's secondary split shifts it one more cycle:

- Z1 = 11b-ii-α-1 **(closed)** → Z1-bis = 11b-ii-α-2 **(closed, QC PASS 2026-05-20)** → **Z1.5 = 11b-ii-β-α** (NEW per this memo) → **Z1.5-bis = 11b-ii-β-β** (NEW per this memo) → Z2 = 11c-ii (Cat-D scaffolding) → Z3 = 11d (member_knowledge projection, **quarter-mark**) → Z4 = 11e (Contacts) → Z5 = 12 (OpenClaw plugin adapters) → Z6 = 14a/b/c (Console, **quarter-mark**) → Z7 = 15 + 15.5 → Z8 = 16 (Bootstrap wizard, Tier C split) → Z9 = 17 → Z10 = 18.

**Total Phase A cycles to bootstrap-ready: ~15-17** (was ~14-16 before this secondary split). The cost is +1 cycle on the Z1.5 chunk; the payoff is two sub-prompts that each clone a proven on-main template instead of one ~8-hour prompt that fails the §2.9 single-cycle judgment.

**`prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation:** deferred to the next partner-state-snapshot / workspace-sync sweep — consistent with the pending PROMPT_SEQUENCE workspace-sync follow-up already noted in `docs/04-split-memo-11b-ii.md` §5.1 and `docs/10-split-memo-11b-ii-alpha.md` §5.1. Row 159 (the "11b" cohort mega-row) is unchanged; β has no per-sub-prompt table row. β's authoritative scope source is this memo + `docs/04-split-memo-11b-ii.md` §2.

---

## §4 Secondary-secondary-split watch flags (defensive)

Per PM-23 lineage:

- **11b-ii-β-α secondary-split candidate:** if the `EKEventStore` PyObjC inbound-poll surface proves materially heavier than Google Calendar's `googleapiclient` (the original `docs/10-split-memo-11b-ii-alpha.md` §4 concern), the inbound poll loop alone could push `adapter.py` past the F-5 ≤500-line ceiling. Watch the §2.9 self-check at β-α v4 refactor — but central-only β-α mirrors 11b-iii's proven ≤500-line adapter envelope, so this is low-risk.
- **11b-ii-β-β secondary-split candidate:** pure α-2-template extension reusing α-1's on-ramp; very low split risk.

Both flags are **defensive** — they may not fire. The pre-authorship gate at β-α / β-β refactor time will catch any envelope blowout.

---

## §5 Operator follow-up

1. **Next Partner session opens with the 11b-ii-β-α refactor** (Type 3 refactor-only). The pre-authorship gate runs against main for β-α's cited paths: `prompts/_active/11b-iii-google-calendar-central.md` + `packs/adapters/calendaring/google-calendar/` (the fresh-Cat-B template); the Cat-B ABC at `adminme/lib/adapters/external_state_mirror_base.py`; `adminme/events/schemas/ingest.py::CalendarEventAddedV1`; `adminme/lib/observation.py`.
2. **11b-ii-β-β refactor follows β-α's merge** — reads β-α's shipped `apple-calendar` pack + α-2's `reminders_apple_eventkit_bridge` connector-pack as canonical references.
3. **PROMPT_SEQUENCE.md Rebase-notes annotation + workspace sync** land together in the next partner-state-snapshot sweep.

---

## §6 What this memo is NOT

- **Not a v4 refactor prompt.** This memo declares the secondary-split disposition only. The 11b-ii-β-α and 11b-ii-β-β v4 refactored prompts follow in separate Partner sessions.
- **Not a constitutional doc edit.** No SYSTEM_INVARIANTS / DECISIONS / architecture-summary / BUILD.md / CONSOLE_PATTERNS / REFERENCE_EXAMPLES / DIAGRAMS touched. PM-28 amendment cycle does NOT apply.
- **Not a cohort-row refactor of PROMPT_SEQUENCE.md.** Row 159 ("11b" cohort mega-row) is unchanged.
- **Not a BUILD_LOG Evidence section.** PM-22 infrastructure PR — split-memo prep is not a build prompt.
- **Not a supersession of `docs/04-split-memo-11b-ii.md`.** That memo remains canonical for the 11b-ii cohort-level split into α + β; this memo only further splits β. The α side (`docs/10-split-memo-11b-ii-alpha.md`) is unchanged.

---

*Authored by Partner during the 2026-05-20 Phase A closeout multi-Z drive session, immediately after the Z1.5 pre-authorship gate PASS + §2.9 pre-split disposition against main `8b256840`. Lands as a single `partner/11-split-memo-11b-ii-beta` PR per PM-22.*
