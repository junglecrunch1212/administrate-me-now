# SESSION-1-ORIENTATION-PROTOCOL.md

**v4.1 — the mechanical orient-then-pause sequence Partner runs at the top of every new Cowork chat.**

This doc supersedes the inline "Mandatory Session 1 orientation" section of `PROJECT-INSTRUCTIONS.md`. The inline version was not enforceable — the agent could produce both the orientation report and downstream work in the same response, and the v4 test drive showed that's what happens. This doc enforces the pause point structurally: orientation has a closing artifact, and the next step (authorship, QC, etc.) cannot begin in the same turn that produced the orientation artifact.

---

## When this runs

The very top of every new Cowork chat session in the AdministrateMe Partner project. No exceptions. Including sessions the operator opens to "just answer a quick question" — orientation is cheap; skipping it is the failure mode.

---

## Phase 1 — Pin verification (do not skip)

Confirm the 14 pinned docs are present in the project. Name each one:

**Constitutional (9):**
1. `ADMINISTRATEME_BUILD.md`
2. `ADMINISTRATEME_CONSOLE_PATTERNS.md`
3. `ADMINISTRATEME_DIAGRAMS.md`
4. `ADMINISTRATEME_REFERENCE_EXAMPLES.md`
5. `ADMINISTRATEME_CONSOLE_REFERENCE.html`
6. `SYSTEM_INVARIANTS.md`
7. `DECISIONS.md`
8. `architecture-summary.md`
9. `openclaw-cheatsheet.md`

**Session (3):**
10. `partner_handoff.md`
11. `qc_rubric.md`
12. `build_log.md`

**Sequence (1):**
13. `PROMPT_SEQUENCE.md`

**Vision (1):**
14. `WHAT-IS-ADMINISTRATEME.md`

For any missing, report by name and halt for operator to pin before proceeding. Do not work without all 14.

## Phase 2 — MCP availability check

Confirm `mcp__github__*` tools are present in the tool list. The tools you need at minimum:

- `mcp__github__get_file_contents`
- `mcp__github__create_branch`
- `mcp__github__create_or_update_file`
- `mcp__github__push_files`
- `mcp__github__create_pull_request`
- `mcp__github__get_pull_request_files`
- `mcp__github__list_commits`

If any are missing, report the gap and halt. Without these, the pre-authorship gate cannot run and Partner is not operational. The fallback is the operator paste path documented in `session-handoff-github-mcp-install.md`; if that's the active path for this session, the operator declares it explicitly in the first message.

## Phase 3 — Live state read

Use `mcp__github__get_file_contents` to fetch the live tail of three files:

1. `docs/build_log.md` at `ref=main` — read the bottom ~200 lines (or fetch the whole file if it's under 100 KB; check size first).
2. `docs/partner_handoff.md` at `ref=main` — read the top ~300 lines to capture the most recent "Last updated" paragraph and the "Current build state" / "Next task queue" sections.
3. `prompts/PROMPT_SEQUENCE.md` at `ref=main` — fetch in full only if the build state suggests a sequence-update PR may have landed since pinning; otherwise the pinned copy is fine.

Cross-check against the pinned copies. If the live tail says a more recent merge than the pinned `partner_handoff.md` mentions, the pinned copies are stale and you note this in Phase 5. Do not work from stale state; flag it.

## Phase 4 — Identify the session task

From the operator's first message in this chat, identify:

- **Session task** — refactor next prompt? QC a merge? Resolve a halt? Doc/bundle refactor? Sidecar?
- **Target prompt or PR** — the specific `<NN>-<slug>` or `PR #<N>`.
- **Session type** — Type 1 (combined QC + refactor + size), Type 2 (QC-only), Type 3 (refactor-only with §2.9), Type 0 (meta/triage/split-memo/amendment) per `E-session-protocol.md`.

If the operator's first message is vague, ask exactly one clarifying question. Do not infer past one question.

## Phase 5 — Produce the orientation report (closing artifact for orientation)

Produce this exact shape inline in chat. No file write yet — this is a chat-level artifact for operator review:

```
## Orientation report

**Pinned docs verified:** all 14 ✓ (or list missing)

**MCP availability:** ✓ (mcp__github__* present) | ✗ (list missing tools)

**Live state read:**
- `docs/build_log.md` tail: <most recent prompt entry, e.g. "Prompt 10c-ii (PR #55, merged 2026-05-05)">
- `docs/partner_handoff.md` head: <most recent Last-updated paragraph summary, e.g. "Last updated 2026-05-05 — 10c-ii merged; next refactor target 10c-iii">
- Pinned-vs-live drift: <"none" | "pinned partner_handoff is N merges behind live; flagging">

**Session task** (from operator's first message): <paraphrase>

**Session type** (per `E-session-protocol.md`): <Type 0 | 1 | 2 | 3>

**Target:** <prompt NN-slug or PR #N or memo name>

**Pre-split disposition forecast** (for Type 1/3 only): <split | likely single | clear single>, with reasoning grounded in the D-prompt-tier-and-pattern-index row + the §2.9 budget against the prompt's known scope.

**Intended depth-read pathway**:
- Pre-authorship gate paths to verify (Gate 1 list of cited paths/symbols): <preview, full list during Gate 2>
- Additional context: <any extra files beyond the standard depth-read list>

**Open questions for operator:** <if any; otherwise "none">

**Ready to proceed.** I will not begin <authorship | QC walk | halt resolution> until you confirm this orientation is correct. If MCP is unavailable, I will also wait for explicit operator direction on whether to proceed in degraded mode or halt.
```

End your turn. **Wait for explicit operator confirmation** (or correction) before proceeding to Phase 6.

This is the load-bearing pause point. The orientation report is a chat-level closing artifact, not a stepping stone — the next phase happens in a *separate turn* after the operator types "proceed" or equivalent. The v3 / v4-cycle failure mode was producing orientation + downstream work in one response, which makes the orientation report descriptive rather than gated.

## Phase 6 — Begin work (only after operator confirmation)

Once operator confirms, proceed to the relevant session-type path:

- **Type 1 (combined QC + refactor):**
  1. Job 1 — three-job QC pass per `qc_rubric.md` against the merged PR. Produces findings + updated `partner_handoff.md` entry text + updated `build_log.md` entry text (inline chat for review).
  2. Pre-authorship gate per `PARTNER-PRE-AUTHORSHIP-CHECKLIST.md` for the next target prompt. PASS or HALT.
  3. On PASS: Job 2 — author the refactored prompt.
  4. Job 3 — §2.9 delivery-gate self-check against the authored draft.
  5. `DRIFT-CONTROL-CHECKLIST.md` walk; record the score.
  6. Open the `partner/refactor-<NN>` PR with the authored prompt, the QC findings, and the updated handoff/build_log entries.

- **Type 2 (QC-only):**
  1. Job 1 — three-job QC pass.
  2. Open the partner-state-snapshot PR with updated `partner_handoff.md` + `build_log.md` entries. Do NOT author a refactor.

- **Type 3 (refactor-only with §2.9):**
  1. Pre-authorship gate per `PARTNER-PRE-AUTHORSHIP-CHECKLIST.md`. PASS or HALT.
  2. On PASS: author the refactored prompt.
  3. §2.9 delivery-gate self-check.
  4. `DRIFT-CONTROL-CHECKLIST.md` walk; record the score.
  5. Open the `partner/refactor-<NN>` PR.

- **Type 0 (meta/triage/split-memo/amendment):**
  1. Identify the artifact (memo, split-memo, amendment-memo).
  2. Author. No build prompt; no Executor cycle implied.
  3. Open the single-purpose infrastructure PR per PM-22 discipline.

In every type, every PR opens via `mcp__github__create_branch` + `mcp__github__create_or_update_file` (or `push_files`) + `mcp__github__create_pull_request`. Branch naming: `partner/refactor-<NN>` for build-prompt refactors; `partner/<descriptor>` for everything else. Base is always `main` for partner-side PRs (Executor PRs may have non-main bases per stacked branch strategy).

---

## What this protocol replaces

Lines 47-127 of `PROJECT-INSTRUCTIONS.md` (the inline orientation section) become a one-line pointer to this doc:

> Run the Session-1 orientation per `SESSION-1-ORIENTATION-PROTOCOL.md`. The pause point at Phase 5 is mandatory.

The other content in `PROJECT-INSTRUCTIONS.md` — role definition, vision discipline, how-to-refactor, how-to-QC, halt handling, citation discipline, never-do list — stays.

---

## Why the pause is structural, not advisory

The Cowork agent receives operator messages one at a time and runs to completion within each turn. Without an explicit "produce orientation report and stop" instruction at the end of Phase 5, the agent treats orientation as a preamble to "real" work and continues authoring in the same turn. The v4 test drive evidence is that this is what happened: the session handoff lists Phases 1-3 (orient + drift-check + author) as completed in a single ~50-minute span, with the operator only intervening at the Phase A push (which was a separate operator-attended step the harness already gated).

The pause point exists because spec-checking the operator's session-prompt-instructions against the binding spec — Partner's load-bearing skill, per `B-partner-init-prompt.md §3` and §12's Findings discipline — requires a discrete moment where the agent has finished orienting but has not yet committed to a draft direction. That moment is the gap between Phase 5 and Phase 6. The operator's confirmation closes the gap; without it, the agent's first draft has already locked in any spec-check errors.
