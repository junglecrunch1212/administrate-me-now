# B03 single-use Google Tasks creation

Status: Phase A implementation contract. This document does not authorize deployment,
credential changes, provider-scope changes, or a live Google task.

Governing baseline: protected-main B02 release
`d4ab34a66530b2bef5c770185e8b6672edd220d1`. The architecture and safety
contracts in this directory remain authoritative.

## Exact production-v2 envelope

B03 adds one action, `google_tasks_create`, under execution policy
`google-tasks-create-production-v2`. It does not modify or reinterpret any
`delegated-fixture-execution-v1` event. The production envelope binds one command to:

- the stable represented CoS and actual OpenClaw automation operator;
- one current service delegation carrying `execute_google_tasks_create_trial`;
- one effective operating agreement naming `google_tasks_create`, `create_task`, the
  exact service-owned account, the pre-existing dedicated list, supervised-trial
  stage, independent OpenClaw approval, and an occurrence ceiling of one;
- the resource owner, provider owner, member or household context, Google Tasks
  adapter, exact account and task-list coordinates;
- the canonical fingerprint of the exact allowlisted task fields; and
- one validity window, envelope revision, idempotency key, and maximum effect count.

The new permission is not a generic live-action or provider-write permission.
`execute_fixture_actions` remains fixture-only. Every other Google Tasks mutation,
Gmail mutation, Calendar mutation, messaging action, recurrence, and bulk operation
remains outside this contract.

## Approval and governance order

The member authority endpoint records the AdministrateMe-controlled domain approval.
OpenClaw then prepares the immutable command. Execution requires a separately signed,
short-lived, allow-once OpenClaw receipt over the exact execute request. A changed
command, actor, represented CoS, scope, owner, provider, account, adapter, destination,
content fingerprint, expiry, or effect ceiling fails closed.

The execution path preserves this order: allowlist, AdministrateMe domain governance,
rate limit, independent OpenClaw approval, final privacy/content guard, scoped
observation admission, provider contact, terminal audit, later normal-source
observation, then reconciliation.

## Scoped observation admission

Global observation mode remains active. The only exception is an in-memory value bound
to the already durable contact-attempt event and the exact command, initiating event,
envelope revision, both approvals, provider/account, operation, destination, content
fingerprint, owner/visibility scopes, expiry, and effect count of one. The final
outbound filter validates every coordinate and emits `external.sent@v3`, explicitly
recording that observation remained active and that this admission was consumed.

The admission is neither ambient authority nor available to polling, another command,
another adapter, or another operation. All unrelated outbound calls remain suppressed.

## Contact lifecycle and ambiguity

Immediately before the adapter call, the dispatcher durably appends
`cos.google_tasks_execution.contact_attempt_committed@v2`. Its idempotency key is
unique per command. A returned provider response is then durably recorded as
`cos.google_tasks_execution.provider_result_received@v2` before terminal success. On
event redelivery or restart, an existing terminal event makes delivery a no-op; a
durable provider result recovers terminal success without contacting Google again; and
a committed contact without durable result evidence becomes
`ambiguous_after_possible_contact`. Automatic retry is forbidden, and later provider
observation or explicit manual resolution is required for ambiguity.

The durable event log preserves possible-contact evidence across rollback. The
projection distinguishes held, approved, contact committed, provider result, terminal
success, pre-contact failure, ambiguity, later observation, reconciliation,
drift/conflict, and revocation.

## Terminal evidence, observation, and reconciliation

A successful terminal event retains the native task ID, list ID, secret-safe account
coordinate, returned revision/etag and status, desired and returned fingerprints,
contact time, command/envelope lineage, and an effect count of one. It does not retain
OAuth material, service-account keys, or an unnecessary public content copy.

The write response is not an observation. The existing Google Tasks polling path must
later emit an owner-aware `list_item.*` fact. The B03 reconciler correlates provider,
account, owner, list, native ID, revision, and desired fingerprint. Exact independent
state yields `matched`; missing/deleted state yields `missing`; a provider edit yields
`drifted`; multiple matching objects yield `conflicted`; an uncertain post-contact
state remains `ambiguous`; and processing failure yields `failed`. Reconciliation has
no provider-write dependency or capability.

## Settings and Doctor

System diagnostics and the member execution inventory show production-v2 support,
action, secret-safe scope/account/destination classes, envelope revision/window,
used/expired/revoked state, both approvals, contact, terminal, later-observation and
reconciliation states, and the continued global observation requirement. Private task
content is never returned. B03 readiness degrades when an exact binding, approval,
idempotency boundary, terminal path, observation path, or reconciliation path is
unavailable. Absence of a trial remains truthfully `not_configured`, not a regression
in the accepted read-only baseline.

## Migration, replay, backup, and rollback

Event-log migration 0005 adds only a recovery index over immutable B03 evidence; it
does not create an envelope or enable a provider permission. Tasks projection v8 adds
separate production-v2 tables and replays retained events. Fixture-v1 tables and event
schemas are unchanged. Restart before execution preserves readiness; restart after a
contact commit refuses another insertion; restart after success preserves used state;
and restart before observation preserves reconciliation eligibility.

Before Phase B, take and decrypt an encrypted backup, complete a restore rehearsal, and
validate the guarded rollback target. Rollback must never erase possible-contact,
terminal, observation, or reconciliation evidence.

## Phase B authorization boundary

Phase A completion is `CODE_COMPLETE_AWAITING_DEPLOYMENT_AUTHORIZATION`. Phase B begins
only after merge to protected main and a separate owner request naming or approving the
immutable merge commit, represented CoS, member/household scope, service-owned account,
pre-existing dedicated list, exact task content and fingerprint, trial window, one
effect maximum, exact Google Tasks scope change, revocation instruction, and stop
conditions. If no pre-existing list is selected, stop; `google_tasks_create` never
authorizes list creation.

Deployment uses the guarded updater from the immutable merge commit. It must verify the
runtime pins, settled B02 sources, encrypted backup/restore, observation mode, exact
binding, both approvals, idempotency/contact persistence, terminal and observation
paths, tenant/privacy isolation, authenticated read-only browser acceptance, unchanged
member-bridge count, and zero Apple, Messages, Gmail, Calendar, or unintended provider
effects. Provider permission changes and credential rotation require their own explicit
owner authorization. No successful trial promotes itself.

## Semantic decisions

- **PRESERVE:** G01-G11 and B01-B02 behavior, event-sourced truth, encryption,
  deterministic projections, fixture-v1 execution, read-only Google sources, global
  observation, isolation, governance order, OpenClaw routing, and secret-free audit.
- **MODERNIZE:** separate production-v2 schemas, full owner/destination dispatch,
  provider-native terminal evidence, scoped outbound admission, durable ambiguity
  protection, and exact Settings/Doctor reporting.
- **SUPERSEDE:** only fixture-only execution, service-owner read-only, and global
  suppression for one valid `google_tasks_create` command with a consumed admission.
- **DEFER:** every other provider mutation, recurrence/bulk behavior, messaging and
  Apple expansion, member bridge enrollment, and general production execution.

## Phase A acceptance

`tests/acceptance/test_b03_google_tasks_create_production.py` exercises the production
composition root without credentials or network access. It proves independent gates,
one effect while global observation remains active, durable contact evidence, native
provider-result and terminal evidence, replay refusal, later observation, matched,
drifted, and missing reconciliation, final revocation, truthful diagnostics, and
unchanged bridge/Messages defaults.
`tests/unit/test_google_tasks_execution.py` covers coordinate, content, permission,
destination, owner, effect-ceiling, and scoped-admission failures. The full supported
Python, invariant, and pinned OpenClaw checks remain required for delivery.
