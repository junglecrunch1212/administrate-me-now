# Prompt 14b: Primary member surfaces and deterministic Daily Brief

**Phase:** v2 Gate 7 member experience; reconciles BUILD L5 through D17,
D18, D26, and D27.
**Depends on:** production composition root, authorized projections, member
session/scope enforcement, and the pinned OpenClaw plugin seam.
**Estimated duration:** 7–9 hours.
**Stop condition:** Today, Triage, People, Knowledge, and Finance render from
the one Python runtime; Today actions round-trip through typed events; strict
process and browser gates pass.

## Read first

1. `docs/SYSTEM_INVARIANTS.md`, especially domain separation, read-time
   calendar merging, and child/view-as scope.
2. `docs/DECISIONS.md` D17, D18, D26, and D27.
3. `docs/refactor/UX-BEHAVIOR-CONTRACT.md` and
   `docs/refactor/BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md`.
4. `ADMINISTRATEME_CONSOLE_REFERENCE.html` only as historical interaction
   vocabulary; do not copy its client-only authorization, mock household data,
   `innerHTML`, Inbox semantics, or false Node application topology.

## Objective

Implement the primary authenticated member surfaces as read models and narrow
typed command routes served by the one long-lived AdministrateMe Python
runtime. Browser assets may be built with Node, but Node is not a second
application runtime. OpenClaw owns generic chat and model routing; the pinned
plugin may consume the same deterministic surface contracts.

## Required surfaces

### Today

- Compose `deterministic-daily-brief-v1` at read time in the configured IANA
  timezone. Do not create a Daily Brief store.
- Include authorized local-day calendar events, Tasks, Commitments, provider
  List Items, individual Recurrences, ordered Routine occurrences, actionable
  Interactions, and pending Triage questions as distinct kinds.
- Default to Day Brief and provide an explicit one-item Carousel over the same
  records. `Later` changes only the browser index.
- `What Now?` chooses an in-progress/next-30-minute calendar event first, then
  the deterministic explained work ranking. No LLM orders or completes work.
- Route each state change through its own idempotent event command. Calendar
  time is not attendance. Routine steps are ordered, dated outcomes and do not
  implicitly complete linked Tasks.
- The pinned OpenClaw Today binding consumes this same contract.

### Triage

- Show derived candidates awaiting the member's confirm, enrich/reroute, or
  dismiss decision per D26. Raw observations and official open work do not
  enter this queue.
- Show source evidence and exact target routing before confirmation.
- Keep old Inbox paths only as explicitly deprecated, non-navigational aliases.

### People, Knowledge, and Finance

- People is the scope-filtered CRM surface over Parties, Relationships,
  Interactions, Commitments, and evidence.
- Knowledge is the read surface over member/household knowledge, Artifacts, and
  Lists. It has no quick-capture prefix router, direct voice upload, or Triage
  mutation. Provider List Items remain distinct from Tasks.
- Finance is scope-filtered and principal/adult-only where required. It must
  not expose mock or privileged values.

## Authorization and privacy

- Navigation is UX; every page, JSON read, and command route authorizes again
  on the server.
- `authMember` authorizes writes and `viewMember` selects only an allowed read.
  View-as never grants the viewed member's write authority.
- Child sessions receive Today and Scoreboard only. Private calendar content is
  redacted server-side before composition.
- Inputs and evidence use safe DOM text assignment. No household identifier,
  credential, or exemplar person appears in platform code or tests.

## Process acceptance

Tests must start the production composition root and prove:

- local-time deterministic replay of the Daily Brief;
- task/commitment/list/recurrence/routine/calendar/message/question separation;
- ordered Routine complete/skip progression, stale-write refusal, idempotent
  retry, and projection rebuild equivalence;
- member-confirmed calendar outcome and no elapsed-time inference;
- child, cross-owner, and view-as denial;
- portal/OpenClaw contract equality;
- responsive Day Brief/Carousel/What Now controls with no browser, console,
  accessibility, or overflow failures.

Run the supported root, strict acceptance, Ruff, mypy, invariant, pinned
OpenClaw, and Gate 7 browser suites. Retain rendered and machine-readable
evidence under `docs/refactor/evidence/gate-7/`.

## Stop

> Primary member surfaces are production-composed and scope-safe. Today is a
> deterministic local Daily Brief with typed one-item execution, including
> ordered Routine occurrences. Triage is the only derived-candidate human gate.
> No second runtime, model decision path, or copied truth store was added.
