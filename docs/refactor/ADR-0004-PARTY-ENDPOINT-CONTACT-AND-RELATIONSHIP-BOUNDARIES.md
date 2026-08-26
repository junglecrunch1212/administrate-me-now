# ADR-0004 — Party, endpoint, contact, and relationship boundaries

Status: **ACCEPTED**

Date: 2026-08-06

Decision owners: household principals and AdministrateMe product/engineering

Governing change order: `CHANGE-ORDER-003-PEOPLE-AND-RELATIONSHIP-MEMORY.md`

## Context

The historical People/CRM path treated an unresolved message sender as a
person. That shortcut made retailers, newsletters, service addresses, and
ambiguous shared endpoints look like canonical household relationships. It
also obscured provider authority, member privacy, and the distinction between
what a source observed and what the household has decided is true.

Relationship memory must reduce recall burden without creating a model-owned
social graph, a guilt score, a copied address book, or a second communications
inbox. The event log remains authoritative and all read models remain
rebuildable.

## Decision

AdministrateMe represents five separate concepts:

1. An **Endpoint** is an observed addressable value. It may belong to a person,
   organization, service, shared mailbox, or unresolved sender. Observation of
   an endpoint never creates a Party.
2. A **Provider Contact Record** is one record from one exact member, provider,
   account, container, native id, and revision. The provider remains
   authoritative for mirrored fields and deletion state.
3. A **Party** is a stable person, organization, or household identity. It is
   created only by explicit entry, household enrollment, an eligible curated
   provider contact, or a human-confirmed Triage candidate.
4. An **Identifier Link** joins an Endpoint to a Party with scope, provenance,
   verification, and revocation state. Exact duplicate evidence is retained;
   an ambiguous or hidden match fails closed.
5. **Memberships, Relationships, Relationship Facts, and Member Relationship
   Profiles** remain distinct. Membership may grant household authority;
   relationship labels never do. Facts retain provenance and sensitivity. A
   member profile holds only that member's explicit cadence, favorite,
   suppression, tags, and cue policy.

A non-human **service principal** is an authorization/routing identity for a
reviewed household service persona; it is not a household member. Its provider
account and vendor credentials, any observed Endpoint, any qualified Party,
the Central Host's infrastructure identity, and an OpenClaw agent/session all
remain separate and independently revocable. In particular, the planned CoS
Apple service identity cannot resolve as either human member and grants no
access to either member's private Apple sources.

Provider records, identifier links, facts, interactions, and member profiles
are owner-private by default. Household-visible Parties do not expose another
adult's private provider record, identifiers, interaction history, or member
profile. Sharing requires a separate, revocable adult grant naming permitted
field groups. Privileged facts are never shared, indexed for models, or
returned through view-as. Child, device, ambient, operator, and generic
OpenClaw sessions receive no People directory.

Identity resolution is deterministic. A single authorized, qualified exact
identifier may resolve; zero or multiple eligible matches remain unresolved.
Name similarity, domain proximity, message volume, graph proximity, and model
output may only create evidence-backed review candidates. Merge, split,
relink, revoke, archive, restore, and relationship correction are explicit
append-only events with reversible allocation evidence.

Relationship cognition separates descriptive measures from member intent.
Last contact, counts, open commitments, and confirmed dates are descriptive.
Reconnect cadence, favorites, quieting, suppression, and cue budgets are
explicit member settings. Deterministic code alone decides cue eligibility.
An LLM may extract schema-bound, exact-evidence candidates through the pinned
OpenClaw seam, but it cannot create or merge a Party, infer a relationship,
rank people, authorize access, choose outreach, or write a provider record.

OpenClaw retains channels, conversations, sessions, generic memory, routing,
and agent orchestration. The one pinned Gateway uses member-bound sessions and
exact provider-account routes. AdministrateMe does not create an agent per
contact, inbox, or source and does not copy People truth into generic memory.

For a service-addressed communication, the adapter must determine exactly one
reviewed `member_private`, `household_shared`, or `external_contact` route from
non-content routing evidence. Zero, multiple, absent, or contradictory matches
remain unresolved and invisible to members. Name similarity, address-book
sharing, provider sharing, or model output cannot choose visibility. A member-
owned Apple source remains member-private even when a provider could share it.

Reviewed bootstrap enrollment crosses this boundary through idempotent domain
events before readiness. The mapping file remains an authentication/enrollment
input, never a projection query source. Its receipt creates household/member
Parties, Memberships, and explicit Relationships; an optional reviewed
external Party does not thereby gain membership, helper authority, portal
identity, or provider fields.

## Consequences

- The primary People directory includes only authorized, qualified person,
  organization, and household Parties; raw endpoints remain review evidence.
- Contact adapters are loss-aware mirrors with explicit source capability and
  freshness. Schema support never claims that a provider is enrolled.
- People becomes a deterministic master/detail relationship-memory workspace
  shared by the member portal and `administrateme.dashboard.people` binding.
- Triage gains distinct Party, relationship, and fact candidate kinds; no
  candidate becomes canonical before human confirmation.
- Local archive is reversible and preferred over provider deletion. Any
  external contact write still crosses allowlist, governance, rate limit,
  independent approvals, privacy guard, revision check, observation, and
  terminal reconciliation.
- AC-30 owns process, isolation, replay, adapter, and browser proof.

## Classification

- **PRESERVE:** event authority, provider authority, typed domain separation,
  member isolation, human gating, governance, observation, and replay.
- **MODERNIZE:** People as relationship memory; provider-neutral contact
  records; scoped identity resolution; member-specific relationship profiles;
  explicit non-human service principals and deterministic channel routing.
- **SUPERSEDE:** sender-as-person, implicit provider-share authorization,
  tenant-wide exact-identifier uniqueness, model closeness/ranking, agent
  proliferation, and any blanket interpretation that forbids a separate CoS
  Apple service identity while preserving the ban on central member-source
  ingestion.
- **DEFER:** only live enrollment of an optional contact provider and its
  separately approved write operations. The People product function, privacy
  boundary, replay, and fixture-backed adapter contracts are not deferred.
