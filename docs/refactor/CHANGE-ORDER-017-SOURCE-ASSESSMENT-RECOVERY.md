# Change Order 017 — Terminal source-assessment recovery

Status: implementation candidate; not merged or deployed.

Governing sources: event-sourced source authority; D19 and D26; C002, C034,
C097, C106, C107, C109, and C113; AC-12, AC-18, AC-21, AC-24, AC-34,
AC-35, and AC-42; G-001 controlled release authority.

## Incident

The production source-assurance proof is complete except for four immutable
message assessments. Each source reached the noise-filtering generation-19
terminal attempt and recorded `language_seam_OpenClawUnreachable`. The ordinary
replay path then tried to reuse the generation-19 assessment idempotency key.
Because a later healthy language result would have a different payload, the
append-only log correctly rejected the reuse as an idempotency conflict. A full
pipeline replay therefore could not replace the occupied event, and no typed
operator path existed to request a fresh assessment identity.

This is a recovery-boundary defect, not permission to rewrite the four failed
events. The failed assessments remain valid historical evidence.

## Required behavior

- Recovery is requested by an authenticated adult operator for exact immutable
  source event IDs. It is never inferred from an arbitrary backlog and is not a
  member Triage decision.
- Every selected source must be a supported message source, private to the
  authenticated member view, non-privileged, and currently governed by an exact
  terminal failure whose reason is either `language_seam_OpenClawTimeout` or
  `language_seam_OpenClawUnreachable`.
- Validation is atomic for the batch. Source, failed assessment, request, and
  runtime session must retain one tenant identity. A stale, cross-tenant,
  cross-member, privileged, unsupported, or non-retryable target cannot enter
  the request.
- The request and any refusal contain identifiers, scope, policy, reason, and
  bounded-attempt metadata only. Source bodies, subjects, senders, account
  identifiers, and extracted text are excluded.
- Processing revalidates the immutable request against current event history
  and re-reads observation state at delivery time. An invalid, stale, or
  no-longer-observation-only request produces one terminal content-free refusal
  rather than a candidate, canonical object, provider action, or poisoned
  subscription.
- The request creates a request-specific assessment identity and invocation
  namespace. One base attempt and at most two transient retries run through the
  pinned OpenClaw `llm-task` seam, with no provider SDK or second model router.
  The request-bound base identity is explicitly allowed to bypass the selected
  predecessor's old causal failure once; its own durable terminal result still
  prevents repetition. Base, recovery-1, and recovery-2 each inspect their own
  exact terminal identity even when a later sibling attempt is the newest
  source-causal event, so a mid-retry restart cannot spend an attempt twice. At
  most two requests execute concurrently.
- A successful or newly failed result is appended as
  `triage.intent.features_extracted@v4`, linked to the superseded failed
  assessment and recovery request. The old failure is never updated or deleted.
- Source assurance selects the newest valid assessment for each exact source.
  A successful replacement clears that source's current failure; a persistent
  replacement failure remains explicit.
- Reusing the same request is idempotent across retries and restarts. Distinct
  command IDs selecting the same failed source are single-flight through the
  terminal append, so only one may supersede it. Recovery and refusal appends
  are ordered after their durable request even when the event log has just
  reopened. A later distinct terminal failure may be addressed only by a new
  request tied to that newer failed assessment.
- Observation mode is mandatory. Recovery may assess retained facts and create
  ordinary human-gated proposals, but it cannot authorize canonical truth or an
  external write. All terminal results remain audited.

## Classification and boundary

| Change | Classification | Boundary preserved |
|---|---|---|
| Typed assessment-recovery request and refusal | HARDEN | Append-only event history; exact private scope; no source content in control events. |
| New request-specific assessment and OpenClaw invocation identities | MODERNIZE | Existing failed events and idempotency meanings remain immutable. |
| Newest-valid-assessment source-assurance rule | HARDEN | Projections read retained facts; no projection becomes a source of truth. |
| Authenticated observation-only operator endpoint | HARDEN | Domain authorization and scope checks precede work; no member decision or provider authority is granted. |
| Guarded release exception for the exact known baseline | HARDEN | The ordinary healthy-release gate remains unchanged for every other commit, count, source, reason, or capability. |
| Noise-filtering generation 20 and event schema v4 | MODERNIZE | The pinned `llm-task` seam, eleven target kinds, human gates, and provider-write ordering remain unchanged. |

This change does not DEFER a required source disposition. It supplies the
bounded mechanism needed to obtain one without altering the historical failed
fact.

## Exact release baseline exception

`bootstrap/release-exceptions/pr-334-source-assessment-recovery.json` is the
only declared exception. Before candidate activation it requires all of the
following simultaneously:

- active production commit
  `2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe`;
- observation mode active;
- 62,623 obligations and 62,623 accounted dispositions;
- zero pending and zero missing dispositions;
- exactly four current failures, all
  `language_seam_OpenClawUnreachable`;
- exact source IDs `ev_01KZS9ZNGD0000`, `ev_01KZSAANK30000`,
  `ev_01KZSB7ZX20000`, and `ev_01KZSDH9CR0000`;
- no unrelated failed Doctor capability or hidden degraded capability.

The updater accepts this baseline only with the explicit
`--approve-declared-source-assessment-repair` flag. It validates the live
Doctor evidence before and after backup, stops services, re-audits the encrypted
event log, and appends only the exact typed recovery requests before selecting
the candidate. Any mismatch follows the ordinary fail-closed rollback path.
The exception expires after successful candidate activation; post-switch
acceptance still requires complete settlement and an entirely healthy Doctor.

## Security and privacy

- Trusted-ingress authentication, principal identity, view scope, private
  ownership, tenant identity, and observation state are checked before append;
  tenant and observation state are checked again at delivery.
- Batch size is at most eight; processing concurrency is two; each request has
  three total language attempts at most.
- Recovery control payloads and diagnostic totals are content-free.
- Privileged content and cross-member targets fail before language execution.
- The encrypted source log is opened with the configured secret store during
  the stopped-service updater audit. Keys and source content are never placed
  in release evidence.
- No provider token, provider SDK, provider call, domain approval, materialized
  canonical object, or external-write request is introduced by the recovery
  control itself.

## Rollback

Rollback restores the previously selected immutable release, OpenClaw plugin,
and launch configuration. It never removes the recovery request, refusal, or
assessment events already appended. Generation 19 ignores the new request event
and can continue to read the older v3 assessment. The guarded request identity
also includes a digest of the stopped noise-filtering checkpoint. Re-advancing
the reviewed candidate reuses an unprocessed request while it remains ahead of
that checkpoint; if v19 later advances beyond it, the next exact guarded audit
appends a new request beyond that position. An already processed request reuses
its durable invocation and assessment identities. A terminal refusal is also
part of the guarded request identity: an unchanged, unprocessed command remains
idempotent, while a recorded refusal permits a later guarded attempt to append
one new command instead of replaying the refused one. A newer terminal failure
becomes the exact predecessor for a separately bounded request.

## Acceptance evidence

Candidate evidence is recorded in
`docs/refactor/evidence/co-017-source-assessment-recovery.md`. AC-42 requires
production-composition proof of success, restart idempotency, bounded persistent
failure, rejection paths, source-assurance replacement, updater exact-baseline
validation, rollback behavior, privacy/isolation, observation mode, and zero
provider effects. Phase A may prepare and review the candidate, but merge and
deployment require the exact G-001 authorization naming the PR and its
40-character reviewed head.
