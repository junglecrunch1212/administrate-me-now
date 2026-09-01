# CO-022 — Human-centered Triage management and learning

Status: **IMPLEMENTED IN PHASE A CANDIDATE; LIVE ACTIVATION DEFERRED**

Date: 2026-09-01

Governing decisions: ADR-0008, CO-011, CO-014, CO-021, AC-51

## Objective

Make Triage an explainable member decision workspace for admitted obligations,
separate alerts from ordinary work, keep permitted context searchable, preview
historical reassessment without mutation, and turn verified member outcomes
into narrow reversible source-policy rules. This change adds no universal
Matter aggregate, provider authority, model authority, source rewrite, or live
deployment.

## Human queue and disposition

When both reconciliation admission and the new G04 runtime behavior are
explicitly enabled, ordinary Triage contains only proposals with an active
`admit`, `conflict_review`, or `unresolved` receipt. The deterministic member
lanes are, in order:

1. direct authenticated member instructions;
2. member promises and commitments;
3. known-human actionable requests;
4. scheduled work and verified deadlines;
5. waiting and follow-up;
6. clarification and conflict;
7. alerts and safety; and
8. low-confidence review.

`alert_only` is presented in the separate alerts lane. `context_only`, exact
evidence attachment, existing-matter update, satisfied, superseded, duplicate,
and source-policy-suppressed results stay outside the ordinary queue. Provider
promotion, spam, routine update, receipt, bulk notification, and incidental
context therefore cannot crowd ordinary work without independent typed
obligation evidence. Comms Focus v1 in this mode requires an admitted ordinary
matter; other authorized messages remain searchable, and alerts retain a
separate view.

## Event-sourced member actions

`triage.item.managed@v1` records an exact member-private action, expected row
revision, predecessor action, reasons, evidence, time, and fixed declarations
that the action is reversible and has no provider effect. Its action vocabulary
is link existing, mark handled, mark duplicate, keep context, route alert,
dismiss not useful, propose suppression, ask clarification, snooze, reopen,
correct extraction, and correct ownership. Existing enrich and confirm flows
continue to own create/enrich and canonical materialization. A member cannot
assign private evidence to another adult through ownership correction, and a
reopen cannot reverse confirmed/provider-terminal truth.

Triage v11 projects current management state and retains every action in a
separate audit table. Compare-and-apply uses the exact last event, action
revision, and predecessor action so stale or crossed commands fail closed.
Nothing in this lifecycle contacts a provider.

## Backlog reassessment preview

`triage-backlog-reassessment-preview-v1` computes stable old and proposed
dispositions from immutable proposals and their latest scoped reconciliation
receipts. It returns deterministic reason/evidence lineage, exact source
coordinate, rule/source grouping, a preview digest, a cursor bound to the
inventory fingerprint, and batches capped at 100 from a capped inventory read.
The endpoint appends no event, deletes no source, performs no mass dismissal,
has no Phase A apply operation, and reports zero provider effects. A changed
inventory makes a continuation cursor stale instead of silently changing the
preview.

## Reversible member-scoped learning

The learned-rule lifecycle is explicit:

`verified member outcome -> preview/diff -> propose -> approve, edit, trial,
reject, or suppress -> bounded activation -> retain, narrow, suspend, or revoke`

`triage.source_policy_rule.proposed@v1` requires at least one exact projected
feedback event, a digest-only scope, affected candidate IDs capped at 100, and
an exact narrow-only diff. A proposal is inert and structurally fixes self
activation, access widening, cross-member effect, and provider effects to
false. `triage.source_policy_rule.decided@v1` requires the same preview digest;
only an explicit self-member approve or a non-empty bounded trial creates a
rule. `triage.source_policy_rule.reviewed@v1` supports retain, narrow, suspend,
and revoke; negative evidence may only narrow, suspend, or revoke.

Context Fabric v4 stores proposals and rules as member-private digest/evidence
indexes. Rules may narrow ordinary admission or retain permitted context; they
cannot widen read scope, create credentials, authorize provider writes, move
private content across adults, or collapse domain truth. The member APIs reject
view-as and cross-adult decisions.

## Runtime, UX, and safety

`ADMINME_HUMAN_TRIAGE_MANAGEMENT_ENABLED` defaults to `0`. Disabled operation
preserves the CO-021/legacy queue and rejects management or rule activation.
Preview and inert proposal construction remain observable without a live
effect. The Triage surface explains lanes, consequences, reversibility, source
evidence, read-only backlog preview, and local management. Comms exposes Alerts
and explains when Focus requires admitted matters. Controls are semantic native
buttons/details/forms with live status regions and retain the established
keyboard, reduced-motion, safe-DOM, responsive, and private-scope contracts.

## Evidence

`tests/unit/test_human_triage_management.py` proves all eight lanes, bounded
preview stability, schema prohibitions, reversible action replay, member
isolation, admitted-only queueing, separate alerts, no self-activation, bounded
trial, negative suspension, and admitted-only Comms Focus.

`tests/acceptance/test_human_centered_triage_management.py` drives the production
composition root. A 105-candidate historical inventory becomes two ordinary
obligations and one alert; preview leaves the event count unchanged; manage and
reopen append exact events; another adult cannot decide the rule; a verified
outcome becomes an inert proposal, then a bounded trial, then a negative-evidence
suspension; restart replay is equivalent; and provider-command effects remain
zero. The member asset contract covers the accessible Alerts, management,
preview, consequence, and Focus explanations.

## Migration and rollback

The change is additive. Context Fabric advances from v3 to v4 and Triage from
v10 to v11; both remain disposable projections rebuilt from the encrypted log.
New columns and tables are added after legacy schema open so existing projection
databases migrate safely. Older releases may ignore the four new event types.
Rollback selects the predecessor release and rebuilds its projections; no
source, canonical object, member decision, credential, provider configuration,
checkpoint, or provider state is rewritten.

## Constitutional disposition

- **PRESERVE:** append-only source truth, deterministic scoped projections,
  member privacy, human confirmation, distinct domain models, one Python
  runtime, pinned OpenClaw boundary, observation, and every provider-write
  guard.
- **MODERNIZE:** admitted matters receive human-centered lanes; local Triage
  actions and narrow learning become typed, replayable, reversible lifecycles;
  Comms Focus derives from admitted obligations.
- **SUPERSEDE:** a flat candidate-heavy ordinary queue, alert/context material
  masquerading as ordinary work, opaque one-click learning, and self-activating
  sender-wide suppression.
- **DEFER:** live flag activation, backlog application/mass mutation,
  deployment, provider enrollment/write enablement, runtime/dependency
  migration, and every provider effect require separate owner authorization.
  Required Phase A queue, preview, action, learning, privacy, replay,
  accessibility, and zero-effect behavior is not deferred.
