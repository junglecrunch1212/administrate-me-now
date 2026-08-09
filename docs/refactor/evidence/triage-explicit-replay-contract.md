# Triage explicit replay contract evidence

Date: 2026-08-08

## Live finding

PR #293 deployed successfully as `ec36783`: exact OpenClaw source selection,
Gateway-first health, runtime readiness, authenticated language execution, and
observation mode all passed. The live `noise_filtering` consumer reported
version 6.0.0, healthy, zero failures, no poison event, and zero lag. Aggregate
event-log evidence nevertheless contained no 4.0.2 classification or semantic
facts and no 4.0.2 skill terminal event. The bus had truthfully drained from its
retained checkpoint, but had not replayed historical source messages.

## Candidate repair

- `EventBus.subscribe` accepts an explicit `replay_on_version_change` flag.
- Pipeline manifests may select only `none` (default) or `full` replay.
- Full replay resets the operational checkpoint and health counters only when
  the reviewed consumer version changes; the append-only log is untouched.
- `noise_filtering` 7.0.0 is opted in and `classify_message_nature` 4.0.3 creates
  a new audited terminal identity.
- A SHA-256 invocation identity includes tenant/scope, source account, event
  direction, source timestamps/thread, and the exact bounded skill inputs. It
  reuses one recorded result for duplicate observations without storing a new
  plaintext identity or changing source causation.
- Candidate generation v7 deduplicates against baseline/v4/v5/v6 kind/key pairs.
- Exact duplicate source observations also share v7 proposal identities, so a
  historical duplicate append cannot multiply the member's review work.

## Candidate verification

- acceptance gates: 36 passed with `--runxfail`
- root and pack suite: 1,643 passed
- member bridge: 60 passed
- pinned OpenClaw plugin: 15 passed; contract, build, and package passed
- Ruff, Mypy, Pyright, invariant, clean-install, tenant-isolation,
  backup/restore, and performance gates passed
- focused replay/skill/pipeline suite: 69 passed

Observation mode remains active. Replay can add pending proposals only; it
cannot confirm, materialize, dispatch, or write to a provider.
