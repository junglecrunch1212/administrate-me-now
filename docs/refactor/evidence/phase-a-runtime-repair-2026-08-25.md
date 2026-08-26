# Phase A runtime-repair evidence — 2026-08-25

## Boundary and provenance

- Branch: `codex/administrateme-v2-runtime`.
- Base tree: current `origin/main` at Phase A start, `6d0f316`.
- Active-family release reference: `d181fad24ef488a71ce5568cc40fb81309cb3817`; repository ancestry was verified read-only. Active deployment content was not inspected or changed.
- Environment: disposable/local, credential-free tests; supported Python 3.14 and Node 24 execution.
- Prohibited operations not performed: deployment, selector or LaunchAgent change, provider write, live recovery, credential mutation, family-Mac experiment, merge, or history rewrite.
- Preserved user-owned untracked paths were excluded from candidate contents.

## Content-free starting evidence

Read-only diagnostics reported service-safe degraded readiness with observation
active, a retained source-assessment backlog behind a poisoned v20 consumer,
Gmail in durable open/backoff state, Calendar without durable provider progress,
and Tasks without durable poll ownership/progress. Counts and status classes
were used to reproduce defects; household content, credentials, and provider
payloads were not copied into code, tests, or this receipt.

## Root causes and disposition

1. Open adapter circuits had no production transition to a single half-open
   probe, and provider progress/lease ownership was incomplete or process-local.
   Additive fenced cursor state and complete-cycle commits harden recovery.
2. Provider write entrypoints could call directly when observation dependencies
   were omitted. They now fail closed.
3. A retained OpenClaw healthy report could outlive the reporting process, and
   service-safe degraded reads were easy to confuse with activation eligibility.
   Current-process heartbeat expiry and explicit readiness axes supersede that
   ambiguity.
4. Same-baseline update preparation selected ambient Node rather than the
   manifest formula. The formula-specific executable and exact version are now
   resolved and checked.
5. Context/proposal lookup repeatedly rescanned complete history, while restart
   stress could close the writer before every registered subscriber reached a
   fixed point. Incremental indexes and a 24-subscriber stable-log quiescence
   barrier preserve semantics and make the failure executable.
6. Triage keyset cursors had no inventory revision. Concurrent root promotion,
   resolution, or insertion could silently duplicate or omit a matter. A
   member/private-scoped revision now rejects stale continuation and the client
   performs an authoritative reload.
7. The deterministic Comms seed could return before noise-filter semantic
   features settled. A local command could then lose its projection CAS while
   the endpoint still returned success. The fixture now settles the semantic
   subscriber and local commands verify their intended projected state;
   concurrent loss returns a truthful 409.
8. The Finance round-trip process proof waited for an intermediate budget fact
   before asserting validation state owned by the later terminal
   `finance.workbook_published` receipt. The proof now waits that receipt's
   money-projection watermark, preserving the production daemon's asynchronous
   composition while removing the false race from release evidence.

The change **PRESERVES** event/source truth, replay, privacy, human decisions,
observation/governance, provider authority, and topology;
**MODERNIZES/HARDENS** durability, fencing, retry, health freshness, bounded
processing, quiescence, and pagination; and **SUPERSEDES** memory-only,
permanently-open, fail-open, ambient-runtime, indefinitely-stale,
full-rescan, and snapshotless behavior. No required product behavior is
deferred.

## Executable evidence

- Source stress fast profile: 48/48 accounted, zero missing/failed/pending,
  zero lag, equal rebuilds, 6.32 s end-to-end, 7.59 facts/s, 0.239 ms append
  p95, 7.49 MiB peak, 72,514 bytes/source.
- Source stress extended profile: 8,760/8,760 accounted, zero
  missing/failed/pending, all 24 subscribers at zero lag, equal digests for four
  rebuilt projections, 563.403 s end-to-end, 15.55 facts/s, 3.141 ms append
  p95, 68.65 MiB peak, 14,796 bytes/source. The end-to-end rate includes
  processing, recovery/retry, two restarts and quiescence, assurance, four
  rebuilds, and shutdown; it is not comparable to an earlier narrower
  settlement-only rate. Poison seeding is excluded.
- Adapter durability/safety: 142 focused tests passed; the broader authorized
  adapter slice passed 107 tests.
- Runtime/OpenClaw/bootstrap: 87 focused Python tests passed; pinned plugin
  type/contract checks and all 17 plugin tests passed.
- Triage/Context Fabric: 20 focused unit/production-composition tests passed,
  including 520/521-item traversal, exact focus, stale cursor, and isolation.
- Gate 7: 690/690 assertions passed with Node 24.19.0, Python 3.14.6,
  Chrome 151, and Playwright 1.62.0. The fixture projected 520 unique Triage
  candidates exactly once; all ten adult surfaces passed at 390, 820, 1024,
  and 1280 pixels plus documented 2× Chromium reflow. There were zero
  unexpected console warnings/errors, page errors, HTTP responses, request
  failures, or transport failures.
- Strict acceptance: 66 passed with one intentional opt-in extended-stress
  skip and one pre-existing Starlette/httpx deprecation warning.
- Shared-worktree configured root: 1,748 passed and one intentional opt-in
  skip; its sole failure was the identity invariant reading preserved,
  unrelated user-owned untracked documentation. The final clean-candidate
  aggregate result is recorded below after the candidate is frozen.
- Ruff and Mypy passed for the production Python tree; exact Node 24 install,
  check, contract, and test commands passed; diff hygiene passed.
- Direct Browser-skill inspection against a separate loopback fixture confirmed
  the semantic shell, ten destinations, one exact `Reply in this thread`
  textarea (`id=comms-reply`), blank-reply `aria-invalid` plus `state-banner`
  association, explicit “Nothing was sent” feedback, and zero console
  warnings/errors.

## Bounded remaining risk

Calendar and Tasks fence final mutations but cannot renew a lease during one
blocking provider SDK call. If a single call exceeds the lease TTL, the stale
worker cannot commit progress or error state, but a successor can cause a
duplicate provider read and idempotently keyed emission attempt before the
stale worker observes lease loss. Eliminating that read amplification requires
a separately bounded async/provider-call wrapper; it does not justify weakening
fencing or progress correctness.

## Final clean candidate and artifacts

- Validated implementation commit: `1d2500095b30c2c233580646f15a21f175e6f963`.
- Exact clean aggregate command: `bash scripts/acceptance/release-candidate.sh`
  — PASS under Python 3.14.6 and Node 24.19.0.
- Strict acceptance: 66 passed, one intentional opt-in extended-stress skip,
  one pre-existing Starlette/httpx deprecation warning.
- Root/package aggregate: 1,918 passed, one intentional skip, the same warning.
- Exact configured root command (`PYTHONPATH=bridge poetry run pytest -q`):
  1,749 passed, one intentional skip, the same warning.
- Bridge: 60 passed. Ruff, format, Mypy (244 files), production Pyright, package
  builds, architecture, dependency audit, clean install, tenant isolation,
  encrypted backup/restore, OpenClaw contract/check/17 tests/build/pack/audit,
  shell syntax, invariants, diff hygiene, and Gate 7 passed.
- Gate 7: 690/690 assertions, 520 real fixture candidates exactly once, zero
  unexpected browser console/page/request/HTTP failures.
- Retained source/package hashes are in the external Phase A artifact manifest
  generated from the final documentation commit; the manifest names its exact
  commit and dirty state to avoid a self-referential archive hash.

Hosted CI and owner review remain pending. Phase B may begin only after ordinary
pull-request review/merge and a separate owner instruction.
