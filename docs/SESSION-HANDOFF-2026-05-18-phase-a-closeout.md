# Session handoff — 2026-05-18 — Phase A closeout brief

**Purpose.** This file is the priority brief for the next Partner session. The bulky `partner_handoff.md` and `build_log.md` remain authoritative for the full history; this file is a compact delta + forward plan so the next session can orient in minutes and start driving in the right direction.

**Read order for the next session's Phase 3 (live state read):**
1. This file (compact, current).
2. `docs/build_log.md` tail (last ~5 entries) — for the most-recent merge details.
3. `docs/partner_handoff.md` head (Current build state paragraph) — for the longer-form snapshot.
4. `prompts/PROMPT_SEQUENCE.md` Rebase notes + remaining rows — only if this brief leaves a question unanswered.

If this file says "X is done" and `partner_handoff.md` says "X is the next refactor target," **this file wins** — it is more recent than the bulky handoff doc which has not been touched since pre-Cohort-D-step-1.

---

## 1. Where the build is right now

Phase A is well past the midpoint. The Cohort D canonical adapter framework is largely shipped. The L1-bridge architecture is wired. The lists projection closes the events→projection loop. The OAuth/Max-plan billing path for executor cycles is live and verified.

**Cohort D (canonical 5-base-class adapter framework) — shipped:**
- 11a — Gmail CommunicationAdapter (canonical) — cycle #18, merged to main `ebfa56ed`.
- 11b-i — Apple Reminders list-item migration ExternalStateMirrorAdapter — cycle #19, merged `4ed7b731`.
- 11b-iii — Google Calendar central ExternalStateMirrorAdapter — cycle #20, merged `04cdee6d`.
- 11b-iv — Google Tasks central ExternalStateMirrorAdapter — cycle #21, merged `f2f77eaf`. Required pre-cycle sidecar PR #170 to extend the Cat-B `family` Literal to include `"tasks"`.
- 11f — lists projection — cycle #22, merged `f0b5e9ce`. Closes the events → projection loop for list-shaped state.
- 11c-i — L1-bridge daemon + ingest (port 3337) — cycle #23, merged `1f88ccec`.
- 11g — Home Assistant Cat-C+E multi-capability adapter — cycle #24, merged `eac5fdfa`. Had a c4 merge conflict resolved via web UI (Cat-E-first ordering preserved per validator's `base_class_conformance` check).

**Earlier canonical work already on main (discovered during this session's rebaseline — pinned `partner_handoff.md` was behind on these):**
- 10c-iii canonical reminder/CRM/custody pipelines at `packs/pipelines/{reminder_dispatch,crm_surface,custody_brief}/`.
- 13a + 13b product surfaces at `adminme/products/{core,comms,capture,automation,bridge}/`.

**Sidecars shipped this session:**
- PR #170 — Cat-B `family` Literal extends to `"tasks"`.
- PR #195 — `smoke-test.yml` OAuth diagnostic + isolation step. Introduced an invalid `if: ${{ secrets.* }}` expression.
- PR #196 — fix for #195's invalid `if:`. Smoke test now runs cleanly on main.

**Billing path:**
- `CLAUDE_CODE_OAUTH_TOKEN` is configured in repo secrets (added 2026-05-18). Token rotates ~1 year out.
- Verified live via smoke test run #6 (commit `bc5cc18`): "OAuth isolation test PASSED. Max-plan subscription billing path is functional." Haiku call succeeded with `ANTHROPIC_API_KEY` intentionally unset.
- All future `executor-cycle.yml` runs use OAuth, not API dollars. Cost per cycle drops from ~$60–120 → ~10–25% of weekly Max quota.
- `ANTHROPIC_API_KEY` remains in secrets as belt-and-suspenders fallback. Optional follow-up: remove it after a few clean OAuth-billed cycles.

---

## 2. What's pending from prior sessions

- **Task #9** (carried since post-11a) — Update `docs/partner_handoff.md` Current-build-state paragraph + patch PR numbers in `docs/build_log.md` entries that have placeholder `#XX`. This was deferred each session because each subsequent merge made the snapshot stale again. Now that Phase A has a closeout brief (this file), the next session can do a single sweep instead of a per-merge update.
- **`docs/build_log.md` entries** for PRs #195 and #196 (this session's sidecars) — not yet recorded. Should be added in the same sweep as Task #9.

---

## 3. Optimal remaining Phase A sequence (next session's job)

Order is engineered for: (a) minimize bug-risk via dependency-correct sequencing, (b) avoid blocking each-other, (c) defer the heaviest split (16 — bootstrap wizard) to the end where its Tier C split is safer.

Sequence Z1 → Z7 below. Each row: prompt ID, what it adds, why it goes here.

**Z1. 11b-ii — Apple Reminders + Apple Calendar dual-deployment ExternalStateMirrorAdapters.** Completes the Apple side of Cat-B parity with Google. Light: same adapter shape as 11b-iii / 11b-iv, just two new family values. No new abstractions.

**Z2. 11c-ii — Cat-D PersonalKnowledgeAdapter scaffolding + first concrete instance.** Introduces the fifth and final Cat (Cat-D). Required before 11d (member_knowledge projection) because the projection consumes Cat-D events. Estimated 1 cycle.

**Z3. 11d — member_knowledge projection.** Mirrors 11f's lists-projection pattern but for Cat-D events. Closes the second events→projection loop.

**Z4. 11e — Contacts adapter (Cat-B family `"contacts"`).** Last Cat-B family. Same shape as 11b-* siblings.

**Z5. 12 — OpenClaw plugin adapters.** Brings plugin surface online. Depends on all Cohort D adapters being canonical (Z1-Z4 done).

**Z6. 14a + 14b + 14c — Console framework + member views + admin views.** Once all adapters + projections + plugins are in, the Console can read from projections and display. 14d is the post-Console checkpoint.

**Z7. 15 + 15.5 — OpenClaw integration test + final Phase A checkpoint.** Integration test exercises plugin + adapter + projection + Console end-to-end.

**Z8. 16 — Bootstrap wizard (Tier C split candidate per pre-split disposition).** Final Phase A prompt. Heaviest. Likely splits into 16a (wizard scaffold) + 16b (first-run flow) + 16c (DB seeding). Open `docs/16-split-memo-bootstrap-wizard.md` before authoring.

**Z9. 17 + 18 + 19 — CLI, integration test, Phase B smoke test.** Final polish + Phase A→Phase B handoff prep.

Estimated cycles to bootstrap-ready (assuming average 1 cycle per prompt + 1 cycle per split chunk on 16): **~12-14 cycles**. With OAuth billing live and the autonomous loop solid, this is ~2-3 weekly quotas, not API dollars.

---

## 4. Vision-drift check for Phase A closeout (what to watch for)

Per `DRIFT-CONTROL-CHECKLIST.md`, the next session should explicitly walk the six questions before authoring any Z1-Z9 prompt. The two questions most likely to surface in this stretch:

- **Job 4 (does this serve the family-of-four user?)** — 16 (bootstrap wizard) is where user experience matters most. If a Z1-Z7 prompt requires a UI choice, defer to the vision doc [VISION §X] over technical convenience.
- **Job 6 (observation mode gates external side effects?)** — 11b-ii's Apple Reminders dual-deployment writes to the user's real device. Make sure observation mode is gated per [§6.20].

---

## 5. Specific instruction for the next session

After Phase 5 orientation report + operator confirmation, the next session should:

**First**, do the partner-state-snapshot sweep:
1. Read this file (already loaded if you're following the read order).
2. Update `docs/partner_handoff.md` Current-build-state paragraph to reflect: 7 Cohort D canonical cycles + 10c-iii canonical correction + 13a/13b discovered on main + 3 sidecars (#170, #195, #196) + OAuth path live + ~12-14 cycles remaining to bootstrap-ready.
3. Patch `docs/build_log.md` placeholder PR numbers AND add entries for #195, #196.
4. Open `partner/state-snapshot-2026-05-18-sweep` PR.

**Second**, drive Z1 (11b-ii). Then Z2. Etc. Quarter-mark check-ins after Z3 and Z6.

**No new architecture amendments are open.** No halts to resolve. The framework is solid; the remaining work is filling in the matrix.

---

## 6. Quick reference — recent live-state SHAs

- main HEAD as of 2026-05-18 19:05 UTC: `bc5cc180cc780726bd015975f24514be508493fa` (PR #196 merge).
- Last Cohort D cycle merge: `eac5fdfa3dbd61b860e399be8c3cacafe121ee4c` (11g c4, PR #194).
- Workflow file referenced: `.github/workflows/executor-cycle.yml` (plumbs both auth env vars; OAuth wins when both present).
- Verified-good smoke test: run #6, commit `bc5cc18`.

---

*Authored by Partner during the 2026-05-18 session. Supersedes nothing in the pinned constitutional docs; supplements the bulky `partner_handoff.md` for the duration of Phase A closeout.*
