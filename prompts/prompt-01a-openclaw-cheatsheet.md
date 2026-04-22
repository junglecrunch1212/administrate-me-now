**Phase + repository + documentation + sandbox discipline.**

You are in Phase A on https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live services. Sandbox egress is allowlisted to `github.com` and `raw.githubusercontent.com`; other hosts return HTTP 403 `host_not_allowed`.

This is prompt 01a: produce `docs/openclaw-cheatsheet.md` — a concise reference (max 100 lines) that answers 8 questions about OpenClaw integration. This cheatsheet becomes the input for prompt 01b, which produces the larger architecture summary.

**Important context on scope.** An earlier attempt at a combined "read everything + produce both deliverables" prompt timed out because it loaded the entire spec set (BUILD.md, CONSOLE_PATTERNS.md, REFERENCE_EXAMPLES.md, DIAGRAMS.md — ~9,500 lines total) into context before any writing started. This prompt is deliberately narrow: **you are producing only the OpenClaw cheatsheet**. You will read OpenClaw documentation from the local mirror at `docs/reference/openclaw/`. You will NOT read the four AdministrateMe spec artifacts. That is prompt 01b's job.

---

## Session start

```bash
git checkout main
git pull origin main
git checkout -b phase-01a-openclaw-cheatsheet   # harness will override with claude/<random>; work on whatever it assigns
```

Verify the OpenClaw mirror is populated on main before doing anything else:

```bash
ls docs/reference/openclaw/ | head -20
wc -l docs/reference/openclaw/_index.md
```

If the `openclaw/` directory is missing or only contains `_index.md`, STOP — prompt 00.5 is incomplete.

---

## What you are producing

**`docs/openclaw-cheatsheet.md`** — max 100 lines. Answers exactly these 8 questions, each in ≤10 lines. Every answer must include at least one specific file-path citation into `docs/reference/openclaw/...`.

The 8 questions (copied verbatim from BUILD.md's "BEFORE YOU START: LEARN OPENCLAW" section):

1. How does an AdministrateMe skill pack get installed into OpenClaw? (Exact commands; file locations.)
2. How does a slash command handler get registered?
3. How does a standing order get registered?
4. How does a plugin get registered?
5. What is the exact shape of a SKILL.md file that OpenClaw accepts? (Frontmatter fields, body conventions.)
6. What is `dmScope: per-channel-peer` and when does it apply vs. `shared`?
7. How does the gateway's approval-gates system interact with `guardedWrite` (CONSOLE_PATTERNS.md §3)? (Where does each run in the request flow?) — **For this one, you don't have access to CONSOLE_PATTERNS.md in this session; just describe where OpenClaw's approval gates run in its own request flow, and note that guardedWrite will be documented in prompt 01b's summary.**
8. Where does OpenClaw store its state on disk, and what of it needs to be backed up (vs. what is derived from `~/.adminme/` and can be rebuilt)?

End the cheatsheet with a `## Sources` section listing 4–8 of the most useful paths under `docs/reference/openclaw/` you actually consulted.

---

## Reading strategy

You will read OpenClaw mirror files. Most are small (under 500 lines each). Cover the 8 questions with a focused subset — you do NOT need to read every file under `docs/reference/openclaw/`.

**Recommended order:**

1. Start with `docs/reference/openclaw/_index.md` for orientation.
2. For each question, identify the likely file(s) and read them. Based on the mirror's directory structure, the primary sources are:

   | Question | Primary file(s) |
   |---|---|
   | Q1 (skill install) | `tools/skills.md`, `tools/clawhub.md` |
   | Q2 (slash commands) | `tools/slash-commands.md`, `tools/skills.md` (command-dispatch frontmatter) |
   | Q3 (standing orders) | `automation/standing-orders.md`, `automation/cron-jobs.md` |
   | Q4 (plugins) | `tools/plugin.md`, possibly `plugins/building-plugins.md` if it exists |
   | Q5 (SKILL.md shape) | `tools/skills.md` (Format section), `tools/creating-skills.md` |
   | Q6 (dmScope) | `concepts/session.md` |
   | Q7 (approval gates) | `tools/exec-approvals.md`, `concepts/agent-loop.md` |
   | Q8 (disk state) | `cli/backup.md`, `gateway/doctor.md`, `concepts/agent-workspace.md` |

3. Use `view` with `view_range` if a file is long. Most OpenClaw mirror files are under 500 lines; reading them whole is fine. Do NOT, however, read the four AdministrateMe artifacts at the repo root — that's out of scope for this prompt.

4. You may dispatch one or two Explore sub-agents if you want parallelized research, but the OpenClaw mirror is small enough that direct reads are fine too. Your call.

5. Keep notes brief as you read. You are producing ~84 lines of output total; the research need not be exhaustive.

---

## What you will NOT do

- Do NOT read `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_DIAGRAMS.md`, or `ADMINISTRATEME_CONSOLE_REFERENCE.html`. Those are 01b's inputs.
- Do NOT produce `docs/architecture-summary.md`. That is prompt 01b's deliverable.
- Do NOT WebFetch anything. The mirror is the source of truth; the sandbox allowlist would block most external fetches anyway.
- Do NOT write any production code.
- Do NOT open a PR.
- Do NOT push to main.

---

## Cheatsheet format

Use this exact structure. Each Q answer is ≤10 lines. Line length target: ~84 total (the previously-completed version was 84 lines; yours can be 70–100).

```markdown
# OpenClaw cheatsheet

_Produced by prompt 01a. Sourced from `docs/reference/openclaw/` mirror. Update if OpenClaw's docs change._

## Q1: How does an AdministrateMe skill pack get installed into OpenClaw?

<answer, ≤10 lines, with file-path citation e.g. "per `tools/skills.md` Locations and precedence">

## Q2: How does a slash command handler get registered?

<answer>

## Q3: How does a standing order get registered?

<answer>

## Q4: How does a plugin get registered?

<answer>

## Q5: What is the exact shape of a SKILL.md file that OpenClaw accepts?

<answer>

## Q6: What is `dmScope: per-channel-peer` and when does it apply vs. `shared`?

<answer>

## Q7: How does the gateway's approval-gates system interact with guardedWrite?

<answer — describe OpenClaw's approval-gate flow; note guardedWrite is an AdministrateMe-side gate documented in prompt 01b's summary>

## Q8: Where does OpenClaw store its state on disk, and what needs backup?

<answer>

## Sources

- `docs/reference/openclaw/<path>` — <what you used it for>
- ... (4–8 entries)
```

---

## Verification

```bash
wc -l docs/openclaw-cheatsheet.md
# expect ≤ 100

# Every Q has at least one "docs/reference/openclaw/" citation
for q in Q1 Q2 Q3 Q4 Q5 Q6 Q7 Q8; do
  count=$(awk "/## $q/,/## (Q[0-9]|Sources)/" docs/openclaw-cheatsheet.md | grep -c "docs/reference/openclaw\\|openclaw/[a-z]")
  echo "$q: $count citations"
done
# every Q should show ≥ 1

# Sources section exists
grep -c "^## Sources" docs/openclaw-cheatsheet.md
# expect 1
```

If any Q shows 0 citations, revise that answer with a proper file path before committing.

---

## Commit and push

```bash
git add docs/openclaw-cheatsheet.md
git status  # confirm only this one file is staged
git commit -m "phase 01a: openclaw cheatsheet

Answers the 8 OpenClaw integration questions posed by
ADMINISTRATEME_BUILD.md \"BEFORE YOU START: LEARN OPENCLAW\" section.
Sourced from the docs/reference/openclaw/ GitHub mirror populated by
prompt 00.5.

Companion to docs/architecture-summary.md (produced by prompt 01b,
next session)."

git push origin HEAD
```

---

## Stop condition

When:
- `docs/openclaw-cheatsheet.md` exists and is ≤100 lines
- All 8 questions answered with file-path citations
- Sources section present
- Branch pushed

Produce a brief summary:

- Branch name (harness-assigned)
- Line count
- Number of OpenClaw mirror files consulted
- Any deviations

Then STOP. Do not proceed to prompt 01b — that's a separate session after this PR merges.
