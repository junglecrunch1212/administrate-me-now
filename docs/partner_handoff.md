# Partner handoff — AdministrateMe Phase A build

_Every new Claude Chat instance that acts as Partner (Quality Control + Prompt Refactoring for Claude Code's Phase A sessions) reads this document first, before anything else._

**Partner's job, in one sentence:** After each Claude Code merge, run the three-job QC pass against the merged work; then refactor the next unrefactored prompt (08 through 19, or a checkpoint) before handing it to Claude Code.

**What Partner is not:** Partner does not execute prompts. Partner does not write code. Partner does not ship to `main`. Partner produces artifacts (refactored prompts; QC findings; updated handoff) that James hands to Claude Code.

---

## Session 1: the mandatory orientation sequence

Do this in order at the top of every new Chat session. Do not skip. Do not reorder. Do not do "real work" (refactor prompts, produce artifacts, write code) until every step is complete and James has confirmed your orientation is correct.

### Step 1 — Read the 9 constitutional docs in full

These define the codebase's architecture. They are binding on every prompt. They are stable (change by deliberate decision, not in passing).

James has attached them to this Chat session as documents. Read each one in full:

1. **ADMINISTRATEME_BUILD.md** — the canonical build specification. Cite as `[BUILD.md §X]`. **Now includes top-level §MEMBER BRIDGES section + Capture demotion + new Product E (`:3337 bridge`) per the Conception-C amendment of 2026-04-29.**
2. **ADMINISTRATEME_CONSOLE_PATTERNS.md** — 12 console patterns. Cite as `[CONSOLE_PATTERNS.md §N]`.
3. **ADMINISTRATEME_DIAGRAMS.md** — 10 architecture diagrams. Cite as `[DIAGRAMS.md §N]`. **§1 L1 ASCII extends with bridge-side adapters; §2 has a second canonical example (Apple Note → confirmed commitment); §7 topology adds bridge Mac Mini shelf row — all per the Conception-C amendment.**
4. **ADMINISTRATEME_REFERENCE_EXAMPLES.md** — 7 worked examples. Cite as `[REFERENCE_EXAMPLES.md §N]`.
5. **ADMINISTRATEME_CONSOLE_REFERENCE.html** — interactive design reference. Skim for structure; read the specific sections the prompt you're working on touches.
6. **docs/SYSTEM_INVARIANTS.md** — 15 sections of binding invariants + §6.19 (bridge sovereignty) + §8.9 (L1 two-place adapter family) post-amendment. Cite as `[§N]`.
7. **docs/DECISIONS.md** — D1 through D17 decisions. Cite as `[DN]`. **D17 (personal knowledge ingestion is L1-bridge-shaped) added 2026-04-29.**
8. **docs/architecture-summary.md** — five-layer model + the **12 projections** table (post-amendment: `member_knowledge` is the 12th). Cite as `[arch §N]`.
9. **docs/openclaw-cheatsheet.md** — 8 Q&As. Cite as `[cheatsheet Qn]`.

Yes — in full. These are Partner's contract. Partner's opinions about "what the codebase should do" are worthless without them.

### Step 2 — Read the three session docs

Still in this session context (small files):

- **partner_handoff.md** — this file. You just read it.
- **qc_rubric.md** — the three-job QC pass you'll run after merges.
- **build_log.md** — Claude Code's record of what shipped per prompt. This tells you what has merged, what's in-flight, and what deviations from prompt-intent occurred.

### Step 3 — Read PROMPT_SEQUENCE.md

James will attach `prompts/PROMPT_SEQUENCE.md`. This is the **single canonical copy** — the root-level duplicate was removed when the `sidecar-prompt-sequence-version-drift` sidecar merged (see PM-1). It gives you:

- The full sequence (prompts 00 through 19).
- The dependency graph.
- The current universal preamble (slim, post-PM-7).
- The per-prompt structure template.

### Step 4 — Identify current state and this session's task

Based on `build_log.md` + `PROMPT_SEQUENCE.md`, identify:

- **Last fully merged prompt** (not IN FLIGHT — merged).
- **In-flight PRs** (if any).
- **Next prompt to write** (or checkpoint to refactor, or QC pass to run).
- **What this session specifically needs to do**, which James tells you explicitly.

### Step 5 — Identify what code context you'll need from the zip

James has attached the **most recent full codebase as a zip**. You have NOT loaded any of it yet. You load specific files from it based on what this session's task needs.

**The principle:** load the minimum. Partner sessions that try to ingest the whole codebase run out of headroom before producing the refactor. Partner sessions that ingest too little produce prompts with broken references.

**Loading rule of thumb by task type:**

| Task type | Load from zip (minimum) |
|---|---|
| Refactor a new build prompt (08, 09a, etc.) | (a) The draft prompt file from `prompts/<NN>-*.md`. (b) The most recently merged prompt file (same directory), as quality-bar reference. (c) Source files the new prompt's "Read first" section references. (d) `pyproject.toml`. |
| Refactor a checkpoint (07.5, 10d, 14e, 15.5) | (a) The checkpoint file. (b) Directory listings of areas the checkpoint audits (e.g. 07.5 audits all 11 projections — load `adminme/projections/` listing + each projection's `schema.sql` + `queries.py`). (c) Related tests. |
| QC pass on a merged PR | (a) The `build_log.md` entry. (b) The prompt file that specified what was to ship. (c) Spot-check files from the diff if Evidence lists seem off. Do NOT load the entire diff. |
| Universal preamble extension / sequence refactor | (a) `prompts/PROMPT_SEQUENCE.md`. (b) `pyproject.toml`. (c) Any scripts or canonical files the proposal mentions. |
| Structural refactor spanning multiple prompts | (a) All affected prompt files. (b) Shared references only. Decompose the task if it's bigger than this. |

**How to load from the zip:** the zip is named `administrate-me-now-main__<N>_.zip`. James attaches the latest. You unzip in your sandbox, then read only the specific paths needed. If you need the directory layout first, ask — or run `ls` on the expected subdirectory and see what's actually there before assuming a file exists.

### Step 6 — Produce orientation report; wait for James to confirm

Produce a short report:

- 13-file Project knowledge inventory (confirmed via `project_knowledge_search`, NOT via `/mnt/project/` filesystem listing — see PM-13).
- Mode: `normal` / `degraded` / `blocked`.
- Current state paraphrase from this file.
- This session's plan (Type 1/2/3/0; which Job 1 / Job 2 / Job 3 work).
- Pre-split disposition forecast for the target prompt if the index pre-flags it as a split candidate.
- Intended depth-reads.
- Open questions for James.

For anything beyond this summary, read the actual constitutional docs (step 1 above). Do not rely on this summary for architectural decisions.

---

## Current build state

**Last updated:** 2026-05-05 (10c-ii merged as PR #55 — proactive pipelines `morning_digest` + `paralysis_detection` + skill pack `compose_morning_digest@v3` + two new event schemas at v1 (`digest.composed`, `paralysis.triggered`) + rewrites of `bootstrap/openclaw/programs/morning_digest.md` + `paralysis_detection.md` from STUB → full per [D1] Corollary. Partner session of 2026-05-05 ran Type 2 QC on the merged 10c-ii. **Findings:** all clean / positive. **(1) Contract check Match-with-overshoot** — every Deliverable shipped at the specified shape. Test floor: ≥30 new tests (whole prompt) + ≥3 fabrication-shape tests + ≥1 sentinel-emission test (validation guard). Shipped: **42 new tests on the `tests/` testpath** (10 schema-registration + 2 compose_morning_digest skill-load + 12 morning_digest unit + 2 morning_digest pack-load + 11 paralysis_detection unit + 2 paralysis_detection pack-load + 3 integration) + **7 pack-internal tests** (2 morning_digest + 2 paralysis_detection + 3 compose_morning_digest); **+40% overshoot vs floor.** Validation-guard floor met: 3 fabrication tests (calendar/commitment/task) + 1 sentinel-emission test on skill-failure path + 1 sentinel-emission test on no-op-loaders path. Suite tally on `tests/` testpath: **508 → 550 passed, 2 skipped**. Out-of-scope guards held: no real on-disk profile/persona/projection-loader modules (UT-14 stays open); no `compose_zeigarnik_teaser` skill; no `paralysis_detection` outbound; no per-member cron substitution; no changes to `adminme/pipelines/runner.py` or `adminme/pipelines/base.py`. **(2) Invariant audit Clean** — `verify_invariants.sh` exit 0 against current main; `[§6.14]` (single `outbound()` enforcement point: `morning_digest`'s `deliver()` is the single call site; `paralysis_detection` has zero call sites and integration test asserts this via stub `outbound_fn` that raises `AssertionError`); `[§7.3]` (no projection direct writes — both pipelines emit only via `ctx.event_log.append`); `[§7.4]` / `[§8]` / `[D6]` (zero LLM/embedding SDK imports; `paralysis_detection` never calls `run_skill_fn`); `[§7.7]` (defensive default — `morning_digest` catches the F-2-widened 7-class tuple and emits sentinel `digest.composed`; `paralysis_detection` defensively skips on every pre-condition miss); `[§12.4]` (zero tenant identity in any new file; fixtures use `member-a` / `member-b` / `member-c` / `member-d`); `[§15]` / `[D15]` (zero hardcoded `~/.adminme` literals; integration tests resolve via `load_instance_config(tmp_path / 'instance')`); `[D7]` (`digest.composed` and `paralysis.triggered` both register at v1 with bare-name shape per PM-26 supersession discipline established by 10c-i); causation/correlation discipline asserted via `causation_id=ctx.triggering_event_id`. **(3) Next-prompt calibration on 10c-iii Clean** — 10c-iii is not yet drafted; every BUILD.md anchor and on-disk artifact 10c-iii will need is on main: `bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md` exist as stubs with `TODO(prompt-10c-iii)` markers (verified); `bootstrap/openclaw/cron.yaml` placeholder entries for the three exist; `packs/pipelines/morning_digest/` and `packs/pipelines/paralysis_detection/` are now available as quality-bar references; constructor-injection pattern (PM-27) carries forward across three consumers. **F-1 (SOFT/accepted): `triggers.proactive: true` shape in pipeline.yaml manifests + structural load tests asserting it.** The 10c-i F-1 carry-forward warned against this, but the 10c-ii prompt explicitly authored the affirmative form as documentation-only, AND the test files' docstrings recognize the carry-forward and route the runner-skip-behavior assertion through a separate `test_runner_discover_skips_*` test using `pack_id not in runner.registered_pack_ids()`. The structural assertion is a manifest-shape canary, not a runner-contract canary; that's defensible. Same shape is fine for 10c-iii. **F-2 (SOFT/accepted): `check_preconditions()` and `select_template()` are sync, not `async def`.** Spec said `async def`; what shipped is sync. Methods are called from inside async `dispatch()` which is fine; the loaders they invoke are synchronous callables anyway. If real async loaders ever ship (UT-14), these methods will need to become async. Carry-forward note for UT-14 resolution. **F-3 (HARD/carry-forward): pack-internal `test_pack_load.py` rename pattern.** Both pack-internal pack-load tests were renamed to `test_<pack>_pack_load_pack_internal.py` to avoid pytest's rootdir-relative-module-name collision when multiple packs ship pack-internal tests with the same basename. **HARD carry-forward to 10c-iii: every new pack-internal pack-load test must use a unique basename.** **No new PM or UT surfaced.** UT-14 stays open (anchor: future loader prompt; no specific prompt assigned). **Next refactor target:** **10c-iii** (proactive pipelines `reminder_dispatch` + `crm_surface` + `custody_brief` + 3 compose_* skill packs + 3 event schemas at v1; rewrites remaining stubs).)

**Last updated:** 2026-05-04 (Amendment-2 cycle complete. Memo PR (`arch-amendment-2-memo-personal-data-layer`) landed `docs/05-architecture-amendment-personal-data-layer.md` per Sessions A-1 + A-2. PR-α-2 split into two PRs in execution per PM-24: PR #51 landed constitutional doc updates across `docs/architecture-summary.md` + `docs/SYSTEM_INVARIANTS.md` + `docs/DECISIONS.md` + `ADMINISTRATEME_DIAGRAMS.md` + `docs/openclaw-cheatsheet.md` (merged 2026-05-04); the `ADMINISTRATEME_BUILD.md` companion PR landed immediately after via GitHub web UI per PM-24 (merged 2026-05-04). PR-β-2 (`arch-amendment-2-sequence-updates`, PR #53, merged 2026-05-05) landed `prompts/PROMPT_SEQUENCE.md` row breakdown for the 11-cohort (rows 11/11a/11b/11c-i/11c-ii landed; new rows 11e/11f/11g added; rows 13b/14b/16/17 modified; dependency graph + parallelizable + hard-sequential bullets updated; total Phase A estimate 93–123 hrs → ~105–141 hrs). PR-γ-2 (this snapshot, `arch-amendment-2-handoff-snapshot`) closes the cycle: PM-30 through PM-35 landed in standing PM section; UT-19 through UT-30 landed in standing UT section (UT-19/20/21/26/27 OPEN with resolution anchors re-pointed from closed Sessions A-1/A-2 to the corresponding prompt-refactor sessions; UT-22/23/24 RESOLVED 2026-04-29-B by their D-decisions; UT-25 DEFERRED; UT-28/29/30 OPEN, resolves at the corresponding prompt's refactor); D18 through D25 already in `docs/DECISIONS.md` per PR-α-2. Eight new D-decisions confirmed and landed: **D18** (lists first-class as 13th projection; list items distinct from tasks; Notes-checklists toggle+add only); **D19** (five-category adapter taxonomy by epistemic role: Communication / External-State-Mirror / Inbound-Data / Personal-Knowledge / Outbound-Action; runtime orthogonal; capabilities-as-list); **D20** (three-layer developer mode: bundled / verified third-party / user-authored); **D21** (Twilio is Cat-E, inbound SMS deferred to v2); **D22** (Apple Calendar dual-deployment Cat-B); **D23** (Apple Contacts bridge per-member + Google Contacts central, both feeding `parties.identifiers`); **D24** (Home Assistant Cat-C+E reference, full bidirectional with observation-mode integration on the Cat-E half); **D25** (Lob/Privacy.com/CalDAV/Drive/iOS Shortcuts deferred or removed). Six new PMs (PM-30 through PM-35) and twelve new UTs (UT-19 through UT-30) landed via this PR. Phase A scope: 93–123 hrs → ~105–141 hrs. **Next refactor target: 10c-ii** (proactive pipelines `morning_digest` + `paralysis_detection`).)

**Last updated:** 2026-04-29 (PR-α `arch-amendment-doc-updates-pr-alpha` merged — 5 constitutional doc amendments per `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md` §4. **Two-commit landing per PM-24 hybrid pattern (first execution; ran cleanly):** Commit 1 (Claude Code, str_replace) — 4 files (`docs/architecture-summary.md`, `docs/SYSTEM_INVARIANTS.md`, `docs/DECISIONS.md`, `ADMINISTRATEME_DIAGRAMS.md`); Commit 2 (GitHub web UI per PM-24) — `ADMINISTRATEME_BUILD.md` 7 edits (B.1 §THE ARCHITECTURE L1 box, B.2 §MACHINE TOPOLOGY adds adminme-bridge-{member}, B.3 §L1 "Three adapter runtimes", B.4 §L5 Capture full rewrite, B.5 new §L5 Product E `:3337 bridge`, B.6 new top-level §MEMBER BRIDGES section, B.7 §BOOTSTRAP WIZARD §10). PR-α landed 13 modifications total: D17 added; §6.19 bridge sovereignty added; §8.9 L1 two-place adapter family added; `member_knowledge` as 12th projection (architecture-summary §1 sentence + §4 row 3.12); bridge product `:3337` as fifth Python product; §10 Bridge enrollment in bootstrap wizard; arch-summary §11 #4 closed per D17. Partner session of 2026-04-29 ran Type 2 QC on PR-α. **Findings:** all 13 spec'd modifications present on main with correct content. **Positive signals:** PR description proactively flagged 3 placement-renumber decisions (memo's "§13.18" → §6.19; memo's "§3 Adapters" → §8.9; memo's `[§13.12]` → `[§6.12]`) — all correct against actual main HEAD numbering, high-quality refactor discipline. Memo §9 widening (kid bridges run Apple Notes + Voice Notes only; no Obsidian) integrated three-place-consistently into BUILD.md §MEMBER BRIDGES + DECISIONS.md D17 + SYSTEM_INVARIANTS.md §6.19. **F-1 (SOFT/cosmetic):** DIAGRAMS.md §2 second canonical example missing surrounding ` ```text ` code fence — renders as plain text instead of preformatted ASCII art. **F-2 (SOFT/cosmetic):** DIAGRAMS.md §7 topology bridge-shelf row → MAC MINI PROCESSES box has visual ASCII-art disjoint (the original single-MAC-MINI box's tee at the bottom hangs off thin air after the shelf-row replacement). Both findings are rendering-only, no content drift, no code is wrong, no downstream prompt is blocked. **Disposition:** single sidecar PR `sidecar-diagrams-rendering-cleanup` queued (option 1 of three considered — bundled fix, single str_replace operation per finding, ~5-min scope). **(2) Invariant audit Clean** — narrow scope on a doc-only PR; D17 affirms D4 (Corollary 1); §6.19 strengthens [§6.12] identity-first privacy; §8.9 does NOT introduce a new OpenClaw seam (the four canonical seams remain skills/slash/standing-orders/channels — the bridge ingest endpoint is L1-to-L2, not an OpenClaw integration). All future-prompt obligations (event schemas at v1, subscription-list extensions, projection implementation, bridge daemon code under `bridge/`) properly scoped to prompt 11c per the memo. **(3) Next-prompt calibration on 10c-ii Clean** — D17 does not change 10c-ii's deliverables; 10c-ii's `morning_digest` and `paralysis_detection` read existing projections (`tasks`, `commitments`, `parties`); they don't subscribe to knowledge events. No coupling. The amendment cycle does NOT delay 10c-ii. **New PMs and UTs:** **PM-28 added (HARD)** — when constitutional documents drift from binding architectural intent, Partner pauses the build, flags the drift, and runs an architecture-amendment cycle (Tier C memo + 3 single-purpose PRs) before resuming. The Conception-C amendment of 2026-04-29 is the canonical example. **PM-29 added (SOFT)** — knowledge-source adapters live on member bridges, not on the central CoS Mac Mini; future prompts that add knowledge-source adapters land in `bridge/`, not `adminme/`. **UT-15 OPEN (NEW)** — bridge daemon and central system share event-schema models via editable install or vendored copy; decide at 11c orientation. **UT-16 OPEN (NEW)** — kid-event routing-restriction enforcement mechanism (`owner_scope_excludes` in pipeline manifests vs `kid_bridge: true` payload flag); decide at 11c orientation OR earlier if a downstream prompt needs to know. **UT-17 OPEN (NEW)** — `member_knowledge` as a new (12th) projection vs extending `artifacts`; recommendation per memo §3.3 is the new projection, already encoded in arch-summary §4 row 3.12 and DIAGRAMS.md §1 ASCII; final decision at the projection-prompt orientation. **UT-18 OPEN (NEW)** — Apple Notes read mechanism (SQLite direct vs AppleScript vs hybrid); recommendation per memo §1.3 is hybrid (SQLite for bulk, AppleScript fallback); final decision at 11c orientation. **Next:** PR-β (sequence updates per memo §5.2 — new prompt 11c, modifications to 13b/14b/16) is the next Partner Type 0 session; PR-γ (this partner-state snapshot) closes the cycle. 10c-ii orientation comes after the amendment cycle closes per memo §6 step 10.)

**Last updated:** 2026-04-29 (10c-i merged as PR #44 — standing-orders infrastructure (six `bootstrap/openclaw/programs/<id>.md` files: one full `reward_dispatch`, five stubs morning_digest / paralysis_detection / reminder_dispatch / crm_surface / custody_brief) + `bootstrap/openclaw/cron.yaml` (five scheduled-program entries; `reward_dispatch` correctly absent because reactive in-runner) + `bootstrap/openclaw/README.md` (consumption contract for bootstrap §8) + `RewardReadyV1` event schema at v1 in `adminme/events/schemas/domain.py` per [D7] + reactive pipeline pack `packs/pipelines/reward_dispatch/` subscribing to `task.completed` and `commitment.completed`. Partner session of 2026-04-29 ran Type 2 QC on the merged 10c-i. **Findings:** all clean / positive. **(1) Contract check Match-with-overshoot** — every Deliverable from the 10c-i prompt shipped at the specified shape. F-1 cosmetic: BUILD_LOG entry says "28 new tests" but `def test_` count totals 30 across the six listed files (8 bootstrap + 3 events + 13 unit packs + 2 pack-internal canary + 4 integration). Floor was 24; shipped 30 → +25% positive overshoot. Suite tally: **480 → 508 passed, 2 skipped** (the +2 pack-internal tests live under `packs/` per the per-commit verification commands). Out-of-scope guards held: no `compose_zeigarnik_teaser` skill (correctly deferred to 10c-ii); no `adminme.reward.dispatched` event registered (the §1210 BUILD.md typo is documented as superseded in handler.py docstring + reward_dispatch.md "What NOT to do"); no `outbound()` call in handler.py; no bootstrap §8 implementation; no console SSE consumer. **(2) Invariant audit Clean** — `verify_invariants.sh` exit 0; `[§7.3]` (no projection direct writes — pipeline emits via `ctx.event_log.append` only); `[§7.4]` / `[§8]` / `[D6]` (zero LLM/embedding SDK imports; zero `run_skill_fn` call — reward dispatch is deterministic); `[§7.7]` (defensive defaults applied for missing profile, missing persona, missing tier template, missing member_id; handler does not raise); `[§15]` / `[D15]` (zero hardcoded `~/.adminme` literals; integration test resolves runtime path via `tmp_path / "config" / "runtime.yaml"`); `[§12.4]` (zero tenant identity in platform code or in 10c-i tests; fixtures use `member-a` / `member-b`); `[D7]` (`reward.ready` registered at v1, not v2-skip-v1); `[§2.2]` (the new emit `reward.ready` is from a *pipeline*, not a projection — pipelines are not subject to the projection-emit allowlist per PM-14; ALLOWED_EMITS not extended, correct); causation/correlation discipline asserted by both unit tests and integration round-trip; `adminme.reward.dispatched` is mentioned only in citation context in handler docstrings + reward_dispatch.md "What NOT to do" — NOT registered. **(3) Next-prompt calibration on 10c-ii Clean with one minor refresh** — every Carry-forward from 10c-i is consistent with current main: `bootstrap/openclaw/programs/morning_digest.md` and `paralysis_detection.md` exist as stubs with `TODO(prompt-10c-ii)` markers; `cron.yaml` `morning_digest` (`0 7 * * *`) and `paralysis_detection` (`0 15,17 * * *`) entries already present as placeholders; runner does not need modification (`discover()` lines 131-138 already skip non-reactive packs). **F-1 (minor refresh):** the 10c-i carry-forward says proactive packs declare `triggers.proactive: true`. The runner skip is *actually* keyed on the **absence** of `triggers.events`, not the presence of `triggers.proactive: true` (per `runner.py:131-138`). Either shape achieves the same skip result, but 10c-ii must not assert the affirmative-flag form in tests. Logged for 10c-ii's depth-read. **New PMs and UTs:** **PM-26 added (SOFT)** — Pipeline emit names that bypass a stale BUILD.md draft must cite the supersession explicitly; 10c-i is the canonical example (`reward.ready` from §1620 / CONSOLE_PATTERNS.md §8 wins over `adminme.reward.dispatched` from §1210 typo). **PM-27 added (SOFT)** — Pipeline classes that need future on-disk loaders accept callable injectors with no-op defaults; `RewardDispatchPipeline.__init__(profile_loader, persona_loader)` is the canonical example. **UT-13 CLOSED** — 10c orientation produced split memo `docs/03-split-memo-10c.md` (10c-i / 10c-ii / 10c-iii) and 10c-i has now merged. **UT-2 CLOSED** — AGENTS.md concatenation path resolved by 10c-i's bootstrap/openclaw/programs/ + cron.yaml + README.md artifacts; bootstrap §8 (prompt 16) is the consumer; contract documented in `bootstrap/openclaw/README.md`. **UT-14 OPEN (NEW)** — profile / persona on-disk loader modules are not yet on main; three pipelines now (10c-i's `reward_dispatch`, 10c-ii's planned `morning_digest` + `paralysis_detection`) need them; constructor-injection pattern (PM-27) lets pipelines ship before the loader infrastructure exists. **Next refactor target:** **10c-ii** (proactive pipelines `morning_digest` + `paralysis_detection` + 2 compose_* skill packs + 2 event schemas at v1; rewrites stub program files morning_digest.md + paralysis_detection.md from stub → full)).

**Last updated:** 2026-04-28 (10c orientation Partner session produced split memo `docs/03-split-memo-10c.md` per PM-23 — 10c is a pre-split candidate per UT-13. Type 0 session at James's direction. **No code touched** — output is the Tier C split memo plus a Claude Code micro-prompt for the sequence-update PR. **Verdict:** 10c splits into **10c-i** (standing-orders infrastructure: `bootstrap/openclaw/programs/<six>.md` + `bootstrap/openclaw/cron.yaml` + `bootstrap/openclaw/README.md` + `reward_dispatch` reactive pipeline pack + `RewardReadyV1` event schema at v1), **10c-ii** (proactive pipelines `morning_digest` + `paralysis_detection` + 2 compose_* skill packs + 2 event schemas at v1; rewrites stubs from 10c-i), and **10c-iii** (proactive pipelines `reminder_dispatch` + `crm_surface` + `custody_brief` + 3 compose_* skill packs + 3 event schemas at v1; rewrites remaining stubs from 10c-i). UT-2 resolution lives at the artifact level: `bootstrap/openclaw/programs/<id>.md` + `bootstrap/openclaw/cron.yaml` are the AGENTS.md-prose + `openclaw cron add` shape per [D1] Corollary; bootstrap §8 (prompt 16) consumes them.)

**Last updated:** 2026-04-28 (10b-ii-β merged — reactive pipeline `thank_you_detection` + skill pack `extract_thank_you_fields@1.0.0`. Partner session of 2026-04-28 ran Type 2 QC on the merged 10b-ii-β. **Findings:** all clean / positive. (1) Contract check **Match-with-overshoot** — F-1 cosmetic: `packs/pipelines/thank_you_detection/handler.py:102` comment reads "five skill-error types plus the F-2 widening pair" while the tuple correctly contains all 7 types (5+2=7); no code defect; tuple itself is identical to `commitment_extraction`'s. F-2: shipped **21 new tests** (4 skill-pack + 1 pack-load canary + 13 handler-direct unit + 3 round-trip integration) vs. floor 15 — strong overshoot, mirrors 10b-ii-α's pattern, positive quality signal. Suite tally on `tests/` testpath: **464 → 480 passed, 2 skipped**. (2) Invariant audit **Clean**. (3) Job 2 (refactor 10b-ii-β) **Complete**. (4) Job 3 delivery-gate self-check **Pass**. **PM-26 added (SOFT/proposed)**, **PM-21 graduates from SOFT-watch to HARD convention**. UT-12 CLOSED.)

**Last updated:** 2026-04-27 (Partner session produced secondary-split memo for 10b-ii at `docs/02-split-memo-10b-ii.md`). Selected option (c)+(a). **Sizing rationale**: 10b-ii-α at ~450–500 lines / ~25–30 tests; 10b-ii-β at ~250–300 lines / ~12–15 tests. **PM-23 added** (secondary splits) and **UT-13 added** (10c is next pre-split candidate).

**Last updated:** 2026-04-26 (10b-i merged as PR #38 — reactive pipelines `identity_resolution` + `noise_filtering` + skill pack `classify_message_nature@2.0.0` + two new event schemas at v1 (`identity.merge_suggested`, `messaging.classified`). **PM-21 update**: refactored prompt committed to repo (first deviation from paste-only convention). UT-11 CLOSED.

**Last updated:** 2026-04-26 (sequence-update PR #37 `sequence-update-10b-split` merged). PM-22 added (sequence updates and split-memo prep PRs are infrastructure, not build prompts).

**Last updated:** 2026-04-26 (sidecar PR #35 `sidecar-raw-data-is-manual-derived` merged — closes 07.5 finding C-1. UT-1 confirmed CLOSED.

This section is the live baton between sessions. Update it at the end of every Partner session.

**Prompts merged to main:** 00, 00.5, 01 (01a/01b/01c), 02, 03, 03.5, 04, 05, 06, 07a, 07b, **PM-7 infrastructure PR (slim preamble + scripts/verify_invariants.sh)**, **07c-α (PR #20, merged 2026-04-24)**, **07c-β (PR #21, merged 2026-04-25)**, **08a (PR #&lt;PR-08a&gt;, merged 2026-04-25)**, **08b (PR #&lt;PR-08b&gt;, merged 2026-04-25)**, **09a (PR #29, merged 2026-04-26)**, **09b (PR #&lt;PR-09b&gt;, merged &lt;merge-date-09b&gt;)**, **10a (PR #33, merged 2026-04-26)**, **10b-i (PR #38, merged 2026-04-26)**, **10b-ii-α (PR #41, merged 2026-04-28)**, **10b-ii-β (PR #&lt;PR-10b-ii-beta&gt;, merged 2026-04-28)**, **10c-i (PR #44, merged 2026-04-29 — standing-orders infrastructure: six `bootstrap/openclaw/programs/<id>.md` files (1 full `reward_dispatch` + 5 stubs `morning_digest`/`paralysis_detection`/`reminder_dispatch`/`crm_surface`/`custody_brief`); `bootstrap/openclaw/cron.yaml` (five scheduled-program entries; `reward_dispatch` correctly absent because reactive in-runner); `bootstrap/openclaw/README.md` documenting the bootstrap §8 consumption contract per [D1] Corollary; `RewardReadyV1` event schema at v1 in `adminme/events/schemas/domain.py` per [D7] (canonical name per [BUILD.md §1620, CONSOLE_PATTERNS.md §8] — supersedes the §1210 `adminme.reward.dispatched` typo per PM-26); reactive pipeline pack `packs/pipelines/reward_dispatch/` subscribing to `task.completed` + `commitment.completed`, deterministic-tier sampling seeded from source `event_id`, persona-template lookup with done-tier fallback, no `outbound()` call (console SSE layer consumes per [CONSOLE_PATTERNS.md §8]); `RewardDispatchPipeline.__init__` accepts `profile_loader` and `persona_loader` callables defaulting to no-ops per PM-27; 30 new tests (8 bootstrap + 3 events + 13 unit packs + 2 pack-internal canary + 4 integration); suite 480 → 508 passed, 2 skipped; `verify_invariants.sh` exit 0; ruff + mypy clean; refactored prompt committed to repo at `prompts/10c-i-standing-orders-infra-and-reward-dispatch.md` (~440 lines) per PM-21; UT-2 + UT-13 both CLOSED on this merge)**, **10c-ii (PR #55, merged 2026-05-05 — proactive pipelines `morning_digest` + `paralysis_detection`; skill pack `compose_morning_digest@v3` (3.0.0); two new event schemas at v1 (`digest.composed` + `paralysis.triggered`); rewrites of `bootstrap/openclaw/programs/morning_digest.md` + `paralysis_detection.md` from STUB → full per [D1] Corollary; constructor-injection-loader pattern extended to three consumers per PM-27; validation-guard pattern per [BUILD.md §1289] for compose-skill-using pipelines; single-site `outbound()` per [§6.14] in `morning_digest`; `paralysis_detection` is deterministic per [BUILD.md §1297-1302] + operating rule 20 — zero LLM calls, zero `outbound()` in v1; 42 new tests on `tests/` testpath + 7 pack-internal; suite 508 → 550 passed, 2 skipped; `verify_invariants.sh` exit 0; ruff + mypy clean; refactored prompt committed to repo at `prompts/10c-ii-morning-digest-and-paralysis-detection.md` (~480 lines) per PM-21)**.

**Sequence updates merged (infrastructure, not build):** **PR #37 `sequence-update-10b-split` (merged 2026-04-26)** — splits 10b into 10b-i / 10b-ii per `docs/01-split-memo-10b.md`. **PR #39 `sequence-update-10b-ii-split` (merged 2026-04-27)** — splits 10b-ii into 10b-ii-α / 10b-ii-β per `docs/02-split-memo-10b-ii.md`. **PR #40 `update-partner-handoff` (merged 2026-04-27)** — partner-state snapshot. **PR #&lt;PR-sequence-update-10c-split&gt; `sequence-update-10c-split` (merged &lt;merge-date&gt;)** — splits 10c into 10c-i / 10c-ii / 10c-iii per `docs/03-split-memo-10c.md`. **PR #&lt;PR-α&gt; `arch-amendment-doc-updates-pr-alpha` (merged &lt;merge-date-α&gt;)** — Conception-C architecture amendment per `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md` §4: 5 constitutional doc amendments landed via two-commit PM-24 hybrid (Claude Code str_replace on 4 files + GitHub web UI on `ADMINISTRATEME_BUILD.md`); 13 modifications total; D17 added; §6.19 + §8.9 added; `member_knowledge` as 12th projection; `:3337 bridge` as fifth Python product; new top-level §MEMBER BRIDGES section. All five are single-purpose infrastructure PRs per PM-22 — no four-commit discipline, no BUILD_LOG entries, no tests.

**Checkpoints landed:** **07.5 (`docs/checkpoints/07.5-projection-consistency.md`, 2026-04-25)** — projection consistency audit. Verdict PASS with 1 non-critical finding C-1 (closed by sidecar PR #35). UT-1 closes here.

**Prompts with PR open, not yet merged:** none on the build-prompt cohort. (10c-ii Claude Code build PR #55 merged 2026-05-05; 10c-i + PR-α + Amendment-2 cycle PRs all merged earlier per the chronology above.)

**Prompts drafted, ready for Claude Code execution:** none. The next refactor target is **10c-iii** (`reminder_dispatch` + `crm_surface` + `custody_brief`; rewrites the three remaining stubs in `bootstrap/openclaw/programs/`). Type 1 (combined QC + refactor) Partner session expected per `docs/03-split-memo-10c.md`; 10c-ii's QC was completed in this same session (2026-05-05). 10c-iii is a clean 3-pipeline extension of the 10c-ii pattern. Orientation depth-read evaluates whether the empirical session window holds for three pipelines + three compose skill packs + three event schemas + three program-prose rewrites; per `D-prompt-tier-and-pattern-index.md` 10c-iii row, if depth-read at refactor reveals it's still over budget, splittable into 10c-iii-α (`reminder_dispatch` alone) + 10c-iii-β (`crm_surface` + `custody_brief`). PM-23 lineage. UT-14 (profile/persona on-disk loaders) remains open across all three consumers; constructor-injection (PM-27) lets 10c-iii ship without it.

**Sidecar PRs queued (non-blocking):** **`sidecar-diagrams-rendering-cleanup`** (queued 2026-04-29 from PR-α QC findings F-1 + F-2; cosmetic-only; ASCII art rendering fixes in `ADMINISTRATEME_DIAGRAMS.md` §2 second canonical example + §7 topology bridge-shelf row; ~5-min scope; sidecar memo + Claude Code micro-prompt produced in PR-α QC closing artifacts). Most recent merged sidecar: `sidecar-raw-data-is-manual-derived` merged as PR #35 on 2026-04-26 (closed 07.5 finding C-1). Most recent merged sequence updates: `sequence-update-10b-split` (PR #37, 2026-04-26), `sequence-update-10b-ii-split` (PR #39, 2026-04-27), `update-partner-handoff` (PR #40, 2026-04-27), `sequence-update-10c-split` (PR #&lt;N&gt;, &lt;date&gt;), and `arch-amendment-doc-updates-pr-alpha` (PR #&lt;PR-α&gt;, merged 2026-04-29) all merged. Recorded here so future Partner sessions see the full PR landscape.

**Next task queue (in order):**

1. **Build cadence advances to 10c-iii.** Partner session: 10c-iii orientation + refactor. Type 1 (combined QC + refactor) is the spec'd shape per `docs/03-split-memo-10c.md`, but the QC half was completed in the 2026-05-05 Partner session — so 10c-iii's session is effectively Type 3 (refactor-only). **Pre-orientation evaluation:** depth-read whether three pipelines + three compose skill packs + three event schemas + three program-prose rewrites fit one Claude Code session window. Reference baselines: 10c-i shipped 30 tests / ~440 lines for one pipeline + infrastructure; 10c-ii shipped 42 tests / ~480 lines for two pipelines + one compose skill. If 10c-iii's depth-read forecasts > §2.9 budget (350 lines / 25KB / ≤4 net-new modules), forecast a secondary split into 10c-iii-α (`reminder_dispatch` alone) and 10c-iii-β (`crm_surface` + `custody_brief`) per PM-23 + the `D-prompt-tier-and-pattern-index.md` 10c-iii row's note. Otherwise output is a single refactored 10c-iii prompt at `prompts/10c-iii-reminder-crm-custody.md` per PM-21.

   10c-iii's depth-read points (from `D-prompt-tier-and-pattern-index.md` 10c-iii row + 10c-ii's BUILD_LOG carry-forwards):
   - `ADMINISTRATEME_BUILD.md` §L4 for `reminder_dispatch` / `crm_surface` / `custody_brief` for the per-pipeline scheduling + validation-guard discipline (or lack thereof for `crm_surface` / `custody_brief`).
   - `bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md` — current stubs with `TODO(prompt-10c-iii)` markers; rewrite Scope (already populated in 10c-i), Approval gate, Escalation, Execution steps, What NOT to do per [D1] Corollary. Each ≤70 lines.
   - `bootstrap/openclaw/cron.yaml` — `reminder_dispatch` (`*/15 * * * *`), `crm_surface` (`0 19 * * 0`), `custody_brief` (`0 20 * * *`) entries already present as placeholders.
   - `packs/pipelines/morning_digest/handler.py` — quality-bar reference for proactive pipeline shape: constructor-injection loaders with no-op defaults per PM-27; F-2-widened `_SKILL_FAILURE_EXCEPTIONS` tuple (7-class set) for any compose-skill-using consumer; single-site `outbound()` in `deliver()` per [§6.14]; `delivered: bool` + `validation_failed: bool` payload fields on the emitted event for downstream surface rendering.
   - `packs/pipelines/paralysis_detection/handler.py` — quality-bar reference for deterministic-emit (no LLM, no `outbound()` in v1) pipelines; SHA-1 round-robin template selection seeded by `(member_id, today_iso)`; pre-condition checks with defensive skip on any miss.
   - `packs/skills/compose_morning_digest/` — quality-bar reference for compose skill packs at v3.0.0; `claimed_event_ids` as the load-bearing field for any future validation guard; `on_failure` shape that returns `validation_failed=true` so the pipeline catches the defensive default cleanly.
   - `adminme/lib/observation.py` `outbound()` — every consumer that delivers via channel layer must call this (observation-mode default-on suppresses).
   - **F-3 carry-forward from 10c-ii QC (HARD):** every new pack-internal `tests/test_pack_load.py` in `packs/pipelines/<name>/` must use a UNIQUE basename, e.g. `test_<pack>_pack_load_pack_internal.py`. Pytest's collection rule rejects multiple `test_pack_load.py` files across rootdir-relative module paths. 10c-ii renamed both new pack-internal tests on the spot; 10c-iii adds three more pack-internal tests and must use unique basenames from the start.

2. **James: drive `sidecar-diagrams-rendering-cleanup` PR** if not already merged. Single-purpose sidecar PR per PM-15. Branch: `sidecar-diagrams-rendering-cleanup`. Two `str_replace` operations in `ADMINISTRATEME_DIAGRAMS.md` (fix F-1: add code fence around §2 second canonical example; fix F-2: clean up §7 topology connector tee). 5-minute Claude Code session; sidecar memo + micro-prompt produced in PR-α QC closing artifacts. Non-blocking for 10c-ii.

3. **Claude Code session: execute 10c-iii.** Following the prep PR (with the refactored prompt committed). Four-commit discipline per PM-2. (10c-ii executed and merged 2026-05-05.)

4. **Partner session: QC of 10c-iii merge + 10d (checkpoint) refactor.** Type 1 combined or Type 0 session — 10d is a Tier C audit memo, not a build prompt. (10c-ii's QC + 10c-iii's orientation/refactor will combine in the next session.)

5. **Claude Code session: execute 10c-iii.** Closes the 10c cohort.

6. **Partner session: QC of 10c-iii merge + 10d (checkpoint) refactor.** Type 1 combined or Type 0 session — 10d is a Tier C audit memo, not a build prompt.

7. **Phase A continuation:** 10d → 11 (framework expansion; pre-split candidate per `D-prompt-tier-and-pattern-index.md`) → 11a / 11b / 11c-i / 11c-ii / 11d / 11e / 11f / 11g (reference-implementation cohort + projection) → 12 → 13a / 13b → 14a–14e → 15 / 15.5 → 16 (pre-split mandatory) → 17 → 18 → 19. Per `prompts/PROMPT_SEQUENCE.md` post-PR-β-2 sequence + dependency graph. Each row is its own Partner session (Type 1/2/3 as appropriate per `E-session-protocol.md`).

---

## Prompt-writing decisions (meta, not architecture)

These are conventions about *how Partner writes prompts for Claude Code*. They are not in `docs/DECISIONS.md` because they're not codebase architecture — they govern the prompt-refactor process.

Each tagged **HARD** (treat as immutable) or **SOFT** (current convention; reconsider if costing more than saving).

### PM-1: Prompt files live in `prompts/` — HARD

`prompts/PROMPT_SEQUENCE.md` is the **single canonical copy**. The root-level duplicate was removed when the `sidecar-prompt-sequence-version-drift` sidecar merged — single source of truth now enforced. Do NOT recreate the root duplicate. Any reference to `PROMPT_SEQUENCE.md` in this file or any future prompt refactor means `prompts/PROMPT_SEQUENCE.md`.

### PM-2: Four-commit discipline per build prompt — HARD

Every prompt structures as four incremental commits: schema/plumbing, first-module build, second-module build, integration+verification+BUILD_LOG+push. Each commit independently verifiable. If Claude Code times out mid-session, no recovery heroics — James re-launches, Claude Code picks up from `git log`.

### PM-3: Citations are compression, not ornament — HARD

Use `[§N]` / `[DN]` / `[BUILD.md §X]` / `[arch §N]` / `[cheatsheet Qn]` / `[CONSOLE_PATTERNS.md §N]` / `[REFERENCE_EXAMPLES.md §N]` / `[DIAGRAMS.md §N]`. One token replaces a paragraph of justification.

### PM-4: BUILD_LOG append lives inside Commit 4 — HARD

Introduced in 07a. Prevents forgotten BUILD_LOG updates. Template lives in `qc_rubric.md`. Partner fills in `PR #<N>` / `<commit4>` / `<merge date>` / `Outcome: MERGED` during post-merge QC housekeeping.

### PM-5: "Out of scope" section names specific prompts that handle deferred work — SOFT

Claude Code extends scope helpfully unless told otherwise. Format: "Do not X — prompt 10b handles X." Standardize across all prompt refactors.

### PM-6: Stub event-type schemas for events emitted by later pipelines — SOFT

First used in prompt 05 (registers `party.merged` v1 schema stub even though prompt 10b emits it). Trade-off: schema shape changes when pipeline is built pay upcaster cost (D7). Accepted so far.

### PM-7: Carry-forwards firing in 3+ prompts graduate to universal preamble — HARD (EXECUTED)

See `docs/universal_preamble_extension.md`. CF-1..CF-7 accumulated in 07a/07b and were extracted via the PM-7 infrastructure PR (slim preamble in `prompts/PROMPT_SEQUENCE.md` + canonical `scripts/verify_invariants.sh`). Status: **EXECUTED 2026-04-24**. All future prompts (07c onward) drafted in slim form; cross-cutting discipline lives in the preamble + verify script, not in each prompt.

### PM-8: Inline implementation code in prompts is a warning sign — SOFT

If Deliverables section runs over 5K tokens, it's spec-heavy rather than contract-heavy — trading Claude Code's judgment for Partner's specificity. Describe contract (inputs, outputs, invariants, errors) when possible; inline bodies only when they're spec (regex patterns a canary must use).

### PM-9: Sheets / features needing unregistered event types get TODO markers, not deferred prompts — HARD

Prompt 07b ships Lists/Members/Assumptions/Dashboard/Balance Sheet/Pro Forma/Budget vs Actual as sheet-builder TODOs. They populate when emitting prompts ship. Fragmenting into more prompts destroys cohesion.

### PM-10: Stub files from earlier scaffold prompts need explicit disposition — SOFT (07c resolved xlsx stubs)

Prompt 02 scaffolded `xlsx_workbooks/forward.py`, `reverse.py`, `schemas.py` as stubs. Prompt 07b built alongside rather than in them. **07c deletes all three** — forward daemon code lives in `__init__.py`/`builders.py`; reverse daemon lives in `adminme/daemons/xlsx_sync/reverse.py` per BUILD.md §3.11 line 995; `schemas.py` was empty noise. PM-10 remains as a SOFT pattern for future prompts: every prompt touching an area with scaffolded stubs explicitly decides repurpose / delete / continue ignoring.

### PM-11: Load only what the session needs from the zip — HARD

Partner sessions that ingest the whole codebase run out of headroom before producing refactored prompts. Load minimum per rule-of-thumb table in Session 1 Step 5. Constitutional docs are separate — always loaded fully. Code files are selective.

### PM-12: Prompt refactor is additive AND subtractive — SOFT

Refactoring doesn't just fix — it also removes what the preamble now covers. Extraction is as valuable as addition. A refactored prompt should be **smaller** than the draft it replaced if the preamble has grown to cover cross-cutting concerns.

### PM-13: Project knowledge is retrievable via search, not enumerable via filesystem — HARD

Claude Chat's Project knowledge is not filesystem-browsable. The `/mnt/project/` mount shows only a subset of uploaded files. Partner discovers Project knowledge contents via the `project_knowledge_search` tool. Running `project_knowledge_search` on targeted terms (e.g. "SYSTEM_INVARIANTS binding invariants", "partner_handoff current build state") confirms files are present. **Never claim a file is missing from Project knowledge based on `/mnt/project/` listing alone — only an empty `project_knowledge_search` result is authoritative evidence of absence.** Partner runs these searches proactively at startup, not when prompted.

### PM-14: Daemons live in `adminme/daemons/`, projections in `adminme/projections/` — HARD

Introduced in 07c. The xlsx reverse daemon is architecturally an L1-adjacent adapter (ingests external state — file edits — and emits typed events into the event log). Per BUILD.md §3.11 line 995, it lives at `adminme/daemons/xlsx_sync/reverse.py`, NOT in `adminme/projections/xlsx_workbooks/`. The two directories enforce a structural distinction:

- `adminme/projections/` — pure-functional event consumers; emit only system events; `verify_invariants.sh`'s §2.2 audit applies (`ALLOWED_EMIT_FILES` allowlist).
- `adminme/daemons/` — adapters/daemons that emit domain events on external authority (file edits, webhook events, time-based ticks). NOT covered by the §2.2 projection-emit allowlist.

The forward xlsx daemon is the exception: it lives in `adminme/projections/xlsx_workbooks/` because it IS a projection (consumes events, regenerates derived state). It only EMITS system events; that's what §2.2 permits.

Future adapter prompts (11, 12) will populate `adminme/adapters/` for adapters that don't share the daemon characteristic (Gmail, Plaid, etc.). The naming convention is therefore: `daemons/` for long-running file/clock-based watchers; `adapters/` for request/response or pull-based external integrations. Both emit domain events; both live outside the projections audit scope.

### PM-15: Two-prompt splits when a draft asks for both new infrastructure AND a long-running daemon consuming it — HARD

Surfaced by 07c. The original `prompts/07c-xlsx-workbooks-reverse.md` draft asked Claude Code to land schema additions, sidecar I/O, descriptors, diff core, full reverse daemon class, watchdog→asyncio bridge, lock contention, undo window, integration round-trip, and smoke script in one session. That overruns Claude Code's session window — proven empirically by two attempts that died partway through.

Resolution: split into 07c-α (foundations: schema, sidecar I/O, descriptors, diff core, forward sidecar writer) and 07c-β (daemon class + watchdog + integration round-trip + smoke). Each fits a session; together they close the round-trip. Both PR descriptions and BUILD_LOG entries label the prompt "Part 1 of 2" / "Part 2 of 2."

Existing examples of the same pattern: 01 → 01a/01b/01c (architecture + cheatsheet + invariants), 07 → 07a/07b/07c-α/07c-β (ops projections + xlsx forward + xlsx round-trip foundations + xlsx reverse daemon), **10b → 10b-i/10b-ii (per PR #37, 2026-04-26), then 10b-ii → 10b-ii-α/10b-ii-β (per PR #39, 2026-04-27)**, and **10c → 10c-i/10c-ii/10c-iii (per `docs/03-split-memo-10c.md`, 2026-04-28)**.

PM-15 supersedes the implicit assumption that every numbered prompt fits one session. PM-2 (four-commit discipline) is per-PR, not per-prompt-number; a split prompt ships two PRs of four commits each.

### PM-16: Descriptor public-API discipline — SOFT

07c-α landed sheet descriptors as private symbols (`_TASKS`, `_COMMITMENTS`, `_RECURRENCES`, `_RAW_DATA`) accessible only through `descriptor_for(workbook, sheet)`, `editable_columns_for(descriptor, row)`, and the `BIDIRECTIONAL_DESCRIPTORS` tuple. The original 07c-β draft Read-first referred to them as `TASKS_DESCRIPTOR` etc. — symbols that don't exist. Neither approach is wrong on its own; the drift is the issue.

Partner discipline: when prompt N specifies module API surface, and prompt N+1 consumes that surface, prompt N+1's depth-read at refactor time must verify symbol names against what landed, not against what prompt N's draft text said would land. The Read-first block of prompt N+1 cites import paths AND symbol names; both are checked.

When the consumer prompt expects public symbols and the producer shipped private ones, either prompt N+1's refactor uses the public accessor (`descriptor_for`) or a single-purpose follow-on PR re-exports the symbols. 07c-β chose the accessor approach (cheaper; no module re-edit needed).

### PM-17: Single-seam enforcement invariants verified by exclusion-grep — HARD

Surfaced by 08b QC. When an invariant takes the form "X must only ever happen at one place" — e.g. [§6.13/§6.14] "every outbound call goes through `outbound()` in `lib/observation.py`; emitting `external.sent` anywhere else is a bug" — the QC verification is an exclusion-grep, not an inclusion-check. Pattern: `grep -rnE "log\.append.*external\.sent|log\.append.*observation\.suppressed" adminme/lib/ adminme/products/ adminme/projections/ adminme/daemons/ adminme/pipelines/` must return zero hits outside the single seam (`adminme/lib/observation.py`).

### PM-18: Reserved version slots for type-name continuity across prompts — SOFT

Surfaced 09a refactor. `SkillCallRecordedV2` is registered at v2; v1 is a reserved slot (no model registered) per the build_log + 09a Commit 1 docstring. Rationale: BUILD.md / DECISIONS.md / arch refer to skill-call-recorded as a single concept. If 09a registered at v1, future schema iteration increments to v2 would need an upcaster. Registering at v2 from the outset, with a documented "v1 reserved — no model" note, sidesteps the upcaster-immediately-on-first-bump trap and signals continuity with the conceptual lineage.

### PM-19: Schema-stub field-shape drift folds into the next prompt's Commit 1, not a sidecar — HARD

Surfaced 09a refactor. `SkillCallRecordedV2.input_tokens` (and friends) was registered as required `int` but ADR-0002 mandates `int | None` — fix lands in 09a Commit 1 because 09a is the first emitter. PM-19 generalizes: future prompts that introduce a seam must check the merged stub against the contract and fold any field-shape drift into Commit 1, not a separate PR.

### PM-20: When a wrapper introduces an HTTP seam to an external service, the test pyramid mocks the HTTP layer with `httpx.MockTransport` rather than `respx` — SOFT

Surfaced in 09a. Both are acceptable; `httpx.MockTransport` is slightly closer to the underlying library and avoids a dep when the test only needs request recording + canned responses. Decision deferred to first 11+ adapter prompt that adds an HTTP wrapper; 09a left both available.

### PM-21: Refactored prompts ship in the build PR — HARD (graduated 2026-04-28)

Surfaced in 10a QC (2026-04-26) as SOFT in flux. Historical pattern (07a–10a era) was paste-only — Partner's refactored text held in chat, James pasted into Claude Code without committing to repo. **Four consecutive build-prompt rounds graduated the convention to HARD; 10c-i confirms it stays:**

- **10b-i (PR #38, merged 2026-04-26)** — refactored 320-line prompt committed at `prompts/10b-i-identity-and-noise.md`.
- **10b-ii-α (PR #41, merged 2026-04-28)** — 370 lines at `prompts/10b-ii-alpha-commitment-extraction.md`.
- **10b-ii-β (PR #&lt;PR-10b-ii-beta&gt;, merged 2026-04-28)** — 330 lines at `prompts/10b-ii-beta-thank-you-detection.md`.
- **10c-i (PR #44, merged 2026-04-29)** — ~440 lines at `prompts/10c-i-standing-orders-infra-and-reward-dispatch.md`.

Going forward, **refactored prompts ship in the build PR**.

### PM-22: Sequence updates and split-memo prep PRs are infrastructure, not build prompts — HARD

Surfaced 2026-04-26 by PR #37 `sequence-update-10b-split` (and reaffirmed by PR #39 `sequence-update-10b-ii-split` 2026-04-27, PR #40 `update-partner-handoff` 2026-04-27, the `sequence-update-10c-split` PR, and PR #&lt;PR-α&gt; `arch-amendment-doc-updates-pr-alpha` 2026-04-29). These PRs:

- Update planning artifacts (`prompts/PROMPT_SEQUENCE.md`, split memos at `docs/NN-split-memo-<N>.md`, partner-state snapshots, **architecture-amendment doc updates per PM-28**).
- Have no four-commit discipline.
- Have no BUILD_LOG entry by design — they don't ship runtime behavior.
- Are NOT sidecars in the PM-15 sense (sidecar = defect-fix in already-merged code). Sequence updates and architecture amendments are forward-looking planning artifacts; they create the conditions for the next refactor session to proceed.

The same convention applies to:
- `D-prompt-tier-and-pattern-index.md` updates (which live in Partner setup, NOT in repo per James's split-memo instruction — handled out-of-band by James after the sequence PR merges).
- Future split memos that ship as `docs/NN-split-memo-<original>.md` style files (the on-disk record of the Partner's Tier C decision).
- Partner-state snapshot PRs that update `docs/partner_handoff.md` and/or `docs/build_log.md` outside a build session.
- **Architecture-amendment cycle PRs** (PR-α / PR-β / PR-γ per PM-28).

PM-22 distinguishes these from the build-prompt cohort that has full ledger entries and BUILD_LOG appends in Commit 4.

### PM-23: Secondary splits are normal when a primary-split sub-prompt's "watch flag" condition fires — HARD

Surfaced by `docs/02-split-memo-10b-ii.md` 2026-04-27. **The discipline:** when a primary-split memo carries a "watch" flag for one of its sub-prompts, the Partner session that opens orientation on that sub-prompt **forecasts the secondary split in the startup report** before drafting any refactored prompt. Drafting a single sub-prompt and then splitting it at §2.9 wastes the session.

**Numbering convention:** secondary splits use Greek-letter suffixes on the primary-split tag (e.g. 10b-ii-α / 10b-ii-β).

**On-disk record:** secondary-split memos use the next ordinal in `docs/NN-split-memo-<original-prompt>.md`. Ordinals are global, not per-original-prompt.

**Sequence-update PR pattern:** identical to PM-22 (single-purpose, no BUILD_LOG, no tests).

**Watch flag for 10c-ii:** UT-14 (profile/persona on-disk loaders) may push 10c-ii over the §2.9 budget. The orientation Partner session must evaluate this and propose 10c-ii-α / 10c-ii-β split if the watch fires.

### PM-24: Long static markdown files (split memos, partner_handoff, build_log) ship via GitHub web UI when Claude Code times out on `create_file` — HARD

Surfaced 2026-04-27 during the 10b-ii sequence-update PR. Three timeouts on Claude Code's `create_file` for the static `docs/02-split-memo-10b-ii.md` (133 lines) before the routing-around landed: Partner produced canonical text in chat, James pasted via GitHub web UI's "Add file → Create new file", a second Claude Code session inherited the branch for the surgical `prompts/PROMPT_SEQUENCE.md` `str_replace` edits. Discipline now: Partner produces full text in chat for static-content files; James pastes via web UI; Claude Code is reserved for code edits + `str_replace` operations on existing files.

**PR-α confirmed PM-24 hybrid pattern works on first execution (2026-04-29).** PR-α landed 4 files via Claude Code str_replace (Landing 1) + 1 file (`ADMINISTRATEME_BUILD.md`, 7 edits including new top-level §MEMBER BRIDGES section ~41 lines) via GitHub web UI (Landing 2) on the same branch as a second commit. Single PR opened after both landings. The pattern is now battle-tested for the architecture-amendment-cycle PR-α scenario where the largest doc edit (BUILD.md §MEMBER BRIDGES + Capture full replacement) exceeds Claude Code's `create_file`/`str_replace` reliability threshold.

### PM-25: Markdown autolinker defense for paste-targeted artifacts — HARD

Surfaced 2026-04-27 during the same 10b-ii sequence-update PR. The chat client James uses auto-converts bare filenames (`*.md`, `*.sh`, `*.py`, etc.) into hyperlinks of the form `name.ext`, breaking literal-string fidelity when Partner-produced content is pasted. Defense: Partner's prompts and memos prefix every paste-targeted bare-filename mention with a "READ THIS FIRST — autolinker normalization" preamble that explicitly instructs the consumer to treat such tokens as literal strings (see the 10c-i prompt's preamble for the canonical form). Applied universally to refactored build prompts and split memos.

### PM-26: Pipeline emit names that bypass a stale BUILD.md draft must cite the supersession explicitly — SOFT

Surfaced 2026-04-29 during 10c-i QC. BUILD.md §1210 names the event `adminme.reward.dispatched` (stale draft). BUILD.md §1620 + CONSOLE_PATTERNS.md §8 name it `reward.ready` (canonical, because the console's SSE layer consumes the event by that name and §7.3/§2.2 forbid a projection from re-emitting under a different name). 10c-i registered `reward.ready` at v1 and cited the supersession in `RewardDispatchPipeline.handle()` docstring + `bootstrap/openclaw/programs/reward_dispatch.md` "What NOT to do" section.

**The discipline:** when BUILD.md internally contradicts itself on an event name (or other shape detail), pick the consumer-side name (the layer that reads the event has the binding contract), cite supersession in code comments + standing-order prose, and document it in the program file's "What NOT to do" so future readers don't accidentally regress to the stale name.

PM-26 is a SOFT convention; future prompts should look for similar drafts-vs-canon disagreements during depth-reads and apply the same supersession-citation pattern.

### PM-27: Pipeline classes that need future on-disk loaders accept callable injectors with no-op defaults — SOFT

Surfaced 2026-04-29 during 10c-i. `RewardDispatchPipeline.__init__(profile_loader, persona_loader)` defaults to `lambda _mid: None` / `lambda: None`, exercising the defensive-default path per [§7.7]. The integration test injects fakes via attribute assignment (`pack.instance._profile_loader = ...`). When real loaders ship (UT-14 resolution), the wiring is constructor-time.

**The discipline:** pipeline packs that need cross-cutting infrastructure not yet on main can ship without the infrastructure by accepting callable injectors with safe no-op defaults. The defaults exercise the defensive-default code path so the pipeline is testable and shippable; production wiring lands when the infrastructure does. This pattern preserves session-window discipline (don't pile loader infrastructure into the same session as a pipeline that uses it) without delaying the pipeline.

PM-27 is the convention 10c-ii's `morning_digest` and `paralysis_detection` should follow if profile/persona loaders are not yet on main when 10c-ii lands. The orientation Partner session decides whether loaders ship in 10c-ii or defer; if they defer, 10c-ii's pipelines use this pattern.

### PM-28: When constitutional documents drift from binding architectural intent, Partner pauses the build, flags the drift, and runs an architecture-amendment cycle — HARD

Surfaced 2026-04-29 by the Conception-C amendment cycle (memo at `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md`). **The discipline:** if Partner detects drift between what the constitutional docs say and the operator's binding intent — signaled by an operator clarification, a depth-read finding, or a recognized contradiction in the docs — Partner does NOT continue the in-progress build prompt's refactor. Partner produces a Tier C architecture-amendment memo at `docs/NN-architecture-amendment-<topic>.md`. James reviews, approves or revises. On approval, the cycle is **three single-purpose PRs in order**:

- **PR-α (constitutional doc amendments)** — modifications to `ADMINISTRATEME_BUILD.md`, `docs/architecture-summary.md`, `docs/SYSTEM_INVARIANTS.md`, `docs/DECISIONS.md`, and (typically) `ADMINISTRATEME_DIAGRAMS.md` per the memo's §4. **Per PM-24, the largest file in this set typically lands via GitHub web UI as a second commit on the same branch; the other files land via Claude Code `str_replace` as the first commit.**
- **PR-β (prompt-sequence + D-index updates)** — modifications to `prompts/PROMPT_SEQUENCE.md` and `D-prompt-tier-and-pattern-index.md` per the memo's §5.2 (new prompts inserted, modified prompts re-scoped, new pre-split disposition entries).
- **PR-γ (partner-state snapshot)** — modifications to `docs/partner_handoff.md` (this file) per the memo's §5.3, plus typically `docs/build_log.md` if any build-prompt QC findings are outstanding.

All three are infrastructure / planning-artifact PRs per PM-22 — no four-commit discipline, no BUILD_LOG entries, no tests. The Conception-C amendment of 2026-04-29 (knowledge vaults + member bridges) is the canonical example. Partner's QC of PR-α is a Type 2 session walking the memo §4 spec line-by-line against what landed; QC of PR-β is similarly a Type 2 against memo §5.2; PR-γ is the closing session, typically combined with the next QC turn.

**When NOT to use this:** a single ambiguity that can be resolved by a single decision in `docs/DECISIONS.md` is a one-line addition, not an amendment cycle. The cycle is reserved for cross-document drift requiring coordinated changes to 3+ constitutional docs.

### PM-29: Knowledge-source adapters live on member bridges, not on the central CoS Mac Mini — SOFT

Surfaced 2026-04-29 by the Conception-C amendment. Knowledge-source adapters (Apple Notes, Voice Notes, Obsidian, third-party connector packs) are bridge-side L1 adapters running on per-member Mac Mini bridges, not central-host adapters running on the CoS Mac Mini. Future prompts that add knowledge-source adapters land in `bridge/`, not `adminme/`. The two directories enforce a structural distinction parallel to PM-14 (`adminme/daemons/` vs `adminme/projections/`) but at the cross-machine level: `bridge/` is bridge-Mac-Mini-side runtime, `adminme/` is CoS-Mac-Mini-side runtime. Per memo §3.6 and BUILD.md §MEMBER BRIDGES.

**The discipline:** when refactoring a future prompt that adds a knowledge-source adapter, place the adapter under `bridge/adminme_bridge/adapters/<source>/`, NOT `adminme/adapters/`. The bridge daemon and the central system share event-schema models via editable install or vendored copy (UT-15, decided at 11c orientation). The convention parallels PM-14: code lives in the directory whose runtime owns the work.

PM-29 is SOFT because the bridge codebase has not yet been instantiated (prompt 11c lands first bridge code). Promote to HARD once 11c merges and the convention is concretely demonstrated.

### PM-30: Architecture-amendment cycles can EXPAND mid-flight when new architectural questions surface that are coupled to the in-flight scope — HARD

Surfaced 2026-04-29-B by Amendment-2 (Conception-C narrow → personal-data-layer expanded). Expansion is preferable to running back-to-back amendment cycles when the questions are conceptually coupled. **The discipline:** scope expansion is acceptable up to ~3-4× the original; beyond that, Partner pauses to confirm with James before continuing. The Amendment-2 expansion (Conception-C narrow at memo `docs/04-architecture-amendment-knowledge-vaults-and-member-bridges.md` → personal-data-layer expanded at memo `docs/05-architecture-amendment-personal-data-layer.md`) is the canonical example: the Conception-C narrow scope was member bridges + knowledge-source adapters + `member_knowledge` projection (~5 sections); the expanded Amendment-2 scope added the five-category adapter taxonomy + adapter framework + lists projection + Apple Calendar adapter + contacts adapters + Home Assistant Cat-C+E reference (~9 sections, ~3× the original). Operationally, this lengthened Amendment-2 from ~3 PRs to ~4 PRs (memo + PR-α-2 [two-commit hybrid per PM-24] + PR-β-2 + PR-γ-2) and from ~2 Partner sessions to 4 (A-1 / A-2 / A-3 / A-4).

### PM-31: Cat-B (external-state-mirror) adapters follow a shared pattern — SOFT

Surfaced 2026-04-29-B by Amendment-2 ([D19] / memo §2). Cat-B adapters mirror state the principal directly maintains in an external system; AdministrateMe both reads from and writes to the upstream. **The shared pattern:** external IDs preserved as `external_<entity>_id` columns on each row; source-kind discriminator on each row (e.g. `external_id_kind = 'apple_reminders' | 'google_tasks' | 'apple_notes_checklist'`); bidirectional where the upstream API allows; deduplication on `(external_id_kind, external_<entity>_id)` when multiple adapters see the same upstream entity (e.g. iCloud Shared List visible from every invited member's bridge collapses to one `lists` row). Future Cat-B adapters should follow this pattern — Apple Reminders, Google Tasks, Apple Calendar, Google Calendar, Apple Contacts, Google Contacts, Apple Notes-checklists are all reference cases.

### PM-32: Bootstrap §5 credential intake lists ONLY credentials that an active adapter or skill consumes — HARD (strengthened 2026-04-29-B)

Surfaced 2026-04-28 by 16 refactor scope review; **strengthened 2026-04-29-B** by Amendment-2 §3.4 + [D24]. Optional credentials with no consumer are dead code in the bootstrap wizard and create operator confusion. The wizard's §5 credential-intake list and the L5 product surfaces' router lists must stay in sync with the actual Phase A adapter inventory.

**ADDENDUM 2026-04-29-B:** For L5 product surfaces that reference adapter-specific routers (e.g. `/api/automation/ha/state`, `/api/automation/ha/services`), the adapter MUST be in Phase A scope or the routers MUST be removed. Routers without backing adapters are a stronger version of credential orphans — the surface itself promises functionality that doesn't exist. Home Assistant being added to Phase A scope as new prompt 11g per [D24] resolves this for the HA routers; Lob (postal mail) and Privacy.com (virtual cards) are removed from Phase A L5 surface entirely per [D25].

### PM-33: Adapter categorization is by epistemic role, not runtime substrate; capabilities are a list — HARD

Surfaced 2026-04-29-B by Amendment-2 ([D19] / memo §1 + §2). Adapter classification is by **epistemic role** (five categories: Communication, External-State-Mirror, Inbound-Data, Personal-Knowledge, Outbound-Action), not by runtime substrate. Capabilities are a list (multi-capability adapters declare each capability as its own seam — see PM-35). Runtime axis (central / bridge / dual-deployment) is an **orthogonal secondary dimension**.

**The discipline:** new adapters declare both axes (`kind`: Cat-A through Cat-E + `runtime`: central | bridge | dual) plus `capabilities: [...]` in their pack manifest. Reference implementations exist per category — Gmail (Cat-A central), Apple Reminders (Cat-B dual), Plaid (Cat-C central), Apple Notes (Cat-D bridge), Home Assistant (Cat-C+E central, multi-capability). Phase A delivers the manifest format extensions, five abstract base classes (one per category), install-time validation framework, and `adminme adapters` CLI subcommand group; post-Phase-A adapter generation is a fill-in-the-form exercise via `adminme adapters scaffold`, not a one-off artisan process.

### PM-34: Adapter framework deliverables are Phase A scope — SOFT

Surfaced 2026-04-29-B by Amendment-2 ([D19] / [D20] / memo §3). Adapter framework deliverables are explicitly Phase A scope and land in prompt 11 (the framework prompt; pre-split candidate per `D-prompt-tier-and-pattern-index.md`):

- Manifest format extensions (capabilities-as-list, write-capabilities, sensitivity defaults, `observation_mode_required`, owner-scope-overridability, sharing-model discriminator).
- Five base classes per category (`CommunicationAdapter`, `ExternalStateMirrorAdapter`, `InboundDataAdapter`, `PersonalKnowledgeAdapter`, `OutboundActionAdapter`).
- Reference adapter per category (lands across prompts 11a / 11b / 11c-i / 11c-ii / 11g per the post-PR-β-2 sequence).
- Install-time validation (capability-runtime coherence, event-schema registration, projection-subscription compatibility, base-class conformance, write_capabilities method-presence, signature check).
- Authoring guide (`docs/adapter-authoring-guide.md`).
- Three-layer developer-mode gate per [D20] (bundled / verified third-party / user-authored).
- `adminme adapters` CLI subcommand group (lands in prompt 17 per PR-β-2 row update).

### PM-35: Multi-capability adapters declare a list of capabilities — SOFT

Surfaced 2026-04-29-B by Amendment-2 ([D19] / memo §2.2). Multi-capability adapters declare a list of capabilities in their pack manifest. **Each capability is its own seam** with its own write-capability declaration and event-family scope. Reference cases at amendment time:

- **Notion** — Cat-B (database mode) + Cat-D (page mode).
- **Home Assistant** — Cat-C (state-read seam) + Cat-E (service-call seam). Lands as prompt 11g.
- **Tesla** — Cat-C (telemetry) + Cat-E (lock/unlock/precondition).
- **Apple Notes** — Cat-D (prose half) + Cat-B (checklist half). Cat-D ships in 11c-ii alongside Cat-B checklist write-back per [D18].
- **Privacy.com** — Cat-E (issue card) + Cat-C (transaction notification). Plaid covers transaction observation in v1; Privacy.com is deferred to v2 per [D25].

**The discipline:** when refactoring a future adapter prompt with multi-capability shape, the manifest declares `capabilities: [{kind: cat_X, write_capabilities: [...], event_family: "..."}, {kind: cat_Y, ...}]` — not a single `kind`. Install-time validation checks each capability's coherence independently per PM-33 + PM-34.

---

## Open tensions / unresolved things

### UT-1: When to refactor checkpoint 07.5 — CLOSED 2026-04-25

Original 07.5 assumed 11 projections in one prompt. After 07a/07b/07c-α/07c-β split, it audits four ops prompts together as a cohort. Audit landed at `docs/checkpoints/07.5-projection-consistency.md` on 2026-04-25 post-07c-β merge. Verdict PASS with one non-critical sidecar finding (C-1, queued as `sidecar-raw-data-is-manual-derived`). Status: **CLOSED**.

### UT-2: Proactive pipeline registration path (10c) — CLOSED 2026-04-29

`[D1]` confirmed: proactive pipelines register via workspace-prose `AGENTS.md` + `openclaw cron add`. **Resolved on 10c-i merge (PR #44, 2026-04-29).** 10c-i shipped `bootstrap/openclaw/programs/<six>.md` (one per proactive pipeline + reward_dispatch as documentation continuity) + `bootstrap/openclaw/cron.yaml` (five scheduled-program entries) + `bootstrap/openclaw/README.md` (the §8 consumption contract). Bootstrap §8 (prompt 16) reads the markdown program files, concatenates them into `~/Chief/AGENTS.md`, and runs `openclaw cron add --cron "<cron>" --message "<message>"` per cron.yaml entry. Per-member cron substitution (e.g. morning_digest's `0 7 * * *` placeholder → member's actual wake time) is bootstrap §8's responsibility, not the cron.yaml's. Status: **CLOSED**.

### UT-3 (RESOLVED 2026-04-25): Prompt 08 split executed

Prompt 08 split into **08a (Session + scope, read side)** and **08b (governance + observation + UT-7 closure, write side)**. Both sub-prompts merged 2026-04-25. Status: **RESOLVED**.

### UT-4: Placeholder values in xlsx protection passwords

07b uses `"adminme-placeholder"` as sheet-protection password. Real secret flow lands in prompt 16 (bootstrap wizard §5). Do not resolve earlier.

### UT-5: `<commit4>` and `<merge date>` placeholders in BUILD_LOG — current

**Filled post-merge during Partner's QC pass per the rubric.** 07c-α: PR #20, commits aa395dd / 7305acd / fcdb592 / 1d770ec, merge date 2026-04-24. 07c-β: PR #21, commits ffa6d9c / bf649ed / 00bff7d / 2788761, merge date 2026-04-25. 08a + 08b advanced 2026-04-25 QC. **10b-ii-α** filled with PR #41, commits a8e1e09 / 2995d13 / 5e37a27 / 8671d06, merge date 2026-04-28. **10b-ii-β** flipped to MERGED; PR/SHA/date placeholders await James's `gh pr list` find-and-replace. **10c-i** PR #44, merge date 2026-04-29; commits `7be528f` (Commit 1: standing-orders infra + reward.ready schema) / `d7fef02` (Commit 2: reward_dispatch pipeline pack) / `4535686` (Commit 3: reward_dispatch handler-direct unit tests) / `37eb874` (Commit 4: reward_dispatch integration + BUILD_LOG). UT-5 will surface again after the 10c-ii merge.

### UT-6: Sidecar-state JSON pathway for xlsx round-trip — RESOLVED 2026-04-25

Resolved on 07c-β merge (PR #21). Status: **RESOLVED**.

### UT-7: Reverse-daemon emit path bypasses Session / guardedWrite — RESOLVED 2026-04-25

Closed by 08b (PR #&lt;PR-08b&gt;, merged 2026-04-25). The reverse-daemon rewrite stayed in 08b's Commit 3 — the sidecar hedge to 08.5 was NOT activated. Status: **RESOLVED**.

### UT-8: `vector_search.nearest` scope carve-out — RESOLVED 2026-04-25

Resolved inline within 08a (three-layer carve-out shipped). Status: **RESOLVED**.

### UT-9: ALLOWED_EMITS per-file allowlisting in scripts/verify_invariants.sh — RESOLVED 2026-04-26

09a took the script-side path: `scripts/verify_invariants.sh` now contains a `SKILL_EMITS` block. Future Partner sessions can lift `external.sent` / `observation.suppressed` from test-side to script-side using this same pattern when convenient. Tracked as a future single-purpose PR; not blocking.

### UT-9 (parallel): Pack-root resolution accepts three forms — RESOLVED 2026-04-26

Closed by 10a (PR #33, merged 2026-04-26). Status: **RESOLVED**.

### UT-10: Pipeline pack loader vs skill pack loader — by-design distinct

Pipeline packs structurally cannot reuse the skill-pack loader (no SKILL.md, no input/output schemas, an instantiable class instead of a function). UT-10 stays open as a tracking entry; 10b-i + 10b-ii-α + 10b-ii-β + 10c-i (`packs/pipelines/reward_dispatch/`) have all shipped using the dual-loader pattern with no friction. **Can close at 10c-iii merge if the pattern continues clean across all six 10c pipelines.**

### UT-11: Pipeline pack location — RESOLVED 2026-04-26

Closed by 10b-i merging at `packs/pipelines/`. Continued by 10c-i shipping `packs/pipelines/reward_dispatch/`. Convention now confirmed: pipeline packs live at `packs/pipelines/<name>/` mirroring 09b's `packs/skills/<name>/`.

### UT-12: Parties-DB seam through PipelineContext — CLOSED 2026-04-28

Closed by 10b-ii-α merge (PR #41, 2026-04-28). `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None` on `PipelineContext`, threaded through `PipelineRunner.__init__` as an optional kwarg with default `None` for backward compatibility. 10b-ii-β confirmed the seam by reusing it for `thank_you_detection` with zero infrastructure changes. **10c-i did not need the seam** (reward_dispatch reads profile/persona, not parties); the seam is dormant for proactive pipelines unless a specific one needs party-resolution.

Status: **CLOSED 2026-04-28**.

### UT-13: 10c is the next pre-split candidate — CLOSED 2026-04-29

Resolved by the Partner orientation session of 2026-04-28 producing `docs/03-split-memo-10c.md` (split into 10c-i / 10c-ii / 10c-iii) and 10c-i merging 2026-04-29. UT-2 sub-question simultaneously resolved by 10c-i's bootstrap/openclaw/ artifacts.

Status: **CLOSED 2026-04-29**.

### UT-14: Profile / persona on-disk loader modules — OPEN

Surfaced 2026-04-29 during 10c-i QC. Three pipelines now (10c-i's `reward_dispatch`, 10c-ii's planned `morning_digest` + `paralysis_detection`) need profile + persona loaders to read `reward_distribution` (per BUILD.md §1884) and `reward_templates.yaml` / `digest_templates.yaml` / `paralysis_templates.yaml` (per BUILD.md §PERSONA PACKS). 10c-i shipped `RewardDispatchPipeline.__init__(profile_loader, persona_loader)` accepting callable injectors with no-op defaults per PM-27; the integration test injects fakes via attribute assignment.

The actual on-disk loader modules under `adminme/profiles/` + `adminme/personas/` (or wherever the depth-read at refactor time lands) are not yet on main. Most likely they ship in either:
- **10c-ii** (alongside `morning_digest` + `paralysis_detection` if the budget allows), OR
- **A later prompt** (most likely 11 / 15 / 16 — bootstrap-adjacent), with 10c-ii pipelines using the same constructor-injection pattern as 10c-i.

The orientation Partner session for 10c-ii **must evaluate this** before drafting and forecast a possible 10c-ii-α / 10c-ii-β secondary split (per PM-23) if loaders push 10c-ii over the §2.9 budget. Until then, integration tests inject fakes via attribute assignment as 10c-i's `tests/integration/test_reward_dispatch_runner.py::_stub_loaders()` demonstrates.

Status: **OPEN, resolves at the prompt that lands the loaders. No build blocker.**

### UT-15: Bridge daemon and central system event-schema sharing mechanism — OPEN

Surfaced 2026-04-29 by the Conception-C amendment (memo §5.3). The bridge daemon (under `bridge/adminme_bridge/`, prompt 11c) needs the same event-schema Pydantic models that the central system uses (`adminme/lib/event_types/`) so bridge emissions match the central event log's expected shape. Two viable approaches:

- **(a) Editable install** — make `adminme/lib/event_types/` pip-installable as a small package (`adminme-events`), depend on it from `bridge/pyproject.toml` via `path = "../adminme/..."` editable. Pro: single source of truth; bridge always sees latest schemas. Con: development-mode setup is more complex; deployment requires the source tree present on each bridge.
- **(b) Vendored copy** — bridge `pyproject.toml` includes a snapshot of `adminme/lib/event_types/` as `bridge/adminme_bridge/event_types/`. Pro: bridge ships standalone. Con: drift risk between central and bridge versions; needs CI guardrail against schema drift.

**Decision deferred to 11c orientation.** Partner's 11c-orientation depth-read at refactor time picks one. Both options preserve all event-schema invariants ([D7] register at v1; upcasters compose forward) regardless of choice.

Status: **OPEN, resolves at 11c orientation. No build blocker until 11c drafts.**

### UT-16: Kid-event routing-restriction enforcement mechanism — OPEN

Surfaced 2026-04-29 by the Conception-C amendment (memo §2.5). Kid bridges emit owner-scoped events (`owner_scope=private:<child_id>`); these events are explicitly excluded from cross-member knowledge-graph derivation (`vector_search`, `graph_miner`, `commitment_extraction`, `recurrence_extraction`, `relationship_summarization`, `closeness_scoring`). Two viable enforcement mechanisms:

- **(a) `owner_scope_excludes` in pipeline manifests** — each excluded pipeline's `pipeline.yaml` lists `owner_scope_excludes: ["private:<child_id>"]`. Pro: explicit at the consumer side; manifests are the canonical place for subscription declarations. Con: requires updating every excluded pipeline's manifest when a new child member is added; manifest maintenance scales linearly with child count.
- **(b) `kid_bridge: true` payload field (or envelope flag)** — the bridge-ingest endpoint flags kid-bridge events with `kid_bridge: true` (or, equivalently, `derived_from_kid_bridge` envelope flag); excluded consumers check this flag in their `apply()` / handler. Pro: child-set-membership is a runtime question, not a manifest question; new children inherit the rule automatically; consumers opt-in to checking. Con: every excluded consumer has to remember to check; the rule is enforced at consumer code, not at subscription time.

The trade-off is **manifest discipline (a) vs code discipline (b).** Decision deferred to 11c orientation OR earlier if a downstream prompt (e.g. 10c-ii's `morning_digest` if it ever needs to subscribe to knowledge events) needs to know.

Status: **OPEN, resolves at 11c orientation. No build blocker until then.**

### UT-17: `member_knowledge` as new (12th) projection vs extending `artifacts` — OPEN

Surfaced 2026-04-29 by the Conception-C amendment (memo §3.3). PR-α already encoded `member_knowledge` as the 12th projection (architecture-summary.md §1 sentence + §4 row 3.12; DIAGRAMS.md §1 ASCII as `member_knowledge (per-member)`). Final implementation decision is at the projection-prompt orientation. Two viable shapes remain:

- **(a) New projection `member_knowledge`** (memo's recommendation; PR-α encoding) — dedicated 12th projection. Tables: `notes`, `note_versions`, `voice_notes`. Subscribes to `note.*` and `voice_note.*` events. Owner-scope-partitioned per [§1.6]. One DB, `owner_scope` column. Pro: notes are first-class knowledge units, query patterns differ from artifacts. Con: one more projection to maintain.
- **(b) Extend the existing `artifacts` projection** — add `note` and `voice_note` as artifact kinds in the existing artifacts projection. Pro: fewer projections; reuses existing queryability. Con: notes are not really "artifacts" in the user-facing sense.

Status: **OPEN, resolves at the projection-prompt orientation. No build blocker until then. Recommendation per memo §3.3 is option (a); already encoded in PR-α; reversal would require a follow-up amendment.**

### UT-18: Apple Notes read mechanism — SQLite direct vs AppleScript vs hybrid — OPEN

Surfaced 2026-04-29 by the Conception-C amendment (memo §1.3 + §3.6). The Apple Notes adapter on a member bridge needs to read `NoteStore.sqlite` (the local Apple Notes database) and detect changes. Three viable approaches:

- **(a) SQLite direct** — open `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite` read-only; query for new/changed notes since cursor. Pro: fast bulk reads; good for initial backfill. Con: schema can change on macOS major-version updates; requires Full Disk Access.
- **(b) AppleScript** — drive Apple Notes via AppleScript bridge; ask Notes for the current state. Pro: schema-stable across macOS versions; no Full Disk Access needed for many operations. Con: slow; brittle on large vaults; requires Notes UI process.
- **(c) Hybrid** — SQLite direct for bulk reads + initial backfill; AppleScript fallback when SQLite schema fingerprint doesn't match a known version. Pro: best of both worlds; degraded mode rather than hard failure on macOS update. Con: more code; two code paths to maintain.

**Recommendation per memo §1.3:** option (c) hybrid. Final decision at 11c orientation.

Status: **OPEN, resolves at 11c orientation. No build blocker until then.**

### UT-19: AppleScript Notes write-back conflict handling — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §5.6). When the bridge writes to a Notes-checklist via AppleScript while the user simultaneously edits the same note on iPhone, last-writer-wins through iCloud sync. Acceptable per James 2026-04-29-B because the toggle+add scope minimizes risk (no remove, no in-place text edit, no reorder per [D18]). Final design lands at 11c-ii orientation — observation hooks for write-back outcomes (success / iCloud-conflict-rejected / AppleScript-error) and the three system observability events specified in memo §5.6.

Status: **OPEN, resolves at 11c-ii orientation. No build blocker until then.**

### UT-20: iCloud Shared List invitation acceptance is async out-of-band — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §5.7). Bootstrap §8 (lists auto-seed sub-step) emits `list.share_invited` events for each invited family member's Apple ID; bridges emit `list.shared` when their adapter sees the list appear post-acceptance. Pending shares surface in the inbox after bootstrap. The async pattern is unavoidable — Apple's iCloud Shared List acceptance happens out of band on each invitee's device, not via API. Final design lands at 11b refactor (Apple Reminders dual-deployment shape) and 16 refactor (bootstrap §8 lists auto-seed sub-step).

Status: **OPEN, resolves at 11b / 16 refactor. No build blocker until then.**

### UT-21: Reminders dual-deployment manifest declaration — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §3.1 + §5.4). Same adapter code, central variant (CoS Apple ID) + bridge variant (per member Apple ID). Pack manifest must declare `runtime: dual` and provide two deployment configurations. The manifest schema for dual-deployment adapters is established by prompt 11 (framework expansion) and exercised by 11b (Cat-B adapters cohort).

Status: **OPEN, resolves at 11 framework refactor. No build blocker until then.**

### UT-22: `reminder.*` events retired in favor of `list.*` / `list_item.*` — RESOLVED 2026-04-29-B by D18

Resolved by [D18] (lists first-class as 13th projection). PR-α-2 (merged 2026-05-04) corrected the `tasks` projection subscription line in `docs/architecture-summary.md` §4 row 3.5. The canonical event family for list state is `list.*` / `list_item.*`; the `tasks` projection's new subscription includes `list_item.promoted_to_task` to materialize promoted tasks per [D18] Corollary.

Status: **RESOLVED 2026-04-29-B.**

### UT-23: Cat-E reference implementation — RESOLVED 2026-04-29-B by D24

Resolved by [D24] (Home Assistant is the Cat-E reference implementation; multi-capability Cat-C state-read seam + Cat-E service-call seam; both ship in Phase A; service calls integrate with observation mode per [§6.20]). Closed by HA being added to Phase A scope as new prompt 11g (landed in PR-β-2 sequence rows).

Status: **RESOLVED 2026-04-29-B.**

### UT-24: L1 adapter inventory cleanup — RESOLVED 2026-04-29-B by D21+D22+D23+D24+D25

Eleven specific drifts dispositioned per memo §4: Twilio is Cat-E (D21); Apple Calendar dual-deployment in Phase A (D22); Apple Contacts bridge per-member + Google Contacts central (D23); Home Assistant Cat-C+E with full bidirectional + observation-mode integration (D24); Stelo/Lob/Privacy.com/CalDAV/Google Drive/iOS Shortcuts disposition (D25). All eleven drifts now reflected in the constitutional docs (PR-α-2) and the sequence table (PR-β-2).

Status: **RESOLVED 2026-04-29-B.**

### UT-25: Stelo / Dexcom CGM / health-telemetry as Cat-C adapter family — DEFERRED

Surfaced 2026-04-29-B by Amendment-2 (memo §1.3 + §4). NOT in Phase A scope. The five-category adapter framework (Phase A, prompt 11) supports Cat-C health-telemetry shape; a community pack post-Phase-A will add Stelo / Dexcom / Apple Health adapters when needed. Architectural placeholder noted in memo §4.

Status: **DEFERRED to v2 community pack. No build action.**

### UT-26: Multi-capability adapter manifest design — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §3.1 + [PM-35]). The manifest schema must support a list of capabilities, each with its own event-family scope and write-capability declaration. Reference cases: Notion (Cat-B + Cat-D), Home Assistant (Cat-C + Cat-E), Tesla (Cat-C + Cat-E), Apple Notes (Cat-D + Cat-B), Privacy.com (Cat-E + Cat-C). Final design at 11 framework refactor — install-time validator must check each capability's coherence independently.

Status: **OPEN, resolves at 11 framework refactor. No build blocker until then.**

### UT-27: Asymmetric write-capability manifest schema — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §3.1). The pack manifest's write-capability declaration is an **explicit per-operation list** (`create`, `update`, `delete`, `toggle_completion`, `add_item`, `reorder`, etc.) rather than a boolean `bidirectional`. Reference cases: Things 3 (read-only externally), Apple Notes-checklists (toggle_completion + add_item only per [D18]), iCloud Shared Photos (read-only on shared albums). Final design at 11 framework refactor — the validator must enforce that handler methods exist for each declared write capability per PM-34's "write_capabilities method-presence, signature check."

Status: **OPEN, resolves at 11 framework refactor. No build blocker until then.**

### UT-28: Apple Calendar adapter dual-deployment shape — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §6, [D22]). Apple Calendar dual-deployment shape parallel to Apple Reminders: central variant on assistant Apple ID + bridge variant per member Apple ID. EventKit access; emits `calendar_event.added@v1` / `.updated@v1` / `.cancelled@v1`; deduplication on `(external_id_kind = 'apple_calendar', external_event_id)`; sharing-model discriminator on each row (`private` | `shared_household` | `icloud_shared_calendar`). Sequence row landed in PR-β-2 (row 11b extended). Final design lands at 11b refactor.

Status: **OPEN, resolves at 11b refactor. No build blocker until then.**

### UT-29: Apple Contacts (bridge per-member) + Google Contacts (central) — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §7, [D23]). Apple Contacts adapter on bridge per-member (Contacts.framework against the member's iCloud); Google Contacts adapter central (People API against the assistant's Workspace). Both feed `parties.identifiers`. Closes the CRM-spine-empty-on-day-1 gap. Sequence row landed in PR-β-2 (new row 11e). Final design lands at 11e refactor.

Status: **OPEN, resolves at 11e refactor. No build blocker until then.**

### UT-30: HA adapter as Cat-C+E reference, multi-capability, full bidirectional — OPEN

Surfaced 2026-04-29-B by Amendment-2 (memo §8, [D24]). Home Assistant is the Cat-E reference implementation; multi-capability adapter (Cat-C state-read seam emitting `ha.state_changed@v1` + Cat-E service-call seam consuming `ha.service_call_requested@v1` and emitting `action.executed@v1` / `action.failed@v1` / `observation.suppressed@v1`). Both seams ship in Phase A. Service calls integrate with observation mode per [§6.20] — when `observation_mode = active`, the Cat-E seam emits `observation.suppressed` with the full would-have-sent payload and does NOT call HA's REST endpoint; the Cat-C seam is unaffected by observation mode (reading is not a side-effect). Sequence row landed in PR-β-2 (new row 11g). Final design lands at 11g refactor.

Status: **OPEN, resolves at 11g refactor. No build blocker until then.**

---

## Workflow norms

### Split QC and next-prompt-refactor into separate sessions when either is big

Small cases (prompts 03–05): QC + refactor one session is fine.

Big cases (07b onward): two sessions. Session 1 runs QC of latest merge, writes findings into this file, writes refactor brief for next prompt. Session 2 picks up brief and writes prompt.

Mirrors Claude Code's incremental-commit discipline: cap per-session cognitive load, make handoffs explicit.

### End every Chat session by updating this file

Under "Current build state": update "last updated" date, move merged prompts from "PR open" to "merged," update "next task queue."

Under "Prompt-writing decisions": add any new PM entry if surfaced. Tag HARD or SOFT.

Under "Open tensions": add any new UT; close any UT this merge resolved.

Rule: if a future Partner session would benefit from knowing this, write it here. If only relevant to current session, don't.

### Don't trust cached readings across sessions

The 9 constitutional docs are not cached between Chat sessions. Fresh instance hasn't read them. Every new session re-reads them in full in Step 1. Non-negotiable.

Similarly, don't trust your cached reading of the **codebase** across sessions. The zip is the source of truth for code state. If session N built code, session N+1 verifies it in the zip before referencing it.

### Always produce full file replacements for build_log.md and partner_handoff.md — HARD (James, 2026-04-28)

Partner does NOT produce patch instructions for `docs/build_log.md` or `docs/partner_handoff.md`. Ever. Always full and complete files ready to upload to repo as drop-in replacements for the current files. James pastes full files into the GitHub web UI per PM-24, or commits via local clone — but the artifacts Partner produces are always whole files.

This rule was made explicit by James 2026-04-28 after a Partner session produced a build_log patch when a full-file replacement was wanted. "Now and always" — bake into Partner discipline indefinitely.

---

## File layout quick reference

```
<repo root>/
├── ADMINISTRATEME_BUILD.md                      # constitutional (post-PR-α: §MACHINE TOPOLOGY adds adminme-bridge-{member}; §L1 "Three adapter runtimes"; §L5 Capture full rewrite + new Product E :3337 bridge; new top-level §MEMBER BRIDGES section; §BOOTSTRAP WIZARD §10)
├── ADMINISTRATEME_CONSOLE_PATTERNS.md           # constitutional
├── ADMINISTRATEME_CONSOLE_REFERENCE.html        # constitutional
├── ADMINISTRATEME_DIAGRAMS.md                   # constitutional (post-PR-α: §1 L1 ASCII bridge-side adapters; §2 second canonical example; §7 topology bridge shelf row)
├── ADMINISTRATEME_REFERENCE_EXAMPLES.md         # constitutional
├── ADMINISTRATEME_FIELD_MANUAL.md               # for James (not Partner)
├── README.md                                    # for James
├── docs/
│   ├── SYSTEM_INVARIANTS.md                     # constitutional (post-PR-α: §6.19 bridge sovereignty + §8.9 L1 two-place adapter family)
│   ├── DECISIONS.md                             # constitutional (D1–D17; D17 added 2026-04-29)
│   ├── architecture-summary.md                  # constitutional (post-PR-α: 12 projections; §9 5 products incl. :3337 bridge; §10 wizard §10; §11 #4 closed)
│   ├── openclaw-cheatsheet.md                   # constitutional
│   ├── build_log.md                             # LIVE: Claude Code's record
│   ├── partner_handoff.md                       # THIS FILE
│   ├── qc_rubric.md                             # companion
│   ├── universal_preamble_extension.md          # PM-7 proposal (EXECUTED)
│   ├── preflight-report.md                      # prompt 00's artifact
│   ├── 01-split-memo-10b.md                     # MERGED (Tier C split memo for 10b → 10b-i / 10b-ii)
│   ├── 02-split-memo-10b-ii.md                  # MERGED 2026-04-27 (Tier C secondary-split memo for 10b-ii → 10b-ii-α / 10b-ii-β)
│   ├── 03-split-memo-10c.md                     # MERGED 2026-04-28 (Tier C split memo for 10c → 10c-i / 10c-ii / 10c-iii)
│   ├── 04-architecture-amendment-knowledge-vaults-and-member-bridges.md  # MERGED 2026-04-29 (Tier C arch-amendment memo per PM-28; PR-α executed §4)
│   ├── 2026-04-25-prompt-08-split.md            # earlier Tier C split memo (08 → 08a / 08b)
│   ├── adrs/                                    # ADRs (longer form than DECISIONS entries)
│   ├── checkpoints/                             # checkpoint audit memos (Tier C — landed via partner-state PRs)
│   │   └── 07.5-projection-consistency.md       # MERGED 2026-04-25 (closes UT-1)
│   └── reference/                               # mirrored external docs
├── scripts/
│   ├── verify_invariants.sh                     # canonical invariant-grep (PM-7)
│   ├── demo_event_log.py
│   ├── demo_projections.py
│   ├── demo_xlsx_forward.py
│   └── demo_xlsx_roundtrip.py                   # added by 07c-β
├── prompts/
│   ├── PROMPT_SEQUENCE.md                       # CANONICAL (single copy; slim preamble; 10b row split via PR #37; 10b-ii row split via PR #39; 10c row split via `sequence-update-10c-split` PR; PR-β will land 11c row + modifications to 13b/14b/16 rows)
│   ├── 00-preflight.md ... 19-phase-b-smoke-test.md
│   ├── 07a-projections-ops-spine.md
│   ├── 07b-xlsx-workbooks-forward.md
│   ├── 07c-alpha-foundations.md                # MERGED (PR #20, 2026-04-24)
│   ├── 07c-beta-reverse-daemon.md              # MERGED (PR #21, 2026-04-25)
│   ├── 07.5-checkpoint-projection-consistency.md  # source contract; audit memo at docs/checkpoints/
│   ├── 08-session-scope-governance.md          # RETIRED (superseded by 08a + 08b)
│   ├── 08a-session-and-scope.md                # MERGED (PR #&lt;PR-08a&gt;, 2026-04-25)
│   ├── 08b-governance-and-observation.md       # MERGED (PR #&lt;PR-08b&gt;, 2026-04-25)
│   ├── 09a-skill-runner.md                     # MERGED (PR #29, 2026-04-26)
│   ├── 09b-first-skill-pack.md                 # MERGED
│   ├── 10a-pipeline-runner.md                  # MERGED (PR #33, 2026-04-26)
│   ├── 10b-reactive-pipelines.md               # RETIRED 2026-04-26 (PR #37; superseded by 10b-i + 10b-ii)
│   ├── 10b-i-identity-and-noise.md             # MERGED (PR #38, 2026-04-26; refactored 320-line prompt committed per PM-21)
│   ├── 10b-ii-alpha-commitment-extraction.md   # MERGED (PR #41, 2026-04-28; refactored 370-line prompt committed per PM-21)
│   ├── 10b-ii-beta-thank-you-detection.md      # MERGED (PR #&lt;PR-10b-ii-beta&gt;, 2026-04-28; refactored 330-line prompt committed per PM-21)
│   ├── 10c-proactive-pipelines.md              # RETIRED 2026-04-28 (sequence-update-10c-split PR; superseded by 10c-i + 10c-ii + 10c-iii)
│   ├── 10c-i-standing-orders-infra-and-reward-dispatch.md   # MERGED (PR #44, 2026-04-29; ~440-line prompt committed per PM-21)
│   ├── 10c-ii-morning-digest-and-paralysis-detection.md  # MERGED (PR #55, 2026-05-05; ~480-line prompt committed per PM-21)
│   ├── 10c-iii-reminder-crm-custody.md         # PENDING
│   ├── 11c-bridge-daemon-and-knowledge-adapters.md  # PENDING (new prompt landing in PR-β per memo §5.2; bridge daemon + knowledge-source adapters + 5 new event schemas at v1 + member_knowledge projection + bridge-ingest endpoint at :3337)
│   ├── d01-*.md ... d08-*.md                    # diagnostic prompts
│   ├── prompt-01a-openclaw-cheatsheet.md
│   ├── prompt-01b-architecture-summary.md
│   └── prompt-01c-system-invariants.md
├── adminme/
│   ├── events/{log,bus,envelope,registry}.py
│   ├── events/schemas/{ingest,crm,domain,governance,ops,system,messaging}.py    # domain.py extended 10c-i with RewardReadyV1 at v1; 10c-ii with DigestComposedV1 + ParalysisTriggeredV1 at v1
│   ├── projections/{base,runner}.py + 11 subdirs (10 sqlite + xlsx_workbooks); member_knowledge subdir lands in 11c
│   ├── daemons/                                 # PM-14: adapters/daemons that emit domain events
│   │   └── xlsx_sync/                           # populated by 07c: diff.py, sheet_schemas.py, reverse.py
│   ├── pipelines/                              # MERGED 10a (base.py, pack_loader.py, runner.py); 10b-ii-α extended PipelineContext + PipelineRunner with parties_conn_factory; pipeline PACKS live under packs/pipelines/ — UT-11 CLOSED 2026-04-26
│   ├── lib/instance_config.py
│   ├── lib/session.py                          # MERGED 08a (Session dataclass, 3 constructors + xlsx_reverse_daemon constructor)
│   ├── lib/scope.py                            # MERGED 08a (allowed_read, privacy_filter, coach_column_strip, child_hidden_tag_filter, ScopeViolation, CHILD_FORBIDDEN_TAGS)
│   ├── lib/governance.py                       # MERGED 08b (GuardedWrite three-layer; ActionGateConfig, RateLimiter, AgentAllowlist)
│   ├── lib/observation.py                      # MERGED 08b (outbound() single seam per [§6.13/§6.14]; ObservationManager default-on)
│   ├── lib/skill_runner/                       # MERGED 09a (wrapper.py, pack_loader.py)
│   └── (products, openclaw_plugins, cli, adapters — stubs or partial)
├── bridge/                                     # PENDING — lands in 11c per PM-29: bridge-side daemon + knowledge-source adapters (Apple Notes, Voice Notes, Obsidian, connector-pack slot) + ingest client to central :3337; separate Poetry project sharing event-schema models with adminme/ (UT-15)
├── tests/{unit,integration,fixtures,e2e}/      # tests/unit/{bootstrap,events,packs}/ added by 10c-i
├── console/  bootstrap/  packs/                 # packs/skills/{classify_thank_you_candidate, classify_message_nature, classify_commitment_candidate, extract_commitment_fields, extract_thank_you_fields}; packs/pipelines/{identity_resolution, noise_filtering, commitment_extraction, thank_you_detection, reward_dispatch}
│                                                # bootstrap/openclaw/{programs/<six>.md, cron.yaml, README.md} added by 10c-i per [D1] Corollary; packs/pipelines/{morning_digest, paralysis_detection} + packs/skills/compose_morning_digest added by 10c-ii; morning_digest.md + paralysis_detection.md program-prose rewritten by 10c-ii (3 stubs remain for 10c-iii)
└── pyproject.toml  poetry.lock  .gitignore
```

---

## End of handoff document

Partner: steps 1–6 before any real work. Orient before acting.
