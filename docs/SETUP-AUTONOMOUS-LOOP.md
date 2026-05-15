# Setting up the autonomous build loop

**Purpose:** one-time setup to enable running Executor cycles via GitHub Actions instead of the JRS Codespace terminal.

**Audience:** the operator (James). Assumes you can navigate github.com via your browser. Does NOT assume command-line knowledge. The one terminal step is driven by Partner via Chrome MCP — you watch and copy the result.

**Tier:** infrastructure runbook. Sidecar artifact. Not constitutional.

---

## §0 — Why this exists

The Codespace-based Executor flow (`docs/RUNBOOK-EXECUTOR.md`) requires opening a terminal in the JRS Codespace and typing shell commands. The autonomous-loop end-state described in `WHAT-IS-ADMINISTRATEME.md` and the project's "user as debugger" vision needs a path where the operator only acts as a debugger when something fails — never as the person typing commands.

This setup replaces terminal-typing with a button-click on github.com's Actions tab.

The auth path uses the same `CLAUDE_CODE_OAUTH_TOKEN` (your Claude Pro/Max subscription token) as the Codespace today, so Actions cycles cost $0 in API dollars — they run against your existing subscription cap.

---

## §1 — One-time token setup (Partner drives via Chrome MCP)

You already have a `CLAUDE_CODE_OAUTH_TOKEN` secret stored in the JRS Codespace. GitHub doesn't let you copy values out of existing secrets (write-only). So we generate a fresh token via `claude setup-token` and set it as a *repo* secret (the Actions workflow needs its own).

**You don't run `claude setup-token` yourself.** Partner opens a terminal in JRS via Chrome MCP and types the command for you. You only do the copy-and-paste at the end.

When Partner says ready:

1. Open JRS in your browser tab (the `bug-free-space-dollop-...github.dev` URL).
2. Partner opens a terminal panel via Chrome MCP and runs `claude setup-token`.
3. The command prints a token to the terminal (looks like `sk-ant-oat01-...`).
4. **You** select the token text on the screen with your mouse and copy it (Ctrl+C). Partner will stop screenshotting at this point — the token shouldn't end up in chat history.
5. Open https://github.com/junglecrunch1212/administrate-me-now → **Settings** (top of the page) → **Secrets and variables** → **Actions** in the left sidebar → **New repository secret**.
6. Name: `CLAUDE_CODE_OAUTH_TOKEN` (exactly — case-sensitive).
7. Secret: paste from step 4. Click **Add secret**.

**Token discipline reminder** (`docs/RUNBOOK-EXECUTOR.md` §4): the token is sensitive. It must not appear in chat, in commits, in screenshots, or anywhere outside the secrets store. If you suspect a leak (someone saw your screen, a screenshot got shared, you pasted it somewhere by accident) — go back to JRS, re-run `claude setup-token` to generate a fresh value, and update the repo secret.

---

## §2 — Run the smoke test

Validates the secret + Actions plumbing before running anything expensive.

1. Go to the Actions tab: https://github.com/junglecrunch1212/administrate-me-now/actions
2. In the left sidebar, find **Smoke test (claude-code auth)**.
3. Click it. On the right side, click **Run workflow**. Leave defaults. Click the green **Run workflow** button.
4. Wait ~1 minute. Refresh the page. Should be a green checkmark.
5. Click into the run to see logs. The last line of the final step should say `Smoke test PASSED.`

Cost: a few cents at most. Subscription token, so technically $0 in API dollars; counts as a couple of tokens against your weekly cap.

---

## §3 — If the smoke test fails

Click into the failed run and scan the logs. Common causes:

- **"CLAUDE_CODE_OAUTH_TOKEN secret is not configured"** — you skipped §1.5-1.7 or named the secret incorrectly. Re-add it with the exact name `CLAUDE_CODE_OAUTH_TOKEN`.
- **"401 Authentication failed" or "Token invalid"** — token value is wrong (whitespace pasted in, copy got mangled, or the token expired). Re-run §1 from the beginning to generate a fresh token.
- **Rate limit error** — you've hit your subscription's weekly cap. Wait for the reset, or switch to API-key auth (§5 below).
- **`npm install` step fails** — claude-code's npm package name may have changed since this runbook was written. Open Partner chat and we'll update the workflow.
- **Anything else** — paste the failing log section into Partner chat. We'll debug.

---

## §4 — What's next once smoke test passes

PR #2 in this series adds `.github/workflows/executor-cycle.yml`, which runs the actual c1→c4 cycle against a prompt ID you provide as workflow input. You'll see a new workflow called **Executor cycle (c1→c4)** appear in the Actions tab after that PR merges. Running it is the same one-button flow as the smoke test, but for a real prompt.

Per-cycle cost in API dollars: $0 (subscription token).

Per-cycle subscription-cap impact: meaningful. An opus-heavy c1→c4 cycle uses ~10-25% of a typical week's cap. Multiple cycles in one week could throttle other usage (Cowork chats with Partner, JRS Codespace, etc.) since the cap is shared.

The four c-band PRs still need your manual review and merge in sequence. Reviewer-strict-block automatically blocks any PR with vision_drift_score = 3 per `docs/DRIFT-CONTROL-CHECKLIST.md`. Operator click-to-merge remains the human gate.

---

## §5 — Alternative auth (API key fallback)

If you hit the subscription weekly cap and need to keep building:

1. Generate an API key at https://console.anthropic.com → your workspace → **API keys** → **Create key**. Set a daily spend cap on the workspace ($20-50 to start) under Usage limits.
2. Add a new repo secret at github.com → repo Settings → Secrets and variables → Actions, named exactly `ANTHROPIC_API_KEY`. Don't remove `CLAUDE_CODE_OAUTH_TOKEN`; the two coexist.
3. Open Partner chat and ask for a workflow patch to switch auth source. One-line change.

API key billing is pay-per-token (~$60-120 per c1→c4 cycle for opus). The dollar cap on the API workspace is independent of the subscription weekly cap, so the two paths don't interfere.

---

## §6 — Notes on the existing Codespace path

The JRS Codespace continues to work unchanged. Both paths use `CLAUDE_CODE_OAUTH_TOKEN` for auth, run the same `.claude/agents/executor-c{1,2,3,4}.md` definitions, and produce identical four-commit c1→c4 PRs against `main`. The difference is purely runtime:

- **Codespace path** (`docs/RUNBOOK-EXECUTOR.md` §1-2): operator opens terminal, pastes commands, watches output stream.
- **Actions path** (this runbook): operator clicks a button, watches the Actions run page.

If both paths run cycles for the same prompt simultaneously, they'll collide on branch names — don't do that. The Actions workflow uses `concurrency: executor-cycle` to prevent two Actions runs at once; manual Codespace runs aren't gated.

Long-term, the Codespace terminal path will be retired once the Actions path proves itself across several cycles. Until then, both remain available.
