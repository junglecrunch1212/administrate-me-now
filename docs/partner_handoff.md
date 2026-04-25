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

**Last updated:** 2026-04-25 (07c-β merged via PR #21, BUILD_LOG housekeeping completed; 07.5 audit memo landed at `docs/checkpoints/07.5-projection-consistency.md`; UT-1 closes; UT-5 advanced; UT-6 RESOLVED; UT-7 carries forward to prompt 08).

This section is the live baton between sessions. Update it at the end of every Partner session.

**Prompts merged to main:** 00, 00.5, 01 (01a/01b/01c), 02, 03, 03.5, 04, 05, 06, 07a, 07b, **PM-7 infrastructure PR (slim preamble + scripts/verify_invariants.sh)**, **07c-α (PR #20, merged 2026-04-24)**, **07c-β (PR #21, merged 2026-04-25 — reverse daemon class + 4 emit pathways + integration round-trip; closes the xlsx round-trip and resolves UT-6)**.

**Checkpoints landed:** **07.5 (`docs/checkpoints/07.5-projection-consistency.md`, 2026-04-25)** — projection consistency audit across the 07a/07b/07c-α/07c-β cohort plus L1-adjacent reverse daemon. Verdict: PASS with 1 non-critical finding (C-1: Raw Data builder `ALWAYS_DERIVED` missing `is_manual` while descriptor `always_derived` includes it; deferred to sidecar PR `sidecar-raw-data-is-manual-derived`). UT-1 closes here.

**Prompts with PR open, not yet merged:** none.

**Prompts drafted, ready for Claude Code execution:** none. (07c-β shipped; 08 is the next refactor target — pre-split candidate per UT-3, see "Next task queue" below.)

**Sidecar PRs queued (non-blocking):**
- **`sidecar-raw-data-is-manual-derived`** — surfaced by 07.5 finding C-1. Two-file change:
  1. `adminme/projections/xlsx_workbooks/sheets/raw_data.py:45` — add `"is_manual"` to `ALWAYS_DERIVED` so it reads `{"txn_id", "plaid_category", "is_manual"}`.
  2. New unit test (location TBD by Claude Code; suggested: extend `tests/unit/test_xlsx_finance_workbook.py`) asserting `from adminme.projections.xlsx_workbooks.sheets.raw_data import ALWAYS_DERIVED` matches `descriptor_for(FINANCE_WORKBOOK_NAME, "Raw Data").always_derived`. The single assertion is the canary that blocks future drift in either direction.

  ≤15 minutes. Cosmetic protection drift, not a correctness bug; does not block prompt 08. **The C-1 finding text in the audit memo doubles as the sidecar memo** — Partner does not need to write a separate `prompts/<NN.5>-<slug>.md` file; James can hand Claude Code the audit-memo §C content + the assertion above.

**Next task queue (in order):**

1. **Sidecar Claude Code session: ship `sidecar-raw-data-is-manual-derived`** — non-blocking; can run in parallel with the 08 split memo session or after.
2. **Partner session: 08 split memo (Type 0)** — pre-split candidate per UT-3 + `D-prompt-tier-and-pattern-index.md`. Partner produces a Tier C split memo proposing **08a (Session construction + scope enforcement)** / **08b (privacy filter + authority gate + observation mode)**. The 07.5 audit confirms 48 explicit + 12 implicit (reverse-daemon emit attribution sites) = **60 attention sites**, supporting the split. Closing PR for that session is a `split-08-<date>` branch updating `prompts/PROMPT_SEQUENCE.md` + `D-prompt-tier-and-pattern-index.md` (flip 08's pre-split disposition to "Was split on arrival") + landing the new 08a + 08b draft files. **Do NOT draft a full single 08 prompt** — pre-split disposition forbids it.
3. **Partner session: refactor 08a** — Session construction + scope enforcement. Touches the 48 explicit TODO(prompt-08) markers in projection query files. UT-3 progresses.
4. **Partner session: refactor 08b** — privacy filter + authority gate + observation mode. UT-7 closes here (route reverse-daemon emits through guardedWrite to attribute principal_member_id; replaces the `actor_identity="xlsx_reverse"` literal across the attribution sites in `adminme/daemons/xlsx_sync/reverse.py`).
5. Continuing through prompt 18 (Phase A build-complete), then 19 (Phase B smoke test).

**Prompts drafted but not yet refactored:** 08, 09a, 09b, 10a, 10b, 10c, 10d, 11, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19. Each needs a refactor session before Claude Code executes it. The slim preamble means each refactor is shorter than 07a/07b were. 08, 15, 16 are pre-split candidates (Tier C memo first); per `D-prompt-tier-and-pattern-index.md`, additional candidates may surface at orientation.

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

---

## Open tensions / unresolved things

### UT-1: When to refactor checkpoint 07.5 — CLOSED 2026-04-25

Original 07.5 assumed 11 projections in one prompt. After 07a/07b/07c-α/07c-β split, it audits four ops prompts together as a cohort. Audit landed at `docs/checkpoints/07.5-projection-consistency.md` on 2026-04-25 post-07c-β merge. Verdict PASS with one non-critical sidecar finding (C-1, queued as `sidecar-raw-data-is-manual-derived`). Status: **CLOSED**.

### UT-2: Proactive pipeline registration path (10c)

`[D1]` confirmed: proactive pipelines register via workspace-prose `AGENTS.md` + `openclaw cron add`. Prompt 10c will generate both. Concrete question: does bootstrap §8 concatenate per-pipeline markdown into AGENTS.md and issue cron adds, or ship AGENTS.md pre-written? Answer lands when 10c is refactored.

### UT-3: Session / scope enforcement seam (prompt 08) needs splitting

48 explicit `# TODO(prompt-08)` markers across the 10 sqlite projections' `queries.py` files (snapshot from 07.5 audit, 2026-04-25). Plus 12 implicit prompt-08 attribution sites in `adminme/daemons/xlsx_sync/reverse.py` (every `_emit_*` helper currently stamps `actor_identity="xlsx_reverse"` per UT-7).¹ Total: **60 attention sites**. Prompt 08 wraps every query with `Session` (authMember + viewMember + scope) and routes reverse-daemon emits through guardedWrite. The 60-site count combined with the four new modules (session.py, scope.py, governance.py, observation.py) supports an 08a (Session construction + scope enforcement) / 08b (privacy filter + authority gate + observation mode) split per `D-prompt-tier-and-pattern-index.md` pre-split disposition. **Next Partner session is the split memo (Type 0)**, then 08a refactor (Type 3), then 08b refactor (Type 3).

¹ The 12-site count is the number of UT-7 attention sites the audit catalogued. The literal `type="..."` / `event_type="..."` emit lines in `reverse.py` are 10 (lines 404, 428, 505, 526, 548, 580, 625, 643, 703, 738); the 12-count adds 2 actor-stamping lines on the system-event envelopes (412, 436). A shared `_emit` helper at line 848 routes all per-pathway domain emits through one envelope construction; one fix to the helper plus per-pathway plumbing closes the seam. Both numbers (10 emit-type literals; 12 attention sites) are correct interpretations of "where prompt 08 must touch the file"; Partner's refactor session for 08b will catalogue these directly.

### UT-4: Placeholder values in xlsx protection passwords

07b uses `"adminme-placeholder"` as sheet-protection password. Real secret flow lands in prompt 16 (bootstrap wizard §5). Do not resolve earlier.

### UT-5: `<commit4>` and `<merge date>` placeholders in BUILD_LOG — current

07a and 07b entries had literal `<commit4>` and `<merge date>` placeholders. **Filled post-merge during Partner's QC pass per the rubric.** 07c-α entry filled with PR #20, commits aa395dd / 7305acd / fcdb592 / 1d770ec, merge date 2026-04-24. **07c-β entry filled with PR #21, commits ffa6d9c / bf649ed / 00bff7d / 2788761, merge date 2026-04-25** during the 2026-04-25 QC session's Pass A housekeeping. The full sequence is up-to-date through 07c-β; UT-5 will surface again after the next merge.

### UT-6: Sidecar-state JSON pathway for xlsx round-trip — RESOLVED 2026-04-25

Per BUILD.md §3.11 line 1009 + line 1015, the sidecar is written by both daemons: forward writes it after each regeneration (in the same lock as the xlsx write), and reverse rewrites it at the end of each cycle. Sidecar lives at `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json` (sibling to xlsx files).

07c-α landed the forward half (PR #20, merged 2026-04-24); 07c-β landed the reverse half (PR #21, merged 2026-04-25). The 07.5 audit confirmed the pathway is closed at both ends with three canaries: `tests/unit/test_xlsx_forward_writes_sidecar.py`, `tests/unit/test_xlsx_reverse_cold_start.py`, `tests/integration/test_xlsx_roundtrip.py`. Status: **RESOLVED**.

### UT-7: Reverse-daemon emit path bypasses Session / guardedWrite — open until prompt 08 (specifically 08b)

The xlsx reverse daemon (07c-β, merged 2026-04-25 via PR #21) emits domain events through `EventLog.append()` directly, with `actor_identity="xlsx_reverse"` as a documented placeholder, without routing through Session/guardedWrite/scope checks. This is the simple seam for now. A hostile file editor (or a malware-injected edit) could in principle drive the daemon to emit events on behalf of the principal without any rate-limit, action-gate, or authority check. Prompt 08b (privacy filter + authority gate + observation mode) will close this — reverse-emitted events should route through guardedWrite with the daemon as the agent identity.

The 07.5 audit (Section F) catalogued the 12 attention sites — 10 emit-type literals at lines 404, 428, 505, 526, 548, 580, 625, 643, 703, 738 + 2 actor-stamping lines on the system-event envelopes at 412, 436. The shared `_emit` helper at line 848 routes all per-pathway domain emits through one envelope construction; one fix to the helper plus per-pathway plumbing closes the seam. Status: **OPEN, scoped to prompt 08b**.

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
│   └── (pipelines, products, openclaw_plugins, cli, adapters — stubs or partial)
├── tests/{unit,integration,fixtures,e2e}/
├── console/  bootstrap/  packs/
└── pyproject.toml  poetry.lock  .gitignore
```

---

## End of handoff document

Partner: steps 1–6 before any real work. Orient before acting.
