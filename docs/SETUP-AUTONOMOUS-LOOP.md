# Setting up the autonomous build loop

**Purpose:** one-time setup to enable running Executor cycles via GitHub Actions instead of the JRS Codespace terminal.

**Audience:** the operator (James). Assumes you can navigate github.com and console.anthropic.com via your browser. Does NOT assume command-line knowledge — everything below is clicks-only.

**Tier:** infrastructure runbook. Sidecar artifact. Not constitutional.

**Status (2026-05-15):** This document was updated mid-session after the JRS Codespace entered a persistent recovery state (error code 1302, container creation failure not resolved by PR #74's sidecar fix). The OAuth path described in earlier versions requires `claude setup-token` to run in JRS, which is currently impossible because the recovery shell has no `claude` CLI. API-key auth (this version) is fully self-contained via web UI and is the active path until the Codespace situation resolves.

---

## §0 — Why this exists

The Codespace-based Executor flow (`docs/RUNBOOK-EXECUTOR.md`) requires opening a terminal in the JRS Codespace and typing shell commands. The autonomous-loop end-state described in `WHAT-IS-ADMINISTRATEME.md` and the project's "user as debugger" vision needs a path where the operator only acts as a debugger when something fails — never as the person typing commands.

This setup replaces terminal-typing with a button-click on github.com's Actions tab.

---

## §1 — Generate an Anthropic API key (web-only, ~3 minutes)

1. Open https://console.anthropic.com/ in your browser. Log in with your Anthropic account if not already.
2. Pick or create a workspace for this build. The workspace can be your default. If you want isolated billing for the AdministrateMe build specifically, click **Settings** → **Workspaces** → create one named e.g. `administrate-me-build`.
3. In your chosen workspace, set a **daily spend cap**. The path is roughly **Settings** → **Limits** → **Spend caps** (UI evolves; the keyword to look for is "limits" or "spend"). Start with **$20/day**. This is the safety net — Anthropic will block API calls if the cap is hit. One executor-cycle costs roughly $60-120, so $20/day caps you to one cycle every ~3-6 days while iterating.
4. Click **API keys** in the left sidebar. Click **Create key**. Name it `administrate-me-actions` or similar. Copy the value — you won't see it again. It looks like `sk-ant-api03-...`.

---

## §2 — Add the API key as a repository secret

1. Open https://github.com/junglecrunch1212/administrate-me-now in your browser.
2. Click **Settings** (top of the page, in the repo nav bar).
3. In the left sidebar, click **Secrets and variables** → **Actions**.
4. Click **New repository secret**.
5. **Name:** `ANTHROPIC_API_KEY` (exactly — case-sensitive).
6. **Secret:** paste the value from §1.4.
7. Click **Add secret**.

GitHub never shows the value again after this — that's normal, the workflow can read it.

---

## §3 — Run the smoke test

1. Go to the Actions tab: https://github.com/junglecrunch1212/administrate-me-now/actions
2. In the left sidebar, find **Smoke test (claude-code auth)**.
3. Click it. On the right, click **Run workflow**. Leave defaults. Click the green **Run workflow** button.
4. Wait ~1 minute. Refresh the page. Should be a green checkmark.
5. Click the run to see logs. The last line of the final step should say `Smoke test PASSED.`

Cost: ~$0.001 in API dollars (one Haiku call). Negligible.

---

## §4 — If the smoke test fails

Click into the failed run and scan the logs. Common causes:

- **"No auth secret configured"** — you skipped §2 or named the secret wrong. Re-add it with the exact name `ANTHROPIC_API_KEY`.
- **"401 Authentication failed"** — the key value is wrong (whitespace pasted in, copy got mangled, or you copied the wrong key). Regenerate at console.anthropic.com and re-paste in §2.
- **"402 Payment required" / "Insufficient credits"** — your Anthropic workspace doesn't have billing set up or has hit its cap. Add a payment method or raise the cap in the console.
- **`npm install` step fails** — the @anthropic-ai/claude-code npm package name may have changed. Open Partner chat and we'll update the workflow.
- **Anything else** — paste the failing log section into Partner chat for debugging.

---

## §5 — What's next once smoke test passes

A follow-up PR will add `.github/workflows/executor-cycle.yml`, which runs the actual c1→c4 cycle against a prompt ID you provide as workflow input. You'll see a new workflow called **Executor cycle (c1→c4)** appear in the Actions tab after that PR merges. Running it is the same one-button flow as the smoke test, but for a real prompt.

Per-cycle cost (API key path): **$60-120** in API dollars per c1→c4. Set spend caps accordingly.

The four c-band PRs still need your manual review and merge in sequence. Reviewer-strict-block automatically blocks any PR with vision_drift_score = 3 per `docs/DRIFT-CONTROL-CHECKLIST.md`. Operator click-to-merge remains the human gate.

---

## §6 — Future: switch to OAuth (subscription billing) when Codespace works

Originally this runbook had OAuth (subscription) as §1 because subscription usage costs $0 per cycle in API dollars (flat-fee instead of pay-per-token). That path needs `claude setup-token` to run somewhere — typically in JRS Codespace's terminal — which produces a long-lived token to paste as `CLAUDE_CODE_OAUTH_TOKEN` repo secret.

As of 2026-05-15, JRS Codespace fails to build (container creation error 1302) and the recovery shell has no `claude` CLI. PR #74 attempted a partial fix (replaced the broken Anthropic devcontainer feature with an npm install fallback) but did not resolve the underlying build failure — the actual root cause is elsewhere in the devcontainer build path and has not been positively identified.

When the Codespace is restored:

1. In JRS terminal: `claude setup-token`. Copy the printed token.
2. Add as repo secret `CLAUDE_CODE_OAUTH_TOKEN` (Settings → Secrets and variables → Actions).
3. *Optional:* delete the `ANTHROPIC_API_KEY` secret to enforce OAuth-only. Or keep both — the workflow accepts either, but claude-code prefers `ANTHROPIC_API_KEY` when both are set.
4. Re-run smoke test to confirm OAuth path works. If yes, future executor-cycle runs cost $0 in API dollars (they consume your subscription's weekly cap instead).

The workflow itself accepts either auth method, so the switch is purely a secrets-config change — no code change required.

---

## §7 — Notes on the existing Codespace path

The JRS Codespace-based Executor path (`docs/RUNBOOK-EXECUTOR.md`) is **currently broken** as of 2026-05-15 19:51 onwards. Container builds fail with error 1302 (UnifiedContainersErrorFatalCreatingContainer). The actual root cause is somewhere in the ~30 lines before line 11718 of `creation.log` but has not been positively identified — attempts to read those lines via Chrome MCP in the recovery shell produced renderer timeouts.

Until the Codespace is restored, this Actions workflow path is the only working route to run the Executor. The existing RUNBOOK-EXECUTOR.md remains as reference for the intended Codespace flow but will not work in practice until the build error is fixed.

Resolving the Codespace failure is a separate (lower-priority) workstream from the autonomous loop itself. If/when restored, see §6 above for the OAuth migration steps.
