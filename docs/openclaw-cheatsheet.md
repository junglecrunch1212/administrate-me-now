# OpenClaw cheatsheet

_Produced by prompt 01a. Sourced from `docs/reference/openclaw/` mirror. Update if OpenClaw's docs change._

## Q1: How does an AdministrateMe skill pack get installed into OpenClaw?

Two paths. (a) Ship skills as a directory under a skill-loader location and let OpenClaw discover them at load time. Per `docs/reference/openclaw/tools/skills.md` Locations-and-precedence, order is: `<workspace>/skills` > `<workspace>/.agents/skills` > `~/.agents/skills` > `~/.openclaw/skills` > bundled > `skills.load.extraDirs`. (b) Publish to ClawHub and install with `openclaw skills install <slug>` (per `tools/skills.md` ClawHub section + `tools/clawhub.md`) — lands in the active workspace `skills/`. Plugins may also ship skills via `openclaw.plugin.json` `skills` dirs. Config toggles live under `skills.entries.<name>.{enabled,env,apiKey,config}`. A gateway restart is not required unless the skills watcher is off.

## Q2: How does a slash command handler get registered?

Three registration surfaces (per `docs/reference/openclaw/tools/slash-commands.md` + `tools/plugin.md`). (a) Core built-ins ship in `src/auto-reply/commands-registry.shared.ts`. (b) Plugins call `api.registerCommand()` / `api.registerCli()` inside their `register(api)` entry (`tools/plugin.md` Plugin-API-overview). (c) Skills with frontmatter `user-invocable: true` auto-expose as `/<name>`; set `command-dispatch: tool` + `command-tool: <name>` to bypass the model and dispatch directly (`tools/skills.md` Format). Text `/` parsing is gated by `commands.text`; native-channel registration by `commands.native` / `commands.nativeSkills` (per-provider overrides under `channels.<provider>.commands`). Authorization is `commands.allowFrom` or channel allowlists + `commands.useAccessGroups`.

## Q3: How does a standing order get registered?

Not a registration API — standing orders are **workspace prose**, not code (per `docs/reference/openclaw/automation/standing-orders.md`). Write each program (Scope / Triggers / Approval gate / Escalation / Execution steps / What-NOT-to-do) inside `AGENTS.md`, which is auto-injected every session by the workspace bootstrap (`concepts/agent-workspace.md` — also auto-injects `SOUL.md`, `TOOLS.md`, `IDENTITY.md`, `USER.md`, `HEARTBEAT.md`, `BOOT.md`, `MEMORY.md`). Pair each program with a cron job (`automation/cron-jobs.md`; `openclaw cron add --cron ... --message "Execute <program> per standing orders"`) for the **when**. Cron defs persist at `~/.openclaw/cron/jobs.json`.

## Q4: How does a plugin get registered?

Install with `openclaw plugins install <spec>` (local path/archive, npm pkg, or `clawhub:<pkg>`) — per `docs/reference/openclaw/tools/plugin.md` Quick-start. Then enable with `openclaw plugins enable <id>` (or `plugins.entries.<id>.enabled: true`) and restart the gateway (`openclaw gateway restart`). Discovery precedence (first match wins): `plugins.load.paths` > `<workspace>/.openclaw/<plugin-root>/*` > `~/.openclaw/<plugin-root>/*` > bundled. Workspace-origin plugins are disabled by default. Native plugins export `definePluginEntry({ id, name, register(api) { api.registerTool/Provider/Channel/Hook/Command(...) } })`. `plugins.deny` wins over `plugins.allow`. Slot categories (`memory`, `contextEngine`) are exclusive.

## Q5: What is the exact shape of a SKILL.md file that OpenClaw accepts?

Per `docs/reference/openclaw/tools/skills.md` Format. Minimum:

```markdown
---
name: <slug>
description: <one-line summary>
---
<body; use {baseDir} for the skill folder path>
```

Parser accepts **single-line frontmatter keys only**. Optional keys: `homepage`, `user-invocable` (default `true`), `disable-model-invocation` (default `false`), `command-dispatch: tool`, `command-tool: <name>`, `command-arg-mode: raw`. `metadata` MUST be a single-line JSON object; under `metadata.openclaw`: `always`, `emoji`, `homepage`, `os` (`darwin|linux|win32`), `requires.{bins,anyBins,env,config}`, `primaryEnv`, `install[]` (brew/node/uv/go/download), `skillKey`. No `metadata.openclaw` means always eligible (unless disabled in config).

## Q6: What is `dmScope: per-channel-peer` and when does it apply vs. `shared`?

Per `docs/reference/openclaw/concepts/session.md` DM-isolation. `session.dmScope` values:

- `main` (default) — all DMs share one session (fine for single-user setups).
- `per-peer` — isolate by sender across channels.
- `per-channel-peer` — isolate by channel + sender (**recommended** when multiple people can DM the agent; prevents Alice's private context leaking to Bob).
- `per-account-channel-peer` — isolate by account + channel + sender (multi-account channels).

(OpenClaw docs do not use the literal term `shared`; in practice "shared" ≈ `main`. To link identities across channels intentionally, configure `session.identityLinks`.) Verify with `openclaw security audit`.

## Q7: How does the gateway's approval-gates system interact with guardedWrite?

Per `docs/reference/openclaw/tools/exec-approvals.md`: OpenClaw's exec approvals run **locally on the execution host** (gateway or node) after tool policy and elevated gating, before the command actually executes. Flow: agent emits an exec tool call → policy layer (`tools.exec.*`) checks `security` (`deny|allowlist|full`) and `ask` (`off|on-miss|always`) → gateway broadcasts `exec.approval.requested` to operator clients (Control UI, macOS app, forwarded chat channels via `approvals.exec.targets` or `/approve`) → operator resolves via `exec.approval.resolve` → gateway forwards the canonical `systemRunPlan` (argv/cwd/env, bound concrete file for interpreter/runtime forms) to the host. Effective policy is the **stricter** of requested + host-local (`~/.openclaw/exec-approvals.json`). `guardedWrite` is an AdministrateMe-side event-log gate (separate layer, above OpenClaw) and will be documented in prompt 01b's architecture summary.

## Q8: Where does OpenClaw store its state on disk, and what needs backup?

Per `docs/reference/openclaw/cli/backup.md`, `concepts/agent-workspace.md`, `concepts/session.md`, `automation/cron-jobs.md`. Everything under `~/.openclaw/`:

- `~/.openclaw/openclaw.json` — active config **(backup)**
- `~/.openclaw/agents/<agentId>/agent/auth-profiles.json` — model OAuth + API keys **(backup)**
- `~/.openclaw/credentials/` — channel/provider state **(backup)**
- `~/.openclaw/agents/<agentId>/sessions/{sessions.json, <sessionId>.jsonl}` — session store + transcripts **(backup if continuity matters)**
- `~/.openclaw/skills/` — managed/local skill overrides **(backup)**
- `~/.openclaw/exec-approvals.json` — host approvals + allowlists **(backup)**
- `~/.openclaw/cron/jobs.json` — cron definitions **(backup; track in git)**
- `~/.openclaw/cron/jobs-state.json` — runtime **(gitignore; derived)**
- `~/.openclaw/workspace/` — AGENTS/SOUL/TOOLS/IDENTITY/USER/HEARTBEAT/memory/ **(backup as private git repo)**
- `~/.openclaw/sandboxes/` — sandbox workspaces **(derived; rebuildable)**

`openclaw backup create` bundles state + config + external credentials + workspaces into a timestamped `.tar.gz`. `--only-config` or `--no-include-workspace` trim scope. AdministrateMe's `~/.adminme/` event log is separate from OpenClaw state; per the build spec, items sourced from `~/.adminme/` (e.g. derived projections, replayable materializations) can be rebuilt and do not need independent backup of the OpenClaw copy.

## Sources

- `docs/reference/openclaw/tools/skills.md` — skill locations/precedence, SKILL.md format, gating metadata, ClawHub install (Q1, Q2, Q5)
- `docs/reference/openclaw/tools/slash-commands.md` — command parsing, native vs text, registry sources, skill-as-command dispatch (Q2)
- `docs/reference/openclaw/tools/plugin.md` — install/discovery/enablement, `definePluginEntry` / `register(api)` surface (Q4)
- `docs/reference/openclaw/automation/standing-orders.md` — workspace-prose model + cron pairing (Q3)
- `docs/reference/openclaw/concepts/agent-workspace.md` — workspace layout + auto-injected bootstrap files (Q3, Q8)
- `docs/reference/openclaw/concepts/session.md` — `dmScope` values + session storage paths (Q6, Q8)
- `docs/reference/openclaw/tools/exec-approvals.md` — approval flow, storage, policy knobs (Q7)
- `docs/reference/openclaw/cli/backup.md` — what `openclaw backup create` covers (Q8)
