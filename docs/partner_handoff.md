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

**Last updated:** 2026-04-24 (07c drafted; PM-7 + 07a + 07b confirmed merged; this update reflects the new Partner session that produced 07c).

This section is the live baton between sessions. Update it at the end of every Partner session.

**Prompts merged to main:** 00, 00.5, 01 (01a/01b/01c), 02, 03, 03.5, 04, 05, 06, 07a, 07b, **PM-7 infrastructure PR (slim preamble + scripts/verify_invariants.sh)**.

**Prompts with PR open, not yet merged:** none.

**Prompts drafted, ready for Claude Code execution:** **07c** (`prompts/07c-xlsx-workbooks-reverse.md`, drafted 2026-04-24, four-commit discipline, ~700 lines, slim form per post-PM-7 preamble).

**Next task queue (in order):**

1. **Claude Code executes 07c** — produces PR; James reviews and merges.
2. **Partner session: QC of 07c merge** — three-job pass per `docs/qc_rubric.md`. Update this handoff. Confirm verify_invariants.sh's extended `ALLOWED_EMITS` is honored. Confirm sidecar JSON pathway is structurally as drafted.
3. **Partner session: refactor 07.5** — original checkpoint was written assuming 11 projections built in one prompt. After the 07a/07b/07c split, it audits all three ops prompts together as a cohort. Per UT-1, this happens AFTER 07c ships.
4. **Partner session: refactor 08** — Session + scope enforcement + governance + observation mode. Large; UT-3 still open (likely splits into 08a/08b). After 07c, the TODO(prompt-08) marker count grows again — reverse daemon's emit path is currently un-Session-wrapped, and 08 closes that seam.
5. Continuing through prompt 18 (Phase A build-complete), then 19 (Phase B smoke test).

**Prompts drafted but not yet refactored:** 08, 09a, 09b, 10a, 10b, 10c, 10d, 11, 12, 13a, 13b, 14a, 14b, 14c, 14d, 14e, 15, 15.5, 16, 17, 18, 19. Each needs a refactor session before Claude Code executes it. The slim preamble means each refactor is shorter than 07a/07b were.

**Prompts not yet drafted:** none. 07c was the last unwritten prompt; everything from 08 onward exists in unrefactored form.

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

### PM-14: Daemons live in `adminme/daemons/`, projections in `adminme/projections/` — HARD (NEW)

Introduced in 07c. The xlsx reverse daemon is architecturally an L1-adjacent adapter (ingests external state — file edits — and emits typed events into the event log). Per BUILD.md §3.11 line 995, it lives at `adminme/daemons/xlsx_sync/reverse.py`, NOT in `adminme/projections/xlsx_workbooks/`. The two directories enforce a structural distinction:

- `adminme/projections/` — pure-functional event consumers; emit only system events; `verify_invariants.sh`'s §2.2 audit applies (`ALLOWED_EMIT_FILES` allowlist).
- `adminme/daemons/` — adapters/daemons that emit domain events on external authority (file edits, webhook events, time-based ticks). NOT covered by the §2.2 projection-emit allowlist.

The forward xlsx daemon is the exception: it lives in `adminme/projections/xlsx_workbooks/` because it IS a projection (consumes events, regenerates derived state). It only EMITS system events; that's what §2.2 permits.

Future adapter prompts (11, 12) will populate `adminme/adapters/` for adapters that don't share the daemon characteristic (Gmail, Plaid, etc.). The naming convention is therefore: `daemons/` for long-running file/clock-based watchers; `adapters/` for request/response or pull-based external integrations. Both emit domain events; both live outside the projections audit scope.

---

## Open tensions / unresolved things

### UT-1: When to refactor checkpoint 07.5

Original 07.5 assumed 11 projections in one prompt. After 07a/07b/07c split, it audits three prompts together. **Refactor 07.5 AFTER 07c ships**, not before — pre-07c refactor would encode 07c assumptions that may not hold. Status: still open until 07c merges.

### UT-2: Proactive pipeline registration path (10c)

`[D1]` confirmed: proactive pipelines register via workspace-prose `AGENTS.md` + `openclaw cron add`. Prompt 10c will generate both. Concrete question: does bootstrap §8 concatenate per-pipeline markdown into AGENTS.md and issue cron adds, or ship AGENTS.md pre-written? Answer lands when 10c is refactored.

### UT-3: Session / scope enforcement seam (prompt 08) may need splitting

38+ `# TODO(prompt-08)` markers across projection query files (growing — more after 07b merged, more after 07c merges). Prompt 08 wraps every query with `Session` (authMember + viewMember + scope). Large — likely ≥15K tokens even with slim preamble. May need 08a (Session construction + scope enforcement) / 08b (privacy filter + authority gate). Decide when 08 is refactored. After 07c merges, UT-7 also feeds into this prompt.

### UT-4: Placeholder values in xlsx protection passwords

07b uses `"adminme-placeholder"` as sheet-protection password. Real secret flow lands in prompt 16 (bootstrap wizard §5). Do not resolve earlier.

### UT-5: `<commit4>` and `<merge date>` placeholders in BUILD_LOG

07a and 07b entries had literal `<commit4>` and `<merge date>` placeholders. Filled post-merge during Partner's QC pass per the rubric. After 07c merges, its BUILD_LOG entry will need the same treatment.

### UT-6: Sidecar-state JSON pathway for xlsx round-trip — RESOLVED in 07c draft

Per BUILD.md §3.11 line 1009 + line 1015, the sidecar is written by both daemons: forward writes it after each regeneration (in the same lock as the xlsx write), and reverse rewrites it at the end of each cycle. Sidecar lives at `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json` (sibling to xlsx files). 07c implements both halves. **Resolved.**

### UT-7: Reverse-daemon emit path bypasses Session / guardedWrite — open until prompt 08 (NEW)

The xlsx reverse daemon (07c) emits domain events through `EventLog.append()` directly, without routing through Session/guardedWrite/scope checks. This is the simple seam for now. A hostile file editor (or a malware-injected edit) could in principle drive the daemon to emit events on behalf of the principal without any rate-limit, action-gate, or authority check. Prompt 08 (session + scope + governance) will close this — reverse-emitted events should route through guardedWrite with the daemon as the agent identity. Carry-forward for 08.

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
│   └── reference/                               # mirrored external docs
├── scripts/
│   ├── verify_invariants.sh                     # canonical invariant-grep (PM-7)
│   ├── demo_event_log.py
│   ├── demo_projections.py
│   ├── demo_xlsx_forward.py
│   └── demo_xlsx_roundtrip.py                   # added by 07c
├── prompts/
│   ├── PROMPT_SEQUENCE.md                       # CANONICAL (single copy; slim preamble)
│   ├── 00-preflight.md ... 19-phase-b-smoke-test.md
│   ├── 07a-projections-ops-spine.md
│   ├── 07b-xlsx-workbooks-forward.md
│   ├── 07c-xlsx-workbooks-reverse.md            # NEW: drafted 2026-04-24
│   ├── 07.5-checkpoint-projection-consistency.md
│   ├── d01-*.md ... d08-*.md                    # diagnostic prompts
│   ├── prompt-01a-openclaw-cheatsheet.md
│   ├── prompt-01b-architecture-summary.md
│   ├── prompt-01c-system-invariants.md
│   └── sidecar-prompt-sequence-version-drift.md
├── adminme/
│   ├── events/{log,bus,envelope,registry}.py
│   ├── events/schemas/{ingest,crm,domain,governance,ops,system}.py
│   ├── projections/{base,runner}.py + 11 subdirs (10 sqlite + xlsx_workbooks)
│   ├── daemons/                                 # NEW (PM-14): adapters/daemons that emit domain events
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
