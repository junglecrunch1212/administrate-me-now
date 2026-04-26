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

**Last updated:** 2026-04-26 (09a refactored and ready for Claude Code execution; prep PR `prep-09a-2026-04-26` queued. Partner Session of 2026-04-26 was Type 3 — refactor-only — targeting 09a. Job 2 surfaced one drift: SkillCallRecordedV2 schema declares token/cost fields as required non-negative numbers, but ADR-0002's graceful-degradation clause requires them to be optional. Folded into 09a Commit 1 rather than spun out as a sidecar — the prompt itself activates the seam. Job 3 §2.9 self-check PASS on every budget; single prompt, no split.)

This section is the live baton between sessions. Update it at the end of every Partner session.

**Prompts merged to main:** 00, 00.5, 01 (01a/01b/01c), 02, 03, 03.5, 04, 05, 06, 07a, 07b, **PM-7 infrastructure PR (slim preamble + scripts/verify_invariants.sh)**, **07c-α (PR #20, merged 2026-04-24)**, **07c-β (PR #21, merged 2026-04-25 — reverse daemon class + 4 emit pathways + integration round-trip; closes the xlsx round-trip and resolves UT-6)**, **08a (PR #&lt;PR-08a&gt;, merged 2026-04-25 — Session model + scope enforcement; 48 TODO(prompt-08) markers cleared across 10 sqlite projection queries.py files; 69 new tests; resolves UT-8 inline via `vector_search.nearest` three-layer carve-out)**, **08b (PR #&lt;PR-08b&gt;, merged 2026-04-25 — guardedWrite three-layer + observation `outbound()` + 6 governance event types at v1; 47 new tests + 4 security-E2E + 1 UT-7 closure case; resolves UT-7 — `_ACTOR` literal removed from reverse daemon, sidecar hedge NOT activated)**.

**Checkpoints landed:** **07.5 (`docs/checkpoints/07.5-projection-consistency.md`, 2026-04-25)** — projection consistency audit across the 07a/07b/07c-α/07c-β cohort plus L1-adjacent reverse daemon. Verdict: PASS with 1 non-critical finding (C-1: Raw Data builder `ALWAYS_DERIVED` missing `is_manual` while descriptor `always_derived` includes it; deferred to sidecar PR `sidecar-raw-data-is-manual-derived`). UT-1 closes here.

**Prompts with PR open, not yet merged:** none.

**Prompts drafted, ready for Claude Code execution:** 09a (`prep-09a-2026-04-26`).

**Sidecar PRs queued (non-blocking):**
- **`sidecar-raw-data-is-manual-derived`** — surfaced by 07.5 finding C-1. Two-file change:
  1. `adminme/projections/xlsx_workbooks/sheets/raw_data.py:45` — add `"is_manual"` to `ALWAYS_DERIVED` so it reads `{"txn_id", "plaid_category", "is_manual"}`.
  2. New unit test (location TBD by Claude Code; suggested: extend `tests/unit/test_xlsx_finance_workbook.py`) asserting `from adminme.projections.xlsx_workbooks.sheets.raw_data import ALWAYS_DERIVED` matches `descriptor_for(FINANCE_WORKBOOK_NAME, "Raw Data").always_derived`. The single assertion is the canary that blocks future drift in either direction.

  ≤15 minutes. Cosmetic protection drift, not a correctness bug; does not block prompt 08. **The C-1 finding text in the audit memo doubles as the sidecar memo** — Partner does not need to write a separate `prompts/<NN.5>-<slug>.md` file; James can hand Claude Code the audit-memo §C content + the assertion above.

**Next task queue (in order):**

1. **Sidecar Claude Code session: ship `sidecar-raw-data-is-manual-derived`** — non-blocking; carried forward from the 07.5 audit (C-1 finding). Two-file change in `adminme/projections/xlsx_workbooks/sheets/raw_data.py:45` + a unit test asserting equivalence with `descriptor_for(FINANCE_WORKBOOK_NAME, "Raw Data").always_derived`. ≤15 minutes. Cosmetic protection drift; does not block prompt 09a.
2. **Partner session: refactor 09a (Skill runner — Type 3, refactor-only).** Draft exists at `prompts/09a-skill-runner.md` in v1 long form (no slim preamble; "Phase:" prose header). Pre-split disposition per `D-prompt-tier-and-pattern-index.md`: single prompt expected (pattern Introduction). Refactor must (a) prepend the slim post-PM-7 preamble verbatim, (b) update "Depends on" from "Prompt 08" to "08a + 08b merged", (c) wire `SkillContext` to import `Session` from `adminme.lib.session`, (d) thread `outbound(session, action, payload, action_fn)` from `adminme.lib.observation` for any external side effect, (e) thread `guarded_write.check(session, "skill.invoke", ...)` from `adminme.lib.governance` before HTTP dispatch to OpenClaw, (f) confirm `docs/reference/openclaw/` has the files 09a depends on (skill manifest shape, `/skills/invoke` endpoint) — if not, this is a prompt 00.5 sidecar before 09a can ship. Job 3 size budget should fit comfortably in slim form (target ~200 lines, well under the 350-line ceiling).
3. **Claude Code session: execute 09a.** James drives. Ships `adminme/lib/skill_runner/wrapper.py` + dummy skill pack `packs/skills/classify_test/`.
4. **Partner session: QC of 09a merge + refactor 09b.** Type 1 combined if QC small. 09b ships the first canonical skill pack (classify_thank_you_candidate per BUILD.md L4).
5. Continuing through prompt 18 (Phase A build-complete), then 19 (Phase B smoke test).

**Pre-merge verification James should run before committing this housekeeping PR:**

```bash
gh pr list --state merged --limit 5 --json number,title,mergedAt,mergeCommit
```

…and find-and-replace `<PR-08a>` / `<PR-08b>` / `<sha1-08a>` etc. in `docs/build_log.md` and `docs/partner_handoff.md` with the actual values. Expected sequence: PR #21 = 07c-β; PR #22 = split-08 prep (drafts only, no code); PR #23 = 08a; PR #24 = 08b. Confirm via `gh` before commit.

**Prompts drafted but not yet refactored:** 09b, 10a, 10b, 10c, 10d, 11, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19. (09a moved to "ready for Claude Code execution" 2026-04-26.) The slim preamble means each refactor is shorter than 07a/07b were. 15, 16 remain pre-split candidates; per `[D-prompt-tier-and-pattern-index.md](http://D-prompt-tier-and-pattern-index.md)`, 10b, 10c, 11, 14b, 17 are also pre-split candidates.

**Prompts not yet drafted:** none. 07c was the last unwritten prompt; it was split into 07c-α (merged) and 07c-β (merged). Everything from 08 onward exists in unrefactored form. The original `prompts/07c-xlsx-workbooks-reverse.md` was retired as part of the α/β split.

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

PM-15 general statement: when a draft prompt asks for both (a) new infrastructural modules (schema + descriptors + diff + sidecar) AND (b) a new long-running daemon class consuming that infrastructure, with a full per-pathway test pyramid for both, **default to splitting**. Foundations land first; the daemon consumes them in part 2. Partner produces a Tier C split memo (per E-session-protocol §Splits) before drafting either sub-prompt.

Existing examples of the same pattern: 01 → 01a/01b/01c (architecture + cheatsheet + invariants), 07 → 07a/07b/07c-α/07c-β (ops projections + xlsx forward + xlsx round-trip foundations + xlsx reverse daemon).

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

### UT-9: ALLOWED_EMITS per-file allowlisting in scripts/verify_[invariants.sh](http://invariants.sh)

09a Commit 4 extends `ALLOWED_EMITS` to permit three new event types from `adminme/lib/skill_runner/[wrapper.py](http://wrapper.py)`. The xlsx single-seam pattern (`ALLOWED_EMIT_FILES`) was carried forward as a comment in 07c-α. 09a ships using the same pattern if the script structure supports it cleanly; if not, falls back to test-side enforcement per PM-17 (the path 08b chose for `external.sent`/`observation.suppressed`). After 09a merges, Partner reviews which path was taken and decides whether to harden the script's per-file allowlist support — a candidate single-purpose PR. Status: open after 09a merge.

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
│   ├── PROMPT_SEQUENCE.md                       # CANONICAL (single copy; slim preamble)
│   ├── 00-preflight.md ... 19-phase-b-smoke-test.md
│   ├── 07a-projections-ops-spine.md
│   ├── 07b-xlsx-workbooks-forward.md
│   ├── 07c-alpha-foundations.md                # MERGED (PR #20, 2026-04-24)
│   ├── 07c-beta-reverse-daemon.md              # MERGED (PR #21, 2026-04-25)
│   ├── 07.5-checkpoint-projection-consistency.md  # source contract; audit memo at docs/checkpoints/
│   ├── 08-session-scope-governance.md          # RETIRED (superseded by 08a + 08b)
│   ├── 08a-session-and-scope.md                # MERGED (PR #&lt;PR-08a&gt;, 2026-04-25)
│   ├── 08b-governance-and-observation.md       # MERGED (PR #&lt;PR-08b&gt;, 2026-04-25)
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
│   ├── lib/instance_config.py
│   ├── lib/session.py                          # MERGED 08a (Session dataclass, 3 constructors + xlsx_reverse_daemon constructor)
│   ├── lib/scope.py                            # MERGED 08a (allowed_read, privacy_filter, coach_column_strip, child_hidden_tag_filter, ScopeViolation, CHILD_FORBIDDEN_TAGS)
│   ├── lib/governance.py                       # MERGED 08b (GuardedWrite three-layer; ActionGateConfig, RateLimiter, AgentAllowlist)
│   ├── lib/observation.py                      # MERGED 08b (outbound() single seam per [§6.13/§6.14]; ObservationManager default-on)
│   └── (pipelines, products, openclaw_plugins, cli, adapters — stubs or partial)
├── tests/{unit,integration,fixtures,e2e}/
├── console/  bootstrap/  packs/
└── pyproject.toml  poetry.lock  .gitignore
```

---

## End of handoff document

Partner: steps 1–6 before any real work. Orient before acting.
