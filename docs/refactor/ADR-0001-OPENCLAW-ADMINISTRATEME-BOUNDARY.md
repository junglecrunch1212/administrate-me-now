# ADR-0001: OpenClaw–AdministrateMe boundary

- Status: Accepted and implemented at Gate 4
- Date: 2026-07-30
- Depends on: ADR-0000

## Decision

Gate 3 evidence: `FixtureMessagingAdapter` supplies a credential-free Cat-A source and an in-memory HTTP-contract client supplies a pinned deterministic response at OpenClaw's existing `llm-task` boundary. Pipeline code still calls only `run_skill`; emitted `skill.call.recorded` facts report the actual `fixture/deterministic-v1` provider/model. The fixture is enabled only by explicit runtime settings and is not a second assistant, scheduler, policy engine, or provider integration.

OpenClaw is the generic assistant runtime. AdministrateMe is the typed household operational system. Integration is one native TypeScript plugin package and four explicit seams; HTTP/event schemas at the boundary are versioned and tested.

Gate 4 initially selected OpenClaw source package version `2026.7.2` at commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`. Live Phase-B execution proved
that snapshot's bundled `llm-task` still launched the retired file-backed
embedded session path without an agent identity; the SQLite session runtime
therefore failed every real invocation before model execution. On 2026-08-08
the exact same-version snapshot moved to official commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`, whose reviewed upstream change
routes `llm-task` through OpenClaw's isolated, tool-free `runtime.llm.complete`
surface. This is a required compatibility repair, not a floating `main`
dependency or an AdministrateMe provider bypass. No later release or commit is
adopted without this ADR being revised and the complete fixture plus disposable
macOS live contract gate passing.

| OpenClaw owns | AdministrateMe owns |
|---|---|
| Channel transports and delivery adapters | Typed communication facts and delivery outcome events |
| Conversations, generic transcripts, one isolated member agent, account-aware peer sessions | Explicit typed domain proposals admitted by policy; member-bound human-to-human provider facts remain Cat-A adapter inputs |
| Generic conversational memory and workspace context | Owner-scoped `member_knowledge` and scope-filtered `vector_search` domain projections |
| Provider/model routing, retries, credentials, token accounting | Skill input/output schemas, sensitivity policy, provenance events, proposal semantics |
| Cron, background execution, multi-step agent orchestration | Domain recurrences, standing-order definitions, event-driven pipelines, durable outcomes |
| Generic tool approval, device nodes, runtime administration | Domain approval, authorization, owner/visibility scope, governance, observation, audit |

AdministrateMe will not build a generic chat store, assistant memory engine, scheduler, workflow runtime, model router, device gateway, application builder, or full OpenClaw console.

## The one plugin

The deliverable is one package (provisional id `administrateme`) containing narrowly scoped registrations, not one plugin per channel:

- `openclaw.plugin.json` with matching id, strict JSON Schema, static contracts, and no secrets.
- `package.json` with TypeScript source entry and built JavaScript runtime entry, `openclaw.compat.pluginApi: "2026.7.2"`, exact `peerDependencies.openclaw: "2026.7.2"`, build metadata containing the selected commit, and the reviewed AdministrateMe Node 24 engine `>=24.15.0 <25`; the macOS lab must still prove this pin before family deployment.
- A default TypeScript entry using the SDK helper appropriate to the selected pinned host. Registration must be safe under non-full discovery modes.
- Loopback client code with bounded timeouts, schema validation, correlation/causation, idempotency keys, and a least-privilege shared-secret reference. It never opens SQLCipher or receives the AdministrateMe master key.

Current official docs and source reviewed through 2026-08-08 define `openclaw.plugin.json`, `package.json#openclaw.extensions/runtimeExtensions`, `definePluginEntry`, manifest-declared tools, authenticated dashboard bindings, plugin permission requests, skills, slash commands, automations/standing orders, typed hooks, and isolated tool-free completion. The exact compatibility inventory and fixture/live distinction are in `OPENCLAW-COMPATIBILITY.md`. Authoritative discovery evidence is https://docs.openclaw.ai/plugins/manifest, https://docs.openclaw.ai/plugins/building-plugins, https://docs.openclaw.ai/web/dashboard-architecture, and https://github.com/openclaw/openclaw/commit/ce67ffb70e83e5ebb56ea02e814e577153f84f61. “Latest” remains prohibited.

## Four preserved seams

1. **Typed skills.** AdministrateMe validates versioned input, scope, and sensitivity; invokes OpenClaw’s enabled `llm-task`; validates versioned output; records actual provider/model, invocation id, token/cost/duration when available, correlation and causation. OpenClaw alone chooses provider fallback. Deterministic decisions stay in code/config. LLM output can propose but cannot write a projection.
2. **Slash commands.** OpenClaw authenticates the channel peer and dispatches a versioned command request. AdministrateMe constructs `authMember` independently, applies domain authorization, and returns a typed response. Commands such as `/whatnow`, `/digest`, `/done`, and `/observation` remain domain operations, not generic OpenClaw commands.
3. **Proactive standing orders.** Version-controlled program definitions and cron declarations are installed idempotently into OpenClaw. A trigger calls a narrow AdministrateMe endpoint with a stable invocation/idempotency key. AdministrateMe never duplicates the schedule.
4. **Scoped channel/conversation bridge.** Only a configured channel plus an explicit consent metadata marker may send one of `commitment_candidate`, `household_action`, or `delivery_outcome` with actor/session/channel/message correlation. No ordinary conversation turn is mirrored. Generic transcript history, hidden reasoning, unrelated memory, credentials, and privileged content do not cross.

## LLM and state-change sequence

```text
domain trigger -> schema/scope check -> OpenClaw llm-task -> output validation
-> skill.call.recorded -> domain proposal event -> domain review/approval
-> guardedWrite -> final content/privacy guard -> observation gate
-> OpenClaw delivery/tool action -> typed outcome event -> projections -> what-now
```

Both approval systems must pass when both apply. OpenClaw approval never grants household authority. AdministrateMe approval never grants a generic OpenClaw tool permission. Autonomous send-as-principal is hard-refused; the assistant may send as its configured identity, and draft-for-principal remains held for review.

## Privacy contract

- Resolve AdministrateMe membership from trusted identity; do not equate OpenClaw operator access with household membership.
- External member DMs use one isolated OpenClaw agent per member and
  `per-account-channel-peer` scope. AdministrateMe independently keeps
  `authMember` distinct from `viewMember` and authorizes against the former.
- Privileged material never enters LLM input, vector processing, cross-owner processing, or bridge payloads unless the owning member explicitly uses an audited privileged path allowed by policy. No broad plugin token may bypass this rule.
- Children receive server-side route denial and data filtering. Hidden navigation is not authorization.
- A narrow member portal is mandatory for members who must not receive OpenClaw operator/runtime access.

## Threat and transport boundary

- OpenClaw calls only an HTTP listener on loopback under `/api/openclaw/v1/`; the plugin rejects non-loopback configuration and the server rejects non-loopback peers.
- A random shared bearer authenticates the installed plugin to the local process. It is injected by environment reference, never placed in a manifest, event, response, log, approval text, or test artifact, and never grants household authority.
- AdministrateMe maps the exact trusted `channel_id|account_id|actor_id` tuple to a configured member. `senderIsOwner`, Gateway operator scopes, a paired client id, and `/tools/invoke` bearer possession are deliberately not authorization inputs. Dashboard calls without `authenticatedUserId` fail before transport.
- All request bodies forbid unknown fields. Side-effect retries carry durable member-qualified idempotency keys and body fingerprints. OpenClaw `allow-once` permission is an extra runtime gate; domain role/scope, GuardedWrite, optimistic event id, approval, privacy, and observation are evaluated inside AdministrateMe.
- The bridge is opt-in and allowlisted; model hooks subscribe only to sanitized `model_call_started/ended` metadata for a tracked `adminme:` session. The plugin never registers `llm_input` or `llm_output`.

## Compatibility and failure behavior

- The integration capability fails closed if the configured OpenClaw version/commit is outside the exact tested pin, the plugin manifest/config is invalid, the `llm-task` tool is unavailable, or required schema negotiation fails. The AdministrateMe process still opens its durable log and safe domain reads.
- OpenClaw unavailable means deterministic/internal functions, event recording, projections, and local reads remain available. LLM/proactive/channel actions show a degraded state and do not silently fall back to a provider SDK.
- Requests and outcomes are idempotent under at-least-once delivery. Timeouts do not imply failure; reconciliation queries or outcome events determine final state.
- `Doctor` reports host version/pin, plugin discovery/activation, manifest validation, `llm-task` enablement and allowlist, a bounded authenticated synthetic JSON execution, loopback reachability, schema versions, standing-order drift, channel-bridge health, and last successful invocation without printing secrets.

## Rejected alternatives

- Multiple Python “OpenClaw plugins.”
- Direct Anthropic/OpenAI/Ollama clients or an AdministrateMe provider fallback loop.
- Mirroring all OpenClaw conversations into the event log.
- Reusing generic OpenClaw memory as household source of truth.
- Running APScheduler or a second cron for proactive product work.
- Treating OpenClaw operator login or approval as AdministrateMe authorization.

## Gate 4 implementation note

`openclaw/administrateme/` is the single native package. It registers eight typed tools, seven authenticated slash commands, three dashboard reads, two dashboard actions, five derived automation declarations, one compiled per-instance SOUL workspace, explicit bridge/model hooks, and one health service. The Python composition root exposes only the authenticated loopback handlers and keeps durable integration state. The official-contract fixture passes on Windows; `npm run test:live:mac` is a deliberately guarded live install/inspect/restart/RPC gate and remains required before deployment.
