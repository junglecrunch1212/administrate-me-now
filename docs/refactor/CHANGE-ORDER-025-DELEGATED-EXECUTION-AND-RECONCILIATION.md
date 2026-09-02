# CO-025 — Delegated execution and reconciliation

Status: **IMPLEMENTED IN PHASE A WITH CREDENTIAL-FREE FIXTURE EFFECTS; LIVE WRITES DEFERRED**

Date: 2026-09-02

Governing decisions: ADR-0000, ADR-0001, ADR-0005, ADR-0008, CO-019,
CO-021, CO-022, CO-023, CO-024, AC-55

## Objective and boundary

G07 extends the CoS work and agreement kernel with exact delegated-action
preparation, approval, fixture dispatch, result accounting, later observation,
and reconciliation. It does not enroll a provider, alter a credential or
permission, disable observation on a real instance, deploy a release, or make a
live provider call. The only executable G07 effects are deterministic,
credential-free, in-memory fixtures composed by the production runtime when
its explicit deterministic-fixture setting is active.

The canonical object, recurrence occurrence, coordination record, desired
representation, actor, operator, represented CoS, active context, on-behalf-of
member, service grant, resource owner, provider-account owner, destination,
audience, domain approval, OpenClaw approval, idempotent command, terminal
result, later observation, and reconciliation remain distinct objects or
coordinates. Provider commands/results continue through `external_sync@v2`;
the new CoS events bind domain lineage around that existing spine rather than
replacing it.

## Authority and ordered execution

One exact active member-private service delegation must grant preparation or
fixture execution. Domain execution authority is then either one immutable
one-off member approval or exactly one current standing envelope. An envelope
binds the service grant, action class, canonical object, occurrence and series,
recurrence strategy, desired representation, provider family/account/owner,
destination, audience, representation kind, provider target/revision, desired
payload digest, and expiry. Zero matches require a member decision; multiple
matches are ambiguous and do not approve. Revocation or expiry removes future
applicability.

Dispatch preserves the reviewed order:

1. generic allowlist;
2. domain governance;
3. rate limit;
4. exact single-use OpenClaw approval;
5. final content/privacy digest check;
6. final observation filter;
7. fixture dispatch;
8. one terminal result when known;
9. later observation; and
10. reconciliation.

AdministrateMe domain authority never includes OpenClaw permission. OpenClaw
allow-always is not accepted by the pinned receipt contract, and an OpenClaw
receipt supplies no domain authority. The final observation filter can
suppress the fixture dispatch while retaining the fully attributed command and
terminal suppression result. If an effect is ambiguous because its terminal
result is missing, the command remains `awaiting_result` and its idempotency key
prevents an unsafe retry.

The service automation operator and simulated individual human operator use
the same grants, provider restrictions, member/OpenClaw approvals, command,
result, observation, and reconciliation path. Actor, operator, service Party,
active member, on-behalf member, authority grant, resource owner, and provider
owner attribution remain independently queryable.

## Action catalog and recurrence

The fixture-executable catalog traces to existing production adapters and exact
fixture destinations for:

- Google Calendar create, update, move, withdraw, and stable recurrence;
- Google Tasks create, update, complete, reopen, and withdraw;
- Google Tasks list create, update, and withdraw; and
- Google Tasks reminder create, update, complete, reopen, and withdraw.

This is fixture support, not live-write enablement. Apple Calendar and Apple
Reminders retain the accepted per-member bridge architecture but are
`dashboard_only` in G07. Draft/held communications retain `held_only` status
because no production draft adapter plus result/observation contract is
available. They are explicitly unsupported for execution rather than claimed
from schemas alone.

Stable recurrence uses provider-series identity. Variable cases use an exact
individual occurrence. Series and occurrence IDs survive projection rebuild.
An exact later completion or reopening of a canonical Task appends
`cos.delegated_action.canonical_outcome_returned@v1`; it does not collapse the
Task, provider object, recurrence, or reconciliation. A direct provider edit is
recorded as conflict; a non-edit mismatch is drift.

## States, ceilings, and failure closure

The projection retains all requested states: `prohibited`, `dashboard_only`,
`shadow`, `desired`, `awaiting_member_approval`,
`awaiting_openclaw_approval`, `dispatched`, `awaiting_result`,
`awaiting_observation`, `active`, `drifted`, `conflicted`, `failed`,
`suppressed`, `withdrawn`, and `reconciled`. Separate normalized tables retain
standing envelopes, desired actions, approvals, commands, results,
observations, reconciliations, and state history.

Money, nonrefundable travel, legal signatures, clinical decisions, child
consent, life-safety systems, public statements, another adult's sources, and
infrastructure administration are hard ceilings and prepare only as
`prohibited`. Stale facts, a changed policy digest, expiry, revocation,
ambiguous standing authority, wrong owner/destination/audience, missing domain
or OpenClaw approval, and degraded OpenClaw health fail closed before dispatch.
One adult cannot grant access to another adult's private provider account.
Member-owned and service-owned destinations have disjoint exact fixture
namespaces.

## Projection, replay, and rollback

No projection is added; the reviewed count remains fifteen. Tasks advances
additively from v6 to v7 with normalized G07 tables and subscriptions. New v1
CoS envelope/action events and the established `external_sync@v2` command and
result schemas are additive. Rebuild reconstructs authority, lineage,
recurrence, approvals, idempotency, result, observation, reconciliation, and
canonical completion/reopening from the encrypted append-only log. Older code
may ignore the additive event family. Rollback selects the protected G06/CO-024
predecessor and rebuilds disposable projections; it does not rewrite a source
fact, domain approval, command/result, provider state, credential, or
checkpoint.

## Constitutional disposition

- **PRESERVE:** distinct domain/provider/recurrence/scheduler objects,
  append-only truth, member privacy, exact ownership and audiences, independent
  AdministrateMe/OpenClaw authority, allowlist-to-reconciliation ordering,
  final observation behavior, one Python runtime, fifteen projections, and the
  pinned OpenClaw seam.
- **MODERNIZE:** CoS desired representations gain exact one-off/standing
  authority, attributed fixture command/result evidence, later observation,
  drift/conflict detection, and canonical Task outcome return.
- **SUPERSEDE:** implicit delegation, broad standing permission, destination or
  account inference, result-as-observation, ambiguous retry, provider edit as
  canonical truth, and simulated-human bypasses.
- **DEFER:** every live write, provider enrollment, scope/credential/permission
  change, deployment, runtime migration, and bridge/provider activation.

## Evidence

`tests/acceptance/test_cos_delegated_execution_reconciliation.py` drives the
production composition root with two adults, the CoS service, the simulated
human operator, the real guarded-write and OpenClaw approval seams, the final
observation filter, the existing external command/result spine, and the G07
fixture adapter. It executes every supported fixture action class, proves
exact effect/approval equality, one-off and standing closure, replayable
recurrence, canonical completion/reopening, observation suppression, missing
result and duplicate behavior, drift/conflict/failure, hard ceilings, exact
owner/destination isolation, and all fail-closed cases with zero live provider
effects.
