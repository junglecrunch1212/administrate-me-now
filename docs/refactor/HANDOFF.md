---
schema_version: 1
current_phase: 4
phase_name: current_openclaw_seam
gate: 4
gate_result: PASS
verified_local_date: 2026-07-30
branch: codex/administrateme-v2-runtime
branch_tip_resolution: git rev-parse HEAD
gate_1_artifact_sha: c4d682c42ff9c9833e59589422bac90eec590173
gate_1_artifact_tree_sha: 5bc5e3733c456cbffbf12dcd51ba0b1744889ab6
draft_pr_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
openclaw_evidence: official_contract_fixture_and_real_adminme_process
next_phase: 5
next_phase_name: member_bridge_private_searchable_knowledge
---

# Handoff

## State

Gate 4 passes at the explicitly declared evidence level. The branch retains
Gates 1–3 and now contains one current native TypeScript plugin at
`openclaw/administrateme/`. The plugin is pinned to OpenClaw source package
version `2026.7.2` and commit
`944cf08f94c334cff2b0fdf7e01187a9efa42715`. The exact SDK surfaces compile
and run against an official-contract fixture; the real encrypted
AdministrateMe composition passes the loopback process gate. A live OpenClaw
Gateway was not run on this Windows host and is not claimed.

## Gate 4 implementation

- The native manifest and package declare eight typed household tools, strict
  configuration, exact peer/API/gateway/build pins, optional side-effect
  exposure, three dashboard reads, two dashboard actions, seven slash command
  aliases, the skill directory, and source/built runtime entries.
- Full trusted provenance crosses the seam: actor, mapped member, agent,
  session key/id, channel, account, message, thread, correlation, call kind,
  OpenClaw version, and source commit. AdministrateMe maps the exact
  `channel|account|actor` tuple; operator scope, `/tools/invoke` bearer
  possession, and `senderIsOwner` grant no household authority.
- Sensitive tools require OpenClaw `allow-once` approval in addition to the
  AdministrateMe role/scope, GuardedWrite, optimistic event-id, domain
  approval, privacy, idempotency, and observation checks.
- The four seams are implemented: an OpenClaw-format skill invokes the one
  schema-valid `llm-task` wrapper; native commands dispatch typed handlers;
  five derived OpenClaw automations invoke a typed standing-order trigger; and
  the bridge accepts only explicit consent-marked, configured, domain events.
  Ordinary conversation and privileged content are not mirrored.
- Model observation is metadata-only for tracked `adminme:` sessions. The
  plugin registers neither `llm_input` nor `llm_output`; prompt and response
  content cannot enter the audit body.
- The active quiet-assistant persona compiles deterministically to per-instance
  `SOUL.md`, `IDENTITY.md`, `USER.md`, and `AGENTS.md`. Member profile additions
  remain separate per-member overlays and persona text has no authority or
  model-routing power.
- The transport is bounded, secret-safe, authenticated, and loopback-only.
  Unavailable/incompatible health is durable. The event log, projections,
  deterministic reads, and captured household truth survive Gateway outage
  and restart without direct-provider fallback.
- The obsolete `adminme/openclaw_plugins/` README and three empty Python
  package files were deleted only after import scanning and replacement tests.

## Acceptance and adversarial evidence

`tests/acceptance/test_openclaw_native_seam.py` boots the production
composition and proves missing/wrong transport auth, exact actor mapping,
unmapped operator denial, child/private isolation, observation-only proposal,
real skill/standing-order/bridge/model events, privileged exclusion, dashboard
scope filtering, full audit provenance, lifecycle event plus scoped what-now,
exact-once approval/lifecycle behavior under retry, durable unavailable state,
restart recovery, incompatible future fixture rejection, and continued safe
domain reads.

The TypeScript fixture tests prove manifest-to-runtime registration, typed tool
requests/responses, optional plus per-call approval, slash and standing-order
dispatch, authenticated dashboard identity, generic-chat non-mirroring,
privileged bridge exclusion, metadata-only model capture, and sanitized
incompatible errors. `npm run contract` additionally scans for forbidden
`llm_input`, `llm_output`, `senderIsOwner`, and OpenClaw operator invoke paths.

Exact compatibility, official references, and fixture/live distinctions are in
`docs/refactor/OPENCLAW-COMPATIBILITY.md`; command results are retained in
`docs/refactor/evidence/gate-4-openclaw-seam.md`.

## Human action and live evidence

No credential or human action is required to reproduce the fixture and process
gate. Before any family-Mac deployment, a maintainer must review the draft PR
and run `npm --prefix openclaw/administrateme run test:live:mac` on a disposable
supported macOS lab with a Node version satisfying the exact source engine, the exact OpenClaw source checkout,
`OPENCLAW_SOURCE_DIR`, and explicit `ADMINME_LIVE_GATE=1`. The script links the
plugin, inspects its live runtime, restarts the Gateway, and requires deep RPC
health. Do not reinterpret fixture success as this live result.

## Exact next phase

**Phase 5 is only the member-owned bridge-to-private-searchable-knowledge
slice.**

Make `tests/acceptance/test_member_bridge_knowledge_vertical_slice.py` pass
without weakening it: enrolled member identity, central owner stamping,
private `member_knowledge`, permitted non-privileged vector representation,
member/child/ambient/cross-owner denial, correction history, replay
equivalence, and two-instance isolation.

Do not reopen the Gate 4 OpenClaw boundary, add optional providers, redesign
the console, create another process, introduce a scheduler/model router, or
broaden conversation mirroring unless Phase 5 exposes a concrete prerequisite
defect.
