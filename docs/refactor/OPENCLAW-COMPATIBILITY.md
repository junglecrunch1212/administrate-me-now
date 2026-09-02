# OpenClaw compatibility matrix

OpenClaw package/source version `2026.7.2` is pinned at exact commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`. This same-version reviewed
snapshot supersedes `944cf08f94c334cff2b0fdf7e01187a9efa42715`, whose
bundled `llm-task` failed against its SQLite session runtime before provider
execution. The replacement includes OpenClaw's official isolated, tool-free
completion surface and remains an exact snapshot, not a floating dependency.

Phase-B language-seam amendment (2026-08-08): the pinned host includes
`@openclaw/llm-task` as a bundled optional plugin that is disabled by default.
AdministrateMe bootstrap and upgrade explicitly enable that exact bundled tool,
preserve unrelated tool-policy entries while adding `llm-task`, validate with
OpenClaw's own writer, and require Doctor runtime-registration plus authenticated
synthetic-execution evidence. The
central Python composition injects the Keychain-resolved Gateway bearer token
into the skill runtime; it never depends on an ambient token for an explicitly
constructed production dependency bundle.

| Surface | Pinned contract used | AdministrateMe implementation | Evidence |
|---|---|---|---|
| Manifest/package | `openclaw.plugin.json`; `openclaw.extensions` and `runtimeExtensions`; `compat.pluginApi`; `build.openclawVersion/pluginSdkVersion` | Strict manifest, exact peer/API/gateway/build version and source commit | `npm run contract`, `npm run build`, `npm pack --dry-run` |
| Runtime lifecycle | `definePluginEntry`; full/setup/snapshot registration modes; service start/stop | Side-effect-free non-full discovery; full mode registers one health service | TypeScript fixture test |
| Typed tools | Manifest `contracts.tools`; runtime factory context; optional tool metadata | Nineteen `administrateme_*` tools. Gate 8 adds catalog/status, minimum Settings context, inert capability request/cancel, and source-policy proposal inspect/decide alongside the existing authorized work, coordination, and delegated-action tools; static declaration is distinct from effective member eligibility | Manifest/registration and request/response tests |
| Approval | `before_tool_call.requireApproval`; plugin approval routing; optional exposure | Side effects request `allow-once` or deny; the plugin resolves a signed, exact-body, expiring receipt and AdministrateMe consumes it independently after domain approval | Approval hook, forgery/change/expiry/replay tests, and process retry proof |
| Dashboard | Manifest data/action binding metadata; authenticated Gateway client; `operator.read`/`operator.write` registration | All ten domain reads plus health; review/lifecycle writes; calls without `authenticatedUserId` denied | Dashboard fixture and child/operator process tests |
| Actor provenance | Tool requester, agent/session, channel/account/delivery/thread; command sender; message hook context | Full metadata audit plus exact `channel|account|actor` mapping. Gate 7 additionally derives portal actor/channel/account only from a valid HMAC-signed isolated session; Python independently recomputes it. Operator scope and `senderIsOwner` remain insufficient. | Fixture and process denial tests |
| Skills/model routing | OpenClaw skills workspace and one bundled, explicitly enabled/allowed `llm-task` tool invocation through isolated tool-free completion | OpenClaw-format skill instructs the typed wrapper; the composition injects the Keychain-resolved bearer token; wrapper uses a canonical member-isolated Gateway session, makes one routed call, and records actual `skill.call.recorded` provenance; Doctor rejects registered-but-unexecutable tool state | Python wrapper and production-composition tests, bootstrap required-tool/Doctor tests, authenticated Phase-B probe |
| Slash commands | Native command definition, required auth, command context | Seven commands dispatch only to typed loopback endpoints | TypeScript fixture test |
| Standing orders | OpenClaw standing orders/automations plus agent workspace instructions | Five derived automation declarations call one typed trigger; no Python scheduler | Contract validation and process event proof |
| Conversation hooks | Typed `message_received`; sanitized `model_call_started/ended` | Explicit consent/channel/event allowlist; no generic mirroring; metadata-only tracked model calls; no `llm_input/output` | Allowlist and privileged exclusion tests |
| Transport/failure | Local plugin config schema and Gateway lifecycle | HTTP loopback only, secret environment reference, bounded timeout, durable degraded/incompatible state | Transport unit and outage/restart process tests |
| Member chat | Gateway protocol v4 `connect.challenge`/`connect`, `chat.send`, `chat.history`, `chat.abort`, `chat` status/delta/final/aborted/error events | Loopback-only bounded WebSocket relay pinned to version `2026.7.2`; per-member signed session; rendered text only; least-privileged sends omit admin-only `systemInputProvenance`, `systemProvenanceReceipt`, and `suppressCommandInterpretation`; browser cannot select provider/model or receive service/provider credentials | Exact-commit source audit, Python protocol contract/acceptance tests, and rendered chat/degraded evidence |

The native package version is `2.0.0-gate8`. It registers ten dashboard read
bindings (ten domain views plus health), two dashboard write bindings, nineteen
typed tools, eight slash commands, and the existing five standing orders. No
generic portal or member operator method was added.

Gate 8 source-proof amendment (2026-09-02): compatibility is established only
from the exact `2026.7.2` source at
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`, its manifest, the exact SDK
declarations used by this package, and the checked contract/runtime tests.
Older bundled documentation or same-version prose is not compatibility proof.
Capability and policy mutations receive the native single-use approval hook;
catalog/context reads and policy inspection do not. Python independently checks
member mapping, exact grant, version/commit, and receipt where applicable.

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
- https://github.com/openclaw/openclaw/commit/ce67ffb70e83e5ebb56ea02e814e577153f84f61
