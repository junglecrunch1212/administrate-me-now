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

1. **ADMINISTRATEME_BUILD.md** — the canonical build specification. Cite as `[BUILD.md §X]`.
2. **ADMINISTRATEME_CONSOLE_PATTERNS.md** — 12 console patterns. Cite as `[CONSOLE_PATTERNS.md §N]`.
3. **ADMINISTRATEME_DIAGRAMS.md** — 10 architecture diagrams. Cite as `[DIAGRAMS.md §N]`.
4. **ADMINISTRATEME_REFERENCE_EXAMPLES.md** — 7 worked examples. Cite as `[REFERENCE_EXAMPLES.md §N]`.
5. **ADMINISTRATEME_CONSOLE_REFERENCE.html** — interactive design reference. Skim for structure; read the specific sections the prompt you're working on touches.
6. **docs/SYSTEM_INVARIANTS.md** — 15 sections of binding invariants. Cite as `[§N]`.
7. **docs/DECISIONS.md** — D1 through D16+ decisions. Cite as `[DN]`.
8. **docs/architecture-summary.md** — five-layer model + the 11 projections table. Cite as `[arch §N]`.
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

**Last updated:** 2026-04-28 (10b-ii-β merged — reactive pipeline `thank_you_detection` + skill pack `extract_thank_you_fields@1.0.0`. Partner session of 2026-04-28 ran Type 2 QC on the merged 10b-ii-β. **Findings:** all clean / positive. (1) Contract check **Match-with-overshoot** — F-1 cosmetic: `packs/pipelines/thank_you_detection/handler.py:102` comment reads "five skill-error types plus the F-2 widening pair" while the tuple correctly contains all 7 types (5+2=7); no code defect; tuple itself is identical to `commitment_extraction`'s. F-2: shipped **21 new tests** (4 skill-pack + 1 pack-load canary + 13 handler-direct unit + 3 round-trip integration) vs. floor 15 — strong overshoot, mirrors 10b-ii-α's pattern, positive quality signal. Suite tally on `tests/` testpath: **464 → 480 passed, 2 skipped** (the +5 pack-internal tests live under `packs/` and run via explicit path per the per-commit verification commands). (2) Invariant audit **Clean** — `verify_invariants.sh` exit 0; `[§7.3]` (no projection direct writes — pipeline emits only via `ctx.event_log.append`), `[§7.4]` (skill calls via `ctx.run_skill_fn` per `[ADR-0002]`), `[§7.7]` (skill failure does NOT raise — emits `commitment.suppressed` with `reason="skill_failure_defensive_default"`), `[§8]`/`[D6]` (zero new SDK imports), `[§12.4]` (no tenant identity in platform code; placeholder member ids in `config.example.yaml`), `[§15]`/`[D15]` (parties-DB path resolved through `ctx.parties_conn_factory`; no hardcoded literals), `[D7]` (zero new event registrations — reuses `commitment.proposed` v1 + `commitment.suppressed` v1) all clean. F-5 carry-forward CLOSED: `thank_you_detection` does NOT subscribe to `messaging.sent` (trigger list is `[messaging.received]` only); the handler additionally early-returns at the top of `handle()` on any non-`messaging.received` event-type as defense-in-depth, with docstring citing the F-5 rationale. **`kind="other"` v1 disposition CONFIRMED**: `BUILD.md §1150` depth-read concluded the spec text reads as descriptive, not as requiring a new value in `CommitmentProposedV1.kind`'s Literal. The kind enum is therefore NOT extended; thank-you commitments emit with `kind="other"` and a `classify_reasons` array conveying the thank-you signals. **Urgency-vocabulary asymmetry handled cleanly**: the pipeline does not coerce/translate at the pipeline layer; `extract_thank_you_fields` produces the canonical urgency value, which round-trips into `CommitmentProposedV1` without coercion drift. (3) Job 2 **N/A** this session — refactor of the next prompt (10c) lives in the next Partner session per PM-23 (10c is itself a pre-split candidate per UT-13). **No new PMs surfaced; no new UTs surfaced.** UT-12 stays CLOSED (closed by 10b-ii-α 2026-04-28). PM-26 stays SOFT/proposed (post-PM-7 byte-budget calibration drift). Next refactor target: **10c orientation** with Tier C split-forecast in startup report.

**Last updated:** 2026-04-28 (10b-ii-α merged as PR #41 — reactive pipelines `commitment_extraction` + `classify_commitment_candidate@3.0.0` + `extract_commitment_fields@2.1.0` skill packs + `commitment.suppressed` event schema at v1 + parties-DB seam through `PipelineContext.parties_conn_factory`. Partner session of 2026-04-28 ran Type 1 combined session: QC of 10b-ii-α merge + refactor of 10b-ii-β. **Findings on 10b-ii-α merge:** all eight findings F-1 through F-8 are positive signals or accepted decisions; zero undershoots, zero violations, zero silent scope changes. (1) Contract check **Match-with-overshoot** — F-1: classify_commitment_candidate ships 4 unit tests (floor 3); F-2: commitment_extraction unit test file ships 11 tests (floor 8); these mirror 10b-i's pattern of test overshoots and are positive quality signals. (2) Invariant audit **Clean** — `verify_invariants.sh` exit 0; `[§7.3]` / `[§7.4]` / `[§8]` / `[D6]` / `[§7.7]` / `[D7]` / `[§12.4]` / `[§15]` / `[D15]` / `[ADR-0002]` all clean; pipeline→projection canary clean; causation-id wiring on every emit; F-2 carry-forward from 10b-i CLOSED defense-in-depth (both new skill packs declare `sensitivity_required: normal` but pipeline catches `SkillSensitivityRefused`/`SkillScopeInsufficient` anyway). F-3: refactored 10b-ii-α prompt committed to repo at `prompts/10b-ii-alpha-commitment-extraction.md` (370 lines) — second consecutive build-prompt round where on-disk file matches what Claude Code executed against, so PM-21 graduates from SOFT-watch to HARD convention. F-4: `_load_config` reads `config.example.yaml` directly via `_config_override` test seam — accepted as the simplest viable seam given prompt 16 will overhaul the config-loading path. F-5 (soft pattern → carry-forward to 10b-ii-β): defensive-default `except` list catches outbound `messaging.sent` events even though pipeline subscribes inbound-only; if 10b-ii-β subscribes both inbound and outbound (because thank-yous land both directions), handler must early-return on outbound at top of method. F-6: `receiving_member_id` derived from `to_identifier` directly — accepted (matches REFERENCE_EXAMPLES.md §2). F-7: pre-existing 2 ruff F401 errors in `docs/reference/plaid/python-sdk-plaid_api.py` (since PR #17, NOT introduced by this work). F-8 (soft → carry-forward to prompt 16): per-event YAML config read should be cached at runner-construction time. (3) Job 2 (refactor 10b-ii-β) **Complete** — refactored prompt at 330 lines / 39KB lands at `prompts/10b-ii-beta-thank-you-detection.md`; reuses 10b-ii-α's parties-DB seam, defensive-default exception tuple, per-member-overrides config skeleton; default `kind="other"` for thank-you `commitment.proposed` (do NOT silently extend `CommitmentProposedV1.kind`'s Literal — open question reserved if `BUILD.md §1150` strictly requires it); F-5 closure baked into the Read first as subscription discipline. (4) Job 3 delivery-gate self-check **Pass** — line count 330 under 350 ceiling; KB count 39 over 25KB but matches 10b-ii-α empirical precedent; PM-25 autolinker grep clean; PM-26 NEW (post-PM-7 byte-budget calibration drift in E-session-protocol §2.9). **PM-26 added (SOFT/proposed)**, **PM-21 graduates from SOFT-watch to HARD convention** (two consecutive merges 10b-i + 10b-ii-α both committed refactored prompt to repo). UT-12 CLOSED by 10b-ii-α merge. Next refactor target advances to **10c orientation** (proactive pipelines; pre-split candidate per UT-13).

**Last updated:** 2026-04-27 (Partner session produced secondary-split memo for 10b-ii). Type 3 → Type 0 session at James's direction. **No code touched** — output is `docs/02-split-memo-10b-ii.md` (Tier C secondary split per the watch flag in `docs/01-split-memo-10b.md` §10b-ii) plus a Claude Code micro-prompt for the sequence-update PR. **Verdict:** 10b-ii splits into **10b-ii-α** (parties-DB seam through `PipelineContext.parties_conn_factory` + `commitment_extraction` pipeline + `classify_commitment_candidate` + `extract_commitment_fields` skill packs + `commitment.suppressed` event schema at v1) and **10b-ii-β** (`thank_you_detection` pipeline + `extract_thank_you_fields` skill pack; reuses 10b-ii-α's seam, no new infrastructure). UT-12 resolution: **option (c)+(a)** — the split itself selects (c), and 10b-ii-α's Commit 1 wires the parties-DB seam through `PipelineContext` per option (a). UT-12 status: **OPEN → CLOSING (formally CLOSED upon sequence-update PR merge)**. **Sizing rationale:** 10b-ii at original split-memo scope plus UT-12 option (a) infrastructure exceeds the empirical one-session budget set by 09b/10b-i; 10b-ii-α is sized at ~450–500 lines / ~25–30 tests / 4 net-new modules + one infrastructure extension; 10b-ii-β is sized at ~250–300 lines / ~12–15 tests / 2 net-new modules. Both within Claude Code's empirically-known one-session window. **New PM-23** added (secondary splits when a primary-split watch flag fires) and **new UT-13** added (10c is next pre-split candidate). **Next refactor target:** 10b-ii-α (after the sequence-update PR merges). **Watch:** 10c is itself a pre-split candidate per `D-prompt-tier-and-pattern-index.md`; pre-split forecast goes in startup report when 10c orientation begins.

**Last updated:** 2026-04-26 (10b-i merged as PR #38 — reactive pipelines `identity_resolution` + `noise_filtering` + skill pack `classify_message_nature@2.0.0` + two new event schemas at v1 (`identity.merge_suggested`, `messaging.classified`). Partner session of 2026-04-27 ran Type 2 QC on the merged 10b-i. Findings: contract check Match-with-cosmetic-undershoot (F-1: BUILD_LOG claimed 24 tests vs actual 22; F-3: extra unit `test_exact_match_returns_without_emit` accepted positive; skill-pack 3 handler-direct cases vs 09b's 4 cosmetic). Invariant audit Clean. **F-2 (soft-watch for 10b-ii)**: `noise_filtering` except list omits `SkillSensitivityRefused` / `SkillScopeInsufficient`; safe today, must verify in 10b-ii. Next-prompt calibration on 10b-ii: Needs refresh — parties-DB seam load-bearing; UT-12 added. **F-4 (silent architectural decision, accepted)**: 10b-i shipped degenerate-clean per option (2). **PM-21 update**: refactored prompt committed to repo (first deviation from paste-only convention). UT-11 CLOSED (`packs/pipelines/<name>/`).

**Last updated:** 2026-04-26 (sequence-update PR #37 `sequence-update-10b-split` merged — splits prompt 10b into 10b-i and 10b-ii per the split memo at `docs/01-split-memo-10b.md`. Partner Type 2 QC: contract check Match; invariant audit Clean (zero code touched); next-prompt calibration **Needs refresh** (resolved by 10b-i merge — UT-11 CLOSED). PM-22 added (sequence updates and split-memo prep PRs are infrastructure, not build prompts).

**Last updated:** 2026-04-26 (sidecar PR #35 `sidecar-raw-data-is-manual-derived` merged — closes 07.5 finding C-1. Two-file change. Partner Type 2 QC: contract check Match; invariant audit Clean; next-prompt calibration Clean. UT-1 confirmed CLOSED.

This section is the live baton between sessions. Update it at the end of every Partner session.

**Prompts merged to main:** 00, 00.5, 01 (01a/01b/01c), 02, 03, 03.5, 04, 05, 06, 07a, 07b, **PM-7 infrastructure PR (slim preamble + scripts/verify_invariants.sh)**, **07c-α (PR #20, merged 2026-04-24)**, **07c-β (PR #21, merged 2026-04-25 — reverse daemon class + 4 emit pathways + integration round-trip; closes the xlsx round-trip and resolves UT-6)**, **08a (PR #&lt;PR-08a&gt;, merged 2026-04-25 — Session model + scope enforcement; 48 TODO(prompt-08) markers cleared across 10 sqlite projection queries.py files; 69 new tests; resolves UT-8 inline via `vector_search.nearest` three-layer carve-out)**, **08b (PR #&lt;PR-08b&gt;, merged 2026-04-25 — guardedWrite three-layer + observation `outbound()` + 6 governance event types at v1; 47 new tests + 4 security-E2E + 1 UT-7 closure case; resolves UT-7 — `_ACTOR` literal removed from reverse daemon, sidecar hedge NOT activated)**, **09a (PR #29, merged 2026-04-26 — skill runner wrapper around OpenClaw `/tools/invoke` + `llm-task`; 30 new tests; ADR-0002 schema relaxation folded in per PM-19; `verify_invariants.sh` extended with `skill.call.*` single-seam check)**, **09b (PR #&lt;PR-09b&gt;, merged &lt;merge-date-09b&gt; — first canonical skill pack `classify_thank_you_candidate` v1.3.0; 8 new tests (4 unit + 4 integration); `bootstrap/pack_install_order.yaml` queued for prompts 15/16; zero domain events, zero `verify_invariants.sh` edits — pure wrapper-consumer)**, **10a (PR #33, merged 2026-04-26 — pipeline runner per BUILD.md §L4; `adminme/pipelines/{base,pack_loader,runner}.py` + `tests/fixtures/pipelines/{echo_logger,echo_emitter}/` + 17 new tests (8+5 unit + 4 integration); pipeline→projection canary armed and clean; `PipelineContext` threads `Session` + `run_skill_fn` + `outbound_fn` + `guarded_write` + `observation_manager` + `triggering_event_id` + `correlation_id`; reactive-only — proactive packs skipped during `discover()` per UT-2 carve-out, OpenClaw standing-order registration deferred to 10c; zero new event-schema registrations)**, **10b-i (PR #38, merged 2026-04-26 — reactive pipelines `identity_resolution` (heuristic-only, degenerate-clean candidate-loader) + `noise_filtering` (skill-call seam to `classify_message_nature`); skill pack `classify_message_nature@2.0.0` (full 09b shape); two new event schemas at v1 (`identity.merge_suggested`, `messaging.classified`); 22 new tests (3 + 1 + 8 + 1 + 5 + 4); suite 423 → 447 passed; `verify_invariants.sh` exit 0; UT-11 closed; refactored prompt committed to repo at `prompts/10b-i-identity-and-noise.md`, 320 lines — first deviation from PM-21's paste-only convention)**, **10b-ii-α (PR #41, merged 2026-04-28 — reactive pipelines `commitment_extraction` (parties-DB seam wired through `PipelineContext.parties_conn_factory`) + skill packs `classify_commitment_candidate@3.0.0` + `extract_commitment_fields@2.1.0` (full 09b shape); one new event schema at v1 (`commitment.suppressed` with closed `Literal["below_confidence_threshold", "dedupe_hit", "skill_failure_defensive_default"]`); 26 new tests on `tests/` testpath + 9 pack-internal tests via explicit path; suite 447 → 464 passed, 2 skipped; F-2 carry-forward CLOSED defense-in-depth; UT-12 CLOSED; `verify_invariants.sh` exit 0; refactored prompt committed to repo at `prompts/10b-ii-alpha-commitment-extraction.md`, 370 lines — second consecutive deviation from PM-21's paste-only convention; PM-21 graduates from SOFT-watch to HARD convention this round)**, **10b-ii-β (PR #&lt;PR-10b-ii-beta&gt;, merged &lt;merge-date-10b-ii-beta&gt; — reactive pipeline `thank_you_detection` (reuses 10b-ii-α's parties-DB seam through `ctx.parties_conn_factory`; calls `classify_thank_you_candidate@^1.3.0` from 09b unchanged → `extract_thank_you_fields@^1.0.0` new this prompt → emits `commitment.proposed` with `kind="other"` or `commitment.suppressed`; F-5 carry-forward CLOSED via `messaging.received`-only trigger list + defense-in-depth early-return at top of `handle()`); skill pack `extract_thank_you_fields@1.0.0` (full 09b shape; output `urgency` enum matches `CommitmentProposedV1.urgency`'s Literal exactly so the pipeline round-trips without coercion drift); zero new event schema registrations — reuses `commitment.proposed` v1 (existing) + `commitment.suppressed` v1 (registered in 10b-ii-α); 21 new tests (4 skill + 1 pack-load + 13 handler-direct unit + 3 integration); suite 464 → 480 passed, 2 skipped; `verify_invariants.sh` exit 0; refactored prompt committed to repo at `prompts/10b-ii-beta-thank-you-detection.md`, 330 lines per PM-21; `kind="other"` v1 disposition CONFIRMED — `CommitmentProposedV1.kind` Literal NOT extended)**.

**Sequence updates merged (infrastructure, not build):** **PR #37 `sequence-update-10b-split` (merged 2026-04-26)** — splits 10b into 10b-i / 10b-ii per `docs/01-split-memo-10b.md`. **PR #39 `sequence-update-10b-ii-split` (merged 2026-04-27)** — splits 10b-ii into 10b-ii-α / 10b-ii-β per `docs/02-split-memo-10b-ii.md`; surfaced PM-24 (long static markdown via GitHub web UI) and PM-25 (markdown autolinker defense). **PR #40 `update-partner-handoff` (merged 2026-04-27)** — partner-state snapshot lands `docs/partner_handoff.md` updates (PM-23/24/25 + UT-12 CLOSING + UT-13 + next-task-queue advance). All three are single-purpose infrastructure PRs per PM-22 — no four-commit discipline, no BUILD_LOG entries, no tests.

**Checkpoints landed:** **07.5 (`docs/checkpoints/07.5-projection-consistency.md`, 2026-04-25)** — projection consistency audit across the 07a/07b/07c-α/07c-β cohort plus L1-adjacent reverse daemon. Verdict: PASS with 1 non-critical finding (C-1: Raw Data builder `ALWAYS_DERIVED` missing `is_manual` while descriptor `always_derived` includes it; deferred to sidecar PR `sidecar-raw-data-is-manual-derived`). UT-1 closes here.

**Prompts with PR open, not yet merged:** none on the build-prompt cohort. (10b-ii-β prep PR + Claude Code build PR both merged 2026-04-28.)

**Prompts drafted, ready for Claude Code execution:** none. The next refactor target is **10c**, currently still in pre-PM-7 unrefactored shape on disk; the **next Partner session** opens 10c orientation with a Tier C split-forecast in the startup report per PM-23 (UT-13 pre-flags 10c as a split candidate).

**Sidecar PRs queued (non-blocking):** none. Most recent sidecar `sidecar-raw-data-is-manual-derived` merged as PR #35 on 2026-04-26 (closed 07.5 finding C-1). Most recent sequence updates `sequence-update-10b-split` (PR #37, 2026-04-26), `sequence-update-10b-ii-split` (PR #39, 2026-04-27), and partner-state snapshot `update-partner-handoff` (PR #40, 2026-04-27) all merged. Recorded here so future Partner sessions see the full PR landscape.

**Next task queue (in order):**

1. **James: drive partner-state snapshot prep PR for this session's QC results.** Single-purpose PR per PM-22 — no four-commit discipline, no BUILD_LOG, no tests. Two changes in one commit: replace `docs/partner_handoff.md` with the updated full file (this file); replace `docs/build_log.md` with the updated full file (with `<PR-10b-ii-beta>` / `<sha1-10b-ii-beta>` … `<sha4-10b-ii-beta>` / `<merge-date-10b-ii-beta>` placeholders find-and-replaced from `gh pr list --state merged --limit 10 --json number,title,mergedAt,mergeCommit` first). **No file deletions.**

2. **Partner session: 10c orientation + refactor (or split-memo).** Type 1 combined session OR Type 0 session, depending on whether Partner's startup report concludes 10c is a split candidate. **UT-13 pre-flags 10c as such.** Per PM-23, the Partner session that opens orientation on a pre-split-flagged prompt forecasts the split in the startup report **before** drafting any refactored prompt content. If split, output is a Tier C split memo at `docs/03-split-memo-10c.md` plus a Claude Code micro-prompt for the sequence-update PR; James then drives the sequence-update PR, then a subsequent Partner session refactors the first sub-prompt. If NOT split (unlikely given UT-13), output is a refactored 10c prompt.

   10c covers proactive pipelines (`morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, plus possibly `recurrence_extraction`, `closeness_scoring`, `relationship_summarization`); split shape will depend on whether grouping is by trigger mechanism (reactive vs proactive — natural coupling to OpenClaw standing-order registration) or by capability axis. **UT-2 must be resolved before 10c can be refactored** — the AGENTS.md-prose + `openclaw cron add` registration path is currently underspecified.

3. **Claude Code session: execute next 10c sub-prompt.** Following the sequence-update PR (if split) or directly (if not split). Four-commit discipline per PM-2.

4. **Partner session: QC of 10c first sub-prompt merge + next 10c sub-prompt orientation.** Continuing through prompt 18 (Phase A build-complete), then 19 (Phase B smoke test).

**Pre-merge verification James should run before committing any PR with placeholders:**

```bash
gh pr list --state merged --limit 10 --json number,title,mergedAt,mergeCommit
```

…and find-and-replace `<PR-09b>`, `<commit4-09b>`, `<merge-date-09b>` in `docs/build_log.md` with the actual values. Same applies to the still-pending `<PR-08a>` / `<PR-08b>` / `<sha1-08a>` etc. placeholders inherited from prior sessions, AND the `<PR-10b-ii-beta>` / `<sha1-10b-ii-beta>` / `<sha2-10b-ii-beta>` / `<sha3-10b-ii-beta>` / `<sha4-10b-ii-beta>` / `<merge-date-10b-ii-beta>` placeholders introduced this round. (PR #38 / commits 22c6195 / 73880d4 / 4c19c80 / 0a3250f / 2026-04-26 are the 10b-i values, already filled in. PR #41 / commits a8e1e09 / 2995d13 / 5e37a27 / 8671d06 / 2026-04-28 are the 10b-ii-α values, already filled in.)

**Prompts drafted but not yet refactored:** 10c, 10d, 11, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19. (10a, 10b-i, 10b-ii-α, 10b-ii-β moved to "merged to main"; 10b retired per PR #37; 10b-ii retired per PR #39.) The slim preamble means each refactor is shorter than 07a/07b were. 15, 16 remain pre-split candidates; per `D-prompt-tier-and-pattern-index.md`, 10c, 11, 14b, 17 are also pre-split candidates.

**Note on on-disk vs. shipped 10b-ii-β (PM-21 confirmation):** Third consecutive build-prompt round (10b-i PR #38 / 10b-ii-α PR #41 / 10b-ii-β this round) where the refactored prompt ships in the build PR. The on-disk file at `prompts/10b-ii-beta-thank-you-detection.md` (330 lines) is the canonical record of what Claude Code executed against. PM-21 graduation (2026-04-28) holds firmly. The `prompts/10a-pipeline-runner.md` file on main remains the unrefactored 90-line v1 draft from the historical paste-only era and is NOT a blocker — historical and out of scope for current work.

**Prompts not yet drafted:** 10c onward exists in unrefactored form.

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

Existing examples of the same pattern: 01 → 01a/01b/01c (architecture + cheatsheet + invariants), 07 → 07a/07b/07c-α/07c-β (ops projections + xlsx forward + xlsx round-trip foundations + xlsx reverse daemon), **10b → 10b-i/10b-ii (identity+noise / commitments+thank-you, per PR #37 sequence update 2026-04-26), then 10b-ii → 10b-ii-α/10b-ii-β (parties-DB seam + commitment_extraction / thank_you_detection, per PR #39 sequence update 2026-04-27)**.

PM-15 supersedes the implicit assumption that every numbered prompt fits one session. PM-2 (four-commit discipline) is per-PR, not per-prompt-number; a split prompt ships two PRs of four commits each.

### PM-16: Descriptor public-API discipline — SOFT

07c-α landed sheet descriptors as private symbols (`_TASKS`, `_COMMITMENTS`, `_RECURRENCES`, `_RAW_DATA`) accessible only through `descriptor_for(workbook, sheet)`, `editable_columns_for(descriptor, row)`, and the `BIDIRECTIONAL_DESCRIPTORS` tuple. The original 07c-β draft Read-first referred to them as `TASKS_DESCRIPTOR` etc. — symbols that don't exist. Neither approach is wrong on its own; the drift is the issue.

Partner discipline: when prompt N specifies module API surface, and prompt N+1 consumes that surface, prompt N+1's depth-read at refactor time must verify symbol names against what landed, not against what prompt N's draft text said would land. The Read-first block of prompt N+1 cites import paths AND symbol names; both are checked.

When the consumer prompt expects public symbols and the producer shipped private ones, either prompt N+1's refactor uses the public accessor (`descriptor_for`) or a single-purpose follow-on PR re-exports the symbols. 07c-β chose the accessor approach (cheaper; no module re-edit needed).

### PM-17: Single-seam enforcement invariants verified by exclusion-grep — HARD

Surfaced by 08b QC. When an invariant takes the form "X must only ever happen at one place" — e.g. [§6.13/§6.14] "every outbound call goes through `outbound()` in `lib/observation.py`; emitting `external.sent` anywhere else is a bug" — the QC verification is an exclusion-grep, not an inclusion-check. Pattern: `grep -rnE "log\.append.*external\.sent|log\.append.*observation\.suppressed" adminme/lib/ adminme/products/ adminme/projections/ adminme/daemons/ adminme/pipelines/` must return zero hits outside the single seam (`adminme/lib/observation.py`). The same pattern applies to UT-7 closure (`_ACTOR` literal grep returns 0), [§2.2] (only allowed projections emit, only allowed system events), and [§15] (no `~/.adminme` literals outside fixtures).

These are already mechanized in `scripts/verify_invariants.sh` for the four invariants the script covers. PM-17 generalizes the pattern: any time a future prompt introduces a new "this happens at exactly one place" rule, the prompt's Commit 4 verification block adds the exclusion-grep AND adds the canary to `verify_invariants.sh`'s permanent set. Future PMs that surface another single-seam invariant should expect to extend the script rather than duplicate the grep inline.

The 08b refactor did NOT extend `verify_invariants.sh` for `external.sent` / `observation.suppressed` — the canary lives in `tests/unit/test_observation.py` instead. This is acceptable because the test asserts the seam directly (it imports `outbound` and confirms it's the only function emitting these types in observation.py's namespace), but a future Partner session may decide to lift it into the script for cross-codebase enforcement. Track: candidate for `verify_invariants.sh` extension when 09a or 11+ adds the next outbound-callable subsystem.

### PM-19: Schema/contract conflicts surfaced during refactor go in the prompt's Commit 1, not in a separate sidecar — when the prompt activates the seam — HARD

A prompt that introduces a new external seam (09a is the first AdministrateMe→OpenClaw HTTP seam) will surface contract drift between merged stub schemas and the actual contract the prompt is about to honor. When the drift is field-shape only (no schema-version bump, no callers to migrate), fold the fix into the prompt's Commit 1 with explicit citation in the schema docstring. Spinning a separate sidecar is correct when the drift affects merged code that's already in flight elsewhere; folding is correct when the prompt itself is the first consumer of the corrected shape. The 09a refactor demonstrates the folding path: `SkillCallRecordedV2.input_tokens` (and friends) was registered as required `int` but ADR-0002 mandates `int | None` — fix lands in 09a Commit 1 because 09a is the first emitter. PM-19 generalizes: future prompts that introduce a seam must check the merged stub against the contract and fold any field-shape drift into Commit 1, not a separate PR.

### PM-20: When a wrapper introduces an HTTP seam to an external service, the test pyramid mocks the HTTP layer with `httpx.MockTransport` rather than `respx` — SOFT

Surfaced in 09a. Prompt 09a specified "use respx (or httpx.MockTransport)." Claude Code's tests used `httpx.MockTransport` directly (subclassed to record requests for body-shape assertions). `respx` was added to dev deps but unused. Both are acceptable; `httpx.MockTransport` is slightly closer to the underlying library and avoids a dep when the test only needs request recording + canned responses. PM-20: future "wrapper around external service" prompts can either (a) prefer `httpx.MockTransport` directly and drop `respx` from the dep list, or (b) standardize on `respx` for fluent route assertions. Decision deferred to first 11+ adapter prompt that adds an HTTP wrapper; 09a left both available.

### PM-21: Refactored prompts ship in the build PR — HARD (graduated 2026-04-28)

Surfaced in 10a QC (2026-04-26) as SOFT in flux. Historical pattern (07a–10a era) was paste-only — Partner's refactored text held in chat, James pasted into Claude Code without committing to repo. Trade-off: the on-disk file lagged what Claude Code actually executed against, but the build_log entries served as durable QC record. **Two consecutive build-prompt rounds graduated the convention to HARD; a third round confirms it:**

- **10b-i (PR #38, merged 2026-04-26)** — refactored 320-line prompt committed to repo at `prompts/10b-i-identity-and-noise.md` as part of the build PR. First deviation from paste-only.
- **10b-ii-α (PR #41, merged 2026-04-28)** — refactored 370-line prompt committed to repo at `prompts/10b-ii-alpha-commitment-extraction.md` as part of the build PR. Second consecutive deviation; convention graduates.
- **10b-ii-β (PR #&lt;PR-10b-ii-beta&gt;, merged 2026-04-28)** — refactored 330-line prompt committed to repo at `prompts/10b-ii-beta-thank-you-detection.md` as part of the build PR. Third consecutive deviation; convention firmly held.

Going forward, **refactored prompts ship in the build PR**. The on-disk file is the canonical record of what Claude Code executed against, providing (a) file-on-disk matches build_log evidence, (b) permanent QC artifact for future Partner sessions to depth-read, (c) a single source of truth for "what shipped in this PR" — at the cost of one extra committed file per build PR (negligible). The historical paste-only files (07a through 10a) remain as unrefactored v1 drafts on disk; not blocking, not in scope for any current work, and explicitly out of scope for backfill. Partner sessions producing prompts going forward expect Claude Code to commit the refactored prompt as part of Commit 1 of the build PR, alongside whatever other Commit 1 work the prompt specifies.

### PM-22: Sequence-update and split-memo prep PRs are infrastructure, not build prompts — HARD

Surfaced by PR #37 (`sequence-update-10b-split`, merged 2026-04-26). When Partner approves a Tier C split, James drives a Claude Code session that lands a single-purpose PR updating `prompts/PROMPT_SEQUENCE.md` (sequence table + dependency graph + hard-sequential-dependency line) and deleting the now-superseded source draft. These PRs:

- **Have no four-commit discipline.** Single commit. Per PR #37's transcript: "Single-purpose infrastructure PR. No four-commit discipline. No BUILD_LOG. No tests. Just file edits."
- **Have no BUILD_LOG entry by design.** They are not build prompts — they do not ship code, do not register events, do not modify projections/pipelines/adapters. They modify Partner's planning artifacts. Recording them in build_log's main ledger would dilute the build-prompt cohort.
- **DO get noted in the "Sequence updates merged" subsection of partner_handoff.md's Current build state** so future Partner sessions see the full PR landscape.
- **DO get a one-paragraph trace in build_log.md's "Sidecar PRs" section** with the same formatting other infrastructure PRs use, but explicitly tagged as a sequence update rather than a sidecar to keep the categorization clean.
- **Are NOT sidecars in the PM-15 sense** (sidecar = defect-fix in already-merged code). Sequence updates are forward-looking planning artifacts; they create the conditions for the next refactor session to proceed.

The same convention applies to:
- `D-prompt-tier-and-pattern-index.md` updates (which live in Partner setup, NOT in repo per James's split-memo instruction — handled out-of-band by James after the sequence PR merges).
- Future split memos that ship as `docs/01-split-memo-NN.md` style files (the on-disk record of the Partner's Tier C decision).
- Partner-state snapshot PRs that update `docs/partner_handoff.md` and/or `docs/build_log.md` outside a build session (PR #40 demonstrated this; the same pattern applies to any future "re-sync repo state to what's been Project-knowledge-uploaded" PR).

PM-22 distinguishes these from the build-prompt cohort that has full ledger entries and BUILD_LOG appends in Commit 4.

### PM-23: Secondary splits are normal when a primary-split sub-prompt's "watch flag" condition fires — HARD

Surfaced by `docs/02-split-memo-10b-ii.md` 2026-04-27. The original 10b primary split (`docs/01-split-memo-10b.md`, 2026-04-26) flagged 10b-ii as a watch — "fits one Claude Code session — barely. … If depth-read at refactor time reveals it's still too big, 10b-ii itself becomes a candidate for 10b-ii-α / 10b-ii-β." The Partner session of 2026-04-27 hit exactly that condition: the original 10b-ii scope plus UT-12's parties-DB seam infrastructure exceeded the empirical one-session budget.

**The discipline:** when a primary-split memo carries a "watch" flag for one of its sub-prompts, the Partner session that opens orientation on that sub-prompt **forecasts the secondary split in the startup report** before drafting any refactored prompt. Drafting a single sub-prompt and then splitting it at §2.9 wastes the session — the same failure mode init prompt §11.5 warns about for primary splits, applied recursively.

**Numbering convention:** secondary splits use Greek-letter suffixes on the primary-split tag, mirroring the 07c-α/β precedent. So 10b-ii becomes 10b-ii-α and 10b-ii-β. Tertiary splits (if ever needed) would extend with Roman numerals or numeric suffixes — but if a sub-prompt is heading there, the more important question is whether the build is decomposing correctly or fighting against natural cohesion.

**On-disk record:** secondary-split memos use the next ordinal in `docs/NN-split-memo-<original-prompt>.md`. The 10b-ii split is `docs/02-split-memo-10b-ii.md` (the 02- ordinal mirrors that 01- was the primary 10b split).

**Sequence-update PR pattern:** identical to PM-22 (single-purpose, no BUILD_LOG, no tests, just sequence-table row replacement + dependency-graph update + hard-sequential-dependency line update + the new memo file). The `D-prompt-tier-and-pattern-index.md` update is out of band per PM-21/PM-22.

### PM-24: Long static markdown files land via GitHub web UI, not Claude Code `create_file` — HARD

Surfaced 2026-04-27 by three consecutive Claude Code timeouts attempting to write `docs/02-split-memo-10b-ii.md` (133 lines) from inside a sequence-update session. Attempt v1 had the memo body as an external paste-block alongside the prompt; attempt v2 inlined the body verbatim between `BEGIN_MEMO` / `END_MEMO` sentinels in the prompt itself; both died at the `create_file` tool call. The pattern was specific enough — same operation, same point in the work order, two different framing strategies — to declare it a class problem rather than a one-off. v3 routed around it: James created the file via GitHub's "Add file → Create new file" web UI directly on the working branch, then a fresh Claude Code session did only the surgical `str_replace` edits + commit/push/PR. The PR (#39, opened 2026-04-27) landed cleanly on the first try.

**The discipline:** when an infrastructure PR adds a long static markdown file (split memos, checkpoint reports, ADRs, anything > ~80 lines of pure prose with no code synthesis required), James creates the file via GitHub's "Add file → Create new file" web UI directly on the working branch. Claude Code's role is reduced to surgical `str_replace` edits in existing files plus the `git add` / `commit` / `push` / `gh pr create` (or MCP fallback) sequence. Two commits on one branch is fine — the single-purpose-PR rule is about scope and intent, not commit count.

**What this rule does NOT cover:** new code files (skill-pack `handler.py`, pipeline-pack handler, projection `queries.py`, test files). Those are still Claude Code's job — they require code synthesis, citation discipline, and contract-aware authorship that a web-UI paste cannot provide. The web-UI-paste path is for prose-only artifacts where Partner has already produced the canonical text and Claude Code's role would otherwise be a slow scribe.

**The branch-naming wrinkle.** When James creates a working branch via web UI for the file commit, then opens Claude Code for the surgical edits, the harness may reassign Claude Code's session to a NEW branch name even though Claude Code is told to check out the existing one. PR #39 demonstrated this: James prepared `claude/update-10b-ii-sequence-UkWwe` via web UI, but Claude Code's session was assigned to `claude/finish-10b-ii-split-VlBFQ` and that latter branch is the one that carried the web-UI memo commit (because James had pre-populated it). The Claude Code prompt under PM-24 must include the fallback `git fetch origin && git branch -a | grep -iE "<pattern>"` to discover whatever branch actually carries the memo commit, and check that out instead of the one James originally typed in.

**Cost / benefit.** Cost: ~60 seconds of paste-and-commit work for James in the GitHub web UI. Benefit: zero session-timeout risk on the slow operation; zero burned Claude Code sessions; clean PR on the first try. PM-24 is an admission that Claude Code's `create_file` for long markdown is not currently a reliable seam, and that routing around it is cheaper than fighting it.

### PM-25: Markdown autolinker defense for paste-targeted artifacts — HARD

Surfaced 2026-04-28 by James reporting that the prep-PR Claude Code micro-prompt for 10b-ii-α arrived at the Claude Code session with autolinker artifacts: bare filenames like `02-split-memo-10b-ii.md` had been transformed into `[02-split-memo-10b-ii.md](http://02-split-memo-10b-ii.md)` by the chat client's markdown-aware renderer. The transform happens because `.md` is a valid TLD (Moldova); same failure mode hits `.io`, `.co`, `.sh`, `.py`, `.yaml`, `.yml`, `.json`, `.toml`, `.html`. Claude Code receives malformed content and either fetches nonexistent URLs or treats the bracketed text as hyperlinks instead of paths. Silent failure mode that wastes the Claude Code session.

**The two-belt rule.** Every Partner artifact destined to be pasted into Claude Code applies BOTH defenses:

- **Belt 1 — backtick every bare filename in prose.** Outside fenced code blocks, any filename or dotted-path token gets single backticks: `prompts/10b-i-identity-and-noise.md`, `scripts/verify_invariants.sh`, `pyproject.toml`, `BUILD.md` §L4.
- **Belt 2 — fence every command line.** All `git`, `gh`, `bash`, `poetry`, `mcp__github__*`, `sed -n`, `grep`, `cat <<EOF` lines live inside triple-backtick fenced blocks, never in prose.

**Mandatory grep pass at §2.9.** The Job 3 delivery-gate self-check now has a seventh item: Partner runs (or simulates) the autolinker grep against every draft and reports the result before the artifact ships. The grep:

```
grep -nE '[^`]([a-zA-Z0-9_./-]+\.(md|sh|py|yaml|yml|json|toml|io|co|html))[^`]' <draft>
```

Zero matches = autolinker-safe. Any matches must be wrapped in backticks or moved into a fenced block before the artifact ships.

**Distribution discipline.** Refactored prompts, prep-PR micro-prompts with embedded prompt bodies, sequence-update micro-prompts, sidecar prompts — anything James will paste into Claude Code — are produced as downloadable files via the file-creation tool, NOT as inline chat content. James downloads the file, opens in a plain text editor, copies from there into Claude Code, bypassing the chat client's renderer entirely. Inline chat content is acceptable only for short artifacts where the render-then-copy pathway is confirmed safe (paragraph-level guidance, BUILD_LOG entry templates James commits via text editor anyway, `partner_handoff.md` update fragments).

The full discipline lives in `E-session-protocol.md` §2.10 (the rule itself + the §2.9 delivery-gate item) and `C-context-loading-spec.md` ("Artifact production discipline" section). Both Partner setup files were updated in the same Project-knowledge refresh that introduced this PM. Future Partner sessions are expected to apply both belts on every artifact, run the §2.9 item-7 grep, and ship paste-targeted artifacts as downloadable files.

**Canonical failure case:** the 2026-04-28 prep-PR micro-prompt for 10b-ii-α — the artifact looked clean in the chat UI source view but rendered with autolinker brackets when copied. Fix shipped same-day: `E-session-protocol.md` §2.10 + `C-context-loading-spec.md` artifact-production section + this PM-25 entry.

### PM-26: Post-PM-7 byte-budget calibration drift in E-session-protocol §2.9 — SOFT (proposed)

Surfaced 2026-04-28 during Job 3 delivery-gate self-check on the 10b-ii-β refactored prompt. The gate's per-prompt 25KB ceiling was calibrated against pre-PM-7 prompts (07a, 07b at ~600 lines / 25KB) when each prompt re-stated its own preamble. Post-PM-7 (slim universal preamble + `scripts/verify_invariants.sh`), refactored prompts should be smaller per PM-12, and they are by line count — but per-character byte size has not collapsed proportionally because each prompt now needs richer Read-first citations (filenames, line ranges, symbol names) that the preamble removed from boilerplate.

**Empirical evidence:**

- 10b-i refactored prompt: 320 lines / ~37KB.
- 10b-ii-α refactored prompt: 370 lines / ~42KB.
- 10b-ii-β refactored prompt: 330 lines / ~39KB.

All three exceed the 25KB ceiling. All three are within the 350-line ceiling (10b-ii-α exceeds slightly but was accepted by James as scoped to its multi-pack scope). All three QC-validated cleanly through Claude Code with zero session-window-overrun observations.

**Disposition (proposed for Tier C decision):**

The 25KB ceiling is artifact-of-calibration drift. The line-count ceiling (350) remains the binding signal of session-budget pressure; KB ceiling tracks the same dimension less reliably post-PM-7 because the line-cost-per-fact has dropped while character-cost-per-fact has grown. Two options:

- **(a) Recalibrate the byte ceiling upward** (proposal: 50KB) to match post-PM-7 empirical prompt sizes. Keeps the dual-signal gate but at properly-calibrated thresholds.
- **(b) Demote byte ceiling to a SOFT signal**, retain line-count as the HARD ceiling, and add a session-window canary (e.g., "if Partner can articulate the prompt's scope in three sentences, it fits one session" — qualitative but cheap).

PM-26 status: **proposed; Partner sessions through 10c apply line-count as the binding gate and treat byte size as informational only.** Decision can be taken at any future Type 0 (workflow-only) Partner session by James in consultation with Partner; not blocking any current build work. **10b-ii-β confirms the empirical pattern**: 330 lines / 39KB shipped cleanly through Claude Code, zero session-window-overrun observations, suite green on first attempt.

---

## Open tensions / unresolved things

### UT-1: When to refactor checkpoint 07.5 — CLOSED 2026-04-25

Original 07.5 assumed 11 projections in one prompt. After 07a/07b/07c-α/07c-β split, it audits four ops prompts together as a cohort. Audit landed at `docs/checkpoints/07.5-projection-consistency.md` on 2026-04-25 post-07c-β merge. Verdict PASS with one non-critical sidecar finding (C-1, queued as `sidecar-raw-data-is-manual-derived`). Status: **CLOSED**.

### UT-2: Proactive pipeline registration path (10c)

`[D1]` confirmed: proactive pipelines register via workspace-prose `AGENTS.md` + `openclaw cron add`. Prompt 10c will generate both. Concrete question: does bootstrap §8 concatenate per-pipeline markdown into AGENTS.md and issue cron adds, or ship AGENTS.md pre-written? Answer lands when 10c is refactored. **Now blocking 10c orientation per next-task-queue item 2.**

### UT-3 (RESOLVED 2026-04-25): Prompt 08 split executed

Prompt 08 split into **08a (Session + scope, read side)** and **08b (governance + observation + UT-7 closure, write side)**. The architectural decision is recorded at `docs/2026-04-25-prompt-08-split.md` (the on-repo split memo from a prior Partner session). The `prompts/PROMPT_SEQUENCE.md` sequence-table and dependency-graph updates landed in an earlier commit. The `split-08-2026-04-25` PR closed the gap by landing `prompts/08a-session-and-scope.md` and `prompts/08b-governance-and-observation.md` and updating this handoff state.

The 60 attention sites catalogued by the 07.5 audit (48 explicit `# TODO(prompt-08)` markers across 10 sqlite projection `queries.py` files + 12 implicit attribution sites in `adminme/daemons/xlsx_sync/reverse.py`) split: 48 to 08a (projection query integration), 12 to 08b (reverse-daemon attribution). Status: **RESOLVED**. UT-7 carries forward into 08b (or 08.5 if the reverse-daemon rewrite triggers the sidecar hedge per 08b's Commit 3).

### UT-4: Placeholder values in xlsx protection passwords

07b uses `"adminme-placeholder"` as sheet-protection password. Real secret flow lands in prompt 16 (bootstrap wizard §5). Do not resolve earlier.

### UT-5: `<commit4>` and `<merge date>` placeholders in BUILD_LOG — current

07a and 07b entries had literal `<commit4>` and `<merge date>` placeholders. **Filled post-merge during Partner's QC pass per the rubric.** 07c-α entry filled with PR #20, commits aa395dd / 7305acd / fcdb592 / 1d770ec, merge date 2026-04-24. 07c-β entry filled with PR #21, commits ffa6d9c / bf649ed / 00bff7d / 2788761, merge date 2026-04-25. **08a and 08b entries advanced 2026-04-25 QC**: merge date filled (`2026-04-25`) and `Outcome` flipped to `MERGED`. PR numbers and commit SHAs left as `<PR-08a>` / `<PR-08b>` / `<sha1-08a>` etc. placeholders for James to find-and-replace from `gh pr list --state merged --limit 5` before committing the housekeeping PR. **10b-ii-α entry advanced 2026-04-28 QC**: filled with PR #41, commits a8e1e09 / 2995d13 / 5e37a27 / 8671d06, merge date 2026-04-28. **10b-ii-β entry advanced this session (2026-04-28 QC)**: `Outcome` flipped to `MERGED` (and per the QC pass the entry was re-confirmed against the merged tree); PR number, commit SHAs, and merge date left as `<PR-10b-ii-beta>` / `<sha1-10b-ii-beta>` / `<sha2-10b-ii-beta>` / `<sha3-10b-ii-beta>` / `<sha4-10b-ii-beta>` / `<merge-date-10b-ii-beta>` placeholders for James to find-and-replace from `gh pr list --state merged --limit 10` before committing the partner-state snapshot PR. Expected sequence: PR #21 = 07c-β; PR #22 = split-08 prep (drafts only, no code); PR #23 = 08a; PR #24 = 08b; PR #29 = 09a; PR #33 = 10a; PR #38 = 10b-i; PR #41 = 10b-ii-α; PR #&lt;PR-10b-ii-beta&gt; = 10b-ii-β. UT-5 will surface again after the 10c-or-sub-prompt merge.

### UT-6: Sidecar-state JSON pathway for xlsx round-trip — RESOLVED 2026-04-25

Per BUILD.md §3.11 line 1009 + line 1015, the sidecar is written by both daemons: forward writes it after each regeneration (in the same lock as the xlsx write), and reverse rewrites it at the end of each cycle. Sidecar lives at `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json` (sibling to xlsx files).

07c-α landed the forward half (PR #20, merged 2026-04-24); 07c-β landed the reverse half (PR #21, merged 2026-04-25). The 07.5 audit confirmed the pathway is closed at both ends with three canaries: `tests/unit/test_xlsx_forward_writes_sidecar.py`, `tests/unit/test_xlsx_reverse_cold_start.py`, `tests/integration/test_xlsx_roundtrip.py`. Status: **RESOLVED**.

### UT-7: Reverse-daemon emit path bypasses Session / guardedWrite — RESOLVED 2026-04-25

The xlsx reverse daemon (07c-β, merged 2026-04-25 via PR #21) emitted domain events through `EventLog.append()` directly, with `actor_identity="xlsx_reverse"` as a documented placeholder, without routing through Session/guardedWrite/scope checks.

**Closed by 08b (PR #&lt;PR-08b&gt;, merged 2026-04-25).** The reverse-daemon rewrite stayed in 08b's Commit 3 — the **sidecar hedge to 08.5 was NOT activated** because the rewrite proved mechanical (single `_append` helper signature change + new `_session_for(workbook)` per-cycle helper + per-pathway plumbing through eight `_emit_*` methods, all hanging off the existing seam). The 08b Evidence section enumerates the closure precisely:

- `_ACTOR = "xlsx_reverse"` literal at line 91 removed; `grep -nE "_ACTOR\s*=" adminme/daemons/xlsx_sync/reverse.py` returns 0.
- `_append` helper now takes `session: Session`, derives `actor_identity` from `session.auth_member_id`, returns `str | None` to support guarded-write refusals.
- New `XlsxReverseDaemon.__init__` parameters: `guarded_write: GuardedWrite | None`, `principal_member_id_resolver: Callable[[str], str | None] | None`. Both optional for backward compatibility; when wired, every `_append` routes through the three-layer check before append.
- New helper `_session_for(workbook)` constructs the per-cycle Session via `build_session_from_xlsx_reverse_daemon(detected_member_id, config)` (added to `adminme/lib/session.py` line 367).
- Each of the eight `_emit_*` methods threads the cycle's session through and replaces literal `"xlsx_reverse"` references in `*_by_party_id` payload fields with `session.auth_member_id`.
- Cycle-terminus `xlsx.reverse_projected` and skip-cycle `xlsx.reverse_skipped_during_forward` events stay system-attributed (`actor_identity="xlsx_reverse"`) per [§13] — they are system observability signals, not domain events.
- Closure canary: `tests/integration/test_xlsx_roundtrip.py` UT-7 case asserts `actor_identity == principal_member_id` (NOT `"xlsx_reverse"`) for the principal-attributed domain emits. Test passes.

Status: **RESOLVED 2026-04-25**.

### UT-8: vector_search privileged-exclusion is a per-projection carve-out — RESOLVED 2026-04-25

Per `[§13.9]` and `[§6.10]`, `vector_search` excludes privileged events outright (not just redacts). 08a's uniform `privacy_filter` does not handle this; the projection needed a per-projection carve-out.

**Closed by 08a (PR #&lt;PR-08a&gt;, merged 2026-04-25).** The carve-out shipped inline in `adminme/projections/vector_search/queries.py` exactly as the 08a draft anticipated — Claude Code did NOT need to surface a more general pattern, so the carve-out stayed local to `vector_search` rather than lifting to a named function in `scope.py`. The implementation is **three layers of defense**:

1. **Handler refuses to insert privileged rows at write time.** Privileged content never reaches the vector index in the first place.
2. **SQL filter at read time** — every `nearest()` query has hardcoded `WHERE vi.sensitivity != 'privileged'`. The exclusion is NOT session-controlled — even a principal-as-owner query for their own privileged content returns empty here, because `vector_search` is permanently privileged-free per [§13.9].
3. **`scope.allowed_read` re-check** as a defense-in-depth third line — if a privileged row somehow leaked into the index, the filter would still drop it from results.

The `nearest()` docstring (lines 49–69) cites all three layers + `[§13.9]` + `[§6.9]` + UT-8 explicitly. Per-cell tests in `tests/unit/test_scope.py` (the privileged × ambient/principal × owner_scope cells) confirm the SQL exclusion is evaluated regardless of session role. No `ScopeViolation` is raised on a privileged-owned vector_search query — the path returns empty so coach context builders downstream cannot inadvertently leak existence through error semantics.

Status: **RESOLVED 2026-04-25**.

### UT-9: ALLOWED_EMITS per-file allowlisting in scripts/verify_invariants.sh

09a Commit 4 extends `ALLOWED_EMITS` to permit three new event types from `adminme/lib/skill_runner/wrapper.py`. The xlsx single-seam pattern (`ALLOWED_EMIT_FILES`) was carried forward as a comment in 07c-α. 09a ships using the same pattern if the script structure supports it cleanly; if not, falls back to test-side enforcement per PM-17 (the path 08b chose for `external.sent`/`observation.suppressed`). After 09a merges, Partner reviews which path was taken and decides whether to harden the script's per-file allowlist support — a candidate single-purpose PR. Status: **partially resolved 2026-04-26**. 09a took the script-side path: `scripts/verify_invariants.sh` now contains a `SKILL_EMITS` block that greps for `skill.call.{recorded,failed,suppressed}` outside the wrapper file. Pattern is grep-based (matches `event_type="..."` literals) rather than the more elaborate per-file allowlist 07c-α deferred. Future Partner sessions can lift `external.sent` / `observation.suppressed` from test-side to script-side using this same pattern when convenient. Tracked as a future single-purpose PR; not blocking.

### UT-9: Pack-root resolution accepts three forms; production should converge on one — RESOLVED 2026-04-26

Introduced in 09a. `adminme/lib/skill_runner/wrapper.py::_resolve_pack_root` accepts three forms of `skill_id`: absolute path, repo-relative slug (test convenience), and `namespace:name` form. Production callers (pipeline runner from 10a onward) were tracked to pass absolute paths derived from `InstanceConfig.packs_dir` so the resolver only sees one shape in production.

**Closed by 10a (PR #33, merged 2026-04-26).** `PipelineRunner.discover(builtin_root, installed_root)` takes both roots as explicit absolute `Path` arguments — callers (production = the future bootstrap §7 wiring; tests = `tests/integration/test_pipeline_runner_integration.py`) pass absolute paths and the loader does not fall back to slug resolution. The runner's `_make_callback` constructs `PipelineContext.run_skill_fn` bound to `run_skill` directly, and pipelines call `await ctx.run_skill_fn(skill_id, inputs, SkillContext(...))` — when production pipelines start landing in 10b-i, they pass absolute paths via `InstanceConfig.packs_dir / "skills" / "<pack-name>"` (the convention is to be confirmed in 10b-i's refactor). The slug fallback in `_resolve_pack_root` remains as test-convenience only; not retired in a sidecar this session because it is not blocking and the convention is now load-bearing in 10a's discovery contract. Status: **RESOLVED 2026-04-26**. If a future Partner session wants to delete the slug fallback for cleanliness, that is a single-purpose PR; not blocking.

### UT-10: Pipeline pack shape vs. skill pack shape — distinct loaders by design

Surfaced in 10a (2026-04-26). `adminme/lib/skill_runner/pack_loader.py` parses the canonical *skill pack* shape (`pack.yaml` + `SKILL.md` frontmatter + `schemas/{input,output}.schema.json` + `prompt.jinja2` + optional `handler.py:post_process`). 10a introduced `adminme/pipelines/pack_loader.py` parsing the *pipeline pack* shape (`pipeline.yaml` + `handler.py` exposing the class named in `runtime.class`, no SKILL.md, no prompt.jinja2). Both loaders cache by `(pack_id, version)`; both expose `invalidate_cache()` for tests; both use `importlib.util.spec_from_file_location` with a sanitized module-name prefix to avoid cross-pack import collisions. **Status: not a tension — by-design distinct.** Logged so future Partner sessions don't try to unify them: pipeline packs structurally cannot reuse the skill-pack loader (no SKILL.md, no input/output schemas, an instantiable class instead of a function). UT-10 stays open as a tracking entry until 10b-i / 10b-ii-α / 10b-ii-β / 10c confirm the dual-loader pattern is comfortable in practice. **10b-i, 10b-ii-α, and 10b-ii-β have all shipped using the dual-loader pattern with no friction; can close at 10c merge if 10c continues clean.**

### UT-11: Pipeline pack location — `packs/pipelines/` vs `adminme/pipelines/` — RESOLVED 2026-04-26

Surfaced by PR #37 QC (2026-04-26). The 10b split memo at `docs/01-split-memo-10b.md` specifies pipeline packs at `packs/pipelines/identity_resolution/` etc., mirroring 09b's `packs/skills/`. 10a's `PipelineRunner.discover(builtin_root, installed_root)` walks two roots: in-tree `adminme/pipelines/` (builtin) and `instance_config.packs_dir / "pipelines"` (installed). The split memo's path is consistent with the `installed_root` second arg if `instance_config.packs_dir == "packs/"` — probable but unconfirmed without depth-reading 10a's runner.py. The 10b-i refactor session must verify the path convention against shipped 10a code before drafting Read first / Deliverables. Resolution lands when 10b-i refactor confirms or reconciles. Status: ~~OPEN, blocking 10b-i refactor~~ **RESOLVED 2026-04-26 by 10b-i merging.** 10b-i shipped `packs/pipelines/identity_resolution/` and `packs/pipelines/noise_filtering/` with no friction; the split memo's path matched 10a's `installed_root = instance_config.packs_dir / "pipelines"` once `packs_dir` resolves to the repo's `packs/` (or the instance's `packs/` post-bootstrap). Convention now confirmed: pipeline packs live at `packs/pipelines/<n>/` mirroring 09b's `packs/skills/<n>/`. 10b-ii-α and 10b-ii-β continue this exact convention.

### UT-12: Parties-DB seam through PipelineContext — CLOSED 2026-04-28

Surfaced by 10b-i QC (2026-04-27); closed by 10b-ii-α merge (PR #41, 2026-04-28). 10b-i shipped `identity_resolution` in degenerate-clean mode — `_default_candidate_loader` returns `[]` because `PipelineContext` did not expose a parties-projection connection. Three options were on the table: (a) thread `parties_conn_factory` through `PipelineContext` as 10b-ii's Commit 1, (b) use 10b-i's injectable-loader pattern again, (c) split 10b-ii into 10b-ii-α and 10b-ii-β. The Partner session of 2026-04-27 selected **option (c)+(a)** — split 10b-ii into 10b-ii-α (parties-DB seam wiring + commitment_extraction) and 10b-ii-β (thank_you_detection reusing the seam). 10b-ii-α merged 2026-04-28 with `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None` on `PipelineContext`, threaded through `PipelineRunner.__init__` as an optional kwarg with default `None` for backward compatibility (all 5 existing 10a runner-integration construction sites stay green without modification). The seam is now generally available to any pipeline that needs read access to the parties projection; **10b-ii-β confirmed the seam by reusing it for `thank_you_detection` with zero infrastructure changes.**

Status: **CLOSED 2026-04-28**.

### UT-13: 10c is the next pre-split candidate — OPEN

`D-prompt-tier-and-pattern-index.md` flags 10c (proactive pipelines: `morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, plus possibly `recurrence_extraction`, `closeness_scoring`, `relationship_summarization`) as a pre-split candidate. Per `docs/architecture-summary.md` §5, several of these are reactive (`recurrence_extraction`, `closeness_scoring`, `relationship_summarization`) and several are proactive (`morning_digest`, `reminder_dispatch`, `reward_dispatch`). The split shape will depend on whether they group by trigger mechanism (reactive vs. proactive — natural coupling to OpenClaw standing-order registration in 10c's case) or by capability axis (commitment-flavored vs. recurrence-flavored vs. relationship-flavored). The Partner session that opens 10c orientation will forecast the split before drafting per PM-23.

**Status:** OPEN, resolves at 10c orientation. UT-2 (AGENTS.md concatenation path for proactive pipeline registration) is a sub-question that splits along with 10c. **This is the work of the next Partner session.**

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
├── ADMINISTRATEME_BUILD.md                      # constitutional
├── ADMINISTRATEME_CONSOLE_PATTERNS.md           # constitutional
├── ADMINISTRATEME_CONSOLE_REFERENCE.html        # constitutional
├── ADMINISTRATEME_DIAGRAMS.md                   # constitutional
├── ADMINISTRATEME_REFERENCE_EXAMPLES.md         # constitutional
├── ADMINISTRATEME_FIELD_MANUAL.md               # for James (not Partner)
├── README.md                                    # for James
├── docs/
│   ├── SYSTEM_INVARIANTS.md                     # constitutional
│   ├── DECISIONS.md                             # constitutional
│   ├── architecture-summary.md                  # constitutional
│   ├── openclaw-cheatsheet.md                   # constitutional
│   ├── build_log.md                             # LIVE: Claude Code's record
│   ├── partner_handoff.md                       # THIS FILE
│   ├── qc_rubric.md                             # companion
│   ├── universal_preamble_extension.md          # PM-7 proposal (EXECUTED)
│   ├── preflight-report.md                      # prompt 00's artifact
│   ├── 01-split-memo-10b.md                     # MERGED (Tier C split memo for 10b → 10b-i / 10b-ii)
│   ├── 02-split-memo-10b-ii.md                  # MERGED 2026-04-27 (Tier C secondary-split memo for 10b-ii → 10b-ii-α / 10b-ii-β)
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
│   ├── PROMPT_SEQUENCE.md                       # CANONICAL (single copy; slim preamble; 10b row split via PR #37; 10b-ii row split via PR #39)
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
│   ├── 10a-pipeline-runner.md                  # MERGED (PR #33, 2026-04-26; on-disk file is unrefactored v1 per PM-21 historical note)
│   ├── 10b-reactive-pipelines.md               # RETIRED 2026-04-26 (PR #37; superseded by 10b-i + 10b-ii)
│   ├── 10b-i-identity-and-noise.md             # MERGED (PR #38, 2026-04-26; refactored 320-line prompt committed per PM-21)
│   ├── 10b-ii-alpha-commitment-extraction.md   # MERGED (PR #41, 2026-04-28; refactored 370-line prompt committed per PM-21)
│   ├── 10b-ii-beta-thank-you-detection.md      # MERGED (PR #&lt;PR-10b-ii-beta&gt;, 2026-04-28; refactored 330-line prompt committed per PM-21)
│   ├── 10c-proactive-pipelines.md              # PENDING (unrefactored pre-PM-7 draft on disk; next Partner session opens orientation; UT-13 pre-flags as split candidate per PM-23)
│   ├── d01-*.md ... d08-*.md                    # diagnostic prompts
│   ├── prompt-01a-openclaw-cheatsheet.md
│   ├── prompt-01b-architecture-summary.md
│   ├── prompt-01c-system-invariants.md
│   └── sidecar-prompt-sequence-version-drift.md
├── adminme/
│   ├── events/{log,bus,envelope,registry}.py
│   ├── events/schemas/{ingest,crm,domain,governance,ops,system,messaging}.py
│   ├── projections/{base,runner}.py + 11 subdirs (10 sqlite + xlsx_workbooks)
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
├── tests/{unit,integration,fixtures,e2e}/
├── console/  bootstrap/  packs/                 # packs/skills/{classify_thank_you_candidate, classify_message_nature, classify_commitment_candidate, extract_commitment_fields, extract_thank_you_fields}; packs/pipelines/{identity_resolution, noise_filtering, commitment_extraction, thank_you_detection}
└── pyproject.toml  poetry.lock  .gitignore
```

---

## End of handoff document

Partner: steps 1–6 before any real work. Orient before acting.
