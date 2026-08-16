# Google Calendar poll-health recovery evidence

Date: 2026-08-16

Status: draft PR #342 remediation candidate on
`codex/google-calendar-recovery-health`; merge, deployment, live restart,
recovery, provider action, and release evidence are not authorized or
performed.

## Content-free diagnosis and exact-head rejection

- Repository base is exact `origin/main`
  `88bd571881bc0636ff90b12cb906626018978563`, including the merged PR #341
  mixed-backlog guards. The candidate contains that base without a rebase,
  force-push, or history rewrite.
- Active release and both LaunchAgents remained on
  `d181fad24ef488a71ce5568cc40fb81309cb3817` during the original read-only
  diagnosis. This remediation task did not inspect provider content or alter
  installed state.
- The original diagnosis remains valid: the active Calendar worker retained a
  transient error even after later content-free Calendar fact counts proved
  successful polls, while Gmail and Google Tasks restored current running
  health after the same transient-then-success sequence.
- Independent G-001 review rejected exact PR head
  `9d31d95b300cd38a4c9c6d012fe8aad9a07f3a5a` because an event without a stable
  provider ID could be skipped while its token advanced, a successful branch
  could publish `running` while the exact durable circuit remained open, and
  the recovery tests replaced the real poll with a bare integer-return stub.
- Classification remains **repository code defect**, now narrowed to poll
  accounting, durable cursor/circuit transition ordering, and production
  health consumption. It is not an operational restart, provider repair,
  configuration/permission change, or runtime migration.

## Bounded MODERNIZE repair

- Every fetched provider event is preflighted before any page fact is appended.
  Missing or malformed IDs, required times, parse failures, unsupported nested
  or collection shapes, invalid pages/tokens, provider page failures, and
  durable append failures fail the poll. The previous durable watermark is
  preserved and the instance stays non-running with `adapter.error` evidence.
- Calendar source dedupe records one fact identity without resetting circuit
  health. Successful poll completion uses the existing `updated_at_ms` as a
  monotonic compare-and-swap token: provider facts are durable first, the exact
  lease-bound watermark persists next, the matching closed/half-open circuit
  closes in the same SQLite transaction, and only then can current health
  become `running`.
- Open circuits refuse provider polling until the existing governed
  `begin_recovery` transition produces a half-open trial. A newer failure,
  another instance, lease transfer, cursor persistence failure, or cross-process
  race fences stale completion. Status and Calendar write admission both read
  the exact durable circuit and fail closed.
- Production readiness and Doctor treat only configured production-loader
  adapters as required. Disabled and unconfigured optional adapters remain
  explicit and nonblocking. Observation-only remains a healthy configured
  inbound state; no provider-write or approval path is added.
- Restart decodes the durable per-calendar token map, reuses deterministic
  provider-revision idempotency, and re-observes an unadvanced page without
  duplicating already durable facts. No schema or data migration is required.

## Executable proof

- Characterization before implementation failed in exactly three ways: the
  missing-ID page reported `running` and advanced its token, the open-circuit
  branch called the provider, and the old supervisor proof returned `0` from a
  substituted `_poll_once`. All three now use the real protocol and pass.
- Unit and integration coverage includes malformed pages/items/IDs/required
  fields/times/shapes, append and cursor persistence failures, open and
  half-open states, repeated failures, recovery-then-failure, restart, replay,
  multiple instances, stale success versus concurrent failure, cross-process
  lease release, fatal authentication, and admission before any provider call.
- Credential-free runtime composition uses the production adapter loader,
  encrypted event log, cursor store, supervisor, readiness endpoint, and member
  Settings composition. It proves a transient failure followed by a realistic
  event and deletion, durable facts, token/circuit completion, truthful
  readiness, replay without duplicate facts, and malformed-data HTTP 503.
- Doctor proves configured degraded Calendar blocks its adapter-health gate,
  recovered observation-only Calendar passes it, and a disabled optional
  adapter does not become required. Gmail, Google Tasks, and PR #341's exact
  received-240/sent-16/combined-256 and stopped-log rules have dedicated
  preservation suites.
- Gate 7 uses the existing authenticated Settings Connections card at desktop
  and narrow widths. Semantic assertions distinguish Backoff/open or malformed
  state from Running/closed/durable recovery, retain the observation-only copy,
  exercise the real synthetic polling protocol, and require zero synthetic
  provider writes.

The exact-bound Outcome Harness receipt, complete root/acceptance/release/macOS
results, supported Python 3.14/Node 24/npm 11 attestations, new immutable head,
hosted checks, and remaining `UNDETERMINED` findings are recorded only after
their final runs. The rejected exact-head review and evidence are not reused.

## Live and protected state explicitly not changed

No adapter restart or live recovery, OAuth action, permission or provider
configuration change, provider write, event/cursor edit, source-assurance
command, deployment, merge, selector/LaunchAgent edit, runtime migration, or
observation change occurred. PR #341 code and its live grouped/stopped-log
conditions were not executed or altered. `.claude/`, `.codex/`,
`FILE_INDEX.md`, `docs/map/`, and `docs/refactor/harness-research/` remain
unmodified, unstaged, and uncommitted; their contents were not opened by this
task.
