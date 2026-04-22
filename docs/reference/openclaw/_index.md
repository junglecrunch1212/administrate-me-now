# OpenClaw documentation

**Purpose in this build:** OpenClaw is the assistant substrate AdministrateMe builds on top of. Skills, plugins, standing orders, slash commands, channels, the agent loop, and SOUL.md all live in OpenClaw. This mirror is the authoritative reference for every OpenClaw concept the prompts reference.

**Source:** https://github.com/openclaw/openclaw (commit pinned at fetch time)
**Upstream docs path:** `docs/` in that repo
**Fetched:** 2026-04-22
**License:** MIT (openclaw/openclaw/LICENSE, Copyright (c) 2025 Peter Steinberger)

## Recommended reading order for Phase A

Per `ADMINISTRATEME_BUILD.md` "BEFORE YOU START: LEARN OPENCLAW" section, read in this order:

1. `index.md` — Overview
2. `start/openclaw.md` — Personal assistant setup (the mental model)
3. `install/` — Installation (Phase B bootstrap)
4. `concepts/architecture.md` — Gateway architecture
5. `concepts/agent-workspace.md` — Workspace layout (`~/Chief`)
6. `concepts/soul.md` — SOUL.md personality guide
7. `concepts/agent-loop.md` — Agent turn lifecycle
8. `concepts/memory.md` — Memory system (boundary with AdministrateMe event log)
9. `concepts/session.md` — Sessions (`dmScope: per-channel-peer`)
10. `concepts/multi-agent.md` — Multi-agent routing
11. `tools/index.md`, `tools/skills.md`, `tools/creating-skills.md` — Skills
12. `tools/slash-commands.md`, `tools/plugin.md` — Commands and plugins
13. `tools/exec.md`, `tools/exec-approvals.md`, `tools/elevated.md` — Exec/approvals
14. `gateway/security/`, `gateway/sandboxing.md`, `gateway/configuration.md`, `gateway/heartbeat.md`, `gateway/protocol.md`
15. `automation/cron-jobs.md`, `automation/hooks.md`, `automation/standing-orders.md`
16. `tools/subagents.md`
17. `channels/bluebubbles.md`, `channels/telegram.md`, `channels/discord.md`, `channels/pairing.md`
18. `nodes/`, `platforms/macos.md`
19. `tools/clawhub.md`, `cli/`

## Mirror scope

- Every `.md`, `.mdx`, and `.json` file under `docs/` preserved with source provenance headers.
- Image assets and `.i18n/` / `.generated/` subtrees omitted (not needed for build reasoning).
- Subdirectory structure preserved; paths match upstream.

## Known gaps

If any OpenClaw doc referenced by a later prompt is not in this mirror, check the upstream repo for a recent addition and note it in `../_gaps.md`. The mirror is pinned at fetch time and does not auto-refresh.
