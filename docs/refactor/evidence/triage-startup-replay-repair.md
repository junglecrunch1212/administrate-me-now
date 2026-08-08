# Triage startup replay repair evidence

Date: 2026-08-08

## Guarded activation finding

PR #292 passed local and GitHub release gates and merged as `c96dbf8`. Its first
candidate-controller invocation omitted the instance-local executable selector
from `PATH`; Doctor failed closed and the previous release was restored. With
that invocation boundary corrected, the candidate selected the reviewed source
but launchd still started the Python runtime before the OpenClaw Gateway. The
reactive Triage consumer therefore began its reviewed replay while the language
seam was unreachable. The post-switch readiness gate rejected the candidate and
restored the previous exact release and source again.

Both failures occurred in observation mode, emitted no canonical materialization
and no provider write, and produced encrypted backups before mutation. The live
rollback was verified healthy after each attempt.

## Candidate repair

- OpenClaw launchd activation precedes the AdministrateMe runtime.
- The service manager accepts the Gateway only after the local `/health` response
  is valid and reports `ok=true`, `status=live` within a bounded window.
- Runtime liveness remains a separate cheap proof, followed by a bounded full
  readiness wait.
- `classify_message_nature` 4.0.2 and `noise_filtering` 6.0.0 create a reviewed
  replay boundary after the failed-start audit facts.
- Baseline, v4, v5, and v6 candidate history is deduplicated by exact target kind
  and deterministic candidate key; source facts and prior proposals are not
  rewritten.

## Candidate proof

- 72 focused service-order, replay, integration, and pack tests passed.
- The complete release-candidate suite passed under Node 24.19.0: 36 strict
  acceptance tests, 1,639 root/pack tests, 60 bridge tests, and 15 native plugin
  tests, plus type, lint, formatting, audit, performance, architecture-negative,
  clean-install, tenant-isolation, backup/restore, package, shell, and diff gates.
