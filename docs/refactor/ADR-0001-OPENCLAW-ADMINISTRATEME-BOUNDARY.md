# ADR-0001: OpenClaw–AdministrateMe boundary

- Status: Accepted for implementation
- Date: 2026-07-29
- Depends on: ADR-0000

## Decision

Gate 3 evidence: `FixtureMessagingAdapter` supplies a credential-free Cat-A source and an in-memory HTTP-contract client supplies a pinned deterministic response at OpenClaw's existing `llm-task` boundary. Pipeline code still calls only `run_skill`; emitted `skill.call.recorded` facts report the actual `fixture/deterministic-v1` provider/model. The fixture is enabled only by explicit runtime settings and is not a second assistant, scheduler, policy engine, or provider integration.

OpenClaw is the generic assistant runtime. AdministrateMe is the typed household operational system. Integration is one native TypeScript plugin package and four explicit seams; HTTP/event schemas at the boundary are versioned and tested.

The v2 implementation target is pinned to stable OpenClaw `v2026.7.1`, release commit `2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4`, npm package `openclaw@2026.7.1`, registry integrity `sha512-ge/Xss99CHAjPL/ikmH/UFoiOrjcxDB4sW3y9mhyCD+dYW3wzV7TKbAVdkrXFgAG2d2BjpJofP97zUZ+umxo8g==`. The primary GitHub release was published 2026-07-13 and reviewed 2026-07-29. No later release is adopted without a compatibility ADR and the full OpenClaw contract gate.

| OpenClaw owns | AdministrateMe owns |
|---|---|
| Channel transports and delivery adapters | Typed communication facts and delivery outcome events |
| Conversations, generic transcripts, sessions, per-channel-peer scoping | Explicit domain-relevant conversation events admitted by policy |
| Generic conversational memory and workspace context | Owner-scoped `member_knowledge` and scope-filtered `vector_search` domain projections |
| Provider/model routing, retries, credentials, token accounting | Skill input/output schemas, sensitivity policy, provenance events, proposal semantics |
| Cron, background execution, multi-step agent orchestration | Domain recurrences, standing-order definitions, event-driven pipelines, durable outcomes |
| Generic tool approval, device nodes, runtime administration | Domain approval, authorization, owner/visibility scope, governance, observation, audit |

AdministrateMe will not build a generic chat store, assistant memory engine, scheduler, workflow runtime, model router, device gateway, application builder, or full OpenClaw console.

## The one plugin

The deliverable is one package (provisional id `administrateme`) containing narrowly scoped registrations, not one plugin per channel:

- `openclaw.plugin.json` with matching id, strict JSON Schema, static contracts, and no secrets.
- `package.json` with TypeScript source entry and built JavaScript runtime entry, `openclaw.compat.pluginApi: "2026.7.1"`, and exact `peerDependencies.openclaw: "2026.7.1"`; the macOS lab must still prove this pin before family deployment.
- A default TypeScript entry using the SDK helper appropriate to the selected pinned host. Registration must be safe under non-full discovery modes.
- Loopback client code with bounded timeouts, schema validation, correlation/causation, idempotency keys, and a least-privilege shared-secret reference. It never opens SQLCipher or receives the AdministrateMe master key.

Current official docs reviewed on 2026-07-29 describe `openclaw.plugin.json`, `package.json#openclaw.extensions/runtimeExtensions`, `definePluginEntry`/`defineChannelPluginEntry`, and `openclaw.compat.pluginApi`. These URLs are discovery evidence, not a floating dependency: https://docs.openclaw.ai/plugins/manifest and https://docs.openclaw.ai/plugins/sdk-entrypoints. The selected release evidence is https://github.com/openclaw/openclaw/releases/tag/v2026.7.1. Phase 2 must verify the recorded integrity/source SHA and macOS behavior before implementation. “Latest” is prohibited.

## Four preserved seams

1. **Typed skills.** AdministrateMe validates versioned input, scope, and sensitivity; invokes OpenClaw’s enabled `llm-task`; validates versioned output; records actual provider/model, invocation id, token/cost/duration when available, correlation and causation. OpenClaw alone chooses provider fallback. Deterministic decisions stay in code/config. LLM output can propose but cannot write a projection.
2. **Slash commands.** OpenClaw authenticates the channel peer and dispatches a versioned command request. AdministrateMe constructs `authMember` independently, applies domain authorization, and returns a typed response. Commands such as `/whatnow`, `/digest`, `/done`, and `/observation` remain domain operations, not generic OpenClaw commands.
3. **Proactive standing orders.** Version-controlled program definitions and cron declarations are installed idempotently into OpenClaw. A trigger calls a narrow AdministrateMe endpoint with a stable invocation/idempotency key. AdministrateMe never duplicates the schedule.
4. **Scoped channel/conversation bridge.** Only allowlisted, domain-relevant events cross: normalized inbound/outbound message facts, explicit conversation turns selected for domain processing, delivery/suppression/failure outcomes, and necessary peer/thread references. Generic transcript history, hidden reasoning, unrelated memory, credentials, and privileged content do not cross.

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
- OpenClaw sessions use per-channel-peer scope. AdministrateMe keeps `authMember` distinct from `viewMember` and authorizes against the former.
- Privileged material never enters LLM input, vector processing, cross-owner processing, or bridge payloads unless the owning member explicitly uses an audited privileged path allowed by policy. No broad plugin token may bypass this rule.
- Children receive server-side route denial and data filtering. Hidden navigation is not authorization.
- A narrow member portal is mandatory for members who must not receive OpenClaw operator/runtime access.

## Compatibility and failure behavior

- Startup fails closed if the configured OpenClaw version/API is outside the exact tested pin, the plugin manifest/config is invalid, the `llm-task` plugin is unavailable, or required schema negotiation fails.
- OpenClaw unavailable means deterministic/internal functions, event recording, projections, and local reads remain available. LLM/proactive/channel actions show a degraded state and do not silently fall back to a provider SDK.
- Requests and outcomes are idempotent under at-least-once delivery. Timeouts do not imply failure; reconciliation queries or outcome events determine final state.
- `Doctor` reports host version/pin, plugin discovery/activation, manifest validation, `llm-task` enablement and allowlist, loopback reachability, schema versions, standing-order drift, channel-bridge health, and last successful invocation without printing secrets.

## Rejected alternatives

- Multiple Python “OpenClaw plugins.”
- Direct Anthropic/OpenAI/Ollama clients or an AdministrateMe provider fallback loop.
- Mirroring all OpenClaw conversations into the event log.
- Reusing generic OpenClaw memory as household source of truth.
- Running APScheduler or a second cron for proactive product work.
- Treating OpenClaw operator login or approval as AdministrateMe authorization.

## Gate 2 implementation note

The central composition root now represents OpenClaw integration as explicit, capability-reported seams for typed skills, slash commands, standing orders, and the scoped conversation bridge. Required seams fail readiness when unavailable; optional integrations remain explicitly disabled instead of being advertised as live. AdministrateMe does not introduce a provider SDK, fallback model router, generic chat store, or scheduler.
