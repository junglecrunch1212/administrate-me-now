---
schema_version: 1
current_phase: 7
phase_name: household_experience
gate: 7
gate_result: PASS
verified_local_date: 2026-08-04
branch: codex/administrateme-v2-runtime
branch_tip_resolution: git rev-parse HEAD
draft_pr_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
evidence_level: credential_free_process_browser_and_exact_contract_fixture
next_phase: 8
next_phase_name: release_hardening_and_supported_mac_rehearsal
---

# Handoff

## State

Gate 7 passes. Gates 1–6 remain intact. The principal has all eight domain
bindings in the exact-pinned OpenClaw Control UI, and authenticated household
members have a small responsive Python-served portal for ACL-sensitive views.
Adults receive eight surfaces; child sessions receive Today and Scoreboard;
shared-screen/device sessions receive Scoreboard only. The API and page routes
enforce those boundaries independently of navigation rendering.

The member chat pane is a bounded OpenClaw Gateway protocol-v4 relay. It binds
an opaque, signed per-member/per-channel-peer session, streams rendered text,
supports history and abort, and exposes no operator method, service token,
provider credential, model picker, hidden prompt, or raw envelope. OpenClaw
owns transcripts, conversational memory, routing, retry, provider selection,
and accounting. Ordinary conversation writes no AdministrateMe event. Only an
explicit typed domain tool call reaches existing authorization, observation,
governance, idempotency, and guarded-write seams.

Private quests are schema-versioned, atomic, data-only saved views over named
scope-filtered queries. They contain presentation configuration and domain
references only. Create/activate/promote/expire/archive transitions produce typed audit
events but never a Project aggregate, copied record, scheduler, workflow,
projection, SQL/template/code seam, or raw-log query. Promotion requires an
explicit named audience; child exposure requires the child-compatible query
and action allowlists at both promotion and evaluation time.

## Gate 7 implementation

- `adminme/products/member/` supplies server-authorized navigation, all eight
  domain read models, CRM detail composition, evidence views, immediate
  lifecycle-to-next-action feedback, chat, and temporary-surface APIs.
- `adminme/member_chat.py` speaks only pinned OpenClaw protocol v4
  `connect`/`chat.send`/`chat.history`/`chat.abort` on loopback, with bounded
  WebSocket queues, cancellation, safe errors, and rendered-text sanitization.
- The native plugin derives portal tool provenance only from a valid signed
  session key. The Python boundary independently verifies actor, signature,
  channel, account, member, role, and tool policy. Child chat cannot invoke
  adult actions, review, correction, skills, bridge, notifications, model
  provenance, standing orders, Inbox, or health administration.
- `adminme/temporary_surfaces.py` is the sole atomic saved-view seam. It uses
  five read query names, five task action verbs, capability declarations,
  owner-bound private defaults, reviewed audience promotion, versions, and
  expiry/archive. Every evaluation re-runs existing projection queries under
  the requesting `Session`.
- The portal uses the retained Fraunces/DM Sans/JetBrains Mono vocabulary,
  cream/blush/lavender/teal tokens, calm pill states, 12px/8px radii,
  responsive hierarchy, reduced motion, keyboard focus, live regions, and
  escaped DOM construction.
- Doctor distinguishes portal installation, deterministic domain-view health,
  OpenClaw chat transport, and model-provider configuration without printing
  credential values or failing the process merely because optional chat is
  unconfigured.

## Evidence and limits

The canonical record is
`docs/refactor/evidence/gate-7-household-experience.md`; machine evidence and
18 inspected screenshots are under `docs/refactor/evidence/gate-7/`. The
acceptance suite covers adult/principal/child/device/unauthenticated roles,
cross-member transcript isolation, no-event ordinary chat, exactly-once
private proposals, signed provenance rejection, child injection/direct-route
denial, eight surfaces, three modes, immediate daily-loop recalculation,
private/promoted/expired/archived quests, invalid declarations, thirteen-
projection stability, and model-loss truthfulness. The pinned plugin contract,
TypeScript tests/build, Doctor proof, full Python suite, lint/types/builds,
invariants, and browser harness are part of the Gate 7 release evidence.

No live Gmail, Apple, Plaid, Home Assistant, OAuth, Tailscale, model provider,
real OpenClaw Gateway, or family-Mac success is claimed from credential-free
fixtures. The deterministic chat fixture proves the exact AdministrateMe side
of the boundary; the already-required disposable supported-mac deep-RPC test
is still the live OpenClaw proof.

## Required human action

Review and merge the draft PR. Before activating a family deployment, run the
supported Apple-silicon rehearsal in `MAC-MINI-OPERATIONS.md`, including
FileVault/Keychain, Tailscale identity, Apple grants, launchd/login recovery,
exact-pinned OpenClaw install/restart/deep RPC, explicit provider configuration,
and member-by-member chat/child policy choices. Do not place a Gateway token or
provider credential in browser configuration or support evidence.

## Exact next phase

**Phase 8 is release hardening and supported-Mac rehearsal only.** Execute the
remaining live/forced-failure acceptance matrix, migration/upgrade rehearsal,
provider-specific consent grants selected by the household, and release
packaging/signoff. Do not build another console, chat/transcript/memory layer,
model router, scheduler, generic app builder, Project aggregate, projection,
or temporary-view runtime. Do not broaden member portal authority or reopen
Gate 7 experience semantics without a failing acceptance contract.
