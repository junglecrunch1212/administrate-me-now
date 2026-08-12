# CO-017 source-assessment recovery evidence

Status: released and authenticated-live-verified in observation mode. PR #336
merge `d181fad24ef488a71ce5568cc40fb81309cb3817` is the active release; all four
declared sources have newer successful v4 assessments while the older failures
remain immutable.

## Reproduction and cause

- Production baseline evidence named four exact message sources with current
  `language_seam_OpenClawUnreachable` assessments.
- Noise-filtering generation 19 had exhausted its base invocation and two
  transient recovery identities.
- A later result under the already occupied generation-19 assessment
  idempotency key would have a different payload, so the encrypted append-only
  log correctly raised an idempotency conflict.
- Full replay could redeliver the source but could not create a fresh terminal
  assessment identity. The defect was the absence of an explicit recovery
  event and identity, not the event log's conflict check.

## Candidate controls

- Typed events: `source.assessment.recovery_requested@v1`,
  `source.assessment.recovery_refused@v1`, and linked
  `triage.intent.features_extracted@v4` replacement evidence.
- Operator route: `POST /api/system/source-assessment-recoveries` through the
  authenticated production composition root.
- Policy: exact source and failed-assessment identity, exact retryable reason,
  private same-member scope, non-privileged message source, observation mode,
  batch maximum eight, concurrency maximum two, and three total language
  attempts per request.
- Execution control: request/source/assessment/runtime tenant identity must
  match; observation is re-read at delivery; distinct commands for one failed
  source are single-flight through terminal append; and cold-reopen derived
  events sort after their request.
- Durable retry control: the request-bound base call bypasses the selected old
  causal failure once, while its own base/recovery-1/recovery-2 terminal
  identities remain non-repeating. A refusal is terminal for its exact command,
  and the guarded updater derives a new identity only after that refusal.
- Release policy v2: the checked-in exception binds the prior live commit,
  original 62,623-obligation floor, exact current full accounting at the
  stopped-log watermark, exact four source IDs/reasons, fresh settlement,
  observation mode, and zero unrelated degraded capabilities. New fully
  accounted source facts may advance the snapshot; regression, pending,
  missing, another failure, or changed identity/reason still fails closed.
- Effect boundary: the control event itself creates no canonical object,
  provider command, provider effect, or domain approval.

## First authorized transaction stop

- G-001 authorization bound PR #335 to exact reviewed head
  `bedd3180ce57c4ba9f8d4c136b3f25411b40f17f`; GitHub produced merge commit
  `2a37aa08179a356fddfe0d4cd30ffd3e29f732a3`.
- Read-only Phase B preflight observed 62,632/62,632 fully accounted
  obligations instead of the checked-in 62,623 equality snapshot, with zero
  pending/missing and the same exact four failures/reasons.
- The same readiness document exposed Google Tasks in `backoff` after a prior
  transient connection reset. The updater was not invoked; no backup, request,
  release copy, pointer switch, service mutation, or provider action occurred.
- A later explicitly requested operational fix used the current immutable
  release's documented stop/start path. Google Tasks returned to running,
  observation-only state with no current error, and the fresh source snapshot
  advanced normally to 62,633/62,633.
- The follow-up candidate makes that recovery state truthful after any later
  successful poll, distinguishes degraded readiness from observation-only
  readiness, permits only fully settled monotonic source growth, records the
  exact stopped-log counts/watermark, binds it to the final live watermark,
  verifies stopped consumer and Gateway-quiescence health, and rejects hidden
  structural degradation during every post-switch wait.

## PR #336 first independent review

The separate reviewer rejected exact head
`7ccb555e2cb57d3b2157931ba66a0d568b9712c0` with two P1 findings:

- unrelated capability state in the stopped audit came from the last live
  readiness document, leaving a validation-to-stop race; and
- readiness was queried only for launchd/source-assurance transients, leaving
  projection/OpenClaw/cron/port-only waits unable to detect hidden degradation.

The amended candidate requires the final live source watermark to equal the
stopped encrypted-log watermark, reads stopped durable consumer failure state,
proves Gateway liveness after runtime quiescence, checks readiness on every
allowed post-switch transient, and fails closed when readiness is unavailable
after runtime liveness. Focused reviewer-finding regressions and the surrounding
updater/source-repair/macOS operations slice pass (84 tests). Complete final-head
gates pass at the counts below; fresh independent review remains pending. The
rejected head has no release authority.

## PR #336 second independent review

The separate reviewer confirmed both first-review findings closed, then
rejected exact amended head `e601a4e14efc15f1127833cc5f4611f36aa02569`
with one P1 finding. If guarded quiescence partly stopped the service pair and
raised, candidate cleanup called `stop()` again without guarding that cleanup
call. A second stop failure could therefore bypass current-selector and plugin
restoration and the predecessor restart.

Candidate activation now records cleanup-stop failure, continues through every
selector, launchd, plugin, and predecessor-runtime restoration step, and
reports the candidate error plus every secondary restoration error. The
explicit rollback path uses the same continue-and-report discipline. A process
regression makes the initial stop unload the runtime and raise, makes cleanup
stop raise again, and proves two stop attempts, exactly one predecessor
restart, healthy final service status, and no candidate pointer. The focused
source-repair, updater, and macOS operations slice passes 85 tests. The second
rejected head has no release authority; complete exact-head gates and a third
independent review remain pending.

## PR #336 third independent review

The separate reviewer rejected exact amended head
`9cfd0da1b4cbdd14cb28940649552664c196de9d` with one P1 finding. Both
restoration branches attempted `ServiceManager.start()` and caught exceptions,
but ignored its returned `ServiceStatus`. Because startup can return
`status="failed"` for blocked readiness without raising, the updater could hide
a failed predecessor/current-release restart and report only the earlier
candidate failure. Isolated activation and explicit-rollback probes reproduced
the missing failure evidence. The reviewer confirmed the first two review
rounds' findings remain closed and found no additional security, privacy,
observation, exact-target, immutable-history, or provider-effect regression.

Both restoration branches now require the returned status to be exactly
healthy and retain non-healthy status, runtime, and Gateway state as a
secondary terminal error. The activation process regression combines partial
quiescence, two stop failures, and a non-raising failed restart. A separate
explicit-rollback regression forces rollback Doctor failure followed by a
failed current-release restoration return. The focused source-repair, updater,
and macOS operations slice passes 86 tests. The third rejected head has no
release authority; complete exact-head gates and a fourth independent review
remain pending.

## PR #336 fourth independent review and hosted fixture repair

The separate reviewer accepted exact head
`1175dc6e498d0a79226179095683f7fbe9b872bb` against exact base
`2a37aa08179a356fddfe0d4cd30ffd3e29f732a3` with no findings. The review
confirmed all prior findings closed, all security/privacy/observation/provider
boundaries preserved, the non-raising failed-restart probes effective, and the
constitutional/evidence history accurate.

The exact-head Constitution check passed. The hosted macOS rehearsal then
failed at `test_upgrade_waits_only_for_known_post_switch_transients` after clean
install and the exact OpenClaw source build had passed. The test supplied a
transient Doctor sequence but no runtime-readiness fixture. On the local family
Mac it accidentally read an unrelated live loopback readiness endpoint and
passed; on the isolated runner readiness was absent, so production code
correctly returned the transient failure immediately. The test now provides an
explicit healthy, content-free readiness document. This changes no production
behavior: missing readiness after runtime liveness and every non-cold-start
transient still fail closed. The accepted-but-hosted-red head has no release
authority; complete gates and a fifth exact-head review remain pending.

## Focused checks completed

| Command or slice | Result |
|---|---|
| `PYTHONPATH=bridge poetry run pytest -q tests/unit/test_source_assessment_recovery.py` | 9 passed |
| Production-composition recovery endpoint integration slice | 1 passed |
| `PYTHONPATH=bridge poetry run pytest -q tests/unit/bootstrap/test_source_assessment_repair.py` | 3 passed, including rollback-checkpoint advance |
| Existing noise-filtering, source-assurance, and updater regression slices | 91 passed |
| Targeted Ruff checks for changed Python modules | Passed |
| Targeted mypy checks for recovery, assurance, runtime, and updater modules | Passed |

## PR #335 final-head local release gates

| Gate | Result |
|---|---|
| `poetry sync --with dev` | Current project installed; no dependency change |
| `PYTHONPATH=bridge poetry run pytest -q` | 1,647 passed; one upstream Starlette deprecation warning |
| `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q` | 55 passed; one upstream Starlette deprecation warning |
| `bash scripts/acceptance/release-candidate.sh` | PASS: 1,814 root/package tests, 60 bridge tests, Ruff format/lint, mypy, Pyright, both dependency audits, performance, builds, architecture-negative, clean-install, tenant-isolation, backup/restore, OpenClaw contract/test/build/audit, shellcheck, syntax, and diff checks |
| `poetry run ruff check adminme bridge/adminme_bridge` | Passed |
| `poetry run mypy adminme bridge/adminme_bridge` | 219 source files passed |
| `bash scripts/verify_invariants.sh` | Passed |
| Required Node 24 OpenClaw `npm ci`, `run check`, and `test` | Passed; 17/17 plugin tests; zero audit vulnerabilities |
| macOS disposable bootstrap rehearsal under reviewed Poetry Python 3.14 | PASS on arm64; 71 tests; both launchd plists valid |
| Node 24 Gate 7 rendered browser harness | 259/259 passed; zero console errors, page errors, request failures, or repository evidence writes |

The first aggregate run stopped at the format gate after its functional suites
passed because Ruff reported eight changed files “would be reformatted.” The
formatter was applied, its diff was checked, and the complete aggregate was
rerun from the beginning to PASS.

The first replacement-head aggregate run passed the 55-test acceptance slice,
1,812 root/package tests, 60 bridge tests, Ruff, mypy, and dependency audits,
then stopped at Pyright with
`Argument of type "str" cannot be assigned to parameter "reason" of type "RecoveryRefusalReason"`.
The refusal exception's public code attribute was annotated with the exact
literal type. Independent preflight also reported
`generated lifecycle catalog is stale` after the refusal vocabulary grew; the
catalog was regenerated and its checked mode passed. Neither interrupted run
is counted as the final aggregate result above; the complete suite was rerun
from the beginning after both repairs.

Independent review then withheld approval from replacement head
`bd93ef2833a3a3a91bccc0797d83fc9ee0abe5d8`: after base and recovery-1 had
failed, the wrapper consulted only the newest source-causal terminal and could
spend the older base identity again after a crash. The wrapper now selects an
exact invocation failure before allowing predecessor-failure bypass. A
production-path test uses the real wrapper and crashes separately after the
base and recovery-1 failures; each restart completes with exactly three total
provider requests and one failure event per attempt identity. The invalid head
is not the candidate described by the final gate table.

The first direct macOS rehearsal resolved its hard-coded `python3` to a host
interpreter without the candidate environment and failed before lab mutation
with `ModuleNotFoundError: No module named 'yaml'`. Re-running with the reviewed
Poetry Python 3.14 environment first on `PATH` passed. The first direct Gate 7
command disclosed Node v22.23.2 against the required `>=24.15.0 <25`; that
result was not counted. The harness was reinstalled and rerun with Homebrew
`node@24`, producing the 259-check result above.

These results prove candidate mechanics and local release readiness only. Live
evidence must be recorded only after separately authorized guarded deployment.

## Guarded-release drift/status candidate gates

| Command or slice | Result |
|---|---|
| Focused policy, service-health, updater, and Google Tasks unit tests | PASS, including blocked-capability fail-closed coverage |
| Combined source-recovery, runtime, updater/rollback, Google Tasks, and macOS operations slice | PASS |
| Candidate v2 policy against the current content-free live readiness/active-release Doctor snapshot | PASS at 62,634/62,634 accounted, zero pending/missing, the exact four declared failures, and watermark `ev_01KZSQZT4M0007`; no source content exposed |
| Final-live versus durable consumer inventory read-only proof | 23 expected and 23 persisted pipeline/projection consumers; no missing/extra IDs and zero unhealthy rows |
| `PYTHONPATH=bridge bash scripts/acceptance/release-candidate.sh` | PASS on the repaired worktree after the third independent review: 57 strict acceptance, 1,827 root/package, 60 bridge, Ruff format/lint, mypy, Pyright, dependency audits, performance, builds, architecture-negative, clean-install, tenant-isolation, backup/restore, OpenClaw 17-test contract/build/package/audit, shell, syntax, and diff checks |
| `PYTHONPATH=bridge poetry run pytest -q` | 1,660 passed |
| Required Ruff, mypy, and invariant commands | Passed; 219 mypy source files |
| `PYTHONPATH=bridge poetry run ./scripts/acceptance/macos-bootstrap-rehearsal.sh` | PASS on arm64: 79 tests; runtime and OpenClaw plists valid |
| Node 24.19.0 Gate 7 rendered browser harness with temporary evidence directory | 259/259 passed; 39 screenshots; zero console errors, page errors, request failures, or repository evidence writes |

The first aggregate attempt reached the format gate only after its 55 strict
acceptance, 1,819 root/package, and 60 bridge tests passed. Ruff identified one
formatting-only change in `bootstrap/source_assessment_repair.py`; the formatter
was applied, the focused policy tests passed, and the complete aggregate was
rerun from the beginning to PASS. After the last fail-closed blocked-capability
assertion was added, the complete aggregate and macOS rehearsal were rerun
again to the final counts above. That interrupted attempt is not counted as the
final aggregate result.

Two final Gate 7 invocations with a manually restricted `PATH` stopped before
browser startup with `Error: Gate 7 fixture server did not become ready at
http://127.0.0.1:3347`; the restriction omitted
`/opt/homebrew/bin/poetry`, so the harness could not spawn its fixture process.
With the Poetry Python 3.14 environment first, Node 24.19 next, and the Poetry
executable directory present, the complete harness passed at the result above.
The two pre-browser environment invocations are not counted as test results.

## Required Phase B evidence (satisfied below)

After exact G-001 authorization and only for the authorized reviewed head:

1. merge and identify the exact merged-main SHA;
2. run the guarded updater with the declared-repair approval flag;
3. preserve the encrypted backup and rollback receipt;
4. prove all four request events and their newer terminal assessment events;
5. prove the four immutable older failures still exist;
6. require complete source settlement, healthy full Doctor, observation mode,
   authenticated Triage/privacy/isolation behavior, and zero provider-called
   terminal effects; and
7. place those content-free receipts in a documentation-only follow-up PR.

## Phase B release receipt

### Authorization, merge, and fail-closed preflight

- Fifth independent review accepted exact PR #336 head
  `fd13157648ae3ad02b4190a95feb817ad99fee46` against exact base
  `2a37aa08179a356fddfe0d4cd30ffd3e29f732a3` with no findings. Constitution
  sync and the hosted macOS bootstrap contract both passed; GitHub reported no
  review threads.
- Exact G-001 authorization produced GitHub merge commit
  `d181fad24ef488a71ce5568cc40fb81309cb3817`. Its parents are the exact base
  and reviewed head, and it was verified as current `origin/main` before Phase
  B.
- The first post-merge preflight stopped before updater invocation because
  Google Calendar exposed an unrelated retained transient network failure as
  `backoff`. Active release
  `2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe`, observation, services, and the
  source log remained selected; no backup, recovery request, candidate release,
  pointer change, or provider effect occurred.
- Separate owner authority covered one predecessor restart only. An initial
  direct call was rejected before service mutation with
  `TypeError: ServiceManager.stop() got an unexpected keyword argument
  'require_openclaw_live_before_stop'`; unchanged health was reverified. The
  one authorized restart then used the predecessor's actual method signature.
  Calendar returned to running observation-only health with no current error,
  and Doctor's sole failure was again the declared source-assurance check.
- Fresh exact-head G-001 authorization resumed the transaction only after the
  baseline matched. The final pre-updater live snapshot was fully accounted,
  observation-active, and contained only the same exact four source failures.

### Guarded updater and backup

- The controller attested immutable merged SHA
  `d181fad24ef488a71ce5568cc40fb81309cb3817` and used only
  `--approve-declared-source-assessment-repair`. Python, Node, OpenClaw
  `2026.7.2`, plugin API, and pinned OpenClaw commit
  `ce67ffb70e83e5ebb56ea02e814e577153f84f61` did not migrate.
- At stopped watermark `ev_01KZV0VRC80003`, source assurance recorded
  62,673 obligations and 62,673 accounted, zero pending/missing, exactly four
  failed targets, content exposed false, eight pipelines, fifteen projections,
  zero unhealthy durable consumers, and Gateway live at quiescence.
- Encrypted backup `administrateme-20260812T125603Z.ambackup` was created at
  `2026-08-12T12:56:09.349294+00:00` with mode 0600. Independent in-memory
  authenticated decryption verified all 6,222 allowlisted file sizes and
  hashes, fifteen projection names, zero unsafe archive members, matching
  envelope/manifest identity
  `8a5138d75cceabcfd73c70de5d4b7fc3b6a453b1e6d03844450a11079446dc04`,
  and encrypted-envelope SHA-256
  `cb2f7e8316c5a042af36c2beb0422e9f33922b0d6db1dac61feb1b5e87f39682`.
- The updater returned `status=upgraded`, controller attested true, and rollback
  available true. The active selector names the merged SHA and retains
  predecessor `2fede3f6a72f3ee6df99c0cefb7fca71471ff7fe`; both immutable releases and
  runtimes are present, and the predecessor passes rollback compatibility for
  the unchanged runtime/OpenClaw baseline. Rollback was not exercised because
  activation passed.

### Append-only recovery proof

| Source | Retained failed assessment | Recovery request | New current v4 assessment | Result |
|---|---|---|---|---|
| `ev_01KZS9ZNGD0000` | `ev_01KZSA04HT0007` | `ev_01KZV0W4KW0000` | `ev_01KZV0YPGJ0001` | `no_candidate`; `language_assessment_completed` |
| `ev_01KZSAANK30000` | `ev_01KZSAB07S0007` | `ev_01KZV0W4KW0001` | `ev_01KZV0YMF20001` | `no_candidate`; `language_assessment_completed` |
| `ev_01KZSB7ZX20000` | `ev_01KZSB88V40003` | `ev_01KZV0W4KW0002` | `ev_01KZV0YTCV0003` | `no_candidate`; `language_assessment_completed` |
| `ev_01KZSDH9CR0000` | `ev_01KZSDHZYR0008` | `ev_01KZV0W4KW0003` | `ev_01KZV0YWCP0008` | `no_candidate`; `language_assessment_completed` |

For every row, the failed v3 assessment still exists with outcome `failed` and
reason `language_seam_OpenClawUnreachable`; event order is strictly old
assessment, request, replacement; the replacement names both exact predecessor
and request; and the replacement is the latest valid assessment for that exact
source. The four requests, sources, predecessors, and replacements are each
distinct. No source content entered this receipt.

### Settlement, Doctor, privacy, and effects

- Final live readiness at watermark `ev_01KZV199ZX0000` is
  observation-only and ready with no degraded capability: 62,674 obligations,
  62,674 accounted, zero pending, zero missing, and zero failed. Normal inbound
  growth after the stopped snapshot was fully settled.
- Independent full Doctor passes 21/21 checks. Its canonical synthetic round
  trip proves all fifteen projections, healthy durable checkpoints, member
  portal and chat health, configured pinned model routing, and zero synthetic
  external side effects.
- Authenticated read-only Triage acceptance through Tailscale rendered the
  private principal surface, observation indicator, no-operator boundary, ten
  primary routes, and Add-for-review control. None of the exact recovered
  source/request IDs appeared in the DOM, and the browser recorded zero warning
  or error entries. No confirmation, dismissal, capture, chat send, or other
  write control was used.
- The authenticated self-view returned its scoped Triage inventory. The same
  read under an exact nonexistent view target returned that target, zero items,
  zero matters, no continuation, and no fallback to or leakage from the
  authenticated member inventory.
- From the stopped watermark through final verification, the encrypted log
  contains exactly the four named recovery requests and four linked replacement
  assessments. It contains no provider terminal event and no observation state
  change; the provider-called terminal count is zero. Observation stayed
  active, no member decision or provider action was authorized, and no external
  write occurred.

This receipt is restricted to governing documentation and content-free
operational metadata. It changes no code, configuration, dependency, lock,
test, installed release, runtime state, credential, permission, provider
enrollment, or observation policy.
