# CO-014 — Deterministic Context Fabric, Candidate Inventory, and Triage Zero

Status: **ACTIVATED IN OBSERVATION MODE; LIVE GATES PASS**

Date: 2026-08-10

Governing decision: ADR-0008

## Objective

Make multi-source context durable and useful without making a language model,
graph database, or projection authoritative. Preserve every eligible candidate
while changing the member workload from an unbounded flat row list into a
bounded queue of independently reviewable matters.

The platform now distinguishes:

1. **Candidate Inventory:** the complete, cursor-paginated set of unresolved
   candidate records. It is an audit/recovery surface, not the member workload.
2. **Triage Decision Queue:** one deterministic first-pass card per bounded
   matter, with all underlying candidates nested and independently decidable.
3. **Context Fabric:** a fifteenth SQLCipher projection containing typed,
   temporal, scoped identifiers and relationship edges. It is disposable and
   rebuildable from the event log.

## Decisions

- No Neo4j, RDF service, vector database, new runtime, model SDK, model router,
  generic memory, Project aggregate, or per-channel agent is introduced.
- Provider conversation identity is exact provenance, not matter identity.
  Triage divides continuous conversations into deterministic episodes at 30
  elapsed days or 100 distinct source observations. A member can explicitly
  confirm, revoke, merge, or split context membership through typed events.
- Model-produced semantic relationships have `llm_proposed` authority and are
  excluded from authoritative traversal. The model cannot choose a context,
  owner, scope, route, priority, suppression, merge, policy, or write.
- Weak participant, address, generic-subject, transitive, or vector-only links
  cannot establish context identity. Cross-member/account/workspace/sensitivity
  traversal fails closed.
- Exact duplicates remain candidate records but become nested evidence instead
  of additional decisions. High-precision exact matches to existing Tasks,
  Calendar events, Reminders, and Recurrences attach evidence to the existing
  object. Ambiguity remains human review.
- Scalar model confidence is retained as historical extraction metadata only.
  Member presentation uses separate evidence, owner, lifecycle, queue-lane,
  existing-object, route-readiness, source-freshness, and policy states.
- Dismissal records a typed reason atomically with the decision. Three repeated
  scoped outcomes may suggest a rule. A member must preview and approve it;
  approved rules are reversible and **shadow-only** in this rollout. They cause
  no suppression, confirmation, routing, or provider effect.

## Triage member contract

The default workspace is matter-first master/detail. It includes Ready, Needs
details, Needs interpretation, Low signal, Recurring exceptions, Learned rules,
All candidates, and Recent lenses. Counts state the complete inventory, exact
duplicate evidence, and existing-object evidence. Stable continuation cursors
make every candidate reachable; a hard UI limit may not masquerade as the
complete queue. Confirming one child never confirms another and heterogeneous
bulk confirmation is prohibited.

Queue lanes are code-owned and based on explicit due/security/financial/
member-promise/member-priority/recurring-exception facts. Within those fixed
lanes, oldest first provides anti-starvation. Model confidence is neither an
admission threshold nor a sort key.

## Event and projection contract

The new explicit decision events are:

- `context.membership.confirmed`
- `context.membership.revoked`
- `context.merged`
- `context.split`
- `triage.feedback.recorded`
- `triage.policy_rule.approved`
- `triage.policy_rule.revoked`

Derived nodes and edges do not append events. Every node and edge records
tenant, private owner/visibility, and sensitivity; source nodes also retain
provider/account/workspace coordinates when available. Edges retain type,
authority, reason codes, source event IDs, algorithm/schema version, temporal
validity, status, and revocation. Source bodies are not copied into the fabric.

The `context_fabric` projection is the fifteenth registered projection. It is
not a source of truth and cannot write to the event log. Doctor, backup/restore,
replay, invariants, Settings, and topology checks use the exact fifteen-name
registry.

## Acceptance and rollout

AC-39 owns this change order. Required evidence includes:

- exact replay and encrypted reopen of both Triage v7 and Context Fabric v1;
- complete, duplicate-free stable pagination through production HTTP;
- multi-member isolation and bounded traversal excluding model-suggested edges;
- temporal matter bounds and no weak-edge giant component;
- exact duplicate/existing-object/recurrence reconciliation without truth loss;
- typed feedback, stale-preview rejection, reversible shadow-rule approval,
  and zero provider effects;
- fixed-seed low/base/high 730-day simulations for two members with 100% high-
  priority admission, zero missing inventory, bounded base backlog, storage,
  and query estimates;
- full release gates, clean migration, backup/restore, Doctor, and browser
  desktop/narrow review.

Phase A is disposable/shadow only. Phase B begins only after reviewed merge and
uses observation mode. Flat candidate inventory remains an advanced recovery
lens. No provider write or automatic confirmation is authorized by CO-014.

## Phase B activation evidence

PR #327 merged the reviewed implementation as `4f42ad0`. The guarded updater
created encrypted rollback backup
`administrateme-20260811T004911Z.ambackup`, added Context Fabric without a
runtime migration, and settled Doctor at 15/15 projections. Authenticated live
review then found two presentation defects rather than hiding them: cursor-
window counts were labelled as complete, and long source metadata could widen
a 390 px phone document. PR #328 made complete inventory versus loaded-window
counts explicit; PR #329 bounded and wrapped Triage matter-card content.

The final reviewed `main` release is `0e0f13e`, activated through the attested
same-runtime updater after encrypted backup
`administrateme-20260811T013510Z.ambackup`. Independent final status and Doctor
are healthy in observation mode: 15/15 projection stores/checkpoints, 8/8
required pipeline subscribers, 62,573/62,573 source obligations accounted for,
zero pending/missing/failed obligations, exact pinned OpenClaw and executable
`llm-task`, and zero synthetic external side effects. Authenticated Triage
showed the complete 474-candidate/213-matter inventory, advanced from 65/474 to
124/474 across two stable cursor pages and 60 rendered matters, preserved
independent nested decisions, and measured 375 px of document content in a
390 px phone viewport. The final Gate 7 run passed 225 assertions with 36
screenshots and zero console, page, or request failures.

## Constitutional disposition

- **PRESERVE:** C001–C109, append-only truth, source authority, isolation,
  human gating, one runtime, OpenClaw ownership, typed domain distinctions, and
  every existing projection capability.
- **MODERNIZE:** context from transient card metadata to a rebuildable temporal
  relationship index; Triage from a truncated flat list to inventory plus
  matter queue; dismissal from terminal-only to typed reversible feedback.
- **SUPERSEDE:** exact-fourteen projection count, hidden 100-row pagination,
  provider-thread-as-lifelong-matter, scalar-confidence presentation, and
  non-learning generic dismissal.
- **DEFER:** enforcement of approved feedback rules and broader provider
  actions until separate evidence and approval. Required inventory, isolation,
  replay, human review, and provider-effect safety are not deferred.
