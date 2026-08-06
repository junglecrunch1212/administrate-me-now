# Today Daily Brief correction evidence

Date: 2026-08-06
Classification: **MODERNIZE** Today presentation and execution;
**PRESERVE** deterministic ranking, typed domain boundaries, event authority,
scope/privacy, governance, and pinned OpenClaw ownership.

## Proven behavior

- `/api/member/v1/surfaces/today` returns one
  `deterministic-daily-brief-v1` read-time composition in the configured IANA
  timezone. It does not expose the former raw `lists` section.
- Day Brief and Carousel render the same typed items. What Now selects an
  in-progress or next-30-minute timed event first, then the deterministic
  ranker's first work item. Carousel `Later` mutates only browser
  position.
- Tasks, commitments, provider list items, individual recurrences, ordered
  Routine occurrences, calendar events, messages, and clarifying questions
  retain distinct item kinds and action contracts.
- Trackable recurrence completion writes `recurrence.completed` and advances
  the recurrence projection. Calendar attended/missed writes
  `calendar.event_outcome_recorded` only after the event starts and records
  `member_confirmation`; calendar elapsed time is not treated as attendance.
- The pinned OpenClaw dashboard consumes the same Daily Brief contract. The
  brief and What Now ranking remain deterministic when model/chat is absent.

D27 adds the formerly missing ordered Routine contract. A Routine definition
contains an RFC 5545 rule, IANA timezone, local time window, and contiguous
steps. The production member route records only the current step's explicit
completed/skipped outcome for one dated occurrence, rejects stale or
out-of-order writes, and leaves any linked Task unchanged. Rebuilding the
recurrences projection reproduces the same occurrence state.

## Executable evidence

```text
PYTHONPATH=bridge poetry run pytest -q \
  tests/unit/test_today_brief.py \
  tests/unit/test_what_now_gate6.py \
  tests/unit/test_projection_calendars.py \
  tests/unit/test_projection_recurrences.py \
  tests/acceptance/test_gate7_household_experience.py
```

The process acceptance starts the production composition root with the real
encrypted event log, durable bus, projection runner, member HTTP routes, and
OpenClaw route. It verifies idempotent member actions and reads the resulting
projection state back through Today.

```text
npm --prefix tools/console-reference-harness run test:gate7
```

The harness retains Day Brief, interactive Carousel, responsive, child,
degraded-chat, reduced-motion, and other Gate 7 captures plus
`visual-results.json` under `docs/refactor/evidence/gate-7/visual/`.

## Final result

- Complete Python/bridge suite: **1,335 passed** (one upstream Starlette/httpx
  deprecation warning).
- Strict acceptance with `--runxfail`: **23 passed**.
- Ruff: passed. Mypy: passed across 188 source files. Invariant script and
  `git diff --check`: passed.
- Pinned OpenClaw TypeScript/contract check: passed. Plugin tests: **14
  passed**.
- Gate 7 rendered browser harness: **81/81 passed** in installed Google Chrome
  through pinned `playwright-core@1.62.0`; no failed result and no fixture
  listener remained.
