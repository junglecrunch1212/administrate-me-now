# CO-013 bounded mailbox history acquisition evidence

Date: 2026-08-10

Status: candidate evidence; live fields intentionally pending

## Failure evidence

- The live authorized Comms projection contained only one retained source
  message for the reviewed multi-message matter and no linked Triage proposal.
- The Gmail adapter's first-use branch called `users.getProfile`, stored the
  returned `historyId`, returned zero, and never called `users.messages.list`.
- Therefore, source assurance correctly accounted for the facts in the event
  log but could not certify provider messages that were never acquired.

No household message text, identifier, address, token, or credential is
retained here.

## Candidate proof

- Focused Gmail adapter, send, retrofit, and production-supervisor suite: 50
  passed.
- Full root/pack/bridge/acceptance release matrix: 1,753 root/pack, 60 isolated
  bridge, and 47 strict acceptance tests passed; Ruff/format/mypy/Pyright,
  dependency/security/architecture/invariant checks, performance budgets,
  builds/packages, clean install, tenant isolation, encrypted backup/restore,
  exact native OpenClaw contract, and 17 plugin tests passed.
- Hosted constitutional cascade and macOS rehearsal: pending.
- Reviewed merge: pending.
- Guarded observation deployment and encrypted backup receipt: pending.
- Settled source assurance and full Doctor: pending.
- Authenticated Comms/Triage proof of recovered multi-message context: pending.

## Safety assertions

- The current incremental cursor is snapshotted before acquisition.
- Bounds are validated and capped; drafts and spam/trash are excluded.
- Per-message event identity, scope, direction, dedupe, and quarantine are
  unchanged.
- Completion state is metadata-only and policy-digest scoped.
- Acquisition appends observations only. It performs no provider mutation,
  candidate confirmation, canonical materialization, or member decision.
