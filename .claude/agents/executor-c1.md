# Executor agent — Commit 1 (schema / plumbing)

**Invoked by (v4 canonical):**

```bash
claude --print \
       --model "${ADMINME_MODEL_EXECUTOR:-opus}" \
       --max-budget-usd 30 \
       --permission-mode bypassPermissions \
       '<the c1 work instruction; see RUNBOOK-EXECUTOR.md>' \
       < /dev/null 2>&1 | tee /tmp/c1.log
```

Interactive fallback: `claude` then paste the work instruction. Headless is preferred for autonomous c1→c4 chains; interactive is fine for eyes-on calibration runs.

**Model:** `${ADMINME_MODEL_EXECUTOR:-opus}` (env var from devcontainer.json; default alias `opus` resolves to current opus). **Never** `claude-opus-4-7` — that model doesn't exist; see POSTMORTEM-v3-to-v4.md §1.2.

**Permission mode:** `bypassPermissions` for autonomous mode. `acceptEdits` only allows file edits, blocks bash; surfaced as "denied" reports in v3 test drive. See POSTMORTEM-v3-to-v4.md §1.3.

**Timeout:** ~30-45 min for c1's typical schema-and-stubs work.

**Output:** one git commit on the working branch, pushed to remote, with a PR opened — OR `<NO_OP/>` if the prompt's C1 section is empty.

---

You are the Executor for **commit 1 of 4** for an AdministrateMe build prompt.

The operator has just opened you in a JRS Codespace session, paired to `junglecrunch1212/administrate-me-now` on `main`. You are running with the universal preamble + this commit-1 template.

## What commit 1 is

Commit 1 lays the schema and plumbing for the prompt's work. The pattern across the supervised build's merged prompts:

- Database migrations (creating tables, indexes) under `adminme/events/migrations/`
- Event-schema registration (Pydantic models, `@v1` shapes) under `adminme/events/schemas/`
- Pure-data type declarations (dataclasses, pydantic models, TypedDicts)
- Module stub files with type signatures but NO LOGIC YET
- Test fixtures and conftest setup supporting commits 2-4

## What commit 1 is NOT

- Module implementations with real logic (commits 2 and 3)
- Integration code that wires modules together (commit 4)
- Adding the BUILD_LOG entry (commit 4)
- Opening a PR (commit 4)

## What you must do

1. **Pre-flight checks** (v4 — runs in the agent's bash preamble, before any model call):
   ```bash
   # All must pass. Failure means stop and report.
   [ -O "$HOME/.claude" ] || { echo "FAIL: ~/.claude not owned by current user"; exit 1; }
   [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ] || { echo "FAIL: CLAUDE_CODE_OAUTH_TOKEN missing"; exit 1; }
   [ -n "$GITHUB_TOKEN" ] || { echo "FAIL: GITHUB_TOKEN missing"; exit 1; }
   git rev-parse --abbrev-ref HEAD | grep -qE "^claude/[^/]+-c1$" || {
     echo "FAIL: not on claude/<prompt>-c1 branch (current: $(git rev-parse --abbrev-ref HEAD))"
     exit 1
   }
   ```

2. **Branch strategy** (v4 — see BRANCH-STRATEGY.md):
   - **Stacked prompts** (c1→c2→c3→c4 share dependencies, like 10c-iii): branch c1 from main; c2 from c1; c3 from c2; c4 from c3. The PR's `base` is the prior c's branch, not main.
   - **Parallel prompts** (c1-c4 independent): each branches from main.
   - The refactored prompt's `## Branch strategy` section says which model applies. If absent, assume **stacked** (the safer default).

2. **Read the refactored prompt.** Find it via `ls prompts/_active/<NN>-*.md`. There should be exactly one file matching the active prompt id. Read it in full.

3. **Locate the C1 block in the prompt's §8 Commit structure.** It tells you what schema/plumbing to ship in commit 1.

4. **If C1 says `<NO_OP/>`,** output exactly `<NO_OP/>` and stop. Do NOT commit. Do NOT advance to C2. Some prompts don't need a schema/plumbing commit.

5. **Otherwise, do the work for C1 ONLY.** Stay strictly within C1 scope as defined in §8 of the refactored prompt.

6. **Read constitutional docs cited in the prompt's §2 Read first** before writing code. Specifically:
   - `docs/SYSTEM_INVARIANTS.md` sections relevant to schema/event-type/projection work (§1, §2, §3, §4, §12, §15)
   - `docs/DECISIONS.md` D-decisions cited by the prompt (commonly D6, D7, D8, D13, D14, D15)
   - `ADMINISTRATEME_BUILD.md` sections cited by the prompt

7. **Make exactly one git commit.** Commit message MUST follow this exact format (v4 — enforced by Reviewer's Job 1 contract check):
   ```
   phase <PROMPT_ID> commit 1: <imperative summary, <72 chars>
   ```
   Example: `phase 10c-iii commit 1: 3 event schemas + 3 program files + cron rows`
   Use `git -c user.email='claude+executor@anthropic.com' -c user.name='claude (executor-c1)' commit -am '<msg>'` to attribute properly.

   **Why strict:** v3 test drive had c4 mis-label its commit as "commit 1" because the format wasn't enforceable. v4 makes the format mandatory.

8. **Run the prompt's §9 Verification block.** Typically:
   ```bash
   poetry run pytest tests/unit/<scope> -v --tb=short
   bash scripts/verify_invariants.sh
   ```
   If verification fails, FIX the failures and amend into commit 1 via `git commit --amend --no-edit`. Do not advance with a broken tree.

9. **If verification still fails after a fix attempt,** emit `<COMMIT_FAILED reason="..."/>` and stop. The operator will diagnose.

10. **Push the c1 branch and open a PR** (v4 — each c is its own PR per `BRANCH-STRATEGY.md`):

    ```bash
    # Push via explicit-URL token injection (works headless; bypasses VS Code credential helper).
    # --no-verify skips the LFS pre-push hook which is removed by post-create.sh anyway.
    git push -u --no-verify \
      "https://x-access-token:${GITHUB_TOKEN}@github.com/junglecrunch1212/administrate-me-now" \
      "claude/<PROMPT_ID>-c1:claude/<PROMPT_ID>-c1"

    # Scrub the token from the tracking config (it leaks otherwise).
    git remote set-url origin https://github.com/junglecrunch1212/administrate-me-now.git
    git config branch."claude/<PROMPT_ID>-c1".remote origin

    # Open PR via GitHub API (curl + JSON file; bypasses shell-quoting hell with embedded JSON).
    python3 -c "import json; print(json.dumps({
      'title': 'phase <PROMPT_ID> commit 1: <one-line summary>',
      'body': '<body — see Body template below>',
      'head': 'claude/<PROMPT_ID>-c1',
      'base': 'main'
    }))" > /tmp/c1_pr.json

    curl -sS -X POST \
      -H "Authorization: token ${GITHUB_TOKEN}" \
      -H 'Content-Type: application/json' \
      --data @/tmp/c1_pr.json \
      https://api.github.com/repos/junglecrunch1212/administrate-me-now/pulls
    ```

    **PR body template:**
    ```
    Generated by executor-c1 from prompts/_active/<PROMPT_ID>-*.md.

    ## What this commit ships
    - <bullet per deliverable, with file path + line count>

    ## Verification
    - poetry run pytest <test paths> -v: <N> passed
    - bash scripts/verify_invariants.sh: exit 0

    Next: executor-c2 from this branch (base for the c2 PR will be `claude/<PROMPT_ID>-c1`).
    ```

11. **After PR opens cleanly, stop.** Output the PR URL on the last line. Wait for the operator to invoke C2 (which will branch from this c1 branch per stacked strategy).

## Hard rules (non-negotiable invariants)

These apply to commits 1-4. Cited per the supervised build's standards.

- **No interactive prompts.** You are headless under `claude -p`, or you have an attentive operator under interactive `claude`. Either way, if a CLI tool prompts, use a non-interactive flag.
- **No LLM SDK imports in `adminme/`** per `[§8.2]` and `[D6]`. The skill runner wraps OpenClaw's gateway at `: