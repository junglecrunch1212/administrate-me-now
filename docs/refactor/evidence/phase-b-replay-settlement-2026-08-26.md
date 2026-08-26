# Phase B replay settlement evidence — 2026-08-26

## Authority and immutable input

- The owner authorized Phase B in ordinary language.
- Protected pull request #346 merged as
  `ec3c77615c6c4fef72dd05b48f45a6d09711cc9c`.
- The merge tree exactly matched reviewed head
  `835b5c4c1cf4c3345e1609272416619d14b7705f`.
- The guarded updater ran from a clean detached checkout of that merge under
  Python 3.14.6, Node 24.19.0, and exact OpenClaw commit
  `ce67ffb70e83e5ebb56ea02e814e577153f84f61`.
- No runtime migration, provider enrollment, credential change, permission
  change, observation disablement, checkpoint edit, or provider-write
  authorization was supplied.

## First guarded attempt and rollback

The updater created an encrypted pre-switch backup, installed the attested
release, atomically selected it over predecessor
`d181fad24ef488a71ce5568cc40fb81309cb3817`, and started both launchd services
on their configured loopback ports. The candidate recovered Gmail, Google
Calendar, and Google Tasks to observation-only healthy operation under the new
durable cursor contracts. OpenClaw reported a fresh current-process heartbeat.
Observation remained active and write admission remained
`observation_suppressed`.

Noise filtering v21 advanced monotonically with no poison. Its full historical
replay nevertheless remained above one thousand type-filtered deliveries at
the end of the unchanged one-hour source-settlement window. The updater failed
closed on `launchd_and_process_state`, `required_reactive_pipeline_delivery`,
and `source_assurance_terminal_dispositions`, then restored exact predecessor
`d181fad`. Both services restarted on loopback. The restored predecessor was
truthfully degraded while its healthy v20 worker consumed the retained
append-only candidate results. A no-synthetic-proof Doctor found all 15
projection checkpoints healthy, all eight required pipeline subscribers
non-poisoned, observation active, and `live_provider_claims: []`; release
readiness remained false because source assurance was not settled.

At the first stable post-rollback audit, source assurance reported 65,675
obligations, 64,654 accounted, 1,021 missing, zero pending, and four failed.
The four failures were the original private `messaging.received` sources with
`language_seam_IdempotencyConflict`; the earlier unreachable target had
recovered. Recovery accounting was nine requested, five succeeded, four
refused, and zero pending. These are content-free control-plane identifiers and
counts; no source body or household identity is retained here.

## Cause and forward repair

The v21 handler constructed the correct bounded context pack and admitted
candidates only when `current_is_thread_anchor` was true, but it still invoked
the language seam for every reliable historical non-anchor. The returned
candidate output was then deterministically discarded. This unnecessary,
non-authoritative work dominated live settlement time.

The follow-up candidate advances the pipeline and idempotency generation to
v22 because partial v21 facts are valid immutable history. A reliable
non-anchor now receives exactly one deterministic terminal assessment:

- outcome: `no_candidate`;
- extractor: `thread-anchor-deferred-v1`;
- reason: `language_assessment_deferred_to_latest_thread_message` plus the
  existing candidate-policy and thread-deferral reasons;
- no language invocation, new classification/semantic interpretation,
  proposal, canonical record, provider command, or source mutation.

The latest thread anchor still invokes the exact pinned
`classify_message_nature@7` skill and alone performs fixed-code fallback,
grounded-language admission, episode reconciliation, and supersession. Member
decisions remain permanent. The checked-in recovery exception is rebound to
exact predecessor `d181fad`, observation mode, the 65,675-obligation floor,
zero missing/pending backlog after predecessor settlement, and the exact four
remaining idempotency-race targets. Any baseline drift aborts before release
selection.

## Verification

- Red regression first reproduced the non-anchor language invocation.
- Focused handler, pack, and production-composition context suites: 43 passed.
- The production-composition assertion proves no `skill.call.recorded` event
  for the older non-anchor and a recorded call for the actual latest anchor.
- Focused release-repair policy suite: 12 passed.
- Full configured Python suite: 1,750 passed, one expected skip.
- Strict acceptance with `--runxfail`: 66 passed, one expected skip.
- Aggregate release command: PASS. Its clean-worktree run included 1,919
  root/package tests, 60 bridge tests, Ruff/format/mypy/Pyright, performance and
  fast source-pipeline stress budgets, architecture checks, clean install,
  tenant isolation, backup/restore, exact OpenClaw build/contract/17 tests,
  shell/invariant hygiene, and the 690-assertion credential-free Gate 7 browser
  matrix under Node 24 and Google Chrome 151.

Hosted checks, guarded retry, live Doctor, authenticated read-only browser,
live backup, privacy/isolation, and zero-provider-effect evidence remain
pending and must be added before Phase B can be declared complete.

## Protected replay repair and rejected pre-switch retry

PR #347 passed `deterministic-consistency` and
`credential-free-rehearsal`, then merged normally as
`af29e858e302f82f73bb7f5ab272927ddbf7433a`. Exact `origin/main` and a clean
detached retry checkout resolved to that merge. Before creating another backup,
stopping services, appending recovery requests, or changing the release
selector, the guarded updater revalidated the live predecessor and rejected
two non-source degraded capabilities:

- `adapter:messaging-gmail-api` / `gmail-primary` / `1.1.1`;
- `adapter:calendaring-google-calendar` /
  `google-calendar-primary` / `2.0.0`.

Both contained the exact content-free constructor error for unexpected field
`retry_not_before_ms`. Noise filtering v20 had zero lag, zero errors, and no
poison. Source assurance remained current and settled at 65,675 accounted of
65,675 obligations, zero missing, zero pending, and the same four exact
idempotency-race failures. Recovery accounting remained nine requested, five
succeeded, four refused, zero failed, and zero pending. Observation remained
active. The rejected retry made no release, event, checkpoint, provider,
credential, or permission change.

## Rollback-schema forward repair

The first candidate added retry timing directly to `adapter_cursors`.
Predecessor `d181fad` constructs `AdapterCursor` from `SELECT *`, so the retained
column survived release rollback but was not code-backward-compatible. The
repair moves retry timing to a separate content-free `adapter_retry_state`
table. Candidate store initialization transactionally copies any existing
timing values, drops only the incompatible column, and then exposes the same
durable half-open/circuit semantics through an explicit join. Provider
watermarks, leases, circuit/error state, dedupe rows, events, and source facts
are retained. A later predecessor code rollback sees its exact original cursor
column set and ignores the companion table.

The release exception gains a two-row exact allowlist for only the capability,
instance, predecessor adapter version, and constructor error observed above.
A healthy adapter remains valid. A different message, extra instance,
different version, blocked state, or any unrelated degradation still aborts
before release selection; post-switch settlement still requires fully healthy
Doctor.

Focused proof currently records 18 passing cursor-store and guarded-baseline
tests, targeted Ruff, and targeted mypy. Full release and hosted checks, guarded
retry, live Doctor, authenticated read-only browser, backup verification, and
zero-provider-effect closure remain pending.
