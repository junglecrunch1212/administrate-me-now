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

Focused proof records 358 passing adapter/bootstrap/process tests, including
the exact v21-column migration, predecessor column shape, retry-state
preservation, current live baseline acceptance, and changed-error rejection.
The clean aggregate release command passed with 1,921 root/package tests, 60
bridge tests, 66 strict acceptance tests and one documented skip, Ruff/format/
mypy/Pyright, performance and fixed-point source stress, package builds,
architecture checks, clean install, tenant isolation, backup/restore, exact
OpenClaw contract/build and 17 plugin tests, and the 690-assertion credential-
free Gate 7 browser matrix with no unexpected console, HTTP, page, or transport
failure. Hosted checks, guarded retry, live Doctor, authenticated read-only
browser, backup verification, and zero-provider-effect closure remain pending.

## Protected rollback-schema activation and terminal-ledger hold

PR #348 passed `deterministic-consistency` and
`credential-free-rehearsal`, then merged normally as
`cfde6b631edd034633e8dc63a0204d06de363ac3`. The governed updater attested that
exact merge and returned `status=upgraded`, `controller_attested=true`, and
`rollback_available=true` without a Python, Node, OpenClaw, credential,
permission, provider-enrollment, checkpoint, or observation-mode migration.
It retained encrypted backup
`administrateme-20260826T124523Z.ambackup`, mode 0600, size 2,681,146,284
bytes, SHA-256
`3a62130b4d1ee3292bdbb6867028f9fc749621b8304e5768ad6f2a4271f93778`.

Final readiness was observation-only and release-ready with safe reads, write
admission `observation_suppressed`, all eight pipelines and fifteen projections
healthy, all configured provider adapters running with durable watermarks and
closed circuits, and current-process OpenClaw healthy. Source assurance settled
65,685/65,685 obligations with zero pending, missing, or failed source
dispositions. Full Doctor passed every check and recorded
`live_provider_claims: []`. Authenticated read-only browser acceptance traversed
all ten adult routes; each had the required scoped shell, current navigation,
observation indicator, and no catastrophic error or horizontal overflow. The
console recorded zero warnings/errors. No write control was used and no
household content entered this receipt.

The separate recovery ledger reported thirteen requested, five succeeded,
four refused, and four pending. Exact content-free inspection proved each new
request existed but had neither `recovery_request_event_id` linkage nor a
refusal. The same sources had ordinary successful v22 assessments, so source
disposition health was truthful while command terminality remained incomplete.
The active release was left healthy and observation-only; Phase B completion
was withheld.

## Recovery-terminality forward repair candidate

The v22 non-anchor early branch called the terminal assessment emitter without
the request recovery context. The repair passes that context through every
early terminal branch. Pipeline startup additionally scans only
`source.assessment.recovery_requested` metadata through one fixed log head,
checks the two exact idempotent terminal identities per request, refuses more
than 64 pending commands, invokes the normal production callback for each
selected command, and verifies an exact terminal afterward. It does not reset,
advance, or synthesize a subscriber checkpoint and does not replay the source
log.

Local proof currently records 21 passing focused tests: an older non-anchor
creates one request-linked v4 assessment without a skill call; runner
reconciliation is bounded, idempotent, and checkpoint-free; and the production
runtime composition converts a checkpointed stale command into exactly one
`failed_assessment_not_current` refusal with zero recovery commands pending.
Targeted Ruff, format, and mypy checks pass. The clean aggregate release gate
also passed: 66 strict acceptance tests and one documented skip, 1,924
root/package tests and one documented skip, 60 bridge tests, Ruff/format/mypy/
Pyright, performance and fixed-point source stress, builds, architecture,
clean-install, tenant-isolation, backup/restore, exact OpenClaw contract/build
and 17 plugin tests, and all 690 credential-free Gate 7 assertions under Node
24 and Chrome 151. Protected hosted checks/merge, governed upgrade, final live
ledger/Doctor/browser/effect proof, and a documentation-only closure receipt
remain pending.

## Protected terminality merge and final live closure

PR #349 passed `deterministic-consistency` in 6m30s and
`credential-free-rehearsal` in 19m32s at exact head
`771ea70c6b7e2088c77bc246183d49c79778d4ca`, then merged normally as
`7ed10ac599e08dc1e616f6b9c3f0ab3b11dfb9b7`. A clean detached checkout and
`origin/main` resolved to that merge. The updater was invoked without either
optional approval flag and returned `status=upgraded`, controller attested,
rollback available, source repair null, and runtime/OpenClaw migration null.
Exact OpenClaw remains 2026.7.2 at
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`.

Before normal bus delivery, candidate startup selected the four non-terminal
requests and appended exactly these content-free refusals:

| Request | Refusal | Reason |
|---|---|---|
| `ev_01M0Z1V92S0000` | `ev_01M0Z7BMSK0000` | `failed_assessment_not_current` |
| `ev_01M0Z1V92S0001` | `ev_01M0Z7BMSQ0000` | `failed_assessment_not_current` |
| `ev_01M0Z1V92S0002` | `ev_01M0Z7BMSS0000` | `failed_assessment_not_current` |
| `ev_01M0Z1V92S0003` | `ev_01M0Z7BMSV0000` | `failed_assessment_not_current` |

The final recovery ledger is thirteen requested, five succeeded, eight
refused, zero failed, and zero pending. No selected request gained both terminal
forms. Noise filtering remains v22 with zero lag, zero consecutive failures,
and no poison; no subscriber checkpoint was reset or advanced by startup
reconciliation.

Final full Doctor reports healthy and safe to attach in observation mode. The
encrypted log opens under SQLCipher with 118,791 events; 65,691/65,691 source
obligations are accounted with zero pending, missing, or failed; all eight
required pipelines and fifteen projections are healthy; exact OpenClaw source,
plugin discovery, live `llm-task` execution, five managed cron jobs, both
launchd services, and loopback-only ports pass. `live_provider_claims` is empty,
and the canonical synthetic round trip records zero external side effects.

Backup `administrateme-20260826T142245Z.ambackup` is mode 0600,
2,708,821,592 bytes, with SHA-256
`4c45bcd36f8b278e9859adbdf9940ebeb39f74e7838c3317975662070e254ca8`.
Authenticated read-only browser verification traversed Today, Triage, Comms,
Reminders, People, Notes, Finance, Calendar, Scoreboard, and Settings at
384 × 652. Every route had the expected path/title, one main, one page heading,
one primary navigation with ten destinations and one current marker, a live
region, visible observation state, no catastrophic error, and no viewport
overflow. The browser recorded zero console issues. No write control was used,
no source or household content was captured, and no provider effect occurred.

Phase B is complete for the owner-authorized observation-only scope. Runtime
migration, provider enrollment, credential/permission changes, observation
disablement, checkpoint edits, and provider writes remain unauthorized.
