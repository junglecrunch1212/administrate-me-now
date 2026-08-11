# CO-017 source-assessment recovery evidence

Status: Phase A candidate evidence. No merge, release activation, or live
recovery is claimed by this file.

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
- Release policy: the checked-in exception binds the prior live commit, exact
  four source IDs/reasons, and exact 62,623/62,623 settled-count baseline. Every
  changed or additional condition fails closed.
- Effect boundary: the control event itself creates no canonical object,
  provider command, provider effect, or domain approval.

## Focused checks completed

| Command or slice | Result |
|---|---|
| `PYTHONPATH=bridge poetry run pytest -q tests/unit/test_source_assessment_recovery.py` | 7 passed |
| Production-composition recovery endpoint integration slice | 1 passed |
| `PYTHONPATH=bridge poetry run pytest -q tests/unit/bootstrap/test_source_assessment_repair.py` | 3 passed, including rollback-checkpoint advance |
| Existing noise-filtering, source-assurance, and updater regression slices | 91 passed |
| Targeted Ruff checks for changed Python modules | Passed |
| Targeted mypy checks for recovery, assurance, runtime, and updater modules | Passed |

## Final-head local release gates

| Gate | Result |
|---|---|
| `poetry sync --with dev` | Current project installed; no dependency change |
| `PYTHONPATH=bridge poetry run pytest -q` | 1,645 passed; one upstream Starlette deprecation warning |
| `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q` | 55 passed; one upstream Starlette deprecation warning |
| `bash scripts/acceptance/release-candidate.sh` | PASS: 1,812 root/package tests, 60 bridge tests, Ruff format/lint, mypy, Pyright, both dependency audits, performance, builds, architecture-negative, clean-install, tenant-isolation, backup/restore, OpenClaw contract/test/build/audit, shellcheck, syntax, and diff checks |
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

The first direct macOS rehearsal resolved its hard-coded `python3` to a host
interpreter without the candidate environment and failed before lab mutation
with `ModuleNotFoundError: No module named 'yaml'`. Re-running with the reviewed
Poetry Python 3.14 environment first on `PATH` passed. The first direct Gate 7
command disclosed Node v22.23.2 against the required `>=24.15.0 <25`; that
result was not counted. The harness was reinstalled and rerun with Homebrew
`node@24`, producing the 259-check result above.

These results prove candidate mechanics and local release readiness only. Live
evidence must be recorded only after separately authorized guarded deployment.

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
