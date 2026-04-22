# Prompt 00: Preflight (Phase A — Claude Code sandbox)

**Phase:** Pre-build. Runs in Claude Code's sandbox against the GitHub repo. The Mac Mini is NOT involved.
**Depends on:** Nothing. This is the first prompt.
**Estimated duration:** 15-20 minutes.
**Stop condition:** `docs/preflight-report.md` exists in the repo and reports all green, OR specific issues identified with clear next steps.

---

## Read first (required)

Read, in order:

1. `ADMINISTRATEME_BUILD.md` section "THIS IS A FIVE-FILE DELIVERABLE — READ ALL FIVE BEFORE STARTING" (near the top). This is your orientation.
2. `ADMINISTRATEME_BUILD.md` section "OPENCLAW IS THE ASSISTANT SUBSTRATE" (next section). You are NOT installing OpenClaw in this prompt — OpenClaw is a **Phase B** concern (Mac Mini bootstrap). Your job here is to build the code that will later integrate with OpenClaw.

Do NOT read the rest of BUILD.md in this prompt. Prompt 01 will do that.

## Operating context

You are Claude Code running in Anthropic's sandbox. The operator is not present during your build — they review your commits and PRs asynchronously. You work **entirely against the GitHub repository** at https://github.com/junglecrunch1212/administrate-me-now. No Mac Mini. No live services.

**Two-phase model.** This is critical:

- **Phase A (you, now):** Generate the AdministrateMe codebase on GitHub. All your work happens in this repo. You write code, run tests that can run in your sandbox (mock external services), commit, and push. The operator reviews PRs. When prompt 18 passes, the codebase is build-complete.

- **Phase B (operator, later):** On the Mac Mini, the operator installs OpenClaw, clones the repo, and runs `./bootstrap/install.sh`. That's when the live instance comes online against real iMessage, real Google Workspace, real Plaid, real Laura and Charlie. You are not involved in Phase B — your job is to have built a bootstrap wizard that Just Works when run.

Your job in **this** prompt is only to verify your own sandbox environment and the state of the GitHub repo. You write no production code. You produce one markdown report and commit it.

## Objective

Produce `docs/preflight-report.md` that verifies:
1. Your sandbox has the tools needed to build AdministrateMe (Python 3.12+, Node 24+, Poetry, npm, git, gh).
2. The repo is accessible, the five artifact files are present, and there is no uncommitted drift that would confuse later prompts.

The report either confirms all green (ready for prompt 00.5) or lists specific issues with clear remediation notes for the operator.

## Out of scope

- Do NOT check for OpenClaw, Tailscale, Plaid, BlueBubbles, or any Phase B service. Those are not installed in your sandbox and don't need to be. They are verified during the Mac Mini bootstrap.
- Do NOT check for macOS-specific things (FileVault, 1Password CLI, LibreOffice). Your sandbox is probably Linux; those are Phase B concerns.
- Do NOT install any software. Only verify what's available.
- Do NOT create any databases, configuration files, or `~/.adminme/` directories.
- Do NOT begin PHASE 0 scaffolding. That is prompt 02.

## Deliverables

**One file, committed to the repo:** `docs/preflight-report.md` with the following structure:

```markdown
# AdministrateMe preflight report (Phase A)

Generated: <ISO timestamp>
Verifier: Claude Code (prompt 00)
Environment: Claude Code sandbox (not Mac Mini)

## Sandbox environment

| Requirement | Version check | Result | Pass? |
|-------------|--------------|--------|-------|
| Python 3.12+ | `python3 --version` | ... | ✓/✗ |
| Node 24+ | `node --version` | ... | ✓/✗ |
| Poetry | `poetry --version` | ... | ✓/✗ |
| npm | `npm --version` | ... | ✓/✗ |
| git | `git --version` | ... | ✓/✗ |
| gh (GitHub CLI) | `gh --version` | ... | ✓/✗ |

## Repository

- Remote: https://github.com/junglecrunch1212/administrate-me-now.git
- Working directory: <pwd>
- Branch: main
- Current commit: <hash> <subject>
- Uncommitted changes: yes/no (list if any)
- `git pull origin main` result: clean / N commits pulled

## Artifact files

| File | Present | Size | SHA256 (first 8) |
|------|---------|------|------------------|
| ADMINISTRATEME_BUILD.md | ✓/✗ | XXK | xxxx |
| ADMINISTRATEME_CONSOLE_REFERENCE.html | ✓/✗ | XXK | xxxx |
| ADMINISTRATEME_CONSOLE_PATTERNS.md | ✓/✗ | XXK | xxxx |
| ADMINISTRATEME_REFERENCE_EXAMPLES.md | ✓/✗ | XXK | xxxx |
| ADMINISTRATEME_DIAGRAMS.md | ✓/✗ | XXK | xxxx |

## Network access (for prompt 00.5)

| Check | Command | Result | Pass? |
|-------|---------|--------|-------|
| GitHub reachable | `curl -sI https://github.com` | ... | ✓/✗ |
| Outbound HTTPS working | (verify by fetching a known-good URL) | ... | ✓/✗ |

## Phase B prerequisites (operator verifies on Mac Mini)

NOT checked in this prompt. Listed as reminders for Phase B:

- macOS 14+ on the Mac Mini
- OpenClaw gateway installed and running on :18789
- BlueBubbles server running (for iMessage channel)
- Tailscale authenticated; Funnel configured (for Plaid webhook)
- 1Password CLI authenticated for secret storage
- Plaid / Google / Apple credentials available to operator

See `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 for the operator's setup checklist.

## Summary

<one paragraph: all green → proceed to prompt 00.5 / issues listed>

## Issues (if any)

For each ✗ above:
- What is missing/wrong
- How to fix (note whether Claude Code can do it or operator must)
```

## Verification

```bash
ls -la ADMINISTRATEME_*.md ADMINISTRATEME_*.html
cat docs/preflight-report.md

git add docs/preflight-report.md
git commit -m "phase 00: preflight report"
git push origin main   # or to a branch if working on a feature branch
```

Expected:
1. All five artifact files visible.
2. Preflight report committed and pushed.
3. Either "all green" summary OR specific issue list.

## Stop

Do not proceed to prompt 00.5 until:

1. Operator has reviewed `docs/preflight-report.md` on GitHub.
2. Any ✗ items addressed.
3. A re-run shows all green.

**Explicit stop message:**

> Phase A preflight complete. `docs/preflight-report.md` committed. Next prompt: **00.5 (mirror external reference docs into the repo).**
>
> Phase B prerequisites (OpenClaw, Tailscale, etc.) are NOT checked here — operator responsibility during bootstrap.
