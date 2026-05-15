# HANDOFF-TEMPLATE.md

**v4 — standard format for session-end documents.**

Every meaningful session (Codespace setup, prompt cycle completion, halt resolution, amendment cycle) produces a handoff doc in this format. The doc lives in the session's output folder (Cowork) OR in `docs/session_log.md` (committed to the repo) depending on whether the session ran in Cowork or directly in the Codespace.

The point of a standardized template: future sessions can grep across `docs/session_log.md` entries to find prior incidents, time spent on each phase, and bug-fix history. v3 had ad-hoc handoff docs (`SETUP-COMPLETE.md`, `TEST-DRIVE-REPORT.md`, `10C-III-COMPLETE.md`) — useful individually but not searchable or comparable.

---

## The template

```markdown
# Session handoff — <Session purpose>

**Session date:** <YYYY-MM-DD>
**Operator:** <name + email>
**Driven by:** <Cowork agent / Partner project / Codespace operator>
**Session type:** <setup | prompt-cycle | halt-resolution | amendment | exploration>
**Outcome:** <COMPLETE | PARTIAL | BLOCKED | FAILED>

---

## §1 — Summary table

| What | Status | Link |
|---|---|---|
| <Deliverable 1 (e.g. "Partner refactor of prompt 10c-iii")> | <DONE / IN-PROGRESS / BLOCKED> | <PR or file link> |
| <Deliverable 2 (e.g. "Executor c1 PR")> | ... | ... |
| <Deliverable 3 (e.g. "Executor c2 PR")> | ... | ... |
| ... | | |

---

## §2 — The N PRs/branches in flight

(Skip if this session didn't open PRs.)

| PR | Branch | Description | Status |
|---|---|---|---|
| #<N> | <branch> | <one-line description> | <Open / Merged / Closed> |

---

## §3 — Caveats for review

(Anything unusual the operator should know before reviewing. v3 examples:
- "PR #67's commit message is mis-labeled 'commit 1' but the content is c4 work."
- "c4 includes redundant c1 content because of branch-strategy confusion."
- "Tests pass but one is skipped pending a future projection schema migration."

If there are no caveats, write "None — clean run.")

---

## §4 — Operator follow-ups

(Concrete numbered next actions with time estimates. v3 examples:
1. Rotate OAuth token — exposed in 2 screenshots — 3 min
2. Patch post-create.sh to chown ~/.claude — 5 min
3. Either install git-lfs or remove the hooks — 2 min)

For each: what's needed, how long, why.

---

## §5 — Three things we learned

(Bugs hit, undocumented behavior, surprises. Brief — one paragraph each. The corpus of "things we learned" sections across sessions feeds the next POSTMORTEM cycle.)

1. **<Short title>**: <one paragraph>
2. **<Short title>**: <one paragraph>
3. **<Short title>**: <one paragraph>

---

## §6 — Suggested next move

(One sentence. What should happen next in the build? E.g., "Merge PR #64 to trigger Reviewer routine for the first time" or "Wait for halt PM-42 resolution before continuing 10c-iii cycle.")

---

## §7 — Time budget actual vs estimated

| Phase | Estimate | Actual | Notes |
|---|---|---|---|
| <e.g., Partner refactor> | <e.g., 30 min> | <e.g., 25 min> | <e.g., faster than expected> |
| <e.g., c1 cycle> | <e.g., 25 min> | <e.g., 45 min> | <e.g., debugged perms> |
| ... | | | |

Total: <estimate vs actual>. Helps calibrate future estimates.

---

## Sources

<Links to PRs, the prompt files involved, related docs, etc.>
```

---

## Three example handoff docs (v3 retrofitted)

The v3 docs `SETUP-COMPLETE.md`, `TEST-DRIVE-REPORT.md`, `10C-III-COMPLETE.md` each map cleanly onto this template. The retrofit is for reference; future sessions just use the template directly.

### SETUP-COMPLETE.md retrofitted

```markdown
# Session handoff — AdministrateMe-Autonomous v3 setup

**Session date:** 2026-05-14
**Operator:** James (jrstice@gmail.com)
**Driven by:** Cowork setup-agent on Claude Desktop (Windows)
**Session type:** setup
**Outcome:** COMPLETE

## §1 — Summary table

| What | Status | Link |
|---|---|---|
| 4 setup PRs (devcontainer, .claude config, agents+schemas, devcontainer fix) | DONE | PRs #59-#62 (merged) |
| Codespaces OAuth token | DONE | Set via gh settings/codespaces |
| Claude GitHub App | DONE | Installed by operator |
| Cowork Partner Project | DONE | Created, 14 docs pinned |
| 4 Reviewer routines | DONE (3) + PARTIAL (1) | claude.ai/code/routines |
| Validation (static check) | DONE (GREEN, 22/22) | validation/run-validation.sh |

## §4 — Operator follow-ups

1. Rebuild Codespace container — 30 sec — picks up PR #62 devcontainer fix
2. Edit Routine 4 (Checkpoint 15.5) to fix trigger from "Via API" to "PR merged" — 30 sec
```

(The other v3 docs map similarly.)

---

## Why each section matters

**§1 (Summary table):** Reviewer or future Partner can grok session state in 10 seconds.

**§2 (PRs/branches):** Quickly answers "what's still open from this session?"

**§3 (Caveats):** Surfaces non-obvious issues. v3's `10C-III-COMPLETE.md` flagged the c4 mis-label here — without that, the operator could merge a confusing diff.

**§4 (Operator follow-ups):** Numbered + time-estimated so the operator can batch them into a single 10-minute cleanup pass.

**§5 (Things learned):** The corpus that feeds POSTMORTEM cycles. Each session contributes 3 learnings; over 33 prompts that's 99 learnings — too many to triage individually but searchable when investigating a specific issue.

**§6 (Suggested next move):** Anti-decision-paralysis. The next session can default to this if no fresh signal.

**§7 (Time budget):** Calibration data. v3 estimated 3-4 hours for 10c-iii; actual was ~90 min for the c1→c4 cycle but Partner refactor + setup overhead pushed total to ~6 hours. v4 estimates should reflect that.

---

## Storage convention

- **Cowork-driven sessions** (Partner + setup runs): handoff doc in the session's `outputs/` folder, named `SESSION-HANDOFF-<YYYY-MM-DD>-<purpose>.md`.
- **Codespace-driven sessions** (Executor c1→c4, sidecar fixes, amendment cycles): handoff doc as an entry appended to `docs/session_log.md` in the repo, committed as part of the session's final commit.

`docs/session_log.md` is structured as a series of dated entries, each in the handoff format. Use H1 (`# Session handoff — ...`) for each entry; the file grows over time. Don't truncate.

The bundle's `02-OPERATE.md` Phase 4 (Operator merges, Reviewer fires) now ends with "Append a session_log.md entry per HANDOFF-TEMPLATE.md before closing the Codespace."
