# CO-020 — Human-Centered Triage Workspace

Status: **PHASE A VERIFIED CANDIDATE; NO DEPLOYMENT AUTHORIZED**

Date: 2026-08-30

Governing sources: ADR-0008, D26, CO-011, CO-012, CO-014, CO-015,
CO-016, UX-39–UX-59, and the acceptance and bootstrap contracts.

## Objective

Make Triage read like a calm human decision workspace while preserving the
complete, replayable candidate inventory underneath it. A member should first
understand what needs a decision, why it needs attention, and what confirming
or dismissing will do. Projection accounting, extraction mechanics, adapter
coordinates, and bulk backlog management remain available but do not compete
with that first decision.

This change begins at the authenticated member surface and changes backend
classification or read models only where the rendered experience proves they
are necessary. It does not authorize deployment, provider enrollment,
automatic confirmation, automatic suppression, or any external write.

## Human audit findings

The production composition was rendered with synthetic data at desktop and
390 px widths in empty, ordinary, mixed-readiness, and 530-candidate states.
The baseline demonstrated these defects:

1. System accounting appears before the work. Members encounter a human-gate
   slogan, source-assurance counts, complete inventory counts, learning
   controls, and eight wrapping lenses before the first decision.
2. A selected candidate opens a large edit form immediately. The source
   explanation, likely effect, and primary decision are visually subordinate
   to implementation fields and destination mechanics.
3. Queue cards expose system vocabulary such as `matter`, `candidate`, generic
   lane names, and raw extraction reasons. Adapter and account identifiers are
   more prominent than sender, source time, or actual due date.
4. At narrow width the global navigation remains sticky and consumes a large
   portion of the viewport. The wrapped lens row then pushes every decision
   below the initial fold.
5. At large scale the surface asks the member to load pages “toward zero.”
   Loaded-window counts appear beside complete inventory counts, turning an
   audit/recovery guarantee into an implied personal inbox-zero obligation.
6. `queue_lane` treats the presence of a nullable date key as a date fact.
   Candidates with `due: null` therefore appear time-bound, weakening trust in
   the very signal meant to explain priority.
7. Gmail category labels are retained as provider observations but disappear
   before Triage presentation. Conversely, unrestricted provider labels would
   be unsafe as ranking or suppression authority and could expose arbitrary
   user-created label text.

## Decisions

### Decision queue versus complete inventory

- The default lens is named **Needs attention**. It contains independently
  decidable candidates with an explicit non-standard attention reason, even
  when one still needs details before confirmation. Standard candidates live
  in **Low signal** and do not simultaneously inflate Needs attention; the
  separate readiness lenses may overlap either attention group.
- Needs attention retains the reviewed deterministic lane order and oldest-
  first anti-starvation within a lane. Arbitrary sorting is not offered there:
  a member sort must never demote a security, time, money, promise, explicit
  priority, or recurring-exception signal.
- **All candidates** remains the complete recovery/audit surface. This is the
  right place for oldest/newest exploration and source/type filtering. Any
  filter is read-only presentation over the same scoped projection; it cannot
  change admission, evidence, state, or authority.
- The complete inventory remains reachable and accurately counted, but it is
  disclosed as system accounting rather than presented as the member's
  required workload. Pagination means “show more,” never “work toward zero.”
- A source's first sync does not require a member to adjudicate every source
  observation. Only policy-eligible derived candidates enter Triage. Bounded
  source acquisition, quiet/manual/review source settings, complete inventory,
  and a separated low-signal lens provide training without manufacturing a
  mandatory zero-out ritual.

### Ranking, grouping, and learning

- Priority remains code-owned and explainable. Only meaningful, non-empty
  typed date fields count as time facts. A small reviewed security phrase set
  may classify an already-admitted candidate; it does not admit a source
  observation by itself.
- Matter grouping remains limited to exact provider conversation/source
  coordinates and independently reviewable children. Triage may visually
  section cards by deterministic attention reason. It must not merge similar
  cross-provider notices merely because their titles look alike.
- Uniform bulk confirmation is prohibited. Bulk dismissal/suppression remains
  deferred until a typed, reversible, auditable event contract can preserve
  per-candidate feedback and privacy. “Dismiss & teach” continues to affect one
  exact candidate and only suggests a visible shadow rule after repeated
  outcomes.
- Gmail system categories may be shown as provider-supplied context and used
  to make low-signal browsing legible. Promotions, Social, Updates, Spam, and
  similar labels do not prove irrelevance, urgency, ownership, or lifecycle;
  they cannot admit, hide, rank, confirm, dismiss, or suppress a candidate.
  Arbitrary user label names are not published by the member API.

### Information hierarchy and language

- The surface opens with the count and plain-language description of decisions
  that need the member. The first decision is visible without traversing
  inventory prose. Empty state says the member is caught up without implying
  that background accounting has disappeared.
- Lens controls remain all available as required by UX-51, but form one compact
  horizontally scrollable row at narrow width instead of a vertical wall.
- Cards show the human reason for attention, actual date where present, source
  channel/category, counterparty, and relationship size. `matter`, raw reason
  codes, adapter names, and provider-account identifiers are not first-pass
  language.
- Detail begins with **Why this is here**, exact source evidence, and the
  proposed effect. Provider classification is explicitly labelled as a
  provider observation. Adapter/account coordinates move into a technical
  disclosure.
- Ready candidates expose the primary confirm/dismiss decision before optional
  editing. Full proposal and destination fields sit in **Edit details**;
  candidates that need details open that disclosure by default.
- Narrow Triage navigation is not sticky. The queue, not persistent chrome,
  owns the viewport after the member scrolls.

## Backend and projection consequences

The minimum required backend work is:

1. correct deterministic lane classification for nullable fields and reviewed
   sign-in/security phrases;
2. derive complete, scoped counts for every decision lens rather than calling
   a cursor-window count complete;
3. retain a deterministic source identifier in the rebuildable Triage
   projection so advanced inventory filters work across the full inventory and
   survive replay;
4. bind pagination cursors to the active inventory sort and filters as well as
   the unresolved-queue revision;
5. expose only reviewed Gmail system-category observations from authorized
   source context; and
6. keep all filtering, category presentation, and counts read-only, scoped,
   deterministic, and outside the event-authority path.

No provider SDK, second model route, source-of-truth projection, new central
runtime, automatic candidate decision, or provider effect is introduced.

## Acceptance

Phase A evidence must prove through the production composition root:

- empty, ordinary, mixed-lane, low-signal/category, 500+ backlog, multi-child,
  exact deep-link, recent-history, and post-decision continuity states;
- desktop and 390 px layouts with the first decision visible and no horizontal
  overflow, inaccessible action, clipped content, or sticky-navigation trap;
- deterministic lane correction, complete lens/facet counts, oldest/newest
  inventory pagination, source/type filters, stale-cursor rejection, replay,
  encrypted reopen, and member isolation;
- provider categories never change admission, lane, confirmation, dismissal,
  policy, or external-write behavior;
- exact per-child confirmation/dismissal settlement and zero unintended
  provider effects; and
- the supported Python, Node, invariant, pack, and acceptance gates, with any
  environment-specific skip or failure recorded verbatim.

Credential-free production-composition evidence is retained in
`docs/refactor/evidence/triage-human-workspace/README.md`. The rendered Gate 7
run passed 696/696 assertions under Node 24 and Python 3.14, including the 520+
inventory, Gmail category, narrow/reflow/accessibility, exact-decision, and
browser-error boundaries. This evidence does not authorize Phase B or any
live-instance change.

## Constitutional disposition

- **PRESERVE:** append-only truth, complete candidate inventory, fixed
  explainable attention lanes, oldest-first anti-starvation, independent child
  decisions, typed dismissal, reversible shadow learning, source provenance,
  privacy/isolation, observation mode, and external-write governance.
- **MODERNIZE:** Triage from projection-first accounting to decision-first
  information hierarchy; source context from technical coordinates to human
  explanation; low-signal/category browsing from an overlapping loaded-window
  lens to a distinct complete-inventory view; advanced inventory from one
  fixed order to cursor-safe read-only exploration.
- **SUPERSEDE:** `due: null` as a time fact, raw rule-code presentation,
  adapter/account-first source headings, stacked narrow lens controls, sticky
  narrow Triage navigation, overlapping Ready/Low signal membership, and
  “continue toward zero” pagination language.
- **DEFER:** semantic cross-provider grouping, heterogeneous batch decisions,
  automatic enforcement of learned rules, and provider-label-driven
  suppression. None is required for complete inventory, usable individual
  review, replay, isolation, or safety.
