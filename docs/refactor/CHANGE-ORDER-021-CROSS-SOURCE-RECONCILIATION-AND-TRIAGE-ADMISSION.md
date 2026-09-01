# CO-021 — Cross-source reconciliation and Triage admission

Status: **IMPLEMENTED IN PHASE A CANDIDATE; LIVE ADMISSION DEFERRED**

Date: 2026-09-01

Governing decisions: ADR-0008, CO-011, CO-014, AC-50

## Objective

Turn authorized source facts and human-gated candidate proposals into one
deterministic, replayable admission decision without creating a universal
Matter aggregate, a second source of truth, model authority, or provider
authority. Reconciliation is an extension of the existing Context Fabric and
Triage projections. Canonical Tasks, Commitments, Calendar events, Reminders,
List items, Recurrences, provider objects, and source facts remain distinct.

## Evidence and authority

`triage.reconciliation.decided@v1` records one immutable decision revision for
one proposal. Its relationship vocabulary is explicit:

- exact duplicate;
- same provider object or provider conversation;
- exact external identifier or canonical object link;
- exact payment, account, or appointment reference;
- strong deterministic correlation;
- weak review suggestion;
- contradiction;
- satisfaction or completion; and
- cancellation or supersession.

Exact provider and typed deterministic evidence may consolidate decision work.
Provider conversation identity alone is recorded but does not establish matter
identity. Weak token overlap is always `review_suggestion`, has
`automatic_effect:false`, and is structurally prohibited from changing
identity, transferring ownership, widening visibility, or authorizing action.
Model output and scalar confidence have no reconciliation authority.

The durable decision vocabulary is `admit`, `update_existing_matter`,
`attach_evidence`, `alert_only`, `context_only`, `already_satisfied`,
`superseded`, `duplicate`, `conflict_review`, `source_policy_suppression`, and
`unresolved`. Every decision retains policy origin/revision, exact source
coordinate, freshness, evidence event IDs, typed relationships, reason codes,
priority basis, decision revision, predecessor decision, and zero-provider-
effect declarations.

## Privacy and shared state

Private reconciliation reads only the same tenant and exact private member
scope. Another member's raw message, account, payment, appointment, or source
body cannot satisfy, cancel, merge, or reprioritize the first member's work.
Cross-member state is admitted only through
`context.reconciliation.typed_fact_shared@v1`, whose envelope is household-
shared and source-member-authored and whose payload contains only typed state,
content-free reference digests, explicit target members, and a share-grant
reference. The schema fixes raw-body inclusion, owner transfer, and action
authority to false. The Context Fabric stores only typed coordinates, digests,
reasons, and lineage; it does not copy source bodies.

Known-human requests require an exact prior outbound endpoint match in the same
private scope. The decision lineage includes that prior interaction event ID.
An outgoing member promise becomes a typed Commitment candidate and carries a
content-free digest of the exact authored source text. Repeated names, sender
claims, provider labels, subject similarity, or model claims do not establish a
person or relationship.

## Priority and admission

Priority is code-owned. The only bases are authenticated member instruction,
exact member-authored commitment, verified known-human actionable request,
typed deadline, current state and consequence, explicit household policy, and
the standard actionable fallback. The deterministic order places direct member
instruction, member promise, and known-human request ahead of deadline-based
provider work. Sender/provider urgency, provider category, formatting, security
vocabulary, model confidence, and source volume are retained only as
non-authoritative evidence and never enter the sort key.

Security-alert-only evidence becomes `alert_only`, not an ordinary task.
Promotions, receipts, spam, bulk updates, and incidental People candidates
become context-only or source-policy-suppressed unless independent typed
relationship value exists. Exact duplicate healthcare app/SMS/email evidence
collapses into one private review matter. Contradictory typed fields remain one
explicit conflict for member review. Later authorized satisfaction,
completion, cancellation, or supersession appends a correction revision rather
than rewriting the earlier decision.

## Runtime and projection contract

One durable-bus consumer reads the encrypted event log and appends admission
receipts. It calls no model and no provider. Context Fabric v3 adds disposable,
rebuildable reconciliation decision, relationship, and typed-share indexes.
Triage v10 records the latest receipt and, only when the explicit runtime gate
is enabled, applies its queue disposition. The runtime flag
`ADMINME_RECONCILIATION_ADMISSION_ENABLED` defaults to disabled. A disabled
receipt remains observable but does not mutate the existing queue state.

The production-composition proof covers mortgage/budget/private payment plus
explicit typed sharing; healthcare app/SMS/email collapse; direct member
instruction, member promise, known-human request, and provider-deadline order;
security alert; promotion, receipt, spam, provider update, and People admission;
weak suggestions; contradiction; satisfaction; supersession correction;
projection replay; default-off behavior; privacy; and zero provider effects.

## Migration and rollback

This change is append-only. No source fact, canonical object, member decision,
credential, provider configuration, or historical event is rewritten. Older
code may ignore the two new event types. The two affected projections are
disposable and rebuildable from the encrypted log. Rollback therefore selects
the predecessor release and rebuilds its projections if needed; it never
reverses provider state because this change has no provider seam.

## Constitutional disposition

- **PRESERVE:** append-only truth, deterministic projections, member isolation,
  human confirmation, distinct domain models, one Python runtime, the pinned
  OpenClaw boundary, and all provider-write guards.
- **MODERNIZE:** exact-source policy receipts become replayable admission
  decisions; Context Fabric gains a typed reconciliation index; Triage gains
  deterministic matter admission, correction, and priority provenance.
- **SUPERSEDE:** exact-title-only duplicate handling, security-first queue
  ordering, unproven known-sender priority, and provider/model urgency as a
  plausible ranking signal.
- **DEFER:** live activation, historical-backlog mutation, deployment,
  provider enrollment, scope change, runtime/dependency migration, and every
  provider effect require separate owner authorization. Required Phase A
  privacy, replay, decision, and zero-effect behavior is not deferred.
