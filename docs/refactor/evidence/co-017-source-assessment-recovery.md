# CO-017 source-assessment recovery evidence

Status: PR #335 recovery implementation merged; release activation stopped
before mutation; follow-up Phase A drift/status hardening is locally green and
awaiting independent exact-head review. No live recovery is claimed by this
file.

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
  exact stopped-log counts/watermark, and rejects hidden structural degradation
  during every post-switch wait.

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
| Candidate v2 policy against the current content-free live readiness/Doctor snapshot | PASS at 62,633/62,633 accounted, zero pending/missing, and the exact four declared failures; no source content exposed |
| `PYTHONPATH=bridge bash scripts/acceptance/release-candidate.sh` | PASS on the final worktree: 55 strict acceptance, 1,820 root/package, 60 bridge, Ruff format/lint, mypy, Pyright, dependency audits, performance, builds, architecture-negative, clean-install, tenant-isolation, backup/restore, OpenClaw 17-test contract/build/package/audit, shell, syntax, and diff checks |
| `PYTHONPATH=bridge poetry run pytest -q` | 1,653 passed |
| Required Ruff, mypy, and invariant commands | Passed; 219 mypy source files |
| `PYTHONPATH=bridge poetry run ./scripts/acceptance/macos-bootstrap-rehearsal.sh` | PASS on arm64: 74 tests; runtime and OpenClaw plists valid |
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

## Required Phase B evidence

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
