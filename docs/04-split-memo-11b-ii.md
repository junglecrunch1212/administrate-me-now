# 04-split-memo-11b-ii.md — Tier C split memo for 11b-ii (Apple Reminders + Apple Calendar dual-deployment)

**Authored:** 2026-05-18 by Partner during the Phase A closeout session.
**Scope:** Pre-split of prompt **11b-ii** into **11b-ii-α** + **11b-ii-β** per PM-23. The Phase A closeout brief (`docs/SESSION-HANDOFF-2026-05-18-phase-a-closeout.md` §3) identified 11b-ii as the **Z1** next refactor target. The pre-authorship gate (Gate 1 sibling-pack read) revealed the brief-described scope exceeds the §2.9 single-cycle envelope. This memo records the split disposition.

**Status:** Disposition decision. **No code touched.** Output is this memo + a `prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation pointing here. The 11b-ii-α + 11b-ii-β v4 refactor prompts follow in separate Partner sessions (Type 3 refactor-only).

---

## §1 Why split

The brief frames 11b-ii as "Apple Reminders + Apple Calendar dual-deployment ExternalStateMirrorAdapters per [D22] — light scope; same adapter shape as 11b-iii / 11b-iv siblings; two new `family` values." On reading the canonical 11b-iii sibling prompt at `prompts/_active/11b-iii-google-calendar-central.md`, **a single fresh Cat-B pack** like Google Calendar's `GoogleCalendarAdapter` is:

- ~500 lines `adapter.py` (F-5 anti-vision ceiling)
- ~30-40 KB v4-refactored prompt
- ≥15 framework tests + ≥3 pack-internal + ≥3 integration
- ~4-4.5 hours wall / ~$80-110 spend
- §2.9 self-check: PASS at the upper edge of the empirical single-cycle envelope (350 prompt lines / ≤45 KB / 6 files-per-commit / 40 inline-code lines / one-cycle judgment)

**Bundling Apple Reminders dual-deployment + Apple Calendar fresh Cat-B + dual-variant under a single 11b-ii** is structurally heavier:

| Subpart | Pack count | Estimated adapter LOC | Estimated tests | Estimated v4 prompt | §2.9 fit |
|---|---|---|---|---|---|
| Apple Reminders dual (bridge variant of existing `packs/adapters/reminders/apple-eventkit/` pack; deployment_targets manifest extension; bridge-side credential + sharing-model wiring) | 0 new packs | ~150-200 added lines | ~6-10 new tests | ~200-260 prompt lines | comfortably ≤350 lines |
| Apple Calendar fresh Cat-B central (new `packs/adapters/calendaring/apple-calendar/`, mirrors `google-calendar` pattern; `family: calendaring` already in Literal so no Literal extension needed) | 1 new pack | ~400-500 lines | ~15-21 new tests | ~320-360 prompt lines | at the §2.9 upper edge |
| Apple Calendar dual variant (bridge-side manifest + credential variance for the new pack) | 0 new packs (same code) | ~50-80 added lines | ~3-5 new tests | ~80-100 prompt lines | trivial standalone; bundled with central is +20% over budget |
| **Combined as single 11b-ii** | **1 new pack** | **~600-780 LOC** | **~24-36 tests** | **~600-720 prompt lines** | **~2x §2.9 ceiling — anti-vision halt at authoring** |

The brief's "Light: same adapter shape as 11b-iii / 11b-iv siblings; two new `family` values" framing is correct **at the architectural level** — no new ABCs, no new abstractions, both adapters slot into the existing canonical Cat-B pattern. But "shape" and "size" are different envelopes. The shape is light; the size is not.

Per PM-23 (secondary splits permitted on discovery of envelope blowout at orientation) + the operator's standing pre-split discipline established by 10b / 10b-ii / 10c, **11b-ii is a Tier C pre-split candidate before refactor**.

The 11b-iii sibling prompt's §1 explicitly anticipates this: "11b-iii is the **first fresh Cat-B adapter** under the canonical-build pattern... 11b-iii establishes the fresh-pack template that 11b-ii Apple Calendar dual + 11b-iv layer on." That template extension is non-trivial; bundling two Cat-B builds under a single prompt undermines it.

---

## §2 Disposition

**Split into 11b-ii-α + 11b-ii-β.** Dependency-correct ordering: α before β.

### 11b-ii-α — Apple Reminders dual-deployment infrastructure + bridge variant

**Scope:** Extend the existing `packs/adapters/reminders/apple-eventkit/` pack to support **dual deployment** (central + per-member bridge) per [D22] / memo 05 §5.4 / [§6.19] bridge sovereignty. **No new pack.** Code-level changes are mostly manifest + small adapter-config differences keyed off `deployment_target`.

**Key deliverables:**

- `packs/adapters/reminders/apple-eventkit/pack.yaml` — extend with `deployment_targets: [central, bridge]` (new manifest field; the canonical Cat-B `runtime: dual` declaration per memo 05 §3.1).
- `packs/adapters/reminders/apple-eventkit/adapter.py` — minor (~50-100 lines added): the AppleEventKitAdapter reads `ctx.deployment_target` (or a config-injected variant) and branches the credential source + sharing_model_discriminator accordingly:
  - **central**: shared central Apple ID credential (existing path); `sharing_model: shared_central`.
  - **bridge**: per-member Apple ID credential injected via bridge-side `BridgeContext` per 11c-i; `sharing_model: per_member_isolated`; `owner_scope` stamped from Tailscale-bound identity per memo 04 §2.2.
- `packs/adapters/reminders/apple-eventkit/config.schema.json` — extend the JSON Schema with `deployment_target: enum["central", "bridge"]` (default `central` for backwards compatibility with existing instance configs).
- `bridge/adminme_bridge/connector_packs/reminders_apple_eventkit_bridge/` — NEW connector-pack scaffold under the bridge daemon's connector-pack loader (per 11c-i scaffolding). One small `__init__.py` declaring the bridge-side load path. The bridge daemon imports the same `AppleEventKitAdapter` class but instantiates it with `deployment_target="bridge"` + per-member credentials.
- **No new event schemas.** Both deployment targets emit the same `reminder.list_item.synced@v1` + the `list_item.*@v1` family from 11b-i. The events propagate to L2 via the bridge daemon's ingest call (per 11c-i) when deployed on bridge; via direct local event-log append when deployed central. The L2 event log doesn't care which path emitted the event.
- **No new `family` Literal value** — both deployments use `family: reminders` (existing in the Cat-B Literal). Distinguishing central-vs-bridge is the `deployment_target` config field, not the family. (NB: if executor orientation discovers that the Cat-B `family` Literal needs a new value like `"reminders-bridge"` for sharing-model-discrimination at the validator level, that's a secondary-split flag — see §4.)

**Estimated envelope:**

- v4 prompt: ~200-260 lines / ~14-18 KB.
- Adapter changes: ~120-180 lines added across `adapter.py` + `pack.yaml` + `config.schema.json`.
- Tests: ≥6 framework tests (deployment_target dispatch; bridge-variant credential injection; sharing_model emission distinct between central + bridge) + ≥3 pack-internal tests + ≥2 integration tests (bridge daemon round-trip).
- Wall time: ~2.5-3 hours.

**Dependencies:** Cohort D step 2-α / 11b-i closed (main `4ed7b731`); 11c-i bridge daemon closed (main `1f88ccec`); existing `packs/adapters/reminders/apple-eventkit/` Cat-B retrofit on main.

**Unlocks:** the bridge-side Cat-B deployment template that 11b-ii-β reuses.

### 11b-ii-β — Apple Calendar fresh Cat-B (central + dual variant)

**Scope:** NEW pack `packs/adapters/calendaring/apple-calendar/` mirroring the canonical Cat-B fresh-pack pattern established by `packs/adapters/calendaring/google-calendar/` (11b-iii). Dual-deployment from inception (no central-only intermediate landing) — the pack supports both `central` and `bridge` deployment targets from day one, reusing the 11b-ii-α dual-deployment infrastructure.

**Key deliverables:**

- `packs/adapters/calendaring/apple-calendar/` — fresh pack, Cat-B, `family: calendaring` (existing Literal value; no extension), `write_capabilities=["create_event"]` per [UT-27] asymmetric, `sharing_model_discriminator` set per deployment_target, `dedup_strategy: {kind: external_id}` keyed on EKEvent.uniqueId, `sensitivity_default: normal`, `observation_mode_required: true`, `deployment_targets: [central, bridge]`.
- `packs/adapters/calendaring/apple-calendar/adapter.py` — ≤500 lines (F-5 ceiling). New `class AppleCalendarAdapter(ExternalStateMirrorAdapter)`. Inbound: poll EKEventStore via PyObjC for events; emit `calendar.event_added@v1` (existing schema; MAP DON'T REGISTER per 11a F-6). Outbound `_apply_write_impl` for `create_event` saves EKEvent + emits via `observation_outbound(...)` per [§6.20] F-3 (HARD: NEITHER `calendar.event_added` NOR `adapter.error` emitted on suppressed path).
- `bridge/adminme_bridge/connector_packs/calendaring_apple_calendar_bridge/` — bridge-side connector pack mirroring α's pattern.
- Tests: ≥15 framework + ≥3 pack-internal + ≥3 integration. Same shape as 11b-iii's test floor.
- **NO new event schemas.** Maps onto existing `calendar.event_added@v1` byte-for-byte per the canonical pattern.

**Estimated envelope:**

- v4 prompt: ~320-360 lines / ~18-22 KB. At the §2.9 upper edge but inside.
- Adapter LOC: ~400-500 lines.
- Tests: ~21-25 total.
- Wall time: ~4-4.5 hours (mirror of 11b-iii).

**Dependencies:** 11b-ii-α closed (dual-deployment infrastructure pattern); 11b-iii closed (`google-calendar` quality-bar reference).

**Unlocks:** Apple-side Cat-B parity with Google. Together with 11b-ii-α, completes the Cohort D 11b cohort. Unblocks 11d (`member_knowledge` projection) consumption of Apple-Calendar-sourced events. Together with 11e (Contacts), closes the Apple-side Cat-B + Cat-D matrix.

---

## §3 Sequence-update implication

`prompts/PROMPT_SEQUENCE.md` row 155 ("11b") is the cohort-level mega-row covering all four Cat-B targets (Apple Reminders dual / Apple Calendar dual / Google Calendar / Google Tasks). The on-main sequence file has not been updated with per-sub-prompt rows for 11b-i / 11b-iii / 11b-iv (which all closed since the row was last touched), nor for the 4-way Cohort D split per memo 08 §3.4.

**Minimal-scope update for this memo:** add a "Cohort D 11b sub-prompt tracking" annotation to the Rebase notes section of `prompts/PROMPT_SEQUENCE.md` (near line 41-43) pointing to this memo as canonical for the 11b-ii sub-split:

```
**Cohort D 11b sub-prompt tracking (updated 2026-05-18):** The on-main row 155
"11b" remains the cohort-level scope-of-record. Sub-prompts executed since
2026-05-15: 11b-i closed (PRs #158-#163, main `4ed7b731`); 11b-iii closed
(PRs #173-#176, main `04cdee6d`); 11b-iv closed (PRs #178-#181 + pre-cycle
sidecar #170, main `f2f77eaf`). **11b-ii** is pre-split into **11b-ii-α**
(Apple Reminders dual-deployment infrastructure + bridge variant) and
**11b-ii-β** (Apple Calendar fresh Cat-B + dual variant) per
`docs/04-split-memo-11b-ii.md`. Sub-prompt rows for 11b-i / 11b-ii-α /
11b-ii-β / 11b-iii / 11b-iv are NOT being added to the table at this time;
the cohort mega-row + this tracking annotation are the canonical reference
until a future cohort-level cleanup PR refactors row 155.
```

Per PM-22, that's an infrastructure update — no four-commit discipline, no BUILD_LOG Evidence section. Lands in the same PR as this memo.

A future fuller cohort-row refactor of PROMPT_SEQUENCE.md row 155 is queued but out-of-scope for this memo. It would resolve the on-main drift around the 11b sub-prompt structure surfaced repeatedly since the 2026-05-16 prompt-11 drift discovery (the v4.3 Gate 0 trigger). Decoupling it from this split memo keeps the present PR scope tight.

---

## §4 Secondary-split watch flags

Per PM-23 lineage:

- **11b-ii-α secondary-split candidate:** if Executor orientation discovers that the Cat-B `family` Literal needs a new value (e.g., `"reminders-bridge"`) for sharing-model-discriminator + validator coherence — analogous to PR #170's pre-cycle sidecar for `family: "tasks"` — then 11b-ii-α should split into 11b-ii-α-1 (Cat-B Literal extension; pre-cycle sidecar PR) + 11b-ii-α-2 (bridge variant + deployment_targets manifest). Pre-cycle sidecar PRs are not Tier C splits in the PM-23 sense (they're sidecar-pattern PR #170 lineage), so this would just be a pre-cycle sidecar + 11b-ii-α as authored. Flag at orientation.
- **11b-ii-β secondary-split candidate:** if EKEventStore PyObjC bindings introduce a heavier-than-anticipated platform-coupling surface (compared to Google Calendar's `googleapiclient`), the inbound polling loop alone may consume 200+ lines. Watch the §2.9 self-check at v4 refactor. If shipped prompt exceeds 360 lines, secondary-split into 11b-ii-β-α (inbound poll only — read-only adapter validating the EKEventStore surface) + 11b-ii-β-β (add outbound `create_event` + dual variant).

Both flags are **defensive** — they may not fire. The default pre-authorship gate at α + β refactor time will catch envelope blowouts.

---

## §5 Operator follow-up

After this memo merges + `prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation lands:

1. **Workspace sync** — fetch live main's `prompts/PROMPT_SEQUENCE.md` to the workspace pinned copy at `C:\Users\Owner\Documents\Claude\Projects\AdministrateMe Partner\PROMPT_SEQUENCE.md`. Closes the v4.3 Gate 0 drift signal that the current Partner session detected (workspace 50,606 bytes / dated 2026-04-14 vs main ~58 KB). Sidecar-style local-file sync only; no separate PR needed for the workspace.

2. **Next session opens with 11b-ii-α refactor.** The next Partner session's orientation report identifies "Z1 = 11b-ii-α" per this split memo. The Type 3 refactor session runs the pre-authorship gate against main for 11b-ii-α's cited paths (existing `packs/adapters/reminders/apple-eventkit/`; bridge daemon at `bridge/adminme_bridge/`; the Cat-B ABC at `adminme/lib/adapters/external_state_mirror_base.py`; the canonical sibling at `prompts/_active/11b-i-apple-reminders-list-migration.md`).

3. **Z1-Z9 sequence shifts by one cycle:**
   - Z1: 11b-ii-α (Apple Reminders dual)
   - Z1.5: 11b-ii-β (Apple Calendar fresh Cat-B + dual)
   - Z2: 11c-ii (Cat-D scaffolding) — unchanged
   - Z3: 11d (member_knowledge projection) — quarter-mark, unchanged
   - Z4: 11e (Contacts) — unchanged
   - Z5: 12 (OpenClaw plugin adapters) — unchanged
   - Z6: 14a + 14b + 14c (Console) — quarter-mark, unchanged
   - Z7: 15 + 15.5 — unchanged
   - Z8: 16 (Bootstrap wizard, Tier C split) — unchanged
   - Z9: 17 + 18 + 19 — unchanged
   - **Total Phase A cycles to bootstrap-ready:** ~13-15 (was 12-14 in the closeout brief; +1 for the 11b-ii sub-split).

---

## §6 What this memo is NOT

- **Not a v4 refactor prompt.** This memo declares the split disposition only. The 11b-ii-α and 11b-ii-β v4 refactored prompts follow in separate Partner sessions.
- **Not a constitutional doc edit.** No SYSTEM_INVARIANTS / DECISIONS / architecture-summary / BUILD.md / CONSOLE_PATTERNS / REFERENCE_EXAMPLES / DIAGRAMS touched. PM-28 amendment cycle does NOT apply.
- **Not a cohort-row refactor of PROMPT_SEQUENCE.md row 155.** Only the Rebase notes section gets a tracking annotation. Row 155 remains unchanged.
- **Not a BUILD_LOG Evidence section.** PM-22 infrastructure PR — sequence/split-memo prep is not a build prompt.

---

*Authored by Partner during the 2026-05-18 Phase A closeout session, immediately after PR #198 (`partner/state-snapshot-2026-05-18-sweep`) merged to main at `19e27d50ee0ff0db50ba34b9a8c53a8f321aa29e`. Lands together with the `prompts/PROMPT_SEQUENCE.md` Rebase-notes annotation in a single `partner/04-split-memo-11b-ii` PR per PM-22.*
