# Google Calendar poll-health recovery evidence

Date: 2026-08-15

Status: local candidate on `codex/google-calendar-recovery-health`; merge,
deployment, live restart, and provider action are not authorized or performed.

## Content-free diagnosis

- Repository base: exact `origin/main`
  `88bd571881bc0636ff90b12cb906626018978563`.
- Active release and both LaunchAgents:
  `d181fad24ef488a71ce5568cc40fb81309cb3817`; runtime and OpenClaw processes
  remained running.
- Configured, enabled, guarded-admission-required instances: Gmail, Google
  Calendar, and Google Tasks. All three configurations validate through the
  production pack catalog and contain well-formed secret references plus
  member/account scope. No credential value or provider content was read into
  evidence.
- Gmail and Google Tasks report running/observation-only. Google Calendar alone
  reports backoff/degraded after a transient network error at
  `2026-08-14T09:54:57Z`, with supervisor restart count zero, no scheduled
  supervisor backoff, and durable circuit state closed.
- The same active process appended 72 `calendar.event_updated`, 10
  `calendar.event_deleted`, and 15 `calendar.event_outcome_recorded` facts after
  that error. Their identifiers and timestamps prove later successful provider
  polls without exposing payloads. Current health nevertheless remained
  backoff. Google Tasks experienced the same transient class one minute later
  and reports running because its loop already restores current state after a
  successful poll.
- Classification: **1 — repository code defect**. It is not an operational
  restart, provider reauthentication/configuration/permission problem,
  stale/orphaned instance, provider outage, intentionally disabled instance, or
  undetermined condition.

## Bounded repair and preserved boundaries

The Google Calendar poll loop sets current state to `running` only on the
successful branch. The existing transient and fatal branches, error event,
provider cursor/dedupe, replay, instance scope, observation write guard,
supervisor restart behavior, disabled-provider inventory, and guarded-admission
logic are unchanged.

The regression failed before implementation with `backoff != running` and
passes afterward. The production-loader/supervisor test proves one transient
error, a later successful poll, current inventory `running`, restart count zero,
and one retained `adapter.error`. Existing restart-dedupe, idempotency,
member/account/calendar scope, stopped-worker, disabled-provider, observation,
Doctor, and release-admission suites remain selected for candidate validation.

## Live state explicitly not changed

No adapter restart/recovery, OAuth action, permission or configuration change,
provider write, event/cursor edit, source-assurance command, deployment, merge,
selector/LaunchAgent edit, runtime migration, or observation change occurred.
Noise filtering remains v20 at checkpoint `ev_01KZP8DCKK0000`, poison
`ev_01KZP8DCKX0000`, and five failures; five prior recoveries remain pending.
Normal observation continued appending inbound source facts during the read-only
diagnosis, so obligation/missing counts advanced without any diagnostic write.

## Verification status

- PASS: failing-before/passing-after Google Calendar poll-health regression.
- PASS: production loader plus supervisor current-health composition.
- PASS: 28 Google Calendar unit and pack-internal tests.
- PASS: 23 Google Calendar supervisor, generic supervisor, and production
  runtime-composition tests.
- PASS: 79 macOS operations and guarded-updater tests, including fail-closed
  hidden-adapter degradation.
- PASS with the two unchanged documented `UNDETERMINED` findings: generated
  constitutional consistency (`KNOWN-PRODUCT-COUNT` and
  `KNOWN-CO016-STATUS`).
- Pending until recorded: aggregate local, hosted, and independent exact-head
  review receipts.
