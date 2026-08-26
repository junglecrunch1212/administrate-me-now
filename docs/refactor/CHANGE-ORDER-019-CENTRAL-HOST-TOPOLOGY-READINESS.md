# Change Order 019 — Central Host topology and service-channel readiness

Status: **PHASE A IMPLEMENTED; PR REVIEW AND PHASE B OWNER ACTIONS PENDING**

Date: 2026-08-26

Governing sources: `BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md`,
`ACCEPTANCE-CONTRACT.md`, ADR-0000, ADR-0001, ADR-0004, ADR-0005,
ADR-0013, CO-003, C002, C024, C034, C057–C068, C071, C080, C086,
C097, C103, C104, C115, and C116.

## Why this correction is required

The central service already used one Python runtime, one pinned OpenClaw
Gateway, loopback listeners, Tailscale Serve, encrypted event storage, and
observation mode. Those are necessary but not sufficient activation evidence.
The prior executable readiness path did not prove that the Central Host was a
non-member infrastructure identity, that both human principals could traverse
distinct trusted-ingress bindings, that the service account was Standard with
a tested independent recovery path, that provider accounts had globally
unique reviewed owners, that backup/rollback evidence was current, or that
historical observation terminals proved zero provider calls. Settings could
also present a weaker notion of readiness than `/api/system/ready`.

The previous communication contract required every central communication
account to belong to a human member. That is correct for current member-owned
adapters but too narrow for the long-term Chief-of-Staff persona. The household
intends a dedicated CoS Apple service identity for future CoS-addressed
iMessage and reviewed Apple services. No Apple account, sign-in, transport,
permission, enrollment, or provider effect is authorized by this change.

## Architectural coordinates

The following coordinates are distinct. Possession, equality, or sharing in
one system never implies another coordinate or authorization:

| Coordinate | Meaning | Prohibited collapse |
|---|---|---|
| Central Host infrastructure identity | Tagged Tailscale node used by Serve and host diagnostics | Never a member login, portal principal, bridge identity, or CoS channel account |
| macOS service account | Dedicated Standard Unix account running the two user services | Never the separate administrator/recovery path or a member Apple identity |
| Member principal | Human household authorization identity | Never inferred from provider ownership, Endpoint text, host ownership, or a service principal |
| Service principal | Non-human household-owned persona authorization identity | Never Principal A/B and never implied member-source access |
| Provider account | One vendor-native account with its own credentials, grants, recovery, workspace, and provenance | Never a Party, member, cross-vendor authority, or OpenClaw session |
| Endpoint | Observed addressable identifier | Never automatically a Party, owner, member, or visibility route |
| Party | Qualified person, organization, household, or service identity in household truth | Never automatically a portal identity, provider owner, or channel session |
| OpenClaw agent/session | Channel conversation/model boundary | Never AdministrateMe domain authority or shared across members/accounts |
| Member bridge enrollment | One member/device/source-specific ingress identity | Never a central account or another member's source authority |

Apple, Google Workspace, Google Cloud, Cloudflare, Twilio, Tailscale,
OpenClaw, and AdministrateMe identities remain separately revocable. A common
CoS mailbox/address namespace may aid recognition, but credentials, tokens,
permissions, provider accounts, and recovery roots stay independent. The CoS
mailbox cannot be the sole recovery root for the domain or administrative
systems.

## Decisions

### Central Host and ingress

- The Central Host must present `tag:administrateme-central`, be online, be the
  only reviewed Tailscale Serve edge to loopback `127.0.0.1:3330`, and run
  exactly one Python runtime and one pinned OpenClaw Gateway.
- A member-owned or member-mapped Central Host blocks deployment readiness. It
  cannot authenticate to member APIs.
- Every interactive member must have one exact case-fold-unique trusted login,
  an explicit role, and a real acceptance receipt. Unknown identities, missing
  roles, unknown view targets, duplicates, and cross-member view requests fail
  closed.
- The service runs as a Standard macOS user. Activation separately requires a
  recent physical proof of a different administrator account, successful
  FileVault unlock/recovery, and disabled automatic login.

### Provider bindings

- Every enabled real provider adapter names exactly one owner principal:
  `member` or `service_principal`; the two are mutually exclusive.
- It also names an exact provider account, explicit `personal|work|service`
  workspace, unique permission scope, reviewed binding provenance, and owner
  attestation. Missing or contradictory evidence blocks startup.
- Exact adapter/account/workspace coordinates are unique. Reuse of one vendor
  account/workspace across owner principals fails closed globally.
- Settings/readiness reports only counts and classification. Provider account
  identifiers are not published or converted to unsalted public hashes.
- Current Gmail, Google Calendar, and Google Tasks implementations remain
  human-member adapters. The generic owner model preserves a future
  service-owned adapter path without pretending those implementations support
  it today.

### Bridges

- With no enrollment, bridge ingest denies and bridge health says the endpoint
  exists but no bridge is enrolled. Portal identity is never a bridge fallback.
- A package-exported enrollment remains provisional. It may submit only its
  one exact, unexpired `bridge.enrolled@v1` bootstrap proof; batch or ordinary
  facts deny. Only an active exact enrollment can authenticate ordinary bridge
  ingest. Revocation remains explicit.
- Each member's private Calendar, Reminders, Contacts, Notes, Voice Memos, and
  similar Apple sources remain behind that member's separately consented
  bridge. A CoS Apple identity and Apple sharing grant no access.

### CoS service-channel identity

The repository has a generic non-human service-principal/channel contract and
one truthful built-in catalog row:

| Item | Current state |
|---|---|
| Identity label | `CoS Apple service identity` |
| Lifecycle | `planned` / `account_not_created` |
| Provider account reference | Absent |
| Endpoint reference | Absent |
| Channel enrollment | False |
| Observation posture | Required before any future trust review |
| Sending | Disabled |
| Current release prerequisite | No; planned optional path does not block the existing central runtime |

The supported lifecycle vocabulary is `planned`, `account_not_created`,
`account_created`, `channel_not_enrolled`, `observation`, `healthy`,
`degraded`, `disabled`, and `revoked`. State/evidence contradictions fail
validation. A reserved address is not configured, authenticated, enrolled, or
healthy.

An inbound service-channel fact must have exactly one service-principal owner
and one reviewed non-content route:

- `member_private` names exactly one known member;
- `household_shared` names no member target;
- `external_contact` names one reviewed non-member Party; or
- `unresolved` names no target and remains outside member visibility.

Zero, duplicate, missing, or unknown route evidence becomes `unresolved`.
Address similarity, contact-list sharing, model output, and message content do
not choose the route. Member-owned v5 message facts are restricted to that
same member's private route. OpenClaw continues to own channel sessions,
conversation/model concerns, and generic memory; AdministrateMe owns typed
message facts, authorization, privacy, provenance, observation, correction,
and outcomes.

Future CoS sending is required to use the normal sequence: allowlist →
governance → rate limit → privacy/content guard → observation → independent
OpenClaw and domain approval → provider seam → one terminal audit. The current
member-only request/approval/dispatcher schema does **not** implement that
service-principal send path, and configuration therefore rejects
`sending_enabled:true` even for a healthy receive-side channel. A later
reviewed schema/adapter gate must add service ownership without weakening any
guard. The service cannot send as or impersonate a member. Every attempted,
suppressed, delivered, failed, and reconciled result must be accounted. No new
Apple/iMessage or Twilio adapter is included; their truthful catalog entries
remain disabled/planned.

### OpenClaw

- Each enabled human member has one unique AdministrateMe portal agent and
  exact channel/account/actor bindings.
- `dmScope` is exactly `per-account-channel-peer` at the Gateway and
  `per_account_channel_peer` in the internal typed report.
- An account cannot be shared between members. Agent/account maps and the live
  plugin heartbeat must agree with the reviewed expectation and the current
  OpenClaw agents, bindings, plugin topology, and session-scope configuration.
  A stale configured assertion cannot mask runtime drift.
- The CoS service principal is not silently added to a human member agent. A
  future service-channel agent/session binding requires its own reviewed gate.

### One readiness truth

Runtime diagnostics, `/api/system/ready`, Settings, and Doctor consume the same
secret-safe `deployment-readiness-v1` result. It reports independent topology,
service-account/recovery, identity acceptance, provider binding,
service-channel, bridge, OpenClaw, backup/rollback, and observation-effect
dimensions. Service liveness and safe reads remain independently visible;
neither means release/activation eligibility. Strict production startup keeps
writes denied whenever any required dimension is not healthy. The provider
dispatcher independently re-evaluates this same object and emits a correlated
`provider_called:false` permission terminal before an adapter can be called
when admission is closed.

Physical evidence expires after 31 days. Owner attestation records only
booleans, member IDs already present in protected configuration, timestamps,
fixed-width fingerprints, and `content_exposed:false`; it does not perform the
check or grant authority. Its release, host, instance, and routing
configuration fingerprints must still match the running deployment.

Terminal-effect integrity is durable: missing, duplicate, malformed, orphaned,
unknown-call, or approval-correlation evidence blocks. An intentional,
independently approved post-observation provider call does not permanently
degrade integrity. Zero-effect activation proof is instead bounded to the
current attested observation window; returning to observation requires a fresh
window after prior live effects.

## Classification

| Change | Classification | Rationale |
|---|---|---|
| One Python runtime, one pinned Gateway, loopback, Serve-only edge, member bridge privacy, observation, governance, independent approvals, terminal audit | PRESERVE | Existing constitutional boundaries remain authoritative. |
| Tagged non-member Central Host and exact A/B ingress acceptance | MODERNIZE / HARDEN | The former prose did not explicitly require a tagged infrastructure identity or fail closed on a member-owned host. |
| Standard service account plus tested separate recovery path | MODERNIZE / HARDEN | “Dedicated” and “non-root” were insufficient activation predicates. |
| Global provider-owner/workspace/permission/provenance validation | MODERNIZE / HARDEN | Per-row schemas and defaults could not prove account ownership or uniqueness. |
| Non-human service principal and deterministic CoS channel routing | MODERNIZE | Preserves the intended service persona without weakening member privacy. |
| Blanket reading of “no Apple ID on the central service account” | Narrow SUPERSEDE | The ban continues for member-source ingestion; it does not prohibit a separate dedicated CoS Apple channel identity. |
| Portal/bridge fail-open defaults and independent Settings readiness | SUPERSEDE | Unknown or absent evidence now denies and one readiness object governs all surfaces. |
| Exact OpenClaw account-aware topology, terminal integrity, and phase-bounded zero-effect accounting | HARDEN | Heartbeat/liveness alone cannot prove isolation; intentional later live writes cannot poison health forever. |
| Apple/iMessage connectivity, account creation, BlueBubbles/Twilio enrollment, permissions, and live sends | Not authorized | These remain separate Phase B owner gates, not deferred required product functions. |

## Compatibility and rollback

Existing event schema versions remain registered. Messaging v5 is additive;
current member adapters continue emitting their reviewed versions. Disabled
provider rows may remain incomplete. Strict production configuration must add
explicit provider evidence before the candidate can become ready; that is an
intentional fail-closed migration.

Rollback selects the retained prior immutable release and restores its prior
configuration pointer through the guarded updater. It does not remove events,
rewrite provider facts, delete attestations, weaken observation, or activate a
channel. Because the predecessor lacks these readiness guards, rollback is an
availability recovery only; it is not evidence that the predecessor satisfies
the corrected topology contract.

## Acceptance evidence

AC-47 owns production-composition proof. Detailed content-free Phase 0/Phase A
evidence, the Human Task Register, and the post-merge Phase B activation,
verification, abort, and rollback packet are in
`evidence/co-019-central-host-topology-readiness.md`.
