# CO-011 candidate policy and Triage context evidence

Date: 2026-08-09

Environment: disposable production-composition instance from
`tools/gate7_visual_server.py`, Python 3.14 candidate checkout, Chrome browser
control, observation mode, no provider credentials or provider effects.

## Process evidence

- Focused candidate policy, Triage context, message/Notes pipeline, lifecycle
  catalog, Settings, and Triage acceptance: 56 passed.
- Strict acceptance with expected-failure release gates enabled: 46 passed.
- Complete release root/pack Python suite: 1,740 passed.
- Isolated member bridge suite: 60 passed.
- Ruff, mypy (231 source files), constitutional invariants, native OpenClaw
  TypeScript contract, and 17 plugin tests passed.
- The full release-candidate script passed under Python 3.14.6 and the reviewed
  Node 24 candidate path, including Pyright, dependency audits, performance,
  wheel/sdist builds, architecture negatives, clean install, tenant isolation,
  encrypted backup/restore, npm build/pack/audit, and shell validation. An
  initial run correctly refused the shell's legacy Node 22; no check was
  waived and the live runtime was not changed.

## Browser interaction evidence

The authenticated adult Settings route
`/member/settings?section=candidate-identification` rendered:

- all ten stable member Add-for-review rows plus OpenClaw, locked to automatic
  review;
- current and planned email/chat, Calendar, Task/Reminder, Notes/transcript,
  Plaid, Home Assistant, and contact-source rows;
- separate adapter/source and candidate-engine states;
- provider/native signals, extraction layers, review/manual/quiet,
  grounded-language contribution, and target-kind ceilings;
- the six non-configurable privacy/truth/write invariants.

The browser changed disposable Gmail policy to `quiet`, disabled grounded
language candidate contribution, saved, reloaded, and observed the exact
success banner plus persisted values. It then restored `review`. No prompt,
credential, provider command, or external effect was involved.

An immutable fixture message produced a human-gated Commitment. Triage showed:

- `Fixture-Cat-A · Inbound`;
- adapter `fixture-messaging` and account `fixture-account`;
- exact sender `sender@fixture.invalid` and observed time;
- “AdministrateMe found a possible Commitment” with kind `reply`;
- the literal source quote “Could you reply today with the reviewed household
  decision?”;
- `Provider facts` method disclosure;
- the existing schema-driven destination/readiness/confirmation controls.

At a 390×844 override the document client and scroll widths both measured 375
pixels, the Triage workspace and Settings candidate grid each resolved to one
column, source facts stacked without breaking the sender identifier, and source
context remained visible. The viewport override was reset. Browser warning and
error logs were empty.

## Live release boundary

PR #311 passed both hosted checks and merged as
`cd49c60d608c1acf3e0baf9aa5f000c982382a66`. The attested same-runtime updater
created encrypted backup `administrateme-20260809T204055Z.ambackup`, retained
rollback, switched the active pointer, reconciled the pinned plugin and
LaunchAgents, and left observation active.

The replay generation advanced the live message, Notes, and structured-source
candidate engines to 11.0.0, 5.0.0, and 2.0.0. Message replay drained from 256
events of lag to zero with no consecutive failure. Source assurance then
settled current at 61,335/61,335 obligations: 28 candidate-bearing facts, 309
explicit no-candidate dispositions, 39 quarantined, and zero pending, missing,
or failed. Final Doctor is healthy and the canonical round trip records zero
external effects.

The authenticated live Settings page renders 33 current/future policy rows and
all eleven candidate kinds. Live Triage renders 100 visible records, including
billing/payment actions sourced from Gmail. One selected candidate visibly
shows source channel/direction, adapter/account provenance, sender, subject,
observed time, possible Task meaning, literal evidence, and deterministic
method badges. Browser warning and error logs are empty. The proof did not
confirm, dismiss, enrich, or route a candidate and did not authorize a provider
write.
