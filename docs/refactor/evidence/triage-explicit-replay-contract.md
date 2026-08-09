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

## Live activation evidence

PR #294 passed both required GitHub checks, merged as
`c484f0a308b5dfcbf599db221304170555806fef`, and deployed with rollback backup
`administrateme-20260809T002636Z.ambackup`.

- `noise_filtering` 7.0.0 began at 342 pending source messages and reached zero
  lag with zero consecutive failures and no poison event.
- 354 immutable message facts produced 83 audited 4.0.3 successes and four
  fail-closed `openclaw_unreachable` terminals; duplicates reused successful
  recorded results.
- v7 appended 26 grounded proposals from 19 source facts: nine tasks, twelve
  reminders, three calendar events, and two party identifiers.
- The final projection contains 86 pending proposals across 50 source facts:
  65 tasks, 13 reminders, three calendar events, two recurrences, two party
  identifiers, and one relationship fact.
- The refreshed private portal exposes all 86: 19 need details and 67 are
  ready. Browser console error count was zero.
- Full Doctor is healthy, including exact source, authenticated `llm-task`,
  fourteen projections, six required subscribers, loopback services, cron,
  and the canonical synthetic round trip.
- Human-gate outcomes and provider/external-write events remain zero.

Signal-quality limitation: 60 proposals predate v7, and 56 of those use the
broad deterministic `explicit_action_verb_v1` rule. Live review proved that
this set contains boilerplate and marketing noise. The records remain pending
rather than being silently dismissed. A follow-up reviewed rule and projection
change is required to supersede broad inbound eligibility while retaining the
26 grounded v7 proposals.
