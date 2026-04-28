# Partner handoff state — durable between Claude Chat sessions

_The Partner's authoritative state file. Read at the start of every Chat session. Updated as the closing artifact of every session._

---

## How a Partner session starts

You are starting a fresh AdministrateMe Build Supervision Partner session in the AdministrateMe Build Partner Project.

### Step 1 — Read the init prompt

The init prompt is the first message of this conversation. If it isn't, ask James to paste it before doing any work.

### Step 2 — Run the mandatory `project_knowledge_search` queries (PM-13)

Per init prompt §11, the Partner discovers Project knowledge contents via the `project_knowledge_search` tool, NOT via filesystem listing. Run these four queries before anything else, recording what each returns:

1. `project_knowledge_search("PROMPT_SEQUENCE universal preamble dependency graph")` — confirms PROMPT_SEQUENCE.md (Tier 1).
2. `project_knowledge_search("SYSTEM_INVARIANTS binding invariants")` — confirms SYSTEM_INVARIANTS.md (Tier 2).
3. `project_knowledge_search("partner_handoff PM UT current build state")` — confirms partner_handoff.md (Tier 2) and surfaces current state.
4. `project_knowledge_search("qc_rubric three-job pass contract check invariant audit")` — confirms qc_rubric.md (Tier 2).

If any search returns empty, the file is genuinely absent — flag it as **degraded** mode in the orientation report. **Never claim a file is missing based on `/mnt/project/` listing alone.**

### Step 3 — Constitutional reading

The 9 constitutional docs in `docs/` are the source of truth for invariants and decisions. They are not cached between sessions; the Partner reads them as needed via `project_knowledge_search` queries:

- **architecture-summary.md** — five-layer model.
- **SYSTEM_INVARIANTS.md** — 15 numbered sections of binding invariants. Cite as `[§N]`.
- **DECISIONS.md** — numbered decisions. Cite as `[DN]`.
- **openclaw-cheatsheet.md** — 8 Q&As on OpenClaw seams. Cite as `[cheatsheet Qn]`.
- **architecture-summary.md** — same as above; layer-by-layer summary.
- **partner_handoff.md** — this file.
- **qc_rubric.md** — the three-job QC pass.
- **build_log.md** — Claude Code's record of what shipped per prompt.
- **universal_preamble_extension.md** — the PM-7 infrastructure proposal that produced `scripts/verify_invariants.sh`.

### Step 4 — Read PROMPT_SEQUENCE.md

`prompts/PROMPT_SEQUENCE.md` is the canonical sequence. It gives:

- The full sequence (prompts 00 through 19).
- The dependency graph.
- The current universal preamble (slim, post-PM-7).
- The per-prompt structure template.

### Step 5 — Identify current state and this session's task

Based on `build_log.md` + `PROMPT_SEQUENCE.md`, identify:

- **Last fully merged prompt** (not IN FLIGHT — merged).
- **In-flight PRs** (if any).
- **Next prompt to write** (or checkpoint to refactor, or QC pass to run).
- **What this session specifically needs to do**, which James tells you explicitly.

### Step 6 — Identify what code context you'll need from the zip

James has attached the **most recent full codebase as a zip**. You have NOT loaded any of it yet. You load specific files from it based on what this session's task needs.

**The principle:** load the minimum. Partner sessions that try to ingest the whole codebase run out of headroom before producing the refactor. Partner sessions that ingest too little produce prompts with broken references.

**Loading rule of thumb by task type:**

| Task type | Load from zip (minimum) |
|---|---|
| Refactor a new build prompt (10b-ii-β, etc.) | (a) The draft prompt file from `prompts/<NN>-*.md`. (b) The most recently merged prompt file (same directory), as quality-bar reference. (c) Source files the new prompt's "Read first" section references. (d) `pyproject.toml`. |
| Refactor a checkpoint (07.5, 10d, 14e, 15.5) | (a) The checkpoint file. (b) Directory listings of areas the checkpoint audits. (c) Related tests. |
| QC pass on a merged PR | (a) The `build_log.md` entry. (b) The prompt file that specified what was to ship. (c) Spot-check files from the diff if Evidence lists seem off. Do NOT load the entire diff. |
| Universal preamble extension / sequence refactor | (a) `prompts/PROMPT_SEQUENCE.md`. (b) `pyproject.toml`. (c) Any scripts or canonical files the proposal mentions. |
| Structural refactor spanning multiple prompts | (a) All affected prompt files. (b) Shared references only. Decompose the task if it's bigger than this. |

**How to load from the zip:** the zip is named `administrate-me-now-main__<N>_.zip`. James attaches the latest. You unzip in your sandbox, then read only the specific paths needed. If you need the directory layout first, ask — or run `ls` on the expected subdirectory and see what's actually there before assuming a file exists.

**Never make up filenames, line numbers, or code that contradicts the zip.** If you're about to reference "prompt 07b's `builders.py` line 142" — don't. Load the file and verify. Confident-sounding inaccuracy is the failure mode most likely to embed errors across sessions.

### Step 7 — Report your orientation

Before producing any artifact, reply to James with:

1. **Current state:** last merged prompt, any IN FLIGHT PRs, what you understand to be the next task.
2. **This session's single task:** one sentence.
3. **Files you plan to load from the zip:** an enumerated list, roughly 3–10 files.
4. **Any concerns or ambiguities you see** before proceeding.

James corrects your orientation before you do real work. This is the value — catching misunderstandings before they're baked into a refactored prompt.

**Do NOT skip step 7.** Partner sessions that skip the orientation report reliably produce work against a wrong mental model.

---

## What AdministrateMe is (two paragraphs, for quick orientation only)

Household chief-of-staff platform. Event-sourced (SQLCipher append-only log at L2), projection-based (11 projections at L3), multi-member (principals + children + ambient), privacy-aware (three sensitivity levels, scope enforcement, observation mode). Built on OpenClaw as the assistant substrate — OpenClaw owns channels/LLM/sessions; AdministrateMe owns event log/projections/pipelines/adapters. Single tenant per deployment, multi-tenant at code level.

Built in two phases. Phase A: Claude Code generates code in Anthropic's sandbox against GitHub. Phase B: operator bootstraps on Mac Mini. **Every build prompt 00 through 19 is Phase A.** Partner works only on Phase A — prompt refactoring + QC after merges.

For anything beyond this summary, read the actual constitutional docs (step 3 above). Do not rely on this summary for architectural decisions.

---

## Current build state

**Last updated:** 2026-04-28 (10b-ii-α merged as PR #41 — parties-DB seam through `PipelineContext.parties_conn_factory` + `commitment_extraction` reactive pipeline pack + 2 skill packs (`classify_commitment_candidate@3.0.0`, `extract_commitment_fields@2.1.0`) + `commitment.suppressed` event schema at v1. Partner session of 2026-04-28 ran Type 1 combined session: Job 1 QC of merged 10b-ii-α (clean — all findings F-1 through F-8 are overshoots or accepted decisions, no undershoots, no violations); Job 2 refactor of 10b-ii-β (artifact at `prompts/10b-ii-beta-thank-you-detection.md`); Job 3 delivery-gate self-check passed (330 lines, well under budget). UT-12 closed by this merge. PM-21 graduates from SOFT-watch to **HARD convention** ("refactored prompts ship in the build PR") on the strength of two consecutive merges (10b-i + 10b-ii-α) following the pattern. New PM-26 added (post-PM-7 byte-budget calibration drift in E-session-protocol §2.9 — the 25 KB ceiling is a measurement artifact predating universal-preamble extraction; line-count is the operationally relevant signal). New F-5 soft pattern observation carried forward to 10b-ii-β: outbound `messaging.sent` defensive-default emits audit-trail noise; address by early-return at top of handler. New F-8 soft pattern observation carried forward to prompt 16: `_load_config` reads YAML per-event; bootstrap should wire config caching at runner-construction time. Next refactor target: **10b-ii-β** (thank_you_detection + extract_thank_you_fields). Per `docs/02-split-memo-10b-ii.md` §"10b-ii-β" scope. **No new infrastructure, no new event types** — pure consumer of 10b-ii-α's parties-DB seam plus 09b's existing `classify_thank_you_candidate@1.3.0`. **Watch:** 10c is itself a pre-split candidate per `D-prompt-tier-and-pattern-index.md`; pre-split forecast goes in startup report when 10c orientation begins (UT-13).

**Merged to main (chronological by merge date):** 00, 00.5, 01a, 01b, 01c, 02, 03, 03.5, 04, 05, 06, **07a (PR #18, merged 2026-04-24 — three projection packs `places_assets_accounts`, `money`, `vector_search` per BUILD.md §L3; 38 new tests; sqlite-vec extension loaded for vector_search; `bootstrap/pack_install_order.yaml` queued for prompts 15/16; bench script `bench/vector_search_query.py` for ops-spine ad-hoc perf checks)**, **07b (PR #19, merged 2026-04-25 — xlsx_workbooks projection forward daemon per BUILD.md §3.11; 8 sheet builders for Finance + Lists + Members + Assumptions + Dashboard + Balance Sheet + Pro Forma + Budget vs Actual; 30 new tests; xlsx forward write coordination via watchdog observer + `xlsx_state.txt` sentinel; `xlsx.regenerated` system event registered at v1; `ALLOWED_EMITS` extended; `verify_invariants.sh §2.2` projection-emit allowlist updated; PM-7 EXECUTED — slim preamble + canonical verify_invariants.sh shipped)**, **07c-α (PR #28, merged 2026-04-25 — xlsx round-trip foundations: schema additions for `xlsx_round_trip` table, sidecar I/O at `~/.adminme/projections/xlsx_workbooks/.xlsx-state/`, bidirectional descriptors for Raw Data + Manual Categorization + Lists + Members + Assumptions sheets, diff core, `xlsx_workbooks/forward.py` extended to write sidecar state alongside the workbook regen; 21 new tests; PM-15 SOFT pattern surfaced — daemon + infrastructure prompts split at α/β when they overrun a single Claude Code session; PR #25/26 timeouts proved this empirically)**, **07c-β (PR #34, merged 2026-04-25 — xlsx reverse daemon at `adminme/daemons/xlsx_sync/reverse.py`; watchdog observer per workbook; lock-contention defaults; 60-second reverse-projection cycle; 10 new tests including 4 lock-contention concurrency tests; `xlsx.reverse_projected` and `xlsx.reverse_skipped_during_forward` system events registered at v1; PM-14 introduced — daemons live in `adminme/daemons/`, projections in `adminme/projections/`; reverse daemon emits domain events on file-edit authority and is NOT a projection; UT-1 CLOSED by 07.5 audit landing 2026-04-25; Raw Data ALWAYS_DERIVED descriptor-drift sidecar `sidecar-raw-data-is-manual-derived` queued)**, **08a (PR #&lt;PR-08a&gt;, merged &lt;merge-date-08a&gt; — Session/scope read side; integrates 48 explicit `# TODO(prompt-08)` markers across 10 sqlite projection `queries.py` files; UT-7 read-side closure)**, **08b (PR #&lt;PR-08b&gt;, merged &lt;merge-date-08b&gt; — governance + observation + UT-7 write-side closure; integrates 12 implicit attribution sites in `adminme/daemons/xlsx_sync/reverse.py`)**, **09a (PR #&lt;PR-09a&gt;, merged &lt;merge-date-09a&gt; — skill runner wrapper around OpenClaw `/tools/invoke`; `httpx`-mediated; 18 new tests; PM-19 introduced (prompts that introduce a seam must check the merged stub against the contract and fold any field-shape drift into Commit 1); `verify_invariants.sh §8` `single-seam check)**, **09b (PR #&lt;PR-09b&gt;, merged &lt;merge-date-09b&gt; — first canonical skill pack `classify_thank_you_candidate` v1.3.0; 8 new tests (4 unit + 4 integration); `bootstrap/pack_install_order.yaml` queued for prompts 15/16; zero domain events, zero `verify_invariants.sh` edits — pure wrapper-consumer)**, **10a (PR #33, merged 2026-04-26 — pipeline runner per BUILD.md §L4; `adminme/pipelines/{base,pack_loader,runner}.py` + `tests/fixtures/pipelines/{echo_logger,echo_emitter}/` + 17 new tests (8+5 unit + 4 integration); pipeline→projection canary armed and clean; `PipelineContext` threads `Session` + `run_skill_fn` + `outbound_fn` + `guarded_write` + `observation_manager` + `triggering_event_id` + `correlation_id`; reactive-only — proactive packs skipped during `discover()` per UT-2 carve-out, OpenClaw standing-order registration deferred to 10c; zero new event-schema registrations)**, **10b-i (PR #38, merged 2026-04-26 — reactive pipelines `identity_resolution` (heuristic-only, degenerate-clean candidate-loader) + `noise_filtering` (skill-call seam to `classify_message_nature`); skill pack `classify_message_nature@2.0.0` (full 09b shape); two new event schemas at v1 (`identity.merge_suggested`, `messaging.classified`); 22 new tests (3 + 1 + 8 + 1 + 5 + 4); suite 423 → 447 passed; `verify_invariants.sh` exit 0; UT-11 closed; refactored prompt committed to repo at `prompts/10b-i-identity-and-noise.md`, 320 lines — first deviation from PM-21's paste-only convention)**, **10b-ii-α (PR #41, merged 2026-04-28 — parties-DB seam through `PipelineContext.parties_conn_factory` (default None for 10a backward compat) + `PipelineRunner.__init__` extended; reactive pipeline `commitment_extraction@4.2.0` (full REFERENCE_EXAMPLES.md §2 architecture: classify → extract → emit, with defensive-default-on-skill-failure F-2 widened to catch all 7 SkillRunner exception types); 2 new skill packs `classify_commitment_candidate@3.0.0` + `extract_commitment_fields@2.1.0` (full 09b shape); one new event schema at v1 (`commitment.suppressed` with closed reason Literal `["below_confidence_threshold", "dedupe_hit", "skill_failure_defensive_default"]`); 26 new tests (4 + 4 + 1 + 11 + 3 + 3); suite tally on `tests/`: 447 → 464 passed, 2 skipped; `verify_invariants.sh` exit 0; UT-12 CLOSED via option (c)+(a) — split itself is option (c), parties-DB seam wired through `PipelineContext` is option (a); refactored prompt committed to repo at `prompts/10b-ii-alpha-commitment-extraction.md`, 370 lines)**.

**Sequence updates merged (infrastructure, not build):** **PR #37 `sequence-update-10b-split` (merged 2026-04-26)** — splits 10b into 10b-i / 10b-ii per the on-disk split memo at `docs/01-split-memo-10b.md`; updates PROMPT_SEQUENCE.md sequence table + dependency graph + hard-sequential-dependency line; deletes `prompts/10b-reactive-pipelines.md`. Single commit on harness-assigned branch `claude/sequence-update-10b-split-OFrFL`. No code touched; no BUILD_LOG entry by design (PM-22). **PR #39 `sequence-update-10b-ii-split` (merged 2026-04-27)** — splits 10b-ii into 10b-ii-α / 10b-ii-β per `docs/02-split-memo-10b-ii.md`; updates PROMPT_SEQUENCE.md sequence table + dependency graph (now `10a → 10b-i → 10b-ii-α → 10b-ii-β → 10c → 10d`) + hard-sequential-dependency line; lands `docs/02-split-memo-10b-ii.md` (PM-24 web-UI path was used for the long static memo). No code touched; no BUILD_LOG entry by design (PM-22). **PR #40 `update-partner-handoff` (merged 2026-04-27)** — partner_handoff snapshot update reflecting the secondary split. Single commit; no BUILD_LOG entry (planning-artifact PR per PM-22).

**Checkpoints landed:** **07.5 (`docs/checkpoints/07.5-projection-consistency.md`, 2026-04-25)** — projection consistency audit across the 07a/07b/07c-α/07c-β cohort plus L1-adjacent reverse daemon. Verdict: PASS with 1 non-critical finding (C-1: Raw Data builder `ALWAYS_DERIVED` missing `is_manual` while descriptor `always_derived` includes it; deferred to sidecar PR `sidecar-raw-data-is-manual-derived`). UT-1 closes here.

**Prompts with PR open, not yet merged:** none.

**Prompts drafted, ready for Claude Code execution:** **10b-ii-β** — refactored prompt produced 2026-04-28 in this Partner session, ready for prep PR + Claude Code execution. Artifact at `prompts/10b-ii-beta-thank-you-detection.md` (330 lines, well under the 350-line ceiling). Per `docs/02-split-memo-10b-ii.md` §"10b-ii-β" scope: ships `extract_thank_you_fields` skill pack at v1.0.0 + `thank_you_detection` pipeline pack at v1.0.0; reuses 10b-ii-α's parties-DB seam, defensive-default exception tuple, and per-member-overrides config shape literally; reuses 09b's `classify_thank_you_candidate@1.3.0` as upstream classifier; **no new infrastructure, no new event types**; emits `commitment.proposed` with `kind="other"` (default v1 disposition; do NOT silently extend the Literal) or `commitment.suppressed`. F-5 carry-forward addressed: pipeline early-returns on `messaging.sent` (vs. `commitment_extraction`'s defensive-default-suppress path). 4-commit decomposition: (1) skill pack, (2) pipeline pack + handler-direct unit tests, (3) per-member-overrides + classify-output-shape edge cases, (4) integration round-trip + BUILD_LOG + push.

**Sidecar PRs queued (non-blocking):** none. Most recent sidecar `sidecar-raw-data-is-manual-derived` merged as PR #35 on 2026-04-26 (closed 07.5 finding C-1). Most recent sequence updates: `sequence-update-10b-split` merged as PR #37 on 2026-04-26; `sequence-update-10b-ii-split` merged as PR #39 on 2026-04-27. Most recent partner_handoff snapshot update: PR #40 merged 2026-04-27.

**Next task queue (in order):**

1. **James: drive prep PR for 10b-ii-β refactored prompt + housekeeping for 10b-ii-α merge.** Single PR landing three changes in one commit on a fresh branch (e.g. `prep-10b-ii-beta`):
   - Create `prompts/10b-ii-beta-thank-you-detection.md` (the artifact produced by this Partner session).
   - Update `docs/build_log.md` in two places: (a) fill the placeholders in the prompt 10b-ii-α entry — `PR #41`, `<sha1>..<sha4>`, `<merge-date>`, change `Outcome: IN FLIGHT (PR open)` to `Outcome: MERGED`; (b) append the sequence-update-10b-ii-split + partner_handoff PRs (#39, #40) to the "Sidecar PRs" section per PM-22.
   - Update `docs/partner_handoff.md` with this Partner session's snapshot (the file Partner produced 2026-04-28).
   No four-commit discipline (this is the prep + housekeeping PR, not a build prompt). No tests. Per PM-22 it's a planning-artifact PR.

2. **James: drive Claude Code session to execute 10b-ii-β.** Paste the refactored prompt verbatim into Claude Code; it self-executes the four commits + opens PR per the "PR creation with gh/MCP fallback" rule. Single Type 3 (refactor-only) Partner session next, after the 10b-ii-β PR merges, to QC + refactor 10c.

3. **No out-of-band `D-prompt-tier-and-pattern-index.md` update needed for 10b-ii-β** — that file already has the "Was split on arrival" disposition with both 10b-ii-α and 10b-ii-β rows from the prior secondary-split sequence update.

4. **Watch: 10c orientation** — pre-split candidate per `D-prompt-tier-and-pattern-index.md`. The Partner session that opens 10c MUST forecast the split before drafting any prompt content (PM-23). UT-2 (proactive pipeline registration: AGENTS.md concatenation path) resolves at 10c orientation. UT-13 (10c is the next pre-split candidate) tracks this.

---

## Prompt-writing decisions (PM)

PM = "prompt-method" decisions surfaced during refactor sessions. Tagged HARD (binding) or SOFT (advisory).

### PM-1: Single canonical PROMPT_SEQUENCE.md — HARD (RESOLVED)

The root-level duplicate was removed via `sidecar-prompt-sequence-version-drift`. Only `prompts/PROMPT_SEQUENCE.md` is canonical. Status: **RESOLVED**.

### PM-2: 9 constitutional docs are not cached between sessions — HARD

Reading them is a per-session activity. Step 3 of session start.

### PM-3: Sidecar discipline — HARD

Defects in already-merged code that bleed forward get sidecar PRs (single-purpose, single branch, separate from the build sequence). 15-25 minute ceiling. If the fix is bigger, it's a split, not a sidecar. Sidecar memos use the convention `prompts/NN.5-<slug>.md`.

### PM-4: Carry-forwards are first-class state — HARD

Each prompt's BUILD_LOG entry includes "Carry-forward for prompt X" sections. These are how the system shares state without a database. Future Partner sessions read them, not just the code.

### PM-5: Tier C memos for non-trivial decisions — HARD

A "split" or "structural reorganization" is a Tier C decision; it gets a memo (`docs/NN-split-memo-<original>.md`) before any sequence update. Provides the durable record of why the change was made.

### PM-6: Quality bar references — HARD

Each refactored prompt cites the previous prompt as the quality bar in its header. Forces explicit comparison; prevents drift.

### PM-7: Carry-forwards firing in 3+ prompts graduate to universal preamble — HARD (EXECUTED)

See `docs/universal_preamble_extension.md`. CF-1..CF-7 accumulated in 07a/07b and were extracted via the PM-7 infrastructure PR (slim preamble in `prompts/PROMPT_SEQUENCE.md` + canonical `scripts/verify_invariants.sh`). Status: **EXECUTED 2026-04-24**. All future prompts (07c onward) drafted in slim form; cross-cutting discipline lives in the preamble + verify script, not in each prompt.

### PM-8: Inline implementation code in prompts is a warning sign — SOFT

If Deliverables section runs over 5K tokens, it's spec-heavy rather than contract-heavy — trading Claude Code's judgment for Partner's specificity. Describe contract (inputs, outputs, invariants, errors) when possible; inline bodies only when they're spec (regex patterns a canary must use). **HARD ceiling: ≤40 lines of inline code (whole prompt) per E-session-protocol §Per-prompt size budgets.**

### PM-9: Sheets / features needing unregistered event types get TODO markers, not deferred prompts — HARD

Prompt 07b shipped Lists/Members/Assumptions/Dashboard/Balance Sheet/Pro Forma/Budget vs Actual as sheet-builder TODOs. They populate when emitting prompts ship. Fragmenting into more prompts destroys cohesion. Same pattern in 10b-ii-α: `telephony.voicemail_transcribed` / `calendar.event.concluded` / `capture.note_created` listed as TODO comments in the manifest, not as separate sub-prompts.

### PM-10: Stub files from earlier scaffold prompts need explicit disposition — SOFT (07c resolved xlsx stubs)

Prompt 02 scaffolded `xlsx_workbooks/forward.py`, `reverse.py`, `schemas.py` as stubs. Prompt 07b built alongside rather than in them. **07c deletes all three** — forward daemon code lives in `__init__.py`/`builders.py`; reverse daemon lives in `adminme/daemons/xlsx_sync/reverse.py` per BUILD.md §3.11 line 995; `schemas.py` was empty noise. PM-10 remains as a SOFT pattern for future prompts: every prompt touching an area with scaffolded stubs explicitly decides repurpose / delete / continue ignoring.

### PM-11: Load only what the session needs from the zip — HARD

Partner sessions that ingest the whole codebase run out of headroom before producing refactored prompts. Load minimum per rule-of-thumb table in Step 6. Constitutional docs are separate — always loaded fully. Code files are selective.

### PM-12: Prompt refactor is additive AND subtractive — SOFT

Refactoring doesn't just fix — it also removes what the preamble now covers. Extraction is as valuable as addition. A refactored prompt should be **smaller** than the draft it replaced if the preamble has grown to cover cross-cutting concerns.

### PM-13: Project knowledge is retrievable via search, not enumerable via filesystem — HARD

Claude Chat's Project knowledge is not filesystem-browsable. The `/mnt/project/` mount shows only a subset of uploaded files. Partner discovers Project knowledge contents via the `project_knowledge_search` tool. Running `project_knowledge_search` on targeted terms (e.g. "SYSTEM_INVARIANTS binding invariants", "partner_handoff current build state") confirms files are present. **Never claim a file is missing from Project knowledge based on `/mnt/project/` listing alone — only an empty `project_knowledge_search` result is authoritative evidence of absence.** Partner runs these searches proactively at startup, not when prompted.

### PM-14: Daemons live in `adminme/daemons/`, projections in `adminme/projections/` — HARD

Introduced in 07c. The xlsx reverse daemon is architecturally an L1-adjacent adapter (ingests external state — file edits — and emits typed events into the event log). Per BUILD.md §3.11 line 995, it lives at `adminme/daemons/xlsx_sync/reverse.py`, NOT in `adminme/projections/xlsx_workbooks/`. The two directories enforce a structural distinction:

- `adminme/projections/` — pure-functional event consumers; emit only system events; `verify_invariants.sh`'s §2.2 audit applies (`ALLOWED_EMIT_FILES` allowlist).
- `adminme/daemons/` — adapters/daemons that emit domain events on external authority (file edits, webhook events, time-based ticks). NOT covered by the §2.2 projection-emit allowlist.

The forward xlsx daemon is the exception: it lives in `adminme/projections/xlsx_workbooks/` because it IS a projection (consumes events, regenerates derived state). It only EMITS system events; that's what §2.2 permits.

Future adapter prompts (11, 12) will populate `adminme/adapters/` for adapters that don't share the daemon characteristic (Gmail, Plaid, etc.). The naming convention is therefore: `daemons/` for long-running file/clock-based watchers; `adapters/` for request/response or pull-based external integrations. Both emit domain events; both live outside the projections audit scope. **Pipelines** (10a, 10b-i, 10b-ii-α) live under `packs/pipelines/` and are also outside the projections audit scope.

### PM-15: Two-prompt splits when a draft asks for both new infrastructure AND a long-running daemon consuming it — HARD

Surfaced by 07c. The original `prompts/07c-xlsx-workbooks-reverse.md` draft asked Claude Code to land schema additions, sidecar I/O, descriptors, diff core, full reverse daemon class, watchdog→asyncio bridge, lock contention, undo window, integration round-trip, and smoke script in one session. That overruns Claude Code's session window — proven empirically by two attempts that died partway through.

Resolution: split into 07c-α (foundations: schema, sidecar I/O, descriptors, diff core, forward sidecar writer) and 07c-β (daemon class + watchdog + integration round-trip + smoke). Each fits a session; together they close the round-trip. Both PR descriptions and BUILD_LOG entries label the prompt "Part 1 of 2" / "Part 2 of 2." Same pattern reapplied at 10b-ii-α/β: infrastructure (parties-DB seam) ships with first consumer (`commitment_extraction`); second consumer (`thank_you_detection`) reuses the seam without further infrastructure work.

### PM-16: Symbol-name verification at consumer-prompt boundary — HARD

When prompt N's draft cites symbols that prompt N-1 was supposed to land (e.g., `TASKS_DESCRIPTOR`, `find_party_by_identifier`, `ctx.parties_conn_factory`), Partner verifies against what actually shipped on main, not against what the producer prompt's draft text said would land. Public vs. private API drifts are a frequent silent-failure mode at the consumer-prompt boundary. Confirmed working in 10b-ii-α (verified `PipelineContext` shape, `find_party_by_identifier` signature, `CommitmentProposedV1` Literal values against on-main code before drafting); reapplied for 10b-ii-β (verified `parties_conn_factory` is on main, `commitment.suppressed` registered at v1, `classify_thank_you_candidate@1.3.0` output shape).

### PM-17: Skill packs use a fully-fledged 09b shape — HARD

Established in 09b: every skill pack ships `pack.yaml` (id, version, kind, optional model block) + `SKILL.md` (YAML frontmatter with name/namespace/version/description/input_schema/output_schema/provider_preferences/max_tokens/temperature/sensitivity_required/context_scopes_required/timeout_seconds/outbound_affecting/on_failure, plus markdown body) + `schemas/input.schema.json` + `schemas/output.schema.json` + optional `prompt.jinja2` + `handler.py` (post_process function) + `tests/test_skill.py` (pack-loader canary + handler-direct cases). 10b-i (`classify_message_nature@2.0.0`) and 10b-ii-α (`classify_commitment_candidate@3.0.0` + `extract_commitment_fields@2.1.0`) all reused this shape. 10b-ii-β reuses it again for `extract_thank_you_fields`.

### PM-18: Pipeline packs use a 10a-shape manifest + 10b-i-shape handler — HARD

Pipeline pack shape: `pipeline.yaml` (manifest with pack/runtime/triggers/depends_on/events_emitted/optional config blocks) + `handler.py` (implements `Pipeline` Protocol) + `tests/test_pack_load.py` (pack-loader canary). `commitment_extraction` extends with `config.example.yaml` + `config.schema.json` for per-member overrides; `thank_you_detection` reuses that shape literally. Future pipelines that don't need configurable thresholds (e.g. `recurrence_extraction`) can omit the config files.

### PM-19: Wrappers introduce field-shape stubs to verify against contract — HARD

Surfaced in 09a. Prompts that introduce a seam must check the merged stub against the contract and fold any field-shape drift into Commit 1, not a separate PR. Reapplied for 10b-ii-α: parties-DB seam shipped in Commit 1 with `parties_conn_factory: Callable[[], "sqlcipher3.Connection"] | None = None` field added to `PipelineContext` AND simultaneous runner-side wiring change.

### PM-20: HTTP-seam tests — `httpx.MockTransport` or `respx` — SOFT

Surfaced in 09a. Prompt 09a specified "use respx (or httpx.MockTransport)." Claude Code's tests used `httpx.MockTransport` directly. Both are acceptable. Decision deferred to first 11+ adapter prompt that adds an HTTP wrapper.

### PM-21: Refactored prompts ship in the build PR — HARD (graduated 2026-04-28)

Surfaced in 10a QC (2026-04-26). Originally SOFT — refactored prompts were paste-only and held in chat; build_log entries served as the durable QC record. **Tracking history:**

- **10b-i (PR #38, 2026-04-26):** refactored 320-line prompt committed to repo at `prompts/10b-i-identity-and-noise.md` as part of the build PR. **First** deviation from the historical paste-only convention.
- **10b-ii-α (PR #41, 2026-04-28):** refactored 370-line prompt committed at `prompts/10b-ii-alpha-commitment-extraction.md` as part of the build PR (separately from the four-commit phase work — Partner produced the file 2026-04-28 and James pre-loaded it onto the working branch via the same prep-PR pattern PM-24 established for long static markdown).

**Two consecutive merges following the pattern.** PM-21 graduates from SOFT-watch to **HARD convention**: refactored prompts ship as part of the build PR's prep step. The on-disk prompt file is now authoritative for QC archaeology. **The prep PR pattern:** James creates a fresh branch, lands `prompts/<NN>-<slug>.md` via GitHub web UI (per PM-24 for long static markdown), then opens Claude Code with the build prompt for the four-commit phase work. Two PRs sequentially: prep PR with the refactored prompt + Partner-side housekeeping (build_log placeholders, partner_handoff snapshot), then build PR with the four-commit phase work.

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
- Partner-handoff snapshot updates (PR #40 was the canonical example) — single-commit, no BUILD_LOG, planning-artifact PR.

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

**Reapplication for 10b-ii-α prep PR:** James used the web-UI path for `prompts/10b-ii-alpha-commitment-extraction.md` (370 lines of static prompt prose). Same pattern recommended for the 10b-ii-β prep PR.

### PM-25: Artifact paste-render risk → ship long artifacts as downloadable files — HARD

Surfaced 2026-04-28 by the 10b-ii-α prep-PR micro-prompt — the artifact looked clean in the chat UI source view but rendered with autolinker brackets when copied via the chat UI. Discipline: **for any artifact > ~50 lines containing code, YAML, or JSON, Partner produces it as a downloadable file via the `create_file` + `present_files` tools, not as inline chat content.** James downloads, opens in a plain text editor, copies from there into Claude Code or the GitHub web UI, bypassing the chat client's renderer entirely. Inline chat content is acceptable only for short artifacts where the render-then-copy pathway is confirmed safe (paragraph-level guidance, BUILD_LOG entry templates James commits via text editor anyway, `partner_handoff.md` update fragments).

The full discipline lives in `E-session-protocol.md` §2.10 (the rule itself + the §2.9 delivery-gate item) and `C-context-loading-spec.md` ("Artifact production discipline" section).

**Canonical failure case:** the 2026-04-28 prep-PR micro-prompt for 10b-ii-α — the artifact looked clean in the chat UI source view but rendered with autolinker brackets when copied. Fix shipped same-day: `E-session-protocol.md` §2.10 + `C-context-loading-spec.md` artifact-production section + this PM-25 entry.

### PM-26: Post-PM-7 byte-budget calibration drift in E-session-protocol §2.9 — SOFT (proposed)

Surfaced 2026-04-28 during 10b-ii-β refactor self-check. The current `E-session-protocol.md` §Per-prompt size budgets table sets `Total prompt size: ≤ 25 KB` with rationale "token economy in Claude Code's reading pass." Empirically, the universal preamble alone (post-PM-7 slim form) is ~5 KB; a 17-item Read-first block is another ~5 KB; Operating Context plus Deliverables put any well-formed post-PM-7 prompt at ~30–40 KB. 10b-ii-α (370 lines, ~40 KB) shipped successfully; 10b-ii-β (330 lines, ~39 KB) is forecast to ship successfully on the same empirical basis.

**The 25 KB ceiling appears to be a measurement artifact** of the budget-table calibration predating universal-preamble extraction. Line count remains the operationally relevant signal; the line-count budget (≤350 lines) is consistent with empirical session-window observations.

**Proposed fix (deferred — not blocking 10b-ii-β):** at the next E-session-protocol revision (Partner setup file refresh), update the byte-budget row to `Total prompt size: ≤ 45 KB` OR replace it with a deliverables-density metric (e.g., new modules per prompt, which is already captured at "≤4 net-new modules"). Until then, line-count is the binding signal at §2.9.

**Why SOFT, not HARD:** the budget-table is in Partner setup, not in repo, so the fix is a Partner-side documentation refresh rather than a sequence-update PR. Defer until 10c orientation surfaces another data point or until a Partner setup refresh cycle naturally rolls.

---

## Open tensions / unresolved things

### UT-1: When to refactor checkpoint 07.5 — CLOSED 2026-04-25

Original 07.5 assumed 11 projections in one prompt. After 07a/07b/07c-α/07c-β split, it audits four ops prompts together as a cohort. Audit landed at `docs/checkpoints/07.5-projection-consistency.md` on 2026-04-25 post-07c-β merge. Verdict PASS with one non-critical sidecar finding (C-1, queued as `sidecar-raw-data-is-manual-derived`). Status: **CLOSED**.

### UT-2: Proactive pipeline registration path (10c) — OPEN

`[D1]` confirmed: proactive pipelines register via workspace-prose `AGENTS.md` + `openclaw cron add`. Prompt 10c will generate both. Concrete question: does bootstrap §8 concatenate per-pipeline markdown into AGENTS.md and issue cron adds, or ship AGENTS.md pre-written? Answer lands when 10c is refactored. Tracked in UT-13 since the broader split shape for 10c is the larger open question.

### UT-3 (RESOLVED 2026-04-25): Prompt 08 split executed

Prompt 08 split into **08a (Session + scope, read side)** and **08b (governance + observation + UT-7 closure, write side)**. Status: **RESOLVED**.

### UT-4 through UT-10 — all RESOLVED in earlier sessions.

(See git history of this file for closure dates and contexts.)

### UT-11: Pipeline pack location — CLOSED 2026-04-26

Convention `packs/pipelines/<n>/` confirmed by 10b-i shipping there with no friction; mirrors 09b's `packs/skills/<n>/`. Status: **CLOSED**.

### UT-12: Parties-DB seam decision — CLOSED 2026-04-28

Three options surfaced during 10b-i QC: (a) thread `parties_conn_factory` through `PipelineContext`; (b) use 10b-i's injectable-loader pattern for `commitment_extraction` too (degenerate-clean); (c) split 10b-ii into 10b-ii-α and 10b-ii-β. Resolution baked into the split: **option (c)+(a)** — split itself is option (c); parties-DB seam wired through `PipelineContext` as 10b-ii-α's Commit 1 is option (a). PR #41 (10b-ii-α) shipped this resolution 2026-04-28. Status: **CLOSED**.

### UT-13: 10c is the next pre-split candidate — OPEN

`D-prompt-tier-and-pattern-index.md` flags 10c (proactive pipelines: `morning_digest`, `paralysis_detection`, `reminder_dispatch`, `reward_dispatch`, plus possibly `recurrence_extraction`, `closeness_scoring`, `relationship_summarization`) as a pre-split candidate. Per `docs/architecture-summary.md` §5, several are reactive and several are proactive. The split shape will depend on whether they group by trigger mechanism (reactive vs. proactive — natural coupling to OpenClaw standing-order registration) or by capability axis (commitment-flavored vs. recurrence-flavored vs. relationship-flavored). The Partner session that opens 10c orientation will forecast the split before drafting per PM-23. UT-2 (AGENTS.md concatenation path for proactive pipeline registration) is a sub-question that splits along with 10c.

**Status:** OPEN, resolves at 10c orientation. **Becomes the active focus once 10b-ii-β merges.**

---

## Workflow norms

### Split QC and next-prompt-refactor into separate sessions when either is big

Small cases (prompts 03–05): QC + refactor one session is fine.

Big cases (07b onward): two sessions. Session 1 runs QC of latest merge, writes findings into this file, writes refactor brief for next prompt. Session 2 picks up brief and writes prompt.

Mirrors Claude Code's incremental-commit discipline: cap per-session cognitive load, make handoffs explicit.

**For 10b-ii-α → 10b-ii-β specifically:** the QC was clean and the refactor was small (sub-prompt of a secondary split, no new infrastructure, no new event types), so a Type 1 combined session worked cleanly 2026-04-28.

### End every Chat session by updating this file

Under "Current build state": update "last updated" date, move merged prompts from "PR open" to "merged," update "next task queue."

Under "Prompt-writing decisions": add any new PM entry if surfaced. Tag HARD or SOFT.

Under "Open tensions": add any new UT; close any UT this merge resolved.

Rule: if a future Partner session would benefit from knowing this, write it here. If only relevant to current session, don't.

### Don't trust cached readings across sessions

The 9 constitutional docs are not cached between Chat sessions. Fresh instance hasn't read them. Re-read as needed via `project_knowledge_search`.
