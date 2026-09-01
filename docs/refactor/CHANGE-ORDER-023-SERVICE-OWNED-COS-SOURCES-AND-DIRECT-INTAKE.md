# CO-023 — Service-owned CoS sources and direct intake

Status: **IMPLEMENTED IN PHASE A CANDIDATE; ENROLLMENT AND LIVE ACTIVATION DEFERRED**

Date: 2026-09-01

Governing decisions: ADR-0008, CO-019, CO-021, CO-022, AC-47, AC-51, AC-52

## Objective

Production-compose a deliberately narrow service-principal ownership contract
for the household Chief-of-Staff Google sources and a provider-neutral direct
intake route. The source account, service principal, provider account,
workspace, member route, and external Party remain distinct coordinates. A
direct authenticated member message is a primary intent cue, not complete
domain semantics, standing authority, or permission to contact a provider.

This Phase A change uses only credential-free fixtures and disposable local
instances. It does not enroll an account or channel, change a permission scope,
purchase or provision a phone, send a message, write a provider object, or
change an Apple permission.

## Exact service ownership

An enabled provider adapter has exactly one `member` or `service_principal`
owner. Service ownership is accepted only for these reviewed adapter kinds and
exact read-only scopes:

| Adapter kind | Reviewed scope |
|---|---|
| `messaging-gmail-api` | `gmail.readonly` |
| `calendaring-google-calendar` | `calendar.readonly` |
| `tasks-google-tasks` | `tasks.readonly` |

Each service-owned row also requires the stable CoS service principal, service
workspace, a distinct provider-account coordinate, ownership attestation,
service-account-delegation provenance, a service-account secret reference,
`read_only=true`, and writes disabled. The central inventory continues to
reject Apple member adapters because they are member-bridge-only. No planned
service identity, Workspace membership, Apple ID, machine, email address, or
phone number implies provider ownership or permission.

The durable adapter namespace includes adapter kind, owner kind, owner ID,
provider account, and workspace. Binding validation rejects duplicate adapter
IDs and any collision on that full coordinate. Existing member cursor rows are
adopted once through a recorded alias, then cursor, lease, retry, and dedupe
state is mirrored to the predecessor key for rollback compatibility. An
occupied or double-bound destination fails closed. Cursor, idempotency, rate
limit, health, and external identifiers therefore cannot silently cross a
member/service boundary or two accounts.

## Current Google source facts

The production Gmail, Google Calendar, and Google Tasks producers accept the
reviewed service binding and emit current owner-aware schemas:

- `messaging.received@v6` and observed `messaging.sent@v6`;
- `calendar.event_added@v3`, `calendar.event_updated@v3`, and
  `calendar.event_deleted@v3`;
- `list_item.added@v4`, `list_item.updated@v4`,
  `list_item.completed@v4`, `list_item.uncompleted@v4`, and
  `list_item.deleted@v3`, plus owner-aware list changes.

The envelope and payload name the service principal, provider account, service
workspace, adapter, and provider-native object identity. Their scope is
`service-private:service-principal:chief-of-staff`; a member ID is absent.
Member-owned producers retain their established schemas and private scopes.
Calendar and list member projections ignore service-owned source facts instead
of forging member ownership. CoS mailbox observations project only to a
separate encrypted `service_interactions` table and are not returned by member
interaction queries.

`service-source-readiness-v1` reports only non-identifying binding, permission,
cursor/health, workspace, and readiness metadata. The checked-in
`config/cos-service-sources.example.yaml` is explicitly a non-active fixture:
its `.invalid` accounts and secret references do not enroll or poll anything.

## Trusted direct routing

The Gmail producer maps only an exact normalized provider-authenticated sender
identifier to a reviewed opaque route key. It does not emit a member route.
The central `direct-intake-v1` consumer resolves that key against the exact
reviewed channel table into one of:

1. one exact member-private route;
2. the explicit household-shared route;
3. one reviewed external-contact Party; or
4. unresolved.

Display name, subject/body language, signature, model classification, and a
self-claimed identity never select a member. A missing, unknown, ambiguous,
wrong-channel, wrong-provider, inactive, or contradictory coordinate fails
closed to unresolved service-private state and exposes no content.

An exact member route copies only the bounded text required for one
member-private `triage.intent.submitted@v1` primary cue. The existing
source-policy/reconciliation seam emits its immutable admission receipt, so the
cue is visible in the CO-022 human-centered Triage queue. The intent remains
pending interpretation: it has no target kind, canonical truth, work order,
provider command, or external authority. `direct_intake.routed@v1` records a
content-free decision and the exact cue event. External, shared, and unresolved
routes create no member cue; an external contact is explicitly recorded as
`external_contact_not_instruction`.

The source body stays only in the service-private source fact and service
projection. The route receipt contains no body, subject, display name,
signature, or model output. Member A and Member B cues use separate private
scopes despite the single CoS mailbox owner, and neither route grants access to
the other adult's provider data.

## Channel readiness

The provider-neutral readiness inventory is truthful and non-enrolling:

- Gmail direct intake: production adapter supported, fixture observation only,
  sending disabled;
- Apple Calendar, Apple Reminders, and iMessage: planned, not enrolled, no
  production service-owned provider support in this change;
- SMS and phone/voicemail transcripts: not provisioned;
- every channel: content hidden and provider effects forbidden.

An Apple account, machine permission, or phone number cannot upgrade any of
these states. Provider-specific support requires its own reviewed architecture,
owner request, credentials/scopes, enrollment, and acceptance proof.

## Evidence

`tests/integration/test_service_owned_google_sources.py` loads the production
adapter packs from the credential-free fixture and calls their real producer
paths through the encrypted event writer. It proves all three reviewed Google
adapters emit service-private current owner/workspace/route schemas.

`tests/acceptance/test_service_owned_cos_sources_direct_intake.py` drives the
production composition root. Exact Member A and Member B mail create one
separate primary cue each; unknown content that claims Member A remains
unresolved; external and shared routes create no member instruction; the
service mailbox body remains outside member interactions; the existing member
source path stays healthy; direct admission, projection rebuild, restart, and
route replay are equivalent; and provider effects remain zero.

Provider-binding and service-identity unit tests prove the three-kind allowlist,
fake-member rejection, unreviewed-adapter rejection, duplicate/full-coordinate
collision rejection, exact route resolution, and content-free readiness.

## Migration and rollback

The event schemas are additive and earlier member schemas remain registered.
Interactions advances additively from v4 to v5 with one service-private table;
Calendar and list schemas do not store service rows in member read models. The
direct-intake and reconciliation consumers are versioned and idempotent. A
projection rebuild or process restart reproduces owner, route, cue, and
admission state without a provider call.

Rollback selects the protected predecessor and rebuilds disposable projections.
Older code may ignore the new schemas and `direct_intake.routed` events. The
content-free member cursor namespace is copied and continuously mirrored to its
retained predecessor key; no watermark, lease fence, retry, or dedupe identity
is discarded. No source, member decision, canonical object, credential,
provider configuration, event-log checkpoint, permission, or provider state is
rewritten.

## Constitutional disposition

- **PRESERVE:** append-only source truth, member privacy, separate provider and
  domain authority, deterministic projections, human confirmation, provider
  governance, observation mode, one central Python runtime, member bridge
  ownership, and the pinned OpenClaw seam.
- **MODERNIZE:** the reviewed Google adapters gain exact service ownership;
  current events and durable namespaces retain the complete owner/account/
  workspace coordinate; direct intake and readiness become typed, replayable,
  provider-neutral contracts.
- **SUPERSEDE:** fake member ownership for CoS accounts, account-wide owner
  assumptions, content/name/model-selected member routing, unresolved private
  disclosure, and external-contact language treated as an instruction.
- **DEFER:** live provider/channel enrollment, Apple provider implementations,
  SMS/phone provisioning, permission or credential changes, deployment,
  sending, writes, purchase, and every provider effect require a separate
  owner-authorized Phase B change. Required Phase A ownership, routing,
  isolation, readiness, replay, and zero-effect behavior is not deferred.
