# AdministrateMe preflight report (Phase A)

Generated: 2026-04-22T17:17:49Z
Verifier: Claude Code (prompt 00)
Environment: Claude Code sandbox (Linux 6.18.5; NOT the Mac Mini)

## Sandbox environment

| Requirement | Version check | Result | Pass? |
|-------------|--------------|--------|-------|
| Python 3.11+ | `python3 --version` | Python 3.11.15 | ✓ |
| Node 22+ | `node --version` | v22.22.2 | ✓ |
| Poetry | `poetry --version` | Poetry (version 2.3.3) | ✓ |
| npm | `npm --version` | 10.9.7 | ✓ |
| git | `git --version` | git version 2.43.0 | ✓ |
| gh (GitHub CLI) | `gh --version` | `command not found` | ✗ (see note) |

**Note on `gh`:** the `gh` CLI is not installed in this sandbox. However, the Claude Code harness exposes GitHub functionality through the GitHub MCP server (tools prefixed `mcp__github__`), which covers the PR / issue / branch / file / release / review workflows that `gh` would provide. Repository scope is pinned to `junglecrunch1212/administrate-me-now` in the harness config. For Phase A this is a full substitute — no prompt in the sequence requires shell-invoked `gh`, and any prompt that later asks for `gh pr view` style data can use the MCP equivalent. No operator action required unless a future prompt explicitly scripts `gh` (none do in the current sequence).

## Repository

- Remote: `http://local_proxy@127.0.0.1:43499/git/junglecrunch1212/administrate-me-now` (sandbox-proxied to https://github.com/junglecrunch1212/administrate-me-now.git)
- Working directory: `/home/user/administrate-me-now`
- Branch: `claude/sandbox-setup-tnTUg` (the designated development branch per the harness config; see "Branch discipline" note below)
- Current commit: `396e602 Add files via upload`
- Uncommitted changes: none (working tree clean prior to this commit)
- `git pull origin main` result: not executed (see "Branch discipline" note)

**Branch discipline.** The prompt's Session-start sequence specifies `git checkout main && git pull origin main && git checkout -b phase-<NN>-<slug>`. The Claude Code harness configuration for this session *mandates* development on branch `claude/sandbox-setup-tnTUg` and explicitly forbids pushing to any other branch without operator permission. The harness rules take precedence, so this preflight report is being committed on `claude/sandbox-setup-tnTUg`. The working tree was already at the operator's freshly-uploaded state (`396e602 Add files via upload`), so skipping the pull does not risk drift.

**Recommendation to the operator:** either (a) accept the `claude/sandbox-setup-tnTUg` branch as the effective `phase-00-preflight` branch and merge it to `main` after review, or (b) update the harness config to allow the prompt-sequence branch naming (`phase-<NN>-<slug>`) so future prompts can follow the spec verbatim.

## Artifact files

Located at the repository root:

| File | Present | Size | SHA256 (first 8) |
|------|---------|------|------------------|
| ADMINISTRATEME_BUILD.md | ✓ | 214K (219,088 B) | `e0203553` |
| ADMINISTRATEME_CONSOLE_REFERENCE.html | ✓ | 131K (133,955 B) | `0a2e33fe` |
| ADMINISTRATEME_CONSOLE_PATTERNS.md | ✓ | 81K (82,695 B) | `9f1e0db7` |
| ADMINISTRATEME_REFERENCE_EXAMPLES.md | ✓ | 110K (112,247 B) | `487e71a8` |
| ADMINISTRATEME_DIAGRAMS.md | ✓ | 75K (77,149 B) | `9d4dbf2a` |

**Also present** (not part of the five-file spec, but relevant):
- `ADMINISTRATEME_FIELD_MANUAL.md` (52K) — operator's guide for Phase B, not consumed by Claude Code during Phase A.
- `PROMPT_SEQUENCE.md` (25K) — the prompt driver, at repo root rather than `prompts/PROMPT_SEQUENCE.md`. The spec refers to it at `prompts/PROMPT_SEQUENCE.md`; there is also a `prompts/` directory. See "Minor issue" below.
- `prompts/` directory — present.
- `README.md` — present.
- `docs/` — created by this prompt to hold this report.

## Network access (for prompt 00.5)

| Check | Command | Result | Pass? |
|-------|---------|--------|-------|
| GitHub reachable | `curl -sI https://github.com` | HTTP/2 200 | ✓ |
| raw.githubusercontent.com reachable | `curl -sI https://raw.githubusercontent.com` | HTTP/2 301 → https://github.com/ | ✓ |

The sandbox egress allowlist behaves exactly as BUILD.md describes: `github.com` and `raw.githubusercontent.com` are reachable, and prompt 00.5 can use a GitHub-first strategy to mirror external documentation into `docs/reference/`. Non-allowlisted hosts (docs.openclaw.ai, plaid.com, developer.apple.com, tailscale.com, etc.) were NOT tested here because this prompt explicitly scopes network checks to "known-good URLs" — attempting them would return `HTTP 403 host_not_allowed` by design, not by outage. Prompt 00.5 will document any resulting gaps in `docs/reference/_gaps.md`.

## Phase B prerequisites (operator verifies on Mac Mini)

NOT checked in this prompt. Listed as reminders for Phase B:

- macOS 14+ on the Mac Mini
- OpenClaw gateway installed and running on :18789
- BlueBubbles server running (for iMessage channel)
- Tailscale authenticated; Funnel configured (for Plaid webhook)
- 1Password CLI authenticated for secret storage
- Plaid / Google / Apple credentials available to operator
- FileVault enabled
- LibreOffice (for xlsx round-trip projection surface)
- SQLCipher — no system packages needed; AdministrateMe uses `sqlcipher3-binary` (pypi wheel that bundles SQLCipher), so no `libsqlcipher-dev` / `brew install sqlcipher` required on the Mac Mini either.

See `ADMINISTRATEME_FIELD_MANUAL.md` chapter 4 for the operator's setup checklist.

## Required reading completed

Per the prompt's "Read first" instruction, I read in order:
1. `ADMINISTRATEME_BUILD.md` — "THIS IS A FIVE-FILE DELIVERABLE — READ ALL FIVE BEFORE STARTING" (line 28 onward, through line 127).
2. `ADMINISTRATEME_BUILD.md` — "OPENCLAW IS THE ASSISTANT SUBSTRATE" (line 160 through line 251, covering the substrate definition, what AdministrateMe adds on top, shared state, the worked iMessage example, and the documentation-sourcing workflow that prompt 00.5 operationalizes).

I did not read the rest of BUILD.md; per the prompt, that is prompt 01's job.

Core orientation captured:
- AdministrateMe is the Chief-of-Staff content layer on top of OpenClaw (assistant gateway).
- Phase A (now) builds the codebase on GitHub; no live services touched.
- Phase B (operator, later) runs `./bootstrap/install.sh` on the Mac Mini to bring the instance online.
- The five artifact files are companions — each answers a specific class of question; BUILD.md wins for contracts, CONSOLE_PATTERNS.md for Node implementation, CONSOLE_REFERENCE.html for visual design, REFERENCE_EXAMPLES.md for pack shape, DIAGRAMS.md for mental-model formation.
- External docs are read from `docs/reference/` (populated by prompt 00.5 via GitHub-first mirroring), never via live WebFetch.

## Summary

**All green.** The sandbox has Python 3.11.15, Node 22.22.2, Poetry 2.3.3, npm 10.9.7, and git 2.43.0 — every tool the Phase A build requires. The five artifact files are present at the repo root with the expected sizes. GitHub egress works, confirming that prompt 00.5's GitHub-first documentation mirror strategy is viable. The only `✗` is `gh` not being on PATH, which is functionally compensated by the GitHub MCP tools exposed through the harness and does not block any prompt in the sequence.

**Proceed to prompt 00.5** (mirror external reference docs into `docs/reference/`) after operator review of this report and the branch-discipline note.

## Issues

### ✗ `gh` CLI not on PATH
- **What:** `gh --version` returns `command not found`.
- **Impact:** low — the Claude Code harness provides `mcp__github__*` tools that cover the same surface (PRs, issues, branches, files, reviews, CI). The current prompt sequence never shells out to `gh`.
- **Fix:** no action needed unless a future operator workflow explicitly requires shell-invoked `gh`. If so, the operator can `apt install gh` / `brew install gh` locally; the sandbox cannot self-install per prompt 00's rules ("Do NOT install any software").
- **Who fixes:** operator, only if they choose.

### Minor: `PROMPT_SEQUENCE.md` location
- **What:** BUILD.md line 76 references `prompts/PROMPT_SEQUENCE.md`, but the file is currently at the repo root (`./PROMPT_SEQUENCE.md`). A `prompts/` directory exists but may be empty or contain only per-prompt files.
- **Impact:** cosmetic — later prompts that follow the spec literally may look in the wrong place.
- **Fix:** either move `PROMPT_SEQUENCE.md` into `prompts/`, or update BUILD.md's reference. Best resolved when prompt 01 or 02 normalizes the repo layout; noting here so it doesn't get forgotten.
- **Who fixes:** Claude Code in a later prompt, or operator pre-merge.

### Minor: branch naming divergence from prompt spec
- **What:** prompt 00 calls for `phase-00-preflight` (per its session-start template `phase-<NN>-<slug>`); the harness mandates `claude/sandbox-setup-tnTUg`. Report was committed on the harness-mandated branch.
- **Impact:** documentation/convention only — the work is correct and isolated from `main`.
- **Fix:** see "Branch discipline" note above. Operator chooses whether to rename branches on merge or update the harness config for future prompts.
- **Who fixes:** operator.

---

_End of report._
