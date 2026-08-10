# CO-013 bounded mailbox history acquisition evidence

Date: 2026-08-10

Status: deployed acquisition and liveness repair; reviewed context-vocabulary
repair and final live fields pending

## Failure evidence

- The live authorized Comms projection contained only one retained source
  message for the reviewed multi-message matter and no linked Triage proposal.
- The Gmail adapter's first-use branch called `users.getProfile`, stored the
  returned `historyId`, returned zero, and never called `users.messages.list`.
- Therefore, source assurance correctly accounted for the facts in the event
  log but could not certify provider messages that were never acquired.
- Private provider inspection then confirmed the member-reviewed example is
  older than the ordinary recent-history default. A recent-only repair would
  therefore still miss the exact matter it was intended to recover.

No household message text, identifier, address, token, or credential is
retained here.

## Candidate proof

- Focused Gmail adapter, contextual reasoning, and production-supervisor suite:
  47 passed after adding historical-seed, exact-participant/date expansion,
  private-query non-disclosure, Python 3.14 parsing, newest-first normalization,
  and durable-restart proof.
- A held-open synchronous Gmail list regression proves initial acquisition runs
  outside the central asyncio loop and cannot starve HTTP health or unrelated
  runtime scheduling.
- Full root/pack/bridge/acceptance release matrix: 1,764 root/pack, 60 isolated
  bridge, and 47 strict acceptance tests passed; Ruff/format/mypy/Pyright,
  dependency/security/architecture/invariant checks, performance budgets,
  builds/packages, clean install, tenant isolation, encrypted backup/restore,
  exact native OpenClaw contract, and 17 plugin tests passed.
- Hosted constitutional cascade and Apple-silicon credential-free rehearsal:
  passed for the acquisition candidate.
- Reviewed acquisition merge: PR #314 merged as
  `6b8e8eba9d93c69a5f29118077dc1f2ca605255b`.
- First guarded activation: correctly rolled back to the prior healthy release
  when the synchronous provider batch prevented the new runtime health surface
  from opening within its bounded cold-start window. The private additive
  settings were removed before restarting the older schema, and the prior
  runtime, OpenClaw, and observation mode returned healthy. No source or
  provider mutation was discarded.
- Startup-liveness repair: PR #315 passed the hosted constitutional cascade and
  17m24s Apple-silicon rehearsal, then merged as
  `7d750cd8811dbed1b84fc104d3a66f2fa791b91e`.
- Guarded activation: the attested updater created an encrypted backup, retained
  rollback, reported no runtime migration, and activated that exact commit.
  Runtime liveness remained available while the bounded Gmail batch ran.
- Acquisition result before final assessment settlement: source facts increased
  from 61,398 to 62,526 and candidate-eligible facts from 13 to at least 18.
- Live structural failure: 45 retained `classify_message_nature` input failures
  share the content-free cause that `participant_reference_overlap` was emitted
  by deterministic context assembly but absent from the pinned skill input and
  typed Triage context schemas. The language model was not called for those
  inputs.
- Context-vocabulary repair: skill 6.0.1 and `noise_filtering` 13.0.0 add the
  missing reason atomically and opt into full immutable-source replay under a
  new idempotency generation. Its local matrix passed 1,764 root/pack, 60
  isolated bridge, and 47 strict acceptance tests plus every static, security,
  clean-install, backup, and pinned OpenClaw gate. PR #316 passed both hosted
  gates and merged as `6cc627ffca4ef11bf9670e440d5eee82bfbdff11`.
- Guarded activation kept runtime liveness and produced no new pipeline
  failures. It raised candidate-eligible accounting into the dozens and began
  superseding the retained schema failures. Content-free live telemetry then
  proved that the roughly 1,500-message language-bound replay could exceed the
  controller's 30-minute settlement ceiling despite clean forward progress.
- A first operational convergence repair passed every local and hosted gate,
  merged as `e8b10b876c299f94deecd05d8a52b39a80b5f287`, and activated. Sixteen-way
  delivery exposed a context/idempotency race: linked sources reached the same
  durable identity with different payloads, and the bus correctly poisoned the
  subscriber after five conflicts without advancing past the failed source.
- Final convergence repair: keep the v13 semantic generation, restore the
  reviewed eight-way context-safe width, retain the one-hour guarded settlement
  ceiling, and make the lightweight wait invoke full Doctor immediately on any
  new non-source capability degradation. Ordered-prefix durability, liveness,
  terminal equality, synthetic proof, and rollback are unchanged. Review,
  and hosted gates passed; PR #318 merged as
  `a9a404521385ae1c60e6183d4bf4443faa083ab0`.
- Its guarded activation proved the fail-fast path: after replay encountered a
  retained generation-13 idempotency conflict from the rejected sixteen-way
  attempt, the controller immediately ran full Doctor, reported pipeline and
  source-assurance failure together, and rolled back. No history was deleted.
- Generation-14 live evidence: replay cleared the retained generation-13
  conflict and processed more than 1,080 message facts, raising eligible
  accounting to at least 133, before a later context-equivalent pair exposed
  that proposal-key read-before-append deduplication could race even at width
  eight. The fail-fast controller reported pipeline and source-assurance
  failure together and rolled back without deleting evidence.
- Generation-15 live evidence: replay crossed the exact generation-14 collision
  with a healthy subscriber, zero consecutive failures, and no poison event,
  proving the atomic first-equivalent-writer repair. A later retained-failure
  batch exposed that several immediate transient retries could synchronize
  against the pinned Gateway and leave failed terminal assessments even though
  the subscriber remained healthy.
- Later generation-15 live evidence: a distinct conflict occurred after one
  assessment and its first three proposals appended. Content-free encrypted
  metadata showed the next candidate reused an earlier candidate's
  `(target_kind, candidate_key)` with a different payload inside the same
  recorded interpretation. The event log correctly refused the ambiguous key
  reuse and the controller rolled back.
- Final replay candidate: `noise_filtering` v17/generation 17 preserves the
  reviewed ordinary width, semantics, source facts, prior terminal evidence,
  member decisions, and one-hour guard. Deliberately shared proposal and
  supersession identities accept the first equivalent event only after the
  atomic event log arbitrates the race; non-equivalent reuse still raises. Only
  transient OpenClaw timeout/unreachable recovery serializes behind one
  runtime-local lock before one retry under the same private invocation
  fingerprint. Fixed code collapses a repeated typed candidate identity within
  one recorded interpretation to its first occurrence before emitting the
  assessment/proposal pair; confidence neither ranks nor authorizes the choice.
  Persistent failure remains auditable and health-degrading. Review, merge,
  activation, settlement, Doctor, and authenticated proof remain pending.
- Guarded observation deployment and encrypted backup receipt: pending.
- Settled source assurance and full Doctor: pending.
- Authenticated Comms/Triage proof of recovered multi-message context: pending.

## Safety assertions

- The current incremental cursor is snapshotted before acquisition.
- Bounds are validated and capped; drafts and spam/trash are excluded.
- Private reviewed seeds win the global cap; fixed code derives exact non-
  member participants and a bounded date window before recent-history fill.
- Seed query text is absent from events, status, and durable cursor IDs.
- The bounded batch appends generic/related facts before reviewed seeds and
  each group oldest-first, making complete seed-thread assessment independent
  of Gmail's search-result order.
- Per-message event identity, scope, direction, dedupe, and quarantine are
  unchanged.
- Completion state is metadata-only and policy-digest scoped.
- Acquisition appends observations only. It performs no provider mutation,
  candidate confirmation, canonical materialization, or member decision.
- Blocking provider acquisition executes in a worker thread while all event
  append, projection, cursor, authority, and terminal handling remains on the
  existing central-runtime path.
