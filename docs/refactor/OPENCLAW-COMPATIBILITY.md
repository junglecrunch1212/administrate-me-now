# OpenClaw compatibility matrix

Gate 4 selects OpenClaw package/source version `2026.7.2` at exact commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`. The selection was made from
official docs and current source on 2026-07-30 because Prompt 4 requires the
current native dashboard binding and authenticated actor contracts. It is not
a floating dependency.

| Surface | Pinned contract used | AdministrateMe implementation | Evidence |
|---|---|---|---|
| Manifest/package | `openclaw.plugin.json`; `openclaw.extensions` and `runtimeExtensions`; `compat.pluginApi`; `build.openclawVersion/pluginSdkVersion` | Strict manifest, exact peer/API/gateway/build version and source commit | `npm run contract`, `npm run build`, `npm pack --dry-run` |
| Runtime lifecycle | `definePluginEntry`; full/setup/snapshot registration modes; service start/stop | Side-effect-free non-full discovery; full mode registers one health service | TypeScript fixture test |
| Typed tools | Manifest `contracts.tools`; runtime factory context; optional tool metadata | Eight `administrateme_*` tools; six side-effect tools are optional | Manifest/registration and request/response tests |
| Approval | `before_tool_call.requireApproval`; plugin approval routing; optional exposure | Side effects request `allow-once` or deny; AdministrateMe role/scope/domain approval remains independent | Approval hook fixture plus process retry proof |
| Dashboard | Manifest data/action binding metadata; authenticated Gateway client; `operator.read`/`operator.write` registration | Today, inbox, health reads; review/lifecycle writes; calls without `authenticatedUserId` denied | Dashboard fixture and child/operator process tests |
| Actor provenance | Tool requester, agent/session, channel/account/delivery/thread; command sender; message hook context | Full metadata audit plus exact `channel|account|actor` member mapping; operator scope and `senderIsOwner` ignored | Fixture and process denial tests |
| Skills/model routing | OpenClaw skills workspace and one `llm-task` tool invocation | OpenClaw-format skill instructs the typed wrapper; wrapper makes one routed call and records actual `skill.call.recorded` provenance | Python wrapper unit and Gate 4 process tests |
| Slash commands | Native command definition, required auth, command context | Seven commands dispatch only to typed loopback endpoints | TypeScript fixture test |
| Standing orders | OpenClaw standing orders/automations plus agent workspace instructions | Five derived automation declarations call one typed trigger; no Python scheduler | Contract validation and process event proof |
| Conversation hooks | Typed `message_received`; sanitized `model_call_started/ended` | Explicit consent/channel/event allowlist; no generic mirroring; metadata-only tracked model calls; no `llm_input/output` | Allowlist and privileged exclusion tests |
| Transport/failure | Local plugin config schema and Gateway lifecycle | HTTP loopback only, secret environment reference, bounded timeout, durable degraded/incompatible state | Transport unit and outage/restart process tests |

## Evidence level

- **Official-contract fixture: PASS on Windows.** The narrow TypeScript
  declaration fixture is copied from only the SDK surfaces used at the pinned
  commit. It validates compile-time and runtime registration contracts but is
  not a live Gateway.
- **AdministrateMe process boundary: PASS.** FastAPI acceptance boots the real
  encrypted runtime, exercises the loopback routes, event log, projections,
  scope filters, restart hydration, and incompatible-host rejection.
- **Live pinned OpenClaw: NOT RUN on this host.** The pinned source requires
  Node `>=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0`; this host has Node `24.14.1` and is Windows. The guarded
  `npm run test:live:mac` requires a disposable macOS lab, the exact source
  checkout, explicit `ADMINME_LIVE_GATE=1`, then performs link/install,
  runtime inspect, Gateway restart, and deep RPC health. It must pass before
  the family Mac is touched.

## Authoritative references

- https://docs.openclaw.ai/plugins/manifest
- https://docs.openclaw.ai/plugins/building-plugins
- https://docs.openclaw.ai/web/dashboard-architecture
- https://docs.openclaw.ai/gateway/tools-invoke-http-api
- https://docs.openclaw.ai/plugins/plugin-permission-requests
- https://docs.openclaw.ai/tools/skills
- https://docs.openclaw.ai/tools/slash-commands
- https://docs.openclaw.ai/automation/standing-orders
- https://docs.openclaw.ai/plugins/hooks
- https://docs.openclaw.ai/concepts/agent-workspace
- https://github.com/openclaw/openclaw/commit/944cf08f94c334cff2b0fdf7e01187a9efa42715
