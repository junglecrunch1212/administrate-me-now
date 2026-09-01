# CO-024 — CoS work coordination and operating agreements

Status: **IMPLEMENTED IN PHASE A CANDIDATE; PROVIDER EXECUTION DEFERRED**

Date: 2026-09-01

Governing decisions: ADR-0008, CO-019, CO-021, CO-022, CO-023, AC-53

## Objective

Give the non-human household Chief of Staff durable domain work, bounded
member-delegated responsibility, minimum typed household coordination, and
structured operating agreements. The service remains a Party and service
principal, never a household member. This change creates no provider command,
scheduled OpenClaw run, credential, permission, enrollment, deployment, or
other provider effect.

## CoS work

`task.created@v3` keeps ownership and assignment as independent axes. A member
obligation retains its `owner_member_id`; a household obligation retains the
household Party; work delegated to or created internally by the CoS is owned by
the CoS Party. All four may name the CoS as responsible/assigned Party without
changing the obligation owner. Exact grant, active/on-behalf-of member,
delegator, beneficiary, linked domain item, source event/invocation,
coordination, schedule, recurrence, waiting/follow-up, work type, and provider
representation ownership are explicit coordinates.

The work types cover operational, waiting, follow-up, clarification, exception,
preparation, held-action, provider-failure, reconciliation, operational-calendar,
and reminder-like work. `recurrence.added` remains a recurrence template; it
does not invoke OpenClaw or materialize work by itself. `cos.work.state_changed`
lets the service start, wait, follow up, hold, resume, complete, or cancel under
the same exact active grant. It records the service actor, automation operator,
service Party, context member when applicable, optimistic predecessor event,
and zero provider effects; it never invents a completing member.

Member-owned and service-owned provider representations are typed metadata.
They are not provider authority and cannot emit a command. Provider calendar
and list source facts remain distinct from Tasks and Recurrences.

## Minimum typed coordination

Coordination remains inside the existing Tasks projection as normalized
records, participants, audience-scoped fields, originator receipts, and private
conversation references. It carries only typed labels, deadlines, intervals,
resources, constraints, and busy state. Raw evidence, source content, and
conversation transcripts are forbidden.

A record distinguishes obligation owner, responsible Party, beneficiary, CoS
assignment, and linked Task or Commitment. A proposed destination becomes
responsible only after that exact participant accepts; rejection, withdrawal,
and reassignment remain separate events. Proposal expiry is tracked separately
from expiry of the coordination itself. Waiting-on-member, follow-up,
revocation, both expiry kinds, and per-field audiences are replayable. Each participant
may link a different private conversation to the same shared coordination ID.
Only that member can read its reference. Busy and overlapping-resource queries
return interval, `busy`, and a content-free resource-conflict bit—never labels,
provider coordinates, or private evidence.

## Structured operating agreements

Operating agreements remain inside the existing Parties projection. Markdown
may render them but is not authority. The four layers are:

1. a release-registered constitutional safety floor;
2. a shared-household revision requiring the exact current adult quorum;
3. a member-private revision visible to and approved by its exact owner; and
4. a one-time or expiring exception in either exact authority scope.

Every immutable rule revision can bind actor and represented service, active
and on-behalf-of members, resource/provider owner, purpose/domain/trigger,
fact freshness and coverage, observations, internal processing, disclosure,
proposal/preparation, prohibitions, independent approval, hard ceilings,
verification, reconciliation, exact trust coordinates, time, provenance, and
lifecycle. `provider_execution_permitted` is fixed false. Source policies are
not embedded and remain the specialized intake-policy family.

Proposals are inert. Member-private activation requires the owner approval;
shared activation requires equality with the recorded full principal set.
Partial approval does not change the proposal revision. Rejection, suspension,
revocation, supersession, expiry, and one-time consumption are explicit. A
revoked, suspended, expired, consumed, or superseded revision is excluded from
future applicability. There is no global trust score and no automatic trust
promotion.

## Projection governance, migration, and replay

No projection is added: the reviewed projection count remains fifteen.
Tasks advances additively from v5 to v6 with service-work columns and
coordination tables. Parties advances additively from v3 to v4 with operating
agreement and approval tables. Existing schemas and member behavior remain
registered. Rebuild, restart, and encrypted backup/restore reconstruct the
same work, coordination, private/shared agreement, quorum, lifecycle, and
revocation state from the append-only log.

Rollback selects the CO-023 predecessor and rebuilds disposable projections.
Older code may ignore the additive event families and columns. No source fact,
member decision, credential, provider configuration, checkpoint, external
object, or provider state is rewritten.

## Constitutional disposition

- **PRESERVE:** append-only source truth, distinct Tasks/Commitments/Calendar/
  Lists/Recurrences/scheduler objects, member privacy, exact scopes, human and
  provider authority, observation, one Python runtime, fifteen projections,
  pinned OpenClaw, and source-policy specialization.
- **MODERNIZE:** service work gains exact ownership/assignment/provenance and a
  non-member lifecycle; coordination and operating agreements become typed,
  replayable, audience-aware, quorum-bound domain state.
- **SUPERSEDE:** service work represented as a fake member obligation, handoff
  by unilateral reassignment, shared private evidence, transcript sharing,
  Markdown authority, self-activating proposals, global trust scores, and
  automatic trust promotion.
- **DEFER:** G07 delegated provider execution, provider/channel enrollment,
  deployment, permission/credential changes, write enablement, and every
  provider effect require a later explicit owner-authorized change.

## Evidence

`tests/acceptance/test_cos_work_coordination_operating_agreements.py` drives
the production composition root across two adults and the CoS service. It
proves distinct obligation/assignment ownership, recurring and lifecycle work,
member/service provider representation, accepted/rejected/withdrawn/reassigned
handoffs, waiting, private field audiences and conversations, busy/resource
conflicts, constitutional/private/shared/expiring agreement behavior, exact
quorum, revocation, projection rebuild, restart, encrypted backup/restore,
privacy, and zero provider effects.
