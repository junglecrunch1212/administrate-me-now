# ADR-0008 — Deterministic temporal Context Fabric

Status: **Accepted**

Date: 2026-08-10

## Context

Provider threads, people, notes, calendar facts, financial signals, candidates,
and canonical work form useful relationships, but none of those relationships
may become a second source of truth. A flat candidate list also scales with the
number of extraction outputs rather than the number of decisions a member can
make. Long-lived message threads make provider conversation identity an unsafe
proxy for current subject matter.

## Decision

AdministrateMe adds one first-party `context_fabric` SQLCipher projection. It is
a typed temporal relationship index rebuilt from immutable events. It stores
references and reason codes, never source bodies. It has no write seam.

Authority is an enum, not a score:

- `provider_exact`: exact provider conversation identity;
- `member_confirmed`: explicit member context/policy decision;
- `deterministic_derived`: reviewed code over typed facts;
- `llm_proposed`: non-authoritative semantic suggestion.

Authoritative bounded traversal excludes `llm_proposed`. It also requires the
exact tenant, owner member, private owner scope, and private visibility scope.
Depth is capped at four and returned nodes at 500.

Provider conversation and member matter are separate levels. Triage's code-
owned matter episode reuses a provider thread for at most 30 elapsed days or
100 distinct source observations. This is deliberately conservative: the
system splits safely and lets a member merge; it never weakly merges and asks a
member to discover the privacy or meaning error later.

## Rejected alternatives

- A graph server, RDF store, or vector database adds an unnecessary runtime,
  operational failure domain, and authority ambiguity at household scale.
- A universal Project or Work graph collapses distinct event, Task,
  Commitment, Calendar, List, Recurrence, and scheduler semantics.
- Model-owned context memory, topic change, priority, or suppression is
  probabilistic, difficult to replay, and incompatible with human authority.
- Provider thread alone cannot represent changing iMessage/group-chat topics.
- Participant overlap, shared address, generic subject, transitive weak edges,
  or semantic similarity alone can create giant or privacy-crossing components.

## Consequences

The default registry grows from fourteen to fifteen projections. Projection
storage and checkpoint work increase, so release evidence must prove replay,
bounded traversal, storage/query budgets, migration, backup/restore, and Doctor.
The benefit is a complete candidate inventory, a bounded member decision queue,
explainable relationships, reversible learning, and deterministic two-year
behavior without a second truth store or expanded LLM authority.
