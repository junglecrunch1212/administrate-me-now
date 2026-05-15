# RUNBOOK-EXECUTOR.md

**v4 — step-by-step invocation runbook for a full c1→c4 Executor cycle.**

Run this when Partner has merged a refactored prompt to main and you're ready to ship the four commits. Time budget: ~45-90 minutes total wall-clock; ~$60-120 in token spend (varies by prompt size).

This runbook assumes the v3 test-drive bugs are fixed (per POSTMORTEM-v3-to-v4.md): ~/.claude perms, LFS hooks, model alias, permission mode, branch strategy.

---

## §0 — Prerequisites

Before starting:

1. **Partner has refactored the next prompt.** PR with `partner/refactor-<PROMPT_ID>` is open or merged. The file `prompts/_active/<PROMPT_ID>-*.md` (or `prompts/<PROMPT_ID>-*.md`) exists on main.

2. **Codespace is in GREEN pre-flight state.** Open the Codespace, run `bash .devcontainer/post-create.sh` if needed, look for `=== Codespace pre-flight verdict: GREEN ===` in output. If YELLOW or RED, fix per `06-TROUBLESHOOTING.md`.

3. **The refactored prompt declares its branch strategy.** Open `prompts/_active/<PROMPT_ID>-*.md` and confirm there's a `## Branch strategy` section saying `STACKED` or `PARALLEL`. If absent, default to STACKED per BRANCH-STRATEGY.md.

4. **You have a free terminal** in the Codespace web UI (or a connected local IDE). The terminal must be a real bash, not a Python REPL or claude interactive.

---

## §1 — The full invocation pattern (stacked example)

For a STACKED prompt with PROMPT_ID `10c-iii`:

### Step 1.1 — Start c1

```bash
# In Codespace terminal
cd /workspaces/administrate-me-now
git checkout main && git pull origin main
git checkout -b claude/10c-iii-c1

claude --print \
       --model "${ADMINME_MODEL_EXECUTOR:-opus}" \
       --max-budget-usd 30 \
       --permission-mode bypassPermissions \
       'Read .claude/agents/executor-c1.md fully and follow it. Work prompt: prompts/_active/10c-iii-reminder-crm-custody.md. Execute Commit 1 only. Branch strategy: stacked per BRANCH-STRATEGY.md. After commit, push via the explicit-URL token pattern in the agent and open a PR with base=main. Stop after PR opens.' \
       < /dev/null 2>&1 | tee /tmp/c1.log
```

**Expected wall-clock:** 10-25 minutes for c1's typical schema/plumbing work.

**Watch for:** the last line of `/tmp/c1.log` should be a `https://github.com/...` PR URL. If it's `<COMMIT_FAILED .../>` or `<NEEDS_AMENDMENT .../>`, stop and read the report.

### Step 1.2 — Verify c1 PR

```bash
# Check the PR exists and tests pass (Reviewer hasn't fired yet — PR isn't merged)
curl -sS -H "Authorization: token $GITHUB_TOKEN" \
  "https://api.github.com/repos/junglecrunch1212/administrate-me-now/pulls?state=open&head=junglecrunch1212:claude/10c-iii-c1" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["html_url"]) if d else print("NO PR")'
```

Open the URL, eyeball the diff. If the prompt's Commit 1 spec matches the PR's content, **merge** c1.

### Step 1.3 — After c1 merges, start c2

```bash
git checkout claude/10c-iii-c1   # branch from c1, NOT main
git pull origin claude/10c-iii-c1
git checkout -b claude/10c-iii-c2

claude --print \
       --model "${ADMINME_MODEL_EXECUTOR:-opus}" \
       --max-budget-usd 40 \
       --permission-mode bypassPermissions \
       'Read .claude/agents/executor-c2.md fully and follow it. Work prompt: prompts/_active/10c-iii-reminder-crm-custody.md. Execute Commit 2 only. Branch strategy: stacked per BRANCH-STRATEGY.md. After commit, push via the explicit-URL token pattern and open a PR with base=claude/10c-iii-c1. Stop after PR opens.' \
       < /dev/null 2>&1 | tee /tmp/c2.log
```

**Note the PR base:** `claude/10c-iii-c1`, NOT main. This is the stacked pattern.

**v3 test-drive lesson:** If c1 wasn't merged yet, c2 can still be opened against c1's branch. But Reviewer won't fire on c2 until c1 merges first. Best practice: merge c1, let Reviewer fire (it's quick — ~5 min), see the verdict, then start c2.

### Step 1.4 — c3 (after c2 merges)

```bash
git checkout claude/10c-iii-c2
git pull origin claude/10c-iii-c2
git checkout -b claude/10c-iii-c3

claude --print --model "${ADMINME_MODEL_EXECUTOR:-opus}" --max-budget-usd 50 \
       --permission-mode bypassPermissions \
       '<see c3 work instruction template>' < /dev/null 2>&1 | tee /tmp/c3.log
```

### Step 1.5 — c4 (after c3 merges)

```bash
git checkout claude/10c-iii-c3
git pull origin claude/10c-iii-c3
git checkout -b claude/10c-iii-c4

claude --print --model "${ADMINME_MODEL_EXECUTOR:-opus}" --max-budget-usd 50 \
       --permission-mode bypassPermissions \
       '<see c4 work instruction template>' < /dev/null 2>&1 | tee /tmp/c4.log
```

c4's pre-flight check (from the v4 agent) requires `git log --oneline main..HEAD | wc -l` to return ≥3 (c1, c2, c3 all visible). If not, c4 fails fast with `<COMMIT_FAILED reason="c4 not stacked"/>`.

---

## §2 — Variations

### §2.1 — Headless autonomous mode (all four in one terminal)

Want to fire all four without manual intervention between each? Use this driver script (saved as `tools/run_full_cycle.sh`):

```bash
#!/bin/bash
# Usage: tools/run_full_cycle.sh 10c-iii
set -e
PROMPT_ID="$1"
[ -z "$PROMPT_ID" ] && { echo "Usage: $0 <PROMPT_ID>"; exit 1; }

# Pre-flight
bash .devcontainer/post-create.sh > /tmp/preflight.log 2>&1
grep -q "GREEN" /tmp/preflight.log || { echo "Pre-flight not GREEN"; exit 1; }

# c1
git checkout main && git pull origin main
git checkout -b "claude/${PROMPT_ID}-c1"
claude --print --model opus --max-budget-usd 30 --permission-mode bypassPermissions \
  "Read .claude/agents/executor-c1.md. Work: prompts/_active/${PROMPT_ID}-*.md. Commit 1 only. Stacked. Open PR base=main. Stop after PR." \
  < /dev/null 2>&1 | tee /tmp/c1.log

# c2 (branches from c1 — works whether c1 is merged or not in stacked mode)
git checkout "claude/${PROMPT_ID}-c1"
git checkout -b "claude/${PROMPT_ID}-c2"
claude --print --model opus --max-budget-usd 40 --permission-mode bypassPermissions \
  "Read .claude/agents/executor-c2.md. Work: prompts/_active/${PROMPT_ID}-*.md. Commit 2 only. Stacked. Open PR base=claude/${PROMPT_ID}-c1. Stop after PR." \
  < /dev/null 2>&1 | tee /tmp/c2.log

# c3
git checkout "claude/${PROMPT_ID}-c2"
git checkout -b "claude/${PROMPT_ID}-c3"
claude --print --model opus --max-budget-usd 50 --permission-mode bypassPermissions \
  "Read .claude/agents/executor-c3.md. Work: prompts/_active/${PROMPT_ID}-*.md. Commit 3 only. Stacked. Open PR base=claude/${PROMPT_ID}-c2. Stop after PR." \
  < /dev/null 2>&1 | tee /tmp/c3.log

# c4
git checkout "claude/${PROMPT_ID}-c3"
git checkout -b "claude/${PROMPT_ID}-c4"
claude --print --model opus --max-budget-usd 50 --permission-mode bypassPermissions \
  "Read .claude/agents/executor-c4.md. Work: prompts/_active/${PROMPT_ID}-*.md. Commit 4 only. Stacked. Open PR base=claude/${PROMPT_ID}-c3. Stop after PR." \
  < /dev/null 2>&1 | tee /tmp/c4.log

echo "All four PRs opened. Review at github.com/junglecrunch1212/administrate-me-now/pulls"
```

Run: `bash tools/run_full_cycle.sh 10c-iii`

**Risk of autonomous mode:** if c1 fails, c2 still tries to run and will fail too (because c2's base doesn't exist). Add a `grep -q "<COMMIT_FAILED" /tmp/c1.log && exit 1` guard between each c-band.

**When to use autonomous:** for prompts you've already calibrated on (i.e., not the first three or four). For new prompt types or untested patterns, run each c-band manually and review before advancing.

### §2.2 — Parallel mode (rare)

For PARALLEL prompts (independent c-bands), all four branches off main:

```bash
for n in 1 2 3 4; do
  git checkout main && git pull origin main
  git checkout -b "claude/${PROMPT_ID}-c${n}"
  claude --print --model opus --max-budget-usd 40 --permission-mode bypassPermissions \
    "Read .claude/agents/executor-c${n}.md. Work: prompts/_active/${PROMPT_ID}-*.md. Commit ${n} only. Parallel. Open PR base=main. Stop after PR." \
    < /dev/null 2>&1 | tee /tmp/c${n}.log
done
```

PR bases all = main. Operator merges any order.

### §2.3 — Eyes-on calibration runs

For the very first time running a new prompt-type (or after a Reviewer FAIL_CRITICAL), invoke `claude` interactively rather than `--print`. You see the TUI, see each tool call as it happens, can interrupt mid-stream.

```bash
git checkout -b claude/10c-iii-c1
claude
```

Then in the interactive prompt:
> Read .claude/agents/executor-c1.md. Execute Commit 1 of prompts/_active/10c-iii-*.md. Open PR base=main after commit.

Trade-off: interactive mode requires you to approve each tool call. With `--permission-mode bypassPermissions` in `--print`, claude moves through tool calls automatically. Interactive forces you to slow down — which is the point of "eyes on calibration."

---

## §3 — Monitoring and recovery

### §3.1 — Is claude still running?

```bash
ps -ef | grep 'claude --print' | grep -v grep
```

If a process is alive but stuck with 0 CPU time over several minutes, check `~/.claude` perms (the v3 bug). The v4 post-create.sh runs the chown at startup but if you're in an old Codespace, run:

```bash
sudo chown -R "$USER:$USER" "$HOME/.claude"
```

### §3.2 — Check current state mid-session

```bash
echo "PROC: $(ps -ef | grep 'claude --print' | grep -v grep | wc -l)"
echo "BRANCH: $(git branch --show-current)"
echo "DIFF SIZE: $(git diff HEAD --stat | tail -1)"
echo "RECENT FILE WRITES:"
find . -mmin -2 -type f -not -path "./.git/*" | head -10
```

### §3.3 — API 529 retry policy

If claude exits with `API Error: 529 Overloaded`, that's a server-side Anthropic issue:

1. Wait 30 seconds.
2. Re-run the exact same `claude --print ...` command.
3. If a third attempt also fails, check status.claude.com.
4. Persistent 529: escalate via `docs/partner_handoff.md` note ("Stuck on 529 since <time>"); don't burn more budget.

### §3.4 — Killing a stuck claude

```bash
pkill -f 'claude --print'
```

If the claude session had committed but not pushed, the work is preserved in the local branch. You can push manually:

```bash
git push -u --no-verify \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/junglecrunch1212/administrate-me-now" \
  "$(git branch --show-current):$(git branch --show-current)"
git remote set-url origin https://github.com/junglecrunch1212/administrate-me-now.git
```

Then open the PR via curl (see executor-c4.md §"What you do" step 8 for the curl template).

### §3.5 — Recovering from a mis-titled commit

If an Executor commits with a non-canonical message (e.g., the v3 c4 issue where the message said "commit 1"), amend in-place before pushing:

```bash
git commit --amend -m "phase <PROMPT_ID> commit <N>: <correct summary>"
```

If you discover the mis-title after pushing, force-push with `--force-with-lease`:

```bash
git push --force-with-lease --no-verify \
  "https://x-access-token:${GITHUB_TOKEN}@github.com/junglecrunch1212/administrate-me-now" \
  "$(git branch --show-current):$(git branch --show-current)"
git remote set-url origin https://github.com/junglecrunch1212/administrate-me-now.git
```

---

## §4 — Token discipline

Several places in this runbook embed `$GITHUB_TOKEN` in a URL or echo it via env-grep. The token is sensitive. Follow these rules:

1. **NEVER paste the token value into a chat, email, document, or anywhere outside the Codespace.** It's injected from Codespaces secrets, never store it elsewhere.
2. **After every `git push https://x-access-token:...@...` invocation, run `git remote set-url origin https://github.com/...`** to scrub the token from `branch.<name>.remote` tracking config.
3. **If you env-grep for diagnostics, redact with `sed 's/=.\{8,\}/=<REDACTED>/'`** (per POSTMORTEM §3.7). Never use partial substring matches like `sed 's/=.*token=.*/'`.
4. **Rotate the token every 90 days OR after any incident** where the value may have been exposed (terminal screenshot, accidentally committed config, etc.). Procedure: `claude setup-token` on a fresh terminal, update the Codespaces secret value at github.com/settings/codespaces.
5. **The first time the token appears in stdout or a screenshot**, treat it as exposed and rotate immediately. v3 test drive exposed the token twice (sed pattern miss + tracking URL leak) — both bad enough to require rotation.

---

## §5 — Time and cost budgets

Typical c1→c4 cycle for a "medium" prompt (~3-4 hours of work in v3 estimates):

| c-band | Wall-clock | Token spend (opus) |
|---|---|---|
| c1 (schema/plumbing) | 10-25 min | $8-20 |
| c2 (module A) | 15-30 min | $15-30 |
| c3 (module B) | 20-40 min | $25-50 |
| c4 (integration + BUILD_LOG) | 15-30 min | $20-40 |
| **Total** | **60-125 min** | **$68-140** |

These are pre-budget-cap caps. `--max-budget-usd` flags in the agents enforce hard ceilings: 30, 40, 50, 50. Actual spend is usually well under because claude doesn't push the cap.

Reviewer fires after each merge (post-merge). Reviewer's spend is separate and bills against the routines product, not the Codespace.

---

## §6 — When to abort

Stop the c1→c4 cycle and re-engage Partner if:

- Any c-band exits `<COMMIT_FAILED reason="..."/>` and the fix isn't obvious
- Any c-band exits `<NEEDS_AMENDMENT topic="..."/>` — that's constitutional drift, Partner handles
- The Reviewer's verdict on cN is `FAIL_CRITICAL` — sidecar fix needed before cN+1
- The Reviewer's Job 4 vision_drift_score is 3 (anti-vision) — strict-block per DRIFT-CONTROL-CHECKLIST.md
- You hit two consecutive API 529s

In all cases: `git checkout main`, the in-flight branches stay as draft PRs the operator can close, and you write a session-handoff doc per HANDOFF-TEMPLATE.md before ending the Codespace session.

---

## §7 — Sources for the patterns in this runbook

Every command here was proven during the v3 test drive (2026-05-14):

- Pre-flight checks: POSTMORTEM-v3-to-v4.md §3.1
- `--print --model opus --permission-mode bypassPermissions`: POSTMORTEM-v3-to-v4.md §3.2
- Explicit-URL token push + tracking URL scrub: POSTMORTEM-v3-to-v4.md §3.3
- curl-based PR creation: POSTMORTEM-v3-to-v4.md §3.4
- Stacked branch topology: POSTMORTEM-v3-to-v4.md §3.5 + BRANCH-STRATEGY.md
- API 529 retry: POSTMORTEM-v3-to-v4.md §3.8
- Token redaction sed pattern: POSTMORTEM-v3-to-v4.md §3.7

If you find any of these patterns failing in v4, update both the runbook and POSTMORTEM-v3-to-v4.md with the new finding.
