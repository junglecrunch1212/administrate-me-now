# QC rubric — Partner's per-merge review pass

_The three-job pass every Claude Chat Partner session runs after a Claude Code merge. Purpose: catch drift between prompt-intent and shipped-reality before it compounds into the next prompt._

**Prerequisite:** you've completed the Session 1 orientation sequence in `partner_handoff.md` (9 constitutional docs + session docs + PROMPT_SEQUENCE + orientation report to James).

**Timing:** Immediately after a Claude Code PR merges. Before writing the next prompt. If QC is run in the same session as the next-prompt refactor, these three jobs come first.

---

## Before starting any job

Load from the zip — minimum necessary:

1. **The prompt file that specified what was to ship** (`prompts/<NN>-*.md`).
2. **The `build_log.md` entry** for this merge (Evidence section specifically).
3. **The actual merged PR on GitHub** — read the PR description + the commit list. James can paste these if loading GitHub directly isn't feasible.

Do NOT load the full PR diff. Spot-check specific files from the zip when a Job's findings warrant deeper investigation.

---

## Job 1: Contract check — did shipped match spec?

**Question:** Did the set of commits, tests, and deliverables match what the prompt specified?

**Not the question:** "Did the code work?" CI and tests answer that. Contract check is about scope.

### Procedure

1. Open the merged PR description.
2. Open the prompt file it executed against.
3. Walk the prompt's "Deliverables" section line by line. For each deliverable, confirm it's present in the PR description or commit list.
4. Walk the prompt's "Tests" subsections. Confirm test files listed exist and contain at least the minimum test counts specified. Spot-check by loading one or two test files from the zip.
5. Walk the prompt's "Out of scope" section. Confirm nothing in there appeared in the diff. If the `build_log.md` Evidence section mentions something the "Out of scope" list forbade, flag it.

### Findings to log

- **Match** — prompt said X, PR shipped X. Default. No log entry needed.
- **Overshoot** — prompt said ≥N tests, PR shipped N+M. Positive signal. Log as "Claude Code exceeded minimum; quality signal, not drift." No action.
- **Undershoot** — prompt said ≥N, PR shipped N-M. Drift. Was the reduction justified in the PR description? If yes, accept and note reasoning. If no, flag in next prompt's "Read first" so a future session knows it was accepted.
- **Silent scope change** — PR shipped something not specified in Deliverables, or skipped something in Deliverables, without calling it out in PR description. **Highest-value finding.** Log prominently; next prompt may need to patch the gap or accept the deviation.
- **Scope extension across "Out of scope" line** — PR shipped something explicitly in "Out of scope." Very high-value. Investigate whether the deferred work is still valid or has been prematurely collapsed.

### Examples from the record

- Prompt 07a said "≥32 tests total"; PR shipped 38. **Overshoot** — positive signal.
- Prompt 07b noted "one pre-existing false positive in pipelines/runner.py docstring noted, not introduced by 07b." **Match** — Claude Code correctly flagged a pre-existing issue in the PR description.
- Prompt 07b chose to build alongside prompt 02's stub files (`forward.py`, `reverse.py`, `schemas.py`) rather than populating them. **Silent architectural decision** — reasonable, worth flagging in partner_handoff.md as PM-10 so future Partner sessions know those stubs are dead code.

---

## Job 2: Invariant audit — did shipped code preserve binding contracts?

**Question:** Did the PR introduce any drift from `docs/SYSTEM_INVARIANTS.md` or `docs/DECISIONS.md`?

**Not the question:** "Is every invariant satisfied globally?" Only ones the new code could plausibly affect need checking.

### Procedure

1. Identify which SYSTEM_INVARIANTS sections the shipped code touches:
   - Projection work → §1 (event log sacred), §2 (projections derived), §6 (privacy), §12 (tenant isolation), §15 (instance-path discipline).
   - Pipeline work → §5, §7 (pipelines), §8 (OpenClaw boundaries).
   - Adapter work → §8, §11 (bootstrap).
   - Surface / product work → §9 (console), §11.
   - Anything touching SQLCipher / event log → §1.

2. For each relevant section, walk its invariants against what shipped. Most invariants are "the code must not do X." Grep the shipped code (via zip) for X.

3. Check DECISIONS.md entries that constrain this work — particularly D6 (no LLM SDKs in adminme/), D13 (sqlcipher3-binary), D14 (async via asyncio.to_thread), D15 (instance-path discipline).

4. Run the standard invariant-grep block against the fresh main. Once `scripts/verify_invariants.sh` lands (from `universal_preamble_extension.md`), this is one command. Until then, the expanded grep block is in the `universal_preamble_extension.md` proposal.

### Findings to log

- **Clean** — no violations. Default.
- **Violation** — a hard invariant is broken. **Stop-the-line.** Open a diagnostic session with Claude Code to fix before writing the next prompt.
- **Soft pattern deviation** — not a hard violation, but the code does something earlier prompts explicitly avoided. Example: direct literal paths in tests instead of going through `InstanceConfig`. Log; decide case-by-case.

### Projection-emit audit

`[§2.2]` says projections never emit DOMAIN events. Partner invented the **system event** category in 07b to resolve this (`xlsx.regenerated` is observability-only, not a domain event). When reviewing future prompts that touch projections, check: does the projection emit anything? If yes, is it from the allowed system-event list? The allowed list grows as prompts add system events. Currently: `xlsx.regenerated`, `xlsx.reverse_skipped_during_forward` (if 07c lands).

Keep this audit synced with `scripts/verify_invariants.sh`'s `ALLOWED_EMITS` pattern (once that script exists).

---

## Job 3: Next-prompt calibration — is the queued prompt still right?

**Question:** Is the next prompt in the queue written against the state that just shipped, or against the state the prior prompt intended to ship?

### Procedure

1. Open the next prompt file (e.g. if 07b just merged, open 07c or the refactor brief for 07c).
2. Walk its "Read first (required)" section. Every file path cited must exist on the now-merged main in the form the prompt expects. Verify by opening the zip.
3. Walk its "Depends on" section. Confirm dependencies match what shipped, not what was promised.
4. Walk its "Operating context" section. Update references to any module / class / event type / projection that shipped in a different shape than planned.
5. Walk its "Carry-forwards" section. Each CF should still be live; retire any the latest merge obsoleted.

### Examples of calibration drift

- Prompt 07b expected to work against "10 projections live" (7 from 05/06 + 3 from 07a). If 07a merged in a different order than assumed, 07b's Read-first list would point at a file that didn't exist. In practice this didn't happen, but the rubric is to check.
- Prompt 07b specified `xlsx.regenerated` in `schemas/system.py` (a new module). If 07b had shipped the event in `schemas/domain.py`, prompt 07c's Read-first list would need to know.
- Prompt 08 (unrefactored) references "~38 TODO(prompt-08) markers across 10 projections." After 07b merges, the count updates. Partner confirms the count during 08's refactor session — Claude Code can count, Partner verifies.

### Findings to log

- **Clean** — next prompt is already calibrated. Default when queued prompt was written after just-merged prompt.
- **Needs refresh** — next prompt has stale references. Fix before Claude Code runs it.
- **Needs refactor** — architecture assumptions are off. Whole-prompt redo, not line-item. Budget a separate session.

### Key check for unrefactored prompts

Every prompt 08 through 15.5 is currently in original PROMPT_SEQUENCE state — drafted before the 07 split, before CF discipline emerged. When each comes up, it needs:

- Header updates to match current conventions (four-commit discipline, BUILD_LOG append, PR via gh/MCP fallback).
- Carry-forwards section added (or skipped if preamble now covers them).
- "Read first" section audited against current `adminme/` tree.
- Test-count minimums reviewed given actual code volume.

This is the refactor-per-prompt work that fills each Partner session between Claude Code runs.

---

## Post-pass housekeeping

After all three jobs complete, update `docs/partner_handoff.md`:

1. **Current build state** section: move just-merged prompt from "PR open" to "Merged to main." Update "Next task queue" if queue advanced.
2. **Prompt-writing decisions** section: add any new PM entry if the pass surfaced one. Tag HARD or SOFT.
3. **Open tensions** section: add any UT entry if surfaced; close any UT the merge resolved.

Then update `docs/build_log.md`'s entry for the merged prompt:

1. Replace `PR #<N>` with actual PR number.
2. Replace `<commit4>` with actual commit SHA of final commit.
3. Replace `<merge date>` with actual merge date.
4. Change `**Outcome**: IN FLIGHT (PR open)` to `**Outcome**: MERGED`.
5. If any contract-check or invariant-audit findings warrant, add a line under Evidence noting the deviation.

---

## BUILD_LOG entry template (canonical)

Reproduce this verbatim in Claude Code's Commit 4. Partner fills placeholders post-merge during Job 3 housekeeping.

```markdown
### Prompt <NN> — <short title>
- **Refactored**: by Partner in Claude Chat, <refactor date>. Prompt file: prompts/<NN>-<slug>.md (~<NNN> lines, quality bar = <prior-prompt>).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge date>.
- **Outcome**: MERGED.  <!-- IN FLIGHT (PR open) until housekeeping completes -->
- **Evidence**:
  - <1–2 lines per shipped deliverable>
  - <tests shipped, total count, breakdown by module>
  - <refactor-relevant refinements, e.g. "Runner gained hook X, default no-op, backward compatible">
  - <explicit citation of any invariant that shaped implementation, e.g. "Privileged-filter at handler time per [§13.8]">
  - <BUILD_LOG update note>
  - <ruff/mypy/inviolable greps status>
- **Carry-forward for prompt <next>**:
  - <concrete handoff — what the next prompt consumes>
- **Carry-forward for prompt <further>** (optional):
  - <concrete handoff>
- **Carry-forward for prompt 08** (if this prompt added TODO(prompt-08) markers):
  - <count of new markers + running total>
```

---

## End of rubric

One pass per merge. Three jobs, in order. Housekeeping at the end. Then write the next prompt.
