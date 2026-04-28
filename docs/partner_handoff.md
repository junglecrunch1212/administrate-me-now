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

**Never make up filenames, line numbers, or code that contradicts the zip.** If you're about to reference "prompt 07b's `builders.py` line 142" — don't. Load the file and verify. Confident-sounding inaccuracy is the failure mode most likely to embed errors across sessions.

### Step 6 — Report your orientation

Before producing any artifact, reply to James with:

1. **Current state:** last merged prompt, any IN FLIGHT PRs, what you understand to be the next task.
2. **This session's single task:** one sentence.
3. **Files you plan to load from the zip:** an enumerated list, roughly 3–10 files.
4. **Any concerns or ambiguities you see** before proceeding.

James corrects your orientation before you do real work. This is the value — catching misunderstandings before they're baked into a refactored prompt.

**Do NOT skip step 6.** Partner sessions that skip the orientation report reliably produce work against a wrong mental model.

---

## What AdministrateMe is (two paragraphs, for quick orientation only)

Household chief-of-staff platform. Event-sourced (SQLCipher append-only log at L2), projection-based (11 projections at L3), multi-member (principals + children + ambient), privacy-aware (three sensitivity levels, scope enforcement, observation mode). Built on OpenClaw as the assistant substrate — OpenClaw owns channels/LLM/sessions; AdministrateMe owns event log/projections/pipelines/adapters. Single tenant per deployment, multi-tenant at code level.

Built in two phases. Phase A: Claude Code generates code in Anthropic's sandbox against GitHub. Phase B: operator bootstraps on Mac Mini. **Every build prompt 00 through 19 is Phase A.** Partner works only on Phase A — prompt refactoring + QC after merges.

For anything beyond this summary, read the actual constitutional docs (step 1 above). Do not rely on this summary for architectural decisions.

---

## Current build state

**Last updated:** 2026-04-26 (10b-i merged as PR #38 — reactive pipelines `identity_resolution` + `noise_filtering` + skill pack `classify_message_nature@2.0.0` + two new event schemas at v1 (`identity.merge_suggested`, `messaging.classified`). Partner session of 2026-04-27 ran Type 2 QC on the merged 10b-i. **Findings on 10b-i merge:** (1) Contract check **Match-with-cosmetic-undershoot** — F-1: BUILD_LOG entry written by Claude Code in Commit 4 claimed 24 tests; actual is 22 (8 unit + 5 unit + 4 integration + 1 + 1 + 3 = 22; the breakdown overcounted both pipeline unit-test counts by 1). Suite tally 423 → 447 passed is correct. F-3: extra unit `test_exact_match_returns_without_emit` added beyond Commit 2 plan — positive signal, accepted. Skill-pack ships 3 handler-direct cases vs 09b reference's 4 (cosmetic; non-dict-input case folded into the coercion test). (2) Invariant audit **Clean** — `verify_invariants.sh` exit 0; `[§7.3]` / `[§7.4]` / `[§8]` / `[D6]` / `[§12.4]` / `[§15]` / `[D7]` all clean; pipeline→projection canary clean; causation-id wiring on every emit; `[ADR-0002]` `ctx.run_skill_fn` is the only skill-call seam in `noise_filtering/handler.py`. **F-2 (soft-watch for 10b-ii):** `noise_filtering`'s except list catches `SkillInputInvalid` / `SkillOutputInvalid` / `OpenClawTimeout` / `OpenClawUnreachable` / `OpenClawResponseMalformed` but NOT `SkillSensitivityRefused` / `SkillScopeInsufficient`. Harmless for `classify_message_nature` (sensitivity_required="normal", context_scopes_required=[]) but a future skill with non-normal sensitivity or non-empty scopes would propagate and non-advance the bus checkpoint per [§7.7]. 10b-ii must verify its three new skills' sensitivity/scopes shapes and either match 09b's normal-empty pattern or widen the except list. (3) Next-prompt calibration on **10b-ii**: **Needs refresh.** The parties-DB seam decision (10b-i punted via degenerate-clean mode) is load-bearing for 10b-ii — `commitment_extraction` per REFERENCE_EXAMPLES.md §2 calls `find_party_by_identifier` to resolve the sender. Three options surfaced (UT-12 below); 10b-ii orientation must pick one. **F-4 (silent architectural decision, accepted):** 10b-i shipped in degenerate-clean mode for `identity_resolution` per the prompt's Open Question option (2) — `_default_candidate_loader` returns []. Seam fully factored on `IdentityResolutionPipeline.__init__(candidate_loader=...)`; future runner-side wiring is one constructor-arg change. **PM-21 update:** the refactored 10b-i prompt **was committed to repo** at `prompts/10b-i-identity-and-noise.md` (320 lines) — first deviation from the historical paste-only convention. Worth tracking; if it stays the new norm, PM-21 graduates from SOFT-watch to HARD-convention. **UT-11 (pipeline-pack location) CLOSED** — `packs/pipelines/<n>/` confirmed by 10b-i shipping there with no friction; mirrors 09b's `packs/skills/`. New **UT-12** added below. Next refactor target advances to **10b-ii** (commitment_extraction + thank_you_detection). Prior session's PR #37 (sequence-update-10b-split) findings retained immediately below.

**Last updated:** 2026-04-27 (Partner session produced secondary-split memo for 10b-ii). Type 3 → Type 0 session at James's direction. **No code touched** — output is `docs/02-split-memo-10b-ii.md` (Tier C secondary split per the watch flag in `docs/01-split-memo-10b.md` §10b-ii) plus a Claude Code micro-prompt for the sequence-update PR. **Verdict:** 10b-ii splits into **10b-ii-α** (parties-DB seam through `PipelineContext.parties_conn_factory` + `commitment_extraction` pipeline + `classify_commitment_candidate` + `extract_commitment_fields` skill packs + `commitment.suppressed` event schema at v1) and **10b-ii-β** (`thank_you_detection` pipeline + `extract_thank_you_fields` skill pack; reuses 10b-ii-α's seam, no new infrastructure). UT-12 resolution: **option (c)+(a)** — the split itself selects (c), and 10b-ii-α's Commit 1 wires the parties-DB seam through `PipelineContext` per option (a). UT-12 status: **OPEN → CLOSING (formally CLOSED upon sequence-update PR merge)**. **Sizing rationale:** 10b-ii at original split-memo scope plus UT-12 option (a) infrastructure exceeds the empirical one-session budget set by 09b/10b-i; 10b-ii-α is sized at ~450–500 lines / ~25–30 tests / 4 net-new modules + one infrastructure extension; 10b-ii-β is sized at ~250–300 lines / ~12–15 tests / 2 net-new modules. Both within Claude Code's empirically-known one-session window. **New PM-23** added (secondary splits when a primary-split watch flag fires) and **new UT-13** added (10c is next pre-split candidate). **Next refactor target:** 10b-ii-α (after the sequence-update PR merges). **Watch:** 10c is itself a pre-split candidate per `D-prompt-tier-and-pattern-index.md`; pre-split forecast goes in startup report when 10c orientation begins.

**Last updated:** 2026-04-26 (sequence-update PR #37 `sequence-update-10b-split` merged — splits prompt 10b into 10b-i (identity_resolution + noise_filtering) and 10b-ii (commitment_extraction + thank_you_detection) per the split memo at `docs/01-split-memo-10b.md`. Three changes in one commit: PROMPT_SEQUENCE.md sequence-table row for 10b replaced with rows for 10b-i and 10b-ii; dependency graph updated to `10a → 10b-i → 10b-ii → 10c → 10d`; hard-sequential-dependency line updated to note "10b-ii consumes identity_resolution output" + "downstream prompts that depended on 10b now depend on 10b-ii"; `prompts/10b-reactive-pipelines.md` deleted (the unrefactored 26-line draft is superseded by the upcoming split sub-prompts). Partner session ran Type 2 QC: contract check Match (three changes specified, three changes landed; row formatting matches existing table style); invariant audit Clean (zero code touched, no `verify_invariants.sh` invocation needed); next-prompt calibration **Needs refresh** — split memo's "Scope" lines say `packs/pipelines/<n>/` while 10a's `PipelineRunner.discover(builtin_root, installed_root)` walks `adminme/pipelines/` as builtin and `instance_config.packs_dir / "pipelines"` as installed; the 10b-i refactor session must depth-read 10a's discover signature to confirm whether `packs/pipelines/` is the `installed_root` second arg (probable, mirroring 09b's `packs/skills/`) or a path mismatch needing reconciliation. **Resolved by 10b-i merge** — UT-11 CLOSED. **Branch reassignment observed:** harness assigned `claude/sequence-update-10b-split-OFrFL` instead of the requested `sequence-update-10b-split` — Claude Code did not fight it, correct preamble discipline. PM-22 added (sidecar transcripts that omit BUILD_LOG by design are a recognized class — sequence updates and split-memo prep PRs are infrastructure, not build prompts; recorded in this section of `build_log.md` but no full ledger entry).

**Last updated:** 2026-04-26 (sidecar PR #35 `sidecar-raw-data-is-manual-derived` merged — closes 07.5 finding C-1. Two-file change: `adminme/projections/xlsx_workbooks/sheets/raw_data.py` ALWAYS_DERIVED now includes `"is_manual"` matching the descriptor's `always_derived`; new `test_raw_data_always_derived_matches_descriptor` in `tests/unit/test_xlsx_finance_workbook.py` is the both-direction drift canary. Partner session ran Type 2 QC: contract check Match (two-file scope respected, no creep); invariant audit Clean (no new event types, no SDK imports, no path literals, no tenant identity, `verify_invariants.sh` exit 0); next-prompt calibration Clean (10b unaffected — sidecar touches Raw Data builder + finance-workbook test, neither in 10b's Read first). Suite tally 411 → 412 passed, 1 skipped (added 1 sync test to the finance-workbook file). UT-1 confirmed CLOSED.

This section is the live baton between sessions. Update it at the end of every Partner session.

**Prompts merged to main:** 00, 00.5, 01 (01a/01b/01c), 02, 03, 03.5, 04, 05, 06, 07a, 07b, **PM-7 infrastructure PR (slim preamble + scripts/verify_invariants.sh)**, **07c-α (PR #20, merged 2026-04-24)**, **07c-β (PR #21, merged 2026-04-25 — reverse daemon class + 4 emit pathways + integration round-trip; closes the xlsx round-trip and resolves UT-6)**, **08a (PR #&lt;PR-08a&gt;, merged 2026-04-25 — Session model + scope enforcement; 48 TODO(prompt-08) markers cleared across 10 sqlite projection queries.py files; 69 new tests; resolves UT-8 inline via `vector_search.nearest` three-layer carve-out)**, **08b (PR #&lt;PR-08b&gt;, merged 2026-04-25 — guardedWrite three-layer + observation `outbound()` + 6 governance event types at v1; 47 new tests + 4 security-E2E + 1 UT-7 closure case; resolves UT-7 — `_ACTOR` literal removed from reverse daemon, sidecar hedge NOT activated)**, **09a (PR #29, merged 2026-04-26 — skill runner wrapper around OpenClaw `/tools/invoke` + `llm-task`; 30 new tests; ADR-0002 schema relaxation folded in per PM-19; `verify_invariants.sh` extended with `skill.call.*` single-seam check)**, **09b (PR #&lt;PR-09b&gt;, merged &lt;merge-date-09b&gt; — first canonical skill pack `classify_thank_you_candidate` v1.3.0; 8 new tests (4 unit + 4 integration); `bootstrap/pack_install_order.yaml` queued for prompts 15/16; zero domain events, zero `verify_invariants.sh` edits — pure wrapper-consumer)**, **10a (PR #33, merged 2026-04-26 — pipeline runner per BUILD.md §L4; `adminme/pipelines/{base,pack_loader,runner}.py` + `tests/fixtures/pipelines/{echo_logger,echo_emitter}/` + 17 new tests (8+5 unit + 4 integration); pipeline→projection canary armed and clean; `PipelineContext` threads `Session` + `run_skill_fn` + `outbound_fn` + `guarded_write` + `observation_manager` + `triggering_event_id` + `correlation_id`; reactive-only — proactive packs skipped during `discover()` per UT-2 carve-out, OpenClaw standing-order registration deferred to 10c; zero new event-schema registrations)**, **10b-i (PR #38, merged 2026-04-26 — reactive pipelines `identity_resolution` (heuristic-only, degenerate-clean candidate-loader) + `noise_filtering` (skill-call seam to `classify_message_nature`); skill pack `classify_message_nature@2.0.0` (full 09b shape); two new event schemas at v1 (`identity.merge_suggested`, `messaging.classified`); 22 new tests (3 + 1 + 8 + 1 + 5 + 4); suite 423 → 447 passed; `verify_invariants.sh` exit 0; UT-11 closed; refactored prompt committed to repo at `prompts/10b-i-identity-and-noise.md`, 320 lines — first deviation from PM-21's paste-only convention)**.

**Sequence updates merged (infrastructure, not build):** **PR #37 `sequence-update-10b-split` (merged 2026-04-26)** — splits 10b into 10b-i / 10b-ii per the on-disk split memo at `docs/01-split-memo-10b.md`; updates PROMPT_SEQUENCE.md sequence table + dependency graph + hard-sequential-dependency line; deletes `prompts/10b-reactive-pipelines.md`. Single commit on harness-assigned branch `claude/sequence-update-10b-split-OFrFL`. No code touched; no BUILD_LOG entry by design (PM-22).

**Sequence-update PR for 10b-ii split queued (Partner session 2026-04-27 produced the artifacts; Claude Code execution + James-merged PR pending).** PR will land as `sequence-update-10b-ii-split` on the same single-purpose-PR pattern as PR #37. Per PM-22, the resulting PR has no four-commit discipline, no BUILD_LOG entry, no tests; just the sequence-table row replacement (10b-ii row → 10b-ii-α + 10b-ii-β rows), dependency-graph update (`10a → 10b-i → 10b-ii-α → 10b-ii-β → 10c → 10d`), hard-sequential-dependency line update, and the `docs/02-split-memo-10b-ii.md` file addition. **No file deletions** — the unrefactored 10b-ii draft was never landed on disk; the only on-repo record of 10b-ii's intended scope was `docs/01-split-memo-10b.md` itself, which stays.

**Checkpoints landed:** **07.5 (`docs/checkpoints/07.5-projection-consistency.md`, 2026-04-25)** — projection consistency audit across the 07a/07b/07c-α/07c-β cohort plus L1-adjacent reverse daemon. Verdict: PASS with 1 non-critical finding (C-1: Raw Data builder `ALWAYS_DERIVED` missing `is_manual` while descriptor `always_derived` includes it; deferred to sidecar PR `sidecar-raw-data-is-manual-derived`). UT-1 closes here.

**Prompts with PR open, not yet merged:** none.

**Prompts drafted, ready for Claude Code execution:** none. **Sequence-update PR queued** for the 10b-ii secondary-split per `docs/02-split-memo-10b-ii.md` (Partner session 2026-04-27). Next refactor target after that PR merges: **10b-ii-α** (parties-DB seam + `commitment_extraction` + `classify_commitment_candidate` + `extract_commitment_fields` skill packs + `commitment.suppressed` event schema at v1). Per `docs/02-split-memo-10b-ii.md` §"10b-ii-α" scope. **UT-12 resolution baked into the split** — orientation does not need to re-litigate the parties-DB seam decision; option (a) is wired through `PipelineContext` as 10b-ii-α's Commit 1 work.

**Sidecar PRs queued (non-blocking):** none. Most recent sidecar `sidecar-raw-data-is-manual-derived` merged as PR #35 on 2026-04-26 (closed 07.5 finding C-1). Most recent sequence update `sequence-update-10b-split` merged as PR #37 on 2026-04-26. Most recent sequence update **expected** as `sequence-update-10b-ii-split` (Partner session 2026-04-27 produced the prep artifacts; James drives Claude Code execution + merge). Recorded here so future Partner sessions see the full PR landscape.

**Next task queue (in order):**

1. **James: drive sequence-update PR for 10b-ii split.** Use the micro-prompt at `claude-code-sequence-update-10b-ii-split.md` (Partner session output 2026-04-27). Single-purpose PR per PM-22 — no four-commit discipline, no BUILD_LOG, no tests. Three changes in one commit: replace the 10b-ii row in `prompts/PROMPT_SEQUENCE.md`'s sequence table with two rows for 10b-ii-α and 10b-ii-β; update the dependency-graph ASCII to `10a → 10b-i → 10b-ii-α → 10b-ii-β → 10c → 10d`; update the hard-sequential-dependency line; create `docs/02-split-memo-10b-ii.md` with the contents from the same Partner session. **No file deletions** — the unrefactored 10b-ii draft was never landed on disk; the only on-repo record of 10b-ii's intended scope was `docs/01-split-memo-10b.md` itself, which stays.

2. **James: out-of-band update of `D-prompt-tier-and-pattern-index.md` in Partner setup** (post sequence-update-PR merge). Flip the `10b-ii` row from "Pre-split candidate (watch)" to "Was split on arrival" with `10b-ii-α` and `10b-ii-β` listed beneath. Re-upload to Project knowledge so the next Partner session sees the updated index. Per PM-21 / PM-22.

3. **Partner session: refactor 10b-ii-α** (parties-DB seam + `commitment_extraction` + `classify_commitment_candidate` + `extract_commitment_fields` skill packs + `commitment.suppressed` event schema at v1). Per `docs/02-split-memo-10b-ii.md` §"10b-ii-α" scope. Type 3 (refactor-only) session — no QC pending; sequence-update PRs do not generate QC work per PM-22. Estimated session size: ~450–500 lines refactored prompt; ~25–30 tests. Quality bar: 10b-i (320 lines, 22 tests, 4 commits) plus the parties-DB seam infrastructure extension. **F-2 carry-forward verification mandatory at depth-read** — read `classify_commitment_candidate` and `extract_commitment_fields` SKILL.md frontmatter (when the prompt drafts them) to determine `sensitivity_required` and `context_scopes_required`; either match 10b-i's normal-empty shape or widen the pipeline's `except` list to catch `SkillSensitivityRefused` / `SkillScopeInsufficient`.

4. **Claude Code session: execute 10b-ii-α.** James drives. Ships parties-DB seam through `PipelineContext` + `PipelineRunner.__init__` + `_make_callback`; runner-test updates; `commitment_extraction` pipeline pack; two new skill packs at full 09b shape; `commitment.suppressed` event schema at v1.

5. **Partner session: QC of 10b-ii-α merge + refactor of 10b-ii-β** (Type 1 combined session — 10b-ii-β is small and well-shaped per `docs/02-split-memo-10b-ii.md` §"10b-ii-β" scope; ~250–300 lines, ~12–15 tests). 10b-ii-β refactor depth-reads heavily on what 10b-ii-α actually shipped versus what was specified — pattern verification is the bulk of the QC work.

6. **Claude Code session: execute 10b-ii-β.** James drives. Ships `thank_you_detection` pipeline pack + `extract_thank_you_fields` skill pack. Reuses 10b-ii-α's parties-DB seam.

7. **Partner session: QC of 10b-ii-β merge + 10c orientation** (proactive pipelines). 10c is itself a pre-split candidate per `D-prompt-tier-and-pattern-index.md` (six proactive pipelines per `architecture-summary.md` §5). Pre-split forecast goes in startup report. **UT-2 must be resolved before 10c can be refactored** — the AGENTS.md-prose + `openclaw cron add` registration path is currently underspecified. Resolution comes from depth-reading `docs/reference/openclaw/automation/standing-orders.md` + `docs/reference/openclaw/concepts/agent-workspace.md` and codifying whether bootstrap §8 concatenates per-pipeline markdown into AGENTS.md or ships AGENTS.md pre-written.

8. Continuing through prompt 18 (Phase A build-complete), then 19 (Phase B smoke test).

**Pre-merge verification James should run before committing this housekeeping PR:**

```bash
gh pr list --state merged --limit 10 --json number,title,mergedAt,mergeCommit
```

…and find-and-replace `<PR-09b>`, `<commit4-09b>`, `<merge-date-09b>` in `docs/build_log.md` and `docs/partner_handoff.md` with the actual values. Same applies to the still-pending `<PR-08a>` / `<PR-08b>` / `<sha1-08a>` etc. placeholders inherited from prior sessions. (PR #38 / commits 22c6195 / 73880d4 / 4c19c80 / 0a3250f / 2026-04-26 are the 10b-i values, already filled in.)

**Prompts drafted but not yet refactored:** 10b-ii (still pending after the 10b split), 10c, 10d, 11, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19. (10a, 10b-i moved to "merged to main" 2026-04-26; 10b retired per PR #37 in favor of 10b-i / 10b-ii.) The slim preamble means each refactor is shorter than 07a/07b were. 15, 16 remain pre-split candidates; per `D-prompt-tier-and-pattern-index.md`, 10c, 11, 14b, 17 are also pre-split candidates.

**Note on on-disk vs. shipped 10b-i (PM-21 update):** unlike 10a (where the refactored prompt was paste-only and the on-disk file `prompts/10a-pipeline-runner.md` lagged), **10b-i's refactored 320-line prompt was committed to the repo at `prompts/10b-i-identity-and-noise.md` as part of PR #38**. This is a deviation from the historical paste-only convention. If it becomes the new norm starting with 10b-ii, PM-21 graduates from SOFT-watch to HARD-convention; track for one or two more cycles before declaring. The `prompts/10a-pipeline-runner.md` file on main remains the unrefactored 90-line v1 draft from a prior phase and is NOT a blocker — historical and out of scope for current work.

**Prompts not yet drafted:** 10b-ii (sub-prompt from the 10b split; the split memo at `docs/01-split-memo-10b.md` is the scope source — refactor session pending). Everything else from 11 onward exists in unrefactored form.

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

Existing examples of the same pattern: 01 → 01a/01b/01c (architecture + cheatsheet + invariants), 07 → 07a/07b/07c-α/07c-β (ops projections + xlsx forward + xlsx round-trip foundations + xlsx reverse daemon), **10b → 10b-i/10b-ii (identity+noise / commitments+thank-you, per PR #37 sequence update 2026-04-26)**.

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

### PM-21: Refactored prompts may be paste-only OR committed to repo — SOFT (in flux 2026-04-27)

Surfaced in 10a QC (2026-04-26). The build_log entry for prompt 10a cites `prompts/10a-pipeline-runner.md (~340 lines, quality bar = 09a)`. The actual on-disk file is the unrefactored 90-line v1 draft — Partner's refactored ~340-line text was held in chat and James pasted it into Claude Code without committing it to repo. This is the historical pattern across every prompt 07a onward. **Trade-off:** committing refactored prompts to disk would (a) make the file-on-disk match build_log evidence, (b) provide a permanent QC artifact for future Partner sessions to depth-read, (c) cost one extra single-purpose PR per prompt (4–6 extra PRs over Phase A). Currently SOFT — the working pattern is fine, and the build_log entries serve as the durable QC record. PM-21 names this convention so future Partner sessions know the on-disk draft of any unmerged prompt is **not** authoritative until Claude Code has executed against the refactored form. **If a future session wants to commit refactored prompts to disk, do it as a single backfill PR covering 10a onward, not piecemeal.**


**Update 2026-04-27 (post-10b-i):** 10b-i's refactored 320-line prompt was committed to the repo at `prompts/10b-i-identity-and-noise.md` as part of PR #38 — the first build-prompt round where the on-disk file matches what Claude Code executed against. If 10b-ii follows suit, PM-21 graduates from "may lag, OK if it does" to a HARD convention of "refactored prompts ship in the build PR." Tracking for one or two more cycles before declaring.

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

---

## Open tensions / unresolved things

### UT-1: When to refactor checkpoint 07.5 — CLOSED 2026-04-25

Original 07.5 assumed 11 projections in one prompt. After 07a/07b/07c-α/07c-β split, it audits four ops prompts together as a cohort. Audit landed at `docs/checkpoints/07.5-projection-consistency.md` on 2026-04-25 post-07c-β merge. Verdict PASS with one non-critical sidecar finding (C-1, queued as `sidecar-raw-data-is-manual-derived`). Status: **CLOSED**.

### UT-2: Proactive pipeline registration path (10c)

`[D1]` confirmed: proactive pipelines register via workspace-prose `AGENTS.md` + `openclaw cron add`. Prompt 10c will generate both. Concrete question: does bootstrap §8 concatenate per-pipeline markdown into AGENTS.md and issue cron adds, or ship AGENTS.md pre-written? Answer lands when 10c is refactored.

### UT-3 (RESOLVED 2026-04-25): Prompt 08 split executed

Prompt 08 split into **08a (Session + scope, read side)** and **08b (governance + observation + UT-7 closure, write side)**. The architectural decision is recorded at `docs/2026-04-25-prompt-08-split.md` (the on-repo split memo from a prior Partner session). The `prompts/PROMPT_SEQUENCE.md` sequence-table and dependency-graph updates landed in an earlier commit. The `split-08-2026-04-25` PR closed the gap by landing `prompts/08a-session-and-scope.md` and `prompts/08b-governance-and-observation.md` and updating this handoff state.

The 60 attention sites catalogued by the 07.5 audit (48 explicit `# TODO(prompt-08)` markers across 10 sqlite projection `queries.py` files + 12 implicit attribution sites in `adminme/daemons/xlsx_sync/reverse.py`) split: 48 to 08a (projection query integration), 12 to 08b (reverse-daemon attribution). Status: **RESOLVED**. UT-7 carries forward into 08b (or 08.5 if the reverse-daemon rewrite triggers the sidecar hedge per 08b's Commit 3).

### UT-4: Placeholder values in xlsx protection passwords

07b uses `"adminme-placeholder"` as sheet-protection password. Real secret flow lands in prompt 16 (bootstrap wizard §5). Do not resolve earlier.

### UT-5: `<commit4>` and `<merge date>` placeholders in BUILD_LOG — current

07a and 07b entries had literal `<commit4>` and `<merge date>` placeholders. **Filled post-merge during Partner's QC pass per the rubric.** 07c-α entry filled with PR #20, commits aa395dd / 7305acd / fcdb592 / 1d770ec, merge date 2026-04-24. 07c-β entry filled with PR #21, commits ffa6d9c / bf649ed / 00bff7d / 2788761, merge date 2026-04-25. **08a and 08b entries advanced this session (2026-04-25 QC)**: merge date filled (`2026-04-25`) and `Outcome` flipped to `MERGED`. PR numbers and commit SHAs left as `<PR-08a>` / `<PR-08b>` / `<sha1-08a>` etc. placeholders for James to find-and-replace from `gh pr list --state merged --limit 5` before committing the housekeeping PR. Expected sequence: PR #21 = 07c-β; PR #22 = split-08 prep (drafts only, no code); PR #23 = 08a; PR #24 = 08b. UT-5 will surface again after the next merge.

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

Surfaced in 10a (2026-04-26). `adminme/lib/skill_runner/pack_loader.py` parses the canonical *skill pack* shape (`pack.yaml` + `SKILL.md` frontmatter + `schemas/{input,output}.schema.json` + `prompt.jinja2` + optional `handler.py:post_process`). 10a introduced `adminme/pipelines/pack_loader.py` parsing the *pipeline pack* shape (`pipeline.yaml` + `handler.py` exposing the class named in `runtime.class`, no SKILL.md, no prompt.jinja2). Both loaders cache by `(pack_id, version)`; both expose `invalidate_cache()` for tests; both use `importlib.util.spec_from_file_location` with a sanitized module-name prefix to avoid cross-pack import collisions. **Status: not a tension — by-design distinct.** Logged so future Partner sessions don't try to unify them: pipeline packs structurally cannot reuse the skill-pack loader (no SKILL.md, no input/output schemas, an instantiable class instead of a function). UT-10 stays open as a tracking entry until 10b-i / 10b-ii / 10c confirm the dual-loader pattern is comfortable in practice.

### UT-11: Pipeline pack location — `packs/pipelines/` vs `adminme/pipelines/` — RESOLVED 2026-04-26

Surfaced by PR #37 QC (2026-04-26). The 10b split memo at `docs/01-split-memo-10b.md` specifies pipeline packs at `packs/pipelines/identity_resolution/` etc., mirroring 09b's `packs/skills/`. 10a's `PipelineRunner.discover(builtin_root, installed_root)` walks two roots: in-tree `adminme/pipelines/` (builtin) and `instance_config.packs_dir / "pipelines"` (installed). The split memo's path is consistent with the `installed_root` second arg if `instance_config.packs_dir == "packs/"` — probable but unconfirmed without depth-reading 10a's runner.py. The 10b-i refactor session must verify the path convention against shipped 10a code before drafting Read first / Deliverables. Resolution lands when 10b-i refactor confirms or reconciles. Status: ~~OPEN, blocking 10b-i refactor~~ **RESOLVED 2026-04-26 by 10b-i merging.** 10b-i shipped `packs/pipelines/identity_resolution/` and `packs/pipelines/noise_filtering/` with no friction; the split memo's path matched 10a's `installed_root = instance_config.packs_dir / "pipelines"` once `packs_dir` resolves to the repo's `packs/` (or the instance's `packs/` post-bootstrap). Convention now confirmed: pipeline packs live at `packs/pipelines/<n>/` mirroring 09b's `packs/skills/<n>/`. 10b-ii continues this exact convention.

### UT-12: Parties-DB seam through PipelineContext — load-bearing for 10b-ii

Surfaced by 10b-i QC (2026-04-27). 10b-i shipped `identity_resolution` in degenerate-clean mode — `_default_candidate_loader` returns `[]` because `PipelineContext` does not currently expose a parties-projection connection. The seam is fully factored on `IdentityResolutionPipeline.__init__(candidate_loader: Callable | None = None)` so unit tests inject a stub loader to exercise the merge-threshold branch.

10b-i could punt because `identity_resolution` is heuristic-only — no skill calls, no contract violation if the candidate list is empty (every miss creates a new party). **10b-ii cannot punt the same way** because `commitment_extraction` per REFERENCE_EXAMPLES.md §2 calls `find_party_by_identifier(conn, session, *, kind, value_normalized)` to resolve the sender, and a degenerate path makes the sender resolution always-None — which is observable behavior that downstream tests will inspect.

Three options to evaluate at 10b-ii orientation:

- **(a) Thread `parties_conn_factory` through `PipelineContext`** as 10b-ii's Commit 1. The factory is `Callable[[], sqlcipher3.Connection]` constructed in `PipelineRunner.__init__` from `instance_config` + the encryption key. The runner's `_make_callback` builds it into the `PipelineContext`. `identity_resolution`'s `_default_candidate_loader` is then replaced with one that opens the parties DB and queries `identifiers` by `(tenant_id, kind, value_normalized)`. Sets the precedent for 10c+ pipelines that need projection reads. **Cost:** one extra commit on top of 10b-ii's already-large scope; the runner test suite needs an update.
- **(b) Use 10b-i's injectable-loader pattern again** — give `commitment_extraction` a `party_loader: Callable | None` constructor arg with a degenerate default. Cohesive with 10b-i's pattern but ships another partially-functional pipeline; 10b-ii's integration tests would have to inject the loader to assert the resolved-sender path.
- **(c) Split 10b-ii into 10b-ii-α** (parties-DB seam wiring + `commitment_extraction` fully wired to read parties) **and 10b-ii-β** (`thank_you_detection` + `extract_thank_you_fields`). The split memo flagged this as a watch.

**Decision criteria:** if the depth-read at 10b-ii refactor time reveals that `commitment_extraction`'s test pyramid is already at the top of one Claude Code session window, pick (c). If there's headroom for one extra commit, pick (a). Avoid (b) unless (a) and (c) both look infeasible — the second degenerate-mode pipeline establishes a worse pattern than the first.

Status: **CLOSING** (resolved by `docs/02-split-memo-10b-ii.md` 2026-04-27, formally CLOSED upon merge of the `sequence-update-10b-ii-split` PR). Selected option: **(c)+(a)** — 10b-ii is split into 10b-ii-α (which carries the parties-DB seam wiring per option (a)) and 10b-ii-β. The two-way split itself is option (c); the parties-DB seam shipping in 10b-ii-α's Commit 1 is option (a) wired into the same path. The original three-way decision collapses to a single coherent path: ship the seam with its first consumer (`commitment_extraction`); reuse the seam for the second consumer (`thank_you_detection`) without further infrastructure changes. **Decision criteria from the original UT-12:** "if the depth-read at 10b-ii refactor time reveals that `commitment_extraction`'s test pyramid is already at the top of one Claude Code session window, pick (c)" — the secondary-split memo's sizing analysis confirmed exactly this condition.

### UT-13: 10c is the next pre-split candidate — OPEN

`D-prompt-tier-and-pattern-index.md` flags 10c (proactive pipelines: `morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, plus possibly `recurrence_extraction`, `closeness_scoring`, `relationship_summarization`) as a pre-split candidate. Per `docs/architecture-summary.md` §5, several of these are reactive (`recurrence_extraction`, `closeness_scoring`, `relationship_summarization`) and several are proactive (`morning_digest`, `reminder_dispatch`, `reward_dispatch`). The split shape will depend on whether they group by trigger mechanism (reactive vs. proactive — natural coupling to OpenClaw standing-order registration in 10c's case) or by capability axis (commitment-flavored vs. recurrence-flavored vs. relationship-flavored). The Partner session that opens 10c orientation will forecast the split before drafting per PM-23.

**Status:** OPEN, resolves at 10c orientation. UT-2 (AGENTS.md concatenation path for proactive pipeline registration) is a sub-question that splits along with 10c.

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
│   ├── 02-split-memo-10b-ii.md                  # PENDING (Tier C secondary-split memo for 10b-ii → 10b-ii-α / 10b-ii-β; 2026-04-27)
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
│   ├── PROMPT_SEQUENCE.md                       # CANONICAL (single copy; slim preamble; 10b row split into 10b-i + 10b-ii per PR #37)
│   ├── 00-preflight.md ... 19-phase-b-smoke-test.md
│   ├── 07a-projections-ops-spine.md
│   ├── 07b-xlsx-workbooks-forward.md
│   ├── 07c-alpha-foundations.md                # MERGED (PR #20, 2026-04-24)
│   ├── 07c-beta-reverse-daemon.md              # MERGED (PR #21, 2026-04-25)
│   ├── 07.5-checkpoint-projection-consistency.md  # source contract; audit memo at docs/checkpoints/
│   ├── 08-session-scope-governance.md          # RETIRED (superseded by 08a + 08b)
│   ├── 08a-session-and-scope.md                # MERGED (PR #&lt;PR-08a&gt;, 2026-04-25)
│   ├── 08b-governance-and-observation.md       # MERGED (PR #&lt;PR-08b&gt;, 2026-04-25)
│   ├── 10b-reactive-pipelines.md               # RETIRED 2026-04-26 (PR #37; superseded by 10b-i + 10b-ii)
│   ├── d01-*.md ... d08-*.md                    # diagnostic prompts
│   ├── prompt-01a-openclaw-cheatsheet.md
│   ├── prompt-01b-architecture-summary.md
│   ├── prompt-01c-system-invariants.md
│   └── sidecar-prompt-sequence-version-drift.md
├── adminme/
│   ├── events/{log,bus,envelope,registry}.py
│   ├── events/schemas/{ingest,crm,domain,governance,ops,system}.py
│   ├── projections/{base,runner}.py + 11 subdirs (10 sqlite + xlsx_workbooks)
│   ├── daemons/                                 # PM-14: adapters/daemons that emit domain events
│   │   └── xlsx_sync/                           # populated by 07c: diff.py, sheet_schemas.py, reverse.py
│   ├── pipelines/                              # MERGED 10a (base.py, pack_loader.py, runner.py); pipeline PACKS live under packs/pipelines/ — UT-11 CLOSED 2026-04-26
│   ├── lib/instance_config.py
│   ├── lib/session.py                          # MERGED 08a (Session dataclass, 3 constructors + xlsx_reverse_daemon constructor)
│   ├── lib/scope.py                            # MERGED 08a (allowed_read, privacy_filter, coach_column_strip, child_hidden_tag_filter, ScopeViolation, CHILD_FORBIDDEN_TAGS)
│   ├── lib/governance.py                       # MERGED 08b (GuardedWrite three-layer; ActionGateConfig, RateLimiter, AgentAllowlist)
│   ├── lib/observation.py                      # MERGED 08b (outbound() single seam per [§6.13/§6.14]; ObservationManager default-on)
│   ├── lib/skill_runner/                       # MERGED 09a (wrapper.py, pack_loader.py)
│   └── (products, openclaw_plugins, cli, adapters — stubs or partial)
├── tests/{unit,integration,fixtures,e2e}/
├── console/  bootstrap/  packs/                 # packs/skills/classify_thank_you_candidate (09b); packs/pipelines/ pending 10b-i (UT-11)
└── pyproject.toml  poetry.lock  .gitignore
```

---

## End of handoff document

Partner: steps 1–6 before any real work. Orient before acting.
