# CO-026 — CoS Settings capability catalog and OpenClaw

Status: **IMPLEMENTED IN PHASE A; INSTALLATION, ENROLLMENT, LIVE WRITES, AND DEPLOYMENT DEFERRED**

Date: 2026-09-02

Governing decisions: ADR-0000, ADR-0001, ADR-0005, ADR-0008, CO-019,
CO-021, CO-022, CO-023, CO-024, CO-025, AC-56

## Objective and boundary

G08 makes Settings the truthful member-facing explanation of what the household
CoS can do, what is merely present in the reviewed product, what is installed or
configured for this member, and what remains unavailable. The versioned
`administrateme-capability-catalog-v1` is tenant-neutral. It is derived from the
checked-in domain products, central adapter manifests and entrypoints, member
bridge manifests and entrypoints, the exact pinned OpenClaw plugin manifest, and
two explicit reviewed future descriptors. Provider names are never inferred
from a UI wish list or an event schema.

This change records and cancels secret-free desired capability requests. Those
events install no software, pair no bridge, enroll no account, read no
credential, change no provider permission, grant no AdministrateMe authority,
change no OpenClaw configuration, and cause no provider effect. Phase B remains
separately owner-gated. G09 is not started by this change.

## Catalog identity and state axes

Every catalog row has a stable capability ID, catalog version, outcome, reviewed
source, implementation/artifact version, owner kinds, runtime location, provider
options, sensitivity, and a six-verb matrix. `read`, `observe`, `draft`,
`prepare`, `hold`, and `execute` remain independent and are each in an exact
supported, fixture-only, planned, or unsupported state.

Catalog presence does not imply installation, setup, provider permission,
AdministrateMe authority, OpenClaw permission, runtime health, freshness, trust,
verification, terminal-result support, later observation, reconciliation, or
effective availability. Member composition reports those axes separately. It
also reports each local-helper, browser, partner-service, commercial-service,
and human prerequisite as required or not required; member, Admin, and
household actions; consent state; unavailable reason; and prohibited reason.
Unknown, missing, ambiguous, stale, paused, revoked, unsupported, or incompatible
evidence fails closed.

Reviewed adapter code may be present while its account is not set up. A provider
scope declaration is not proof of a provider grant. A running adapter without an
exact CoS delegation is not an OpenClaw tool permission. A domain delegation is
not an OpenClaw approval. Observation or a later provider fact is not a terminal
command result. These distinctions are preserved in both the JSON contract and
the member UI.

## Member bridges and planned communications

Each adult bridge remains one separate, adult-owned edge process. Settings
reports bridge software availability, compatible package availability,
enrollment/request state, package readiness, device pairing, Tailscale identity,
capability request, local macOS permission, adapter authentication, observation,
source settlement, degraded state, pause, revocation, unsupported state, and
effective readiness independently for:

- Apple Calendar;
- Apple Reminders;
- Apple Contacts;
- Apple Notes;
- Apple Voice Memos; and
- Obsidian.

Pairing without an exact attested local permission is degraded and unavailable.
Transport does not substitute for adapter authentication or settlement. No
principal or Admin can complete another adult's bridge or provider consent.
Apple Messages observation is an explicit `planned_g10` catalog descriptor and
Apple Messages reply is `planned_g11`; neither is installable, executable, or
represented as current support in G08.

## Settings information architecture and authorization

The adult Settings surface has exactly these twelve URL-backed sections:

1. Overview
2. My identity
3. CoS behavior
4. Source intake & context
5. How my CoS works
6. Capabilities
7. Connections
8. Devices & bridges
9. Learning & trust
10. Household & privacy
11. Safety & system
12. Admin requests

The surface uses semantic controls, visible status text, calm hierarchy,
keyboard operation, reduced-motion support, and the established narrow-screen
reflow. The capability card keeps all unlike state axes visible rather than
reducing them to a single enabled badge.

Adult private Settings, connection rows, bridge rows, capability requests,
private operating agreements, source policy, and compiled CoS context are
self-owned. View-as is read-only and omits the viewed adult's private Settings
state. Admin requests expose only requester, desired outcome, scope, reviewed
provider option, state, and revision; they contain no credential and confer no
consent or authority. Admin can make a reviewed option available but cannot
approve another adult's private source. Child and non-member requests retain the
existing fail-closed Settings boundary.

## CoS identity, trust, and compiled context

The same stable CoS service Party and service principal are shown for each
adult, while each adult retains an isolated OpenClaw agent, workspace, account-
aware peer session, source policy, operating agreements, and delegations. There
is no shared transcript, shared inbox, shared generic memory, or raw cross-member
context. Shared household authority exists only through typed, audience-scoped
agreements with exact adult quorum.

`administrateme-cos-compiled-context-v1` is a minimum typed view for the active
member CoS. It includes only member identity/agent coordinates, bounded
preferences, effective plain-language operating agreements, exact active
delegations, and effective source-policy summary. It explicitly excludes
credentials and secure references, raw private evidence, another adult's private
state, provider message/note bodies, shared inboxes, generic household memory,
transcripts, and infrastructure administration. Its digest covers the compiled
value; it creates no new source of truth or authority.

## Exact pinned OpenClaw seam

Compatibility is proved against OpenClaw `2026.7.2` at exact commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`. The exact checked source and its
validated manifest/SDK fixture are proof; prose copied from older bundled docs
is not proof. The one loopback Gateway topology is unchanged.

The native plugin now declares typed tools for member capability catalog/status,
effective Settings context and agreements, source-policy explanation/proposal
inspection and decision, inert capability request/cancellation, the existing
authorized CoS work/coordination operations, and existing delegated-action
proposal/prepare/execute/observe operations. Read operations use exact
`read_context` authority. Capability changes and policy decisions require a
single-use OpenClaw approval, which remains independent of domain authorization.
Policy inspection is read-only and needs no approval. Static tool declaration is
reported separately from effective member eligibility.

## Migration, replay, and rollback

The secret-free Settings journal migrates additively from schema v3 to v4 and
preserves preferences, candidate/source policies, profiles, connection state,
bridge requests, and prior operational receipts. New
`settings.capability_requested@v1` and
`settings.capability_request_cancelled@v1` events are private, revisioned,
idempotent, and replayed into the cache at startup. Existing service delegations
and operating agreements remain in Parties; no projection is added and the
reviewed projection count remains fifteen.

Encrypted backup/restore rebuilds the fifteen projections and replays the
capability request journal without a provider effect. Older code may ignore the
additive event family. Rollback selects the protected G07 predecessor and
rebuilds disposable projections/caches; it does not rewrite a source fact,
agreement, delegation, request event, credential, provider state, or checkpoint.

## Constitutional disposition

- **PRESERVE:** append-only household truth, distinct domain/provider/scheduler
  objects, adult-private consent and source state, stable CoS identity, isolated
  per-adult OpenClaw agents/workspaces/account peers, independent domain and
  OpenClaw authority, final observation handling, one Python runtime, fifteen
  projections, one exact-pinned Gateway, and provider-effect accounting.
- **MODERNIZE:** Settings gains a versioned reviewed capability catalog, twelve
  sections, explicit bridge and effective-state axes, minimum compiled CoS
  context, typed request/cancel events, and typed pinned OpenClaw access.
- **SUPERSEDE:** speculative integration lists, catalog-presence-as-availability,
  config-as-health, paired-as-permitted, static-tool-as-authorized, Admin-as-
  consent-owner, cross-adult bridge assumptions, shared-memory shortcuts, and
  undocumented prerequisite or unavailable states.
- **DEFER:** G09 and later goals; G10 iMessage observation; G11 reply execution;
  software installation, bridge pairing, provider enrollment, credentials,
  Keychain or OS permission changes, scope/write enablement, live provider
  effects, runtime migration, deployment, and unrelated live repair.

## Evidence

`tests/unit/test_capability_catalog.py` validates catalog derivation, required
state fields, non-speculative membership, migration, and inert request events.
`tests/acceptance/test_cos_settings_capability_catalog.py` boots the production
composition with two adults, exact OpenClaw provenance/approval, bridge and
adapter state changes, grant/revocation, view-as and Admin privacy, restart,
encrypted backup/restore, and zero provider effects. The native plugin contract,
TypeScript tests, repository release gates, and authenticated browser acceptance
cover manifest parity, tool routing, approval hooks, the twelve sections,
keyboard-visible state, narrow reflow, and error-free rendering.
