# Session handoff — github MCP install for Partner Cowork project

**Date:** 2026-05-15
**Operator:** James
**Partner session:** ad-hoc, meta (process improvement, not a build-prompt refactor)
**Branch (proposed, not yet pushed):** `sidecar-github-mcp-install`
**Status:** install staged locally; PAT-entry + Claude Desktop restart on James; doc refactor pending operator review

---

## §1 — Summary

Closed the long-standing gap noted in `session-handoff-10c-iii-partner-cycle-attempt.md` §83: Partner had no direct GitHub access in Cowork, forcing the "Partner produces full text in chat → James pastes via GitHub web UI" routing-around (PM-24 Partner-side path) for every doc-shaped artifact. Installed GitHub's official MCP server locally so Partner now has the `mcp__github__*` toolset that PROMPT_SEQUENCE.md and `prompts/_active/10c-iii-reminder-crm-custody.md` already reference. The decision retroactively validates those prompts' assumptions and retires the Partner-side leg of PM-24 (Executor-side stays in force).

## §2 — Decision: which connection method

Inventory of every Cowork-to-GitHub path surveyed on 2026-05-15:

| # | Method | Verdict |
|---|---|---|
| 1 | Anthropic "Add from GitHub" file-picker (Chats/Projects) | Read-only; no PRs; wrong shape. |
| 2 | Anthropic native Cowork GitHub MCP connector | Currently broken — [anthropics/claude-code#52549](https://github.com/anthropics/claude-code/issues/52549) OAuth routing bug. Future migration target once fixed. |
| 3 | Official GitHub MCP server, Docker stdio | Identical tool surface to #4; needs Docker Desktop (~500 MB + WSL2 + background service). Rejected per compaction rule. |
| 4 | **Official GitHub MCP server, binary stdio** | **Chosen.** ~30 MB, PAT auth, no admin, no service, identical `mcp__github__*` surface. |
| 5 | Official GitHub MCP server, remote HTTP (Custom Connector) | Requires GitHub App OAuth which Claude Desktop doesn't support yet (per github-mcp-server install-claude.md). Skipped. |
| 6 | Composio Connect | Adds a third-party trust hop. Skipped. |
| 7 | `engineering` plugin from `anthropics/knowledge-work-plugins` | Bundles 10 unrelated MCPs (slack, linear, asana, atlassian, notion, pagerduty, datadog, gcal, gmail) + 10 skills overlapping QC rubric. Violates 2026-05-14 compaction rule. Skipped. |
| 8 | `mcp-remote` proxy | Only useful as bridge to #5 which doesn't work. Skipped. |
| 9 | Claude in Chrome browsing github.com | Last-resort fallback. Kept in toolchain, not the primary path. |
| 10 | CLI in Codespace (status quo) | Executor's path. Doesn't help Partner. Unchanged. |
| 11 | DIY Cowork plugin via cowork-plugin-management | Way too much surface area. Skipped. |

**Pick (#4) rationale:**

- **Tool names already in the repo.** `PROMPT_SEQUENCE.md` line 33-34 and `prompts/_active/10c-iii-reminder-crm-custody.md` line 242 already cite `mcp__github__create_pull_request` and `mcp__github__pull_request_read`. Installing this MCP makes those references real instead of aspirational. Compaction-positive (no new naming surface to learn).
- **PAT auth dodges the OAuth bug.** Method #2 is the long-term right answer once Anthropic ships the fix; until then, PAT is the lower-risk path.
- **Retires the Partner-side leg of PM-24.** Partner now commits directly via `mcp__github__create_or_update_file` and `mcp__github__push_files`. Executor-side PM-24 unchanged (Claude Code timeouts on long static markdown in the Codespace remain).
- **Compaction-positive.** No admin, no background service, no reboot, no WSL2, no third-party SaaS. Single ~30 MB binary the user can delete by `rm -rf tools/github-mcp-server/`.

## §3 — What was installed

**Binary:** `github-mcp-server.exe` v1.0.4 (Windows x86_64), SHA-256 `c30cde1ba7f11e562d48a8e1c96dfcdf5bb8d0ae6be9ec6a5b50475e8138833a`. Verified against the release's published `github-mcp-server_1.0.4_checksums.txt`.

**Install path:** `C:\Users\Owner\Documents\Claude\Projects\AdministrateMe Partner\tools\github-mcp-server\github-mcp-server.exe` (also `LICENSE` and `README.md` from the same release zip).

**Claude Desktop config:** `%APPDATA%\Claude\claude_desktop_config.json` — added `mcpServers.github` block pointing at the binary with `args: ["stdio"]` and a `GITHUB_PERSONAL_ACCESS_TOKEN` env entry. The token field is a placeholder (`<PASTE_GITHUB_PAT_HERE>`) — operator must paste the value before restarting Claude Desktop.

**PAT scope:** Operator confirmed the existing Codespace PAT works. That token has `repo` + `workflow` scopes (per `RUNBOOK-EXECUTOR.md` §6). The Partner-side use needs `repo` (RW) + `pull_requests` (RW) + `issues` (RW). The existing token is over-scoped relative to Partner-only needs; consider migrating to a fine-grained PAT scoped specifically to `junglecrunch1212/administrate-me-now` in a follow-up.

## §4 — What changed in project docs (full-sweep refactor per operator selection)

- `PROJECT-INSTRUCTIONS.md` — replaced 7 ambiguous "via the GitHub connector" references with concrete `mcp__github__*` tool surfaces; added an install-state paragraph near the top of the role definition pointing to this handoff doc.
- `partner_handoff.md` — added "Partner-side path superseded 2026-05-15" sub-note under the PM-24 entry; amended the "always produce full file replacements" rule (line ~611) to add direct-commit as the default path alongside the existing James-pastes-via-web-UI fallback.
- `qc_rubric.md` — replaced "James can paste these if loading GitHub directly isn't feasible" with concrete `mcp__github__get_pull_request` / `mcp__github__list_commits` / `mcp__github__get_pull_request_files` instructions plus a "pre-2026-05-15 fallback" preservation of the paste path.
- `RUNBOOK-EXECUTOR.md` — added a §0 scope-note clarifying that this runbook covers Executor-only, that Partner has a parallel path via the MCP, and that nothing in the runbook changes for Executor.
- `build_log.md` — appended an INFRASTRUCTURE-class entry under Sidecar PRs documenting this work.

**Constitutional docs untouched.** `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_DIAGRAMS.md`, `architecture-summary.md`, `DECISIONS.md`, `SYSTEM_INVARIANTS.md`, `WHAT-IS-ADMINISTRATEME.md`, `openclaw-cheatsheet.md`, `CONSOLE_PATTERNS.md`, `REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_CONSOLE_REFERENCE.html` — all references to GitHub in these are to GitHub the *service* (registry repos, source URL, GitHub-first doc mirroring), not the connector. No amendment memo required.

**No prompt files touched.** The `mcp__github__*` references in `PROMPT_SEQUENCE.md` and `prompts/_active/10c-iii-*.md` are now real; no edits needed.

## §5 — What's still on the operator

1. **Paste the GitHub PAT** into `%APPDATA%\Claude\claude_desktop_config.json` (replace `<PASTE_GITHUB_PAT_HERE>` with the value of the Codespace PAT or a fine-grained replacement).
2. **Restart Claude Desktop** so it spawns the new MCP server. After restart, the tools should appear under `mcp__github__*` in Partner's tool list.
3. **Smoke-test via a one-shot read:** in a new Cowork session, ask Partner to `mcp__github__get_file_contents` on `docs/build_log.md` at `ref=main`. Expected: the contents of build_log.md identical to what Partner pinned locally. If the tool isn't there, see §6.
4. **Review this handoff and the doc refactor diffs** before approving the `sidecar-github-mcp-install` PR. Partner has NOT pushed any of the above; everything is local-only until operator approves.
5. **Decide on PAT scope migration:** keep the over-scoped Codespace PAT, or mint a fine-grained PAT (`junglecrunch1212/administrate-me-now` only, `Contents` RW + `Pull requests` RW + `Issues` RW) and rotate.

## §6 — Failure modes + troubleshooting

- **Tools don't appear after restart.** Check the Claude Desktop logs at `%APPDATA%\Claude\logs\mcp-server-github.log`. Common causes: bad JSON in config (validate with `python -m json.tool`), wrong binary path (check the path resolves), missing PAT (the env field is empty string).
- **All `mcp__github__*` calls return 401.** PAT is invalid, expired, or pasted with leading/trailing whitespace. Generate a fresh fine-grained PAT and update the config.
- **All `mcp__github__*` calls return 403.** PAT lacks required scopes. Add `repo` + `workflow` (classic PAT) or `Contents RW` + `Pull requests RW` + `Issues RW` (fine-grained).
- **Partner appears to have the tools but can't reach `junglecrunch1212/administrate-me-now`.** Fine-grained PAT was scoped to a different repo. Re-mint with this repo selected.
- **Tools appear but commit/push fails with "branch not found".** Use `mcp__github__create_branch` first with `from_branch=main`, then `mcp__github__create_or_update_file` on that branch.

## §7 — Time budget actual-vs-estimated

- **Estimated:** 30 minutes (research + decision + install).
- **Actual:** ~50 minutes (research took longer than expected because the Anthropic native connector is documented as available but has a known OAuth bug; needed to verify and route around).
- **Remaining (operator):** ~5 minutes to paste PAT, restart, and smoke-test.

## §8 — Followup candidates

- **PR-style integration of this handoff.** Currently this doc is a workspace-folder artifact, not on the repo. Once operator approves, push to a `sidecar-github-mcp-install` branch and open a PR — call it the first commit using the new MCP path as a self-test.
- **Update the Cowork project_instructions field.** PROJECT-INSTRUCTIONS.md was edited but operator still needs to copy-paste the updated content into the Cowork app's project Instructions field (a manual step that PROJECT-INSTRUCTIONS.md's own header calls out).
- **Migrate to method #2 (Anthropic native connector) when the OAuth bug ships a fix.** Track [anthropics/claude-code#52549](https://github.com/anthropics/claude-code/issues/52549). Migration is: remove `mcpServers.github` from claude_desktop_config.json, connect via Settings → Connectors → +. Tool names should stay the same; only the auth path differs.
