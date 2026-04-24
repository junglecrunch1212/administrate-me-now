# Sidecar fix — `prompts/PROMPT_SEQUENCE.md` version drift

## Phase + repository + documentation + sandbox discipline

You are in **Phase A**: generating code/changes in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, BlueBubbles, Plaid, or any external service.

**Sandbox egress is allowlisted.** `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `host_not_allowed`. This task touches no external hosts.

**Session start (required sequence)** — before touching any file:

```bash
git checkout main
git pull origin main
git checkout -b prep/sidecar-prompt-sequence-version-drift
```

Do NOT `git pull` again during the session. Do NOT push to `main`. You WILL open a PR at the end via `gh pr create`; James reviews and merges it.

**This is a single-purpose prep PR.** Do not bundle any other changes. If you notice something else that needs fixing, note it in your final message and stop — James decides whether to open a separate PR.

---

## Context

The AdministrateMe repo has two copies of the prompt driver:

- **`PROMPT_SEQUENCE.md`** at the repo root — the canonical/README-referenced copy
- **`prompts/PROMPT_SEQUENCE.md`** — what executing Claude Code sessions actually read

These two files have diverged on exactly one line: the Phase A prerequisites for sandbox runtime versions. The root says Python 3.11+/Node 22+ (correct — matches `pyproject.toml` which pins `python = "^3.11"` and matches `docs/DECISIONS.md` D10 "Platform version lock" which confirms Python 3.11 + Node 22 LTS). The `prompts/` copy says Python 3.12+/Node 24+ (incorrect — drifted).

Because executing sessions follow the `prompts/` copy when they load the preamble, this drift will cause a future session to attempt Python 3.12 / Node 24 features that aren't available in the sandbox and that violate the version lock.

Fix is a single line.

---

## Objective

Change line 48 of `prompts/PROMPT_SEQUENCE.md` from:

```
3. Claude Code's sandbox has Python 3.12+, Node 24+, Poetry, npm, git, gh.
```

to:

```
3. Claude Code's sandbox has Python 3.11+, Node 22+, Poetry, npm, git, gh.
```

That is the entire change. No other lines move. No other files change.

---

## Out of scope

- Modifying `PROMPT_SEQUENCE.md` at the repo root (it is already correct)
- Modifying `pyproject.toml` (already correct)
- Modifying `docs/DECISIONS.md` (D10 is already correct)
- Any other version references anywhere in the repo
- Any BUILD_LOG update (the Partner handles BUILD_LOG maintenance)
- Any other drift you may notice between the two PROMPT_SEQUENCE.md copies (there is none — verified by diff — but if you somehow find one, stop and report, do not fix)

---

## Verification before editing

Run this diff to confirm the scope of divergence before you edit:

```bash
diff PROMPT_SEQUENCE.md prompts/PROMPT_SEQUENCE.md
```

**Expected output (exactly one hunk):**

```
48c48
< 3. Claude Code's sandbox has Python 3.11+, Node 22+, Poetry, npm, git, gh.
---
> 3. Claude Code's sandbox has Python 3.12+, Node 24+, Poetry, npm, git, gh.
```

If the diff shows more than this one hunk, **stop** and report what you see. Do not proceed with the edit.

If the diff shows exactly this one hunk, proceed.

---

## The edit

Edit `prompts/PROMPT_SEQUENCE.md`. Change line 48 only. Match the root exactly.

Use a single precise edit (`str_replace` or equivalent). Do not rewrite the file.

---

## Verification after editing

```bash
# 1. Confirm the diff is now empty
diff PROMPT_SEQUENCE.md prompts/PROMPT_SEQUENCE.md
# Expected: no output (files identical)

# 2. Confirm exactly the expected line changed
git diff prompts/PROMPT_SEQUENCE.md
# Expected: one line removed (3.12+/24+), one line added (3.11+/22+), nothing else

# 3. Confirm no other files changed
git status
# Expected: only prompts/PROMPT_SEQUENCE.md in "Changes not staged for commit"
```

All three checks must pass. If any fails, stop and report — do not commit.

---

## Commit and push

```bash
git add prompts/PROMPT_SEQUENCE.md
git commit -m "Sidecar: align prompts/PROMPT_SEQUENCE.md Phase A runtime versions with root + D10

Line 48 drift: Python 3.12+/Node 24+ -> Python 3.11+/Node 22+.
Matches repo-root PROMPT_SEQUENCE.md, pyproject.toml (python ^3.11),
and docs/DECISIONS.md D10 (platform version lock, Python 3.11 + Node 22 LTS).

The prompts/ copy is what executing Claude Code sessions load as the
universal preamble, so this drift would have caused future sessions to
violate the version lock.

Single-line, single-purpose PR."
git push origin prep/sidecar-prompt-sequence-version-drift
```

---

## Open the PR

```bash
gh pr create \
  --base main \
  --head prep/sidecar-prompt-sequence-version-drift \
  --title "Sidecar: align prompts/PROMPT_SEQUENCE.md Phase A runtime versions with root + D10" \
  --body "Single-line sidecar fix.

Line 48 of \`prompts/PROMPT_SEQUENCE.md\`: Python 3.12+/Node 24+ -> Python 3.11+/Node 22+.

Matches:
- repo-root \`PROMPT_SEQUENCE.md\` (canonical)
- \`pyproject.toml\` (\`python = \"^3.11\"\`)
- \`docs/DECISIONS.md\` D10 (platform version lock: Python 3.11 + Node 22 LTS)

The \`prompts/\` copy is what executing Claude Code sessions load as the universal preamble, so this drift would have caused future sessions to violate the version lock.

Single-line, single-purpose PR. No other changes."
```

If `gh pr create` fails for any reason, report the exact error to James and stop — do not attempt alternative routes, do not retry with modified flags.

---

## Stop

When the PR is open, report to James:

- Branch pushed: `prep/sidecar-prompt-sequence-version-drift`
- PR URL returned by `gh pr create`
- Output of the three post-edit verification commands (`diff PROMPT_SEQUENCE.md prompts/PROMPT_SEQUENCE.md`, `git diff prompts/PROMPT_SEQUENCE.md`, `git status`)

Do **not** merge the PR. Do **not** push to `main`. Do **not** continue with any other task. James reviews the PR and merges when satisfied.
