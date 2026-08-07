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
| Typed tools | Manifest `contracts.tools`; runtime factory context; optional tool metadata | Nine `administrateme_*` tools; seven side-effect/standing-order tools are optional | Manifest/registration and request/response tests |
| Approval | `before_tool_call.requireApproval`; plugin approval routing; optional exposure | Side effects request `allow-once` or deny; AdministrateMe role/scope/domain approval remains independent | Approval hook fixture plus process retry proof |
| Dashboard | Manifest data/action binding metadata; authenticated Gateway client; `operator.read`/`operator.write` registration | All ten domain reads plus health; review/lifecycle writes; calls without `authenticatedUserId` denied | Dashboard fixture and child/operator process tests |
| Actor provenance | Tool requester, agent/session, channel/account/delivery/thread; command sender; message hook context | Full metadata audit plus exact `channel|account|actor` mapping. Gate 7 additionally derives portal actor/channel/account only from a valid HMAC-signed isolated session; Python independently recomputes it. Operator scope and `senderIsOwner` remain insufficient. | Fixture and process denial tests |
| Skills/model routing | OpenClaw skills workspace and one `llm-task` tool invocation | OpenClaw-format skill instructs the typed wrapper; wrapper makes one routed call and records actual `skill.call.recorded` provenance | Python wrapper unit and Gate 4 process tests |
| Slash commands | Native command definition, required auth, command context | Seven commands dispatch only to typed loopback endpoints | TypeScript fixture test |
| Standing orders | OpenClaw standing orders/automations plus agent workspace instructions | Five derived automation declarations call one typed trigger; no Python scheduler | Contract validation and process event proof |
| Conversation hooks | Typed `message_received`; sanitized `model_call_started/ended` | Explicit consent/channel/event allowlist; no generic mirroring; metadata-only tracked model calls; no `llm_input/output` | Allowlist and privileged exclusion tests |
| Transport/failure | Local plugin config schema and Gateway lifecycle | HTTP loopback only, secret environment reference, bounded timeout, durable degraded/incompatible state | Transport unit and outage/restart process tests |
| Member chat | Gateway protocol v4 `connect.challenge`/`connect`, `chat.send`, `chat.history`, `chat.abort`, `chat` status/delta/final/aborted/error events | Loopback-only bounded WebSocket relay pinned to version `2026.7.2`; per-member signed session; rendered text only; browser cannot select provider/model or receive service/provider credentials | Python contract/acceptance tests and rendered chat/degraded Edge evidence |

The native package version is `2.0.0-gate7`. It registers ten dashboard read
bindings (ten domain views plus health), two dashboard write bindings, nine
typed tools, seven slash commands, and the existing five standing orders. No
generic portal or member operator method was added.

## Evidence level

- **Official-contract fixture: PASS on macOS with Node 24.19.0.** The narrow TypeScript
  declaration fixture is copied from only the SDK surfaces used at the pinned
  commit. It validates compile-time and runtime registration contracts but is
  not a live Gateway.
- **AdministrateMe process boundary: PASS.** FastAPI acceptance boots the real
  encrypted runtime, exercises the loopback routes, event log, projections,
  scope filters, restart hydration, and incompatible-host rejection.
- **Live pinned OpenClaw on the candidate baseline: NOT RUN on the family Mac.**
  The candidate requires Node `>=24.15.0 <25`; the plugin contract, build, and
  package gates pass with Node `24.19.0`. The active family release remains on
  Node 22 until the separately approved ADR-0006 migration. The guarded
  `npm run test:live:mac` requires a disposable macOS lab, the exact source
  checkout, explicit `ADMINME_LIVE_GATE=1`, then performs link/install,
  runtime inspect, Gateway restart, and deep RPC health. It must pass in a
  disposable macOS lab before the candidate can replace the active release.

## Gate 5 host and installer matrix

| Component | Supported/pinned Gate 5 contract | Enforcement |
|---|---|---|
| macOS | majors 14, 15, 26; 26 preferred for a new Mac mini | mutation-free shell/Python preflight |
| CPU | native Apple silicon `arm64` only | Rosetta/x86_64 fails before instance mutation |
| Python | `>=3.14,<3.15`, resolved from `poetry.lock` | release-specific venv and Poetry `2.4.1` |
| Node/npm | `>=24.15,<25`; npm `>=11.6,<12` | installer and plugin contract checks |
| SQLCipher | runtime `>=4.12,<5`; cross-platform `sqlcipher3==0.6.2` | Exact Poetry lock plus Doctor cipher/migration checks |
| OpenClaw/plugin | exact existing version, commit, API, gateway, and plugin package pins | upgrade validation occurs before service replacement |
| Bun | not required | declared false in `bootstrap/versions.json` |

The stable OpenClaw pin was not silently moved during Gate 5. User launchd
definitions invoke secret-safe Python launchers; the plugin never receives the
event-log key. Credential-free healthy observation mode is a fake-provider
claim only. See `MAC-MINI-OPERATIONS.md` and
`evidence/gate-5-mac-mini-operations.md`.

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
