# CO-018 source-time and context-correction evidence

Date: 2026-08-13

Status: PR #338 merged at reviewed head `ebefb5c` as merge commit `5b6c140`.
Guarded activation failed its post-switch Doctor and automatically restored
active release `d181fad`. The merged release is not active. A forward-only
repair candidate now has a failing-then-passing concurrency regression,
focused source-repair proof, complete supported local validation, and passing
hosted checks on its first published head. A later read-only live preflight
found exact baseline drift described below, so that head is superseded and
fresh hosted checks plus independent review remain pending. No provider-called
terminal effect occurred.

## Guarded activation failure and preserved rollback evidence

- Pre-switch status and 21/21 Doctor checks passed after the separately
  authorized predecessor restart cleared an unrelated Google Calendar 503.
  Observation was active, source assurance was 63,610/63,610 with zero
  pending/missing/failed, and the provider-called terminal count was zero.
- The updater attested immutable merged commit `5b6c140`, used no runtime
  migration flag, prepared the reviewed OpenClaw contract, created encrypted
  backup `administrateme-20260813T162012Z.ambackup` with mode 0600, validated
  both LaunchAgent definitions, and selected the predecessor as rollback.
- Post-switch Doctor failed `launchd_and_process_state` and
  `source_assurance_terminal_dispositions`. Automatic rollback restored the
  `d181fad` selector and predecessor service definitions. The candidate release
  directory and encrypted backup were retained for diagnosis and recovery.
- Four historical message sources received current v21 failed assessments with
  reason `language_seam_IdempotencyConflict`. A controlled concurrent wrapper
  regression reproduced the exact mechanism: two identical invocations both
  passed the initial absence check, one recorded the durable result, and the
  loser's different result hit the unique idempotency boundary. The pipeline's
  broad non-authoritative failure fallback then incorrectly made that storage
  race a terminal source failure.
- The repair makes the first durable `skill.call.recorded` event canonical for
  every losing exact concurrent caller. The regression failed before the code
  change and passes afterward; the widened wrapper, pipeline, recovery,
  updater/rollback, runtime, macOS operations, and production context journey
  slice passes 173 tests. The shared-runtime case proves one model request;
  the separate-runtime case proves append-boundary convergence.
- Rollback did not and must not delete candidate-appended events. The four
  failures are therefore declared for forward recovery through the existing
  typed request/v4 replacement contract.
- During candidate validation, restored generation 20 advanced to exact source
  `ev_01KZV4FY0B0000`, repeated an append-only replay collision five times,
  and persisted its normal poison state at checkpoint
  `ev_01KZV3JKPE0000`. One older source remains failed with the already
  allowlisted `language_seam_OpenClawUnreachable` reason, and new ordinary
  messages accumulate behind the paused consumer. The live readiness document
  remains available and content-free; observation remains active.
- The amended release exception is bound to active commit `d181fad`, the
  63,610-obligation floor, all five exact failed source IDs/reasons, the exact
  v20 checkpoint/poison/failure state, zero pending, and no unrelated
  degradation. It admits at most 100 later `messaging.received` obligations
  carrying only the canonical after-checkpoint/grace missing reason. The
  stopped audit requires that same single poisoned worker, every other durable
  consumer healthy, an unchanged event watermark, and Gateway-live quiescence.
  Doctor's reactive-pipeline, aggregate launchd, and source-assurance failures
  are permitted only as that exact declared combination; every other Doctor or
  capability failure remains fatal.
- Observation has never been disabled and the provider-called terminal count
  remains zero. No member decision, canonical object, external provider write,
  credential, permission, runtime baseline, or provider enrollment changed.

## Forward-repair validation

- Focused change-path slice: 173 passed.
- Configured root suite: 1,706 passed.
- Strict acceptance: 63 passed.
- Complete aggregate: PASS with 1,875 root/package tests and 60 bridge tests;
  Ruff lint/format, mypy, Pyright, dependency audits, performance, builds,
  architecture-negative checks, clean install, tenant isolation,
  backup/restore, pinned OpenClaw contract/17 tests/build/audit, shell checks,
  and diff checks all passed.
- Lifecycle catalog freshness and repository invariants passed.

The first pre-single-flight aggregate attempt reached 1,871 passing root/package tests
and then hit the previously documented intermittent Comms snooze assertion:
the immediate read observed `focus` after a successful snooze command. The
exact test passed on isolated rerun. The complete aggregate was rerun from the
beginning and passed. After tightening the repair to shared-runtime
single-flight plus cross-runtime first-writer convergence, the focused, root,
and complete aggregate suites were rerun again; only those final results are
counted above.

## Implemented proof surface

- Gmail adapter v1.3.0 emits `messaging.received@v4` and
  `messaging.sent@v4`, preserving provider message time in envelope
  `occurred_at`, RFC continuity metadata, and the HTML MIME alternative for
  historical Sent observations.
- Noise filtering v21 and `classify_message_nature` v7 use
  `message-context-pack-v2` and `triage-context-interpretation-v2`.
- Context assembly enforces 30-day/100-source provider-thread episodes and
  strong cross-thread edges. Repeated names, addresses, participants, subjects,
  and filenames alone are excluded. Each related source records the exact
  admitting primary edge, so a later thread anchor cannot resurrect a rejected
  pair. Gmail message-local attachment IDs remain non-admitting.
- Missing/invalid Gmail provider time quarantines without ingestion-time
  substitution. HTML-only authored text reaches the pinned seam; uncertain
  quote signals terminate as no-candidate. For multipart Gmail, an explicit
  HTML quote boundary constrains the plain alternative and mismatch fails
  closed before deterministic fallback or the language seam.
- Retained Gmail Sent v1-v3 facts have no HTML quote-boundary provenance. Their
  immutable bodies are excluded from authored evidence with the explicit v21
  assessment reason `legacy_gmail_sent_quote_boundary_unavailable`. Replay
  supersedes only untouched system proposals rooted in those bodies and
  preserves member-enriched or decided proposals. Supersession v3 binds the
  proposal revision inspected by replay; deterministic projection
  compare-and-apply ignores the receipt if member action won append order.
- Triage displays Message date separately from Imported / recorded time and
  offers exact wrong-context relationship/text corrections only when the
  server declares sufficient relationship provenance or uniquely grounded
  authored evidence. Exact admitting edge sources outside the model display
  window are separately privacy-authorized and hydrated.
- The 24,000-character model payload reserves the latest anchor and retains the
  source under assessment, while replay reconciliation covers the complete
  bounded episode rather than only the 12 displayed messages.
- Four append-only correction/reversal events rebuild into Context Fabric v2.
  Text correction is proposal/candidate/digest/span scoped and preserves any
  other authored ask; the original source and member decision history remain
  immutable.
- Full replay is enabled for the reviewed pipeline version; untouched stale
  context proposals retire and member-decided proposals remain permanent. An
  unchanged context-free same-source deterministic fallback that remains
  eligible under the current high-signal rule keeps its existing pending
  proposal instead of being superseded and then suppressed as a historical
  duplicate. If the prior fallback carries stale contextual interpretation,
  replay supersedes that old proposal, excludes its exact event from active-
  history dedupe, and emits the current context-free fallback under a distinct
  replay-versioned proposal identity. Projection admits that replacement only
  when the predecessor bears the exact successful system
  `eligibility_rule_replaced` disposition. Enrichment, dismissal, confirmation,
  and later terminal states that defeat stale supersession therefore suppress
  the replacement and cannot become duplicate or reopened work.

## Focused evidence

The following suites are the minimum AC-43 evidence and use tenant-neutral
fixtures only:

- `tests/unit/test_message_context.py`
- `tests/unit/test_pipeline_noise_filtering.py`
- `tests/unit/test_projection_context_fabric.py`
- `tests/integration/test_adapter_supervisor_gmail.py`
- `tests/acceptance/test_context_correctness_vertical_slice.py`

The production-composition tests prove provider time differs from append time,
strong-edge admission, the member dismissal/correction batch, exact detach,
explicit relink and text reinstatement, projection state, and rebuild equality.
They also map actual Gmail adapter facts, seed a v20 durable consumer
checkpoint, start the production v21 runner, prove full replay, supersede the
untouched crossed-wire proposal even when its source is outside the 12-message
model window, exclude weak/cross-member sources, and retain one valid
reviewable sibling. Production composition also maps quoted-only multipart
Sent history through `messaging.sent@v4`, proves a terminal no-candidate result,
replays a retained Gmail Sent v3 quoted-only body from a v20 checkpoint into an
explicit no-candidate assessment and append-only supersession of its seeded
untouched v20 false commitment, and hydrates a displayed related message's exact
admitting edge from outside the display window. The tests create no canonical
record or provider effect. A controlled handler interleaving appends member
enrichment after descendant inspection but before supersession; the production
projection process then proves the stale v3 receipt cannot dismiss that item.
It then appends the replay replacement and proves the enriched predecessor
suppresses that row deterministically. Parallel dismissal and confirmation
cases prove terminal member decisions remain terminal through the same event
order.
The same Gmail replay process also seeds a still-eligible v20 same-source
deterministic fallback carrying stale contextual metadata, records the v21
eligible assessment, supersedes the old contextual proposal, and proves
exactly one distinct context-free replacement remains pending without a
duplicate or queue disappearance. The handler-level proof separately retains
an unchanged context-free prior without replacement.

## Validation record

The complete supported local command set passed on Python 3.14 and Node
24.19.0:

- `poetry sync --with dev`: lock satisfied; no dependency change.
- The 16-file changed-path review slice passed 188 tests.
- `PYTHONPATH=.:bridge poetry run pytest tests packs -q --import-mode=importlib`:
  1,871 passed. The ordinary configured root discovery
  (`PYTHONPATH=bridge poetry run pytest -q`) passed 1,702 tests under
  `pyproject.toml`'s `testpaths = ["tests"]` boundary.
- `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q`: 63
  passed.
- Bridge package suite: 60 passed; bridge Ruff, mypy, and audit passed.
- `poetry run ruff check adminme bridge/adminme_bridge` and the additional
  unscoped `poetry run ruff check .`: passed.
- Full mypy: 242 source files passed; Pyright reported zero errors, warnings,
  or information diagnostics; Ruff confirmed 538 files formatted.
- `bash scripts/verify_invariants.sh`: passed.
- Node 24 `npm ci`: 0 vulnerabilities; `npm run check`: TypeScript and the
  pinned OpenClaw 2026.7.2 contract at
  `ce67ffb70e83e5ebb56ea02e814e577153f84f61` passed; `npm test`: 17 passed.
- Lifecycle catalog freshness, JavaScript syntax, and `git diff --check`:
  passed.
- Performance budgets, architecture-negative checks, clean install, tenant
  isolation, backup/restore, Python and bridge builds, and the aggregate
  release-candidate script passed.

There were no test skips. An earlier pre-final candidate aggregate attempt
stopped in the unrelated Comms snooze acceptance gate because its immediate
read observed `focus` instead of `snoozed` after a successful command. That
exact test passed on an immediate isolated rerun; the next complete aggregate
passed, and the final candidate's expanded complete aggregate passed from the
beginning. The first legacy-Sent safeguard aggregate then correctly rejected a
current synthetic Gmail Sent fixture that still declared schema v2; the fixture
was advanced to the current v4 contract, its targeted process passed, and the
complete aggregate passed from the beginning with the counts above. Pytest
emitted the existing Starlette/httpx
deprecation warning. `npm ci` reported that the existing
`esbuild` and `fsevents` install scripts are not yet listed in `allowScripts`;
installation, audit, contract, typecheck, and tests still completed
successfully. Pyright reported that a newer release is available, and Poetry
reported its existing deprecated metadata-key warnings. Dependency audit
skipped only the two local, non-PyPI project packages and found no known
third-party vulnerabilities.

An internal exact-head advisory found that the first legacy-Sent safeguard
emitted the correct v21 no-candidate assessment but returned before retiring an
untouched v20 proposal already rooted in that source. The repair adds exact-
source append-only supersession, preserves member-enriched/decided proposals,
and extends the production v20-to-v21 fixture with the old false commitment
itself. The complete aggregate was rerun from the beginning and passed with the
then-current counts. A second internal exact-head advisory exposed an
inspection-to-append race in that repair: a member enrichment committed after
the descendant read could still be followed by a system supersession.
`triage.item.superseded@v3` now carries the inspected proposal revision, the
projection performs deterministic compare-and-apply, and controlled unit plus
production-composition interleavings prove a newer member revision remains
pending. The complete aggregate was again rerun from the beginning and passed
with the final counts above. These advisories are not the separate user-visible
independent review required by ADR-0009. No Phase B action is authorized by this
evidence.

The separate user-visible review of exact head `12c4a16` then rejected the
candidate for two findings: replay superseded a still-valid same-source v20
fallback and suppressed its v21 replacement because the historical candidate
key already existed, and rollback prose named `messaging.sent@v3` instead of
the emitted `messaging.sent@v4`. The repair treats an exact unchanged
context-free fallback as retained review work, adds handler and production
composition regression proof, and corrects the schema reference. The amended
worktree then passed the 185-test changed-path slice, the complete aggregate
from the beginning, the separate 1,699-test configured root discovery, and the
invariant scan; the counts above now describe that fresh run. The replacement
commit still requires hosted checks and a fresh independent exact-head review.
No Phase B action is authorized.

The next separate user-visible review of exact head `c915c1c` rejected the
candidate because the first repair exempted only prior proposals without an
interpretation dictionary. `merge_intent_candidates` can attach contextual
metadata to an overlapping deterministic `message-fallback-*` identity, making
the disappearing-item path reachable. The amended implementation returns the
exact proposal-event set superseded by contextual reconciliation, omits only
that set from active-history dedupe, and derives a distinct deterministic
proposal ID from the replay-versioned proposal key. The regression proof now
covers both prior shapes through the handler and the contextualized shape
through actual Gmail mapping, the production v21 runner, projection, and member
Triage API. The amended worktree then passed the 186-test changed-path slice,
the complete aggregate release-candidate suite from the beginning, the
separate 1,700-test configured root discovery, and the invariant scan; the
counts above describe this fresh run. The rejected receipt grants no Phase B
or release authority, and the amended head still requires hosted checks and a
fresh independent review. Final audit added projection-level closure for the
companion member-race case described above. The race-hardened worktree then
passed the 186-test changed-path slice, complete aggregate release-candidate
suite from the beginning, separate 1,700-test configured root discovery, and
the invariant scan. The counts above remain current; hosted checks and fresh
independent review are still required for the final head.

The following independent review of exact head `93bc404` rejected the candidate
because the replacement guard covered only active predecessor states. A member
dismissal or confirmation could win after descendant inspection, make the
revision-bound supersession a no-op, and still be followed by a new pending
replacement. The amended projection no longer infers authority from a broad
state class: it allows a replay replacement only after the predecessor carries
the exact system `eligibility_rule_replaced` disposition. The production race
case is parameterized across enrichment, dismissal, and confirmation and first
failed exactly for both terminal decisions before the repair. All three now
preserve the member revision and suppress the replacement. The amended worktree
then passed the 188-test changed-path slice, complete aggregate release-
candidate suite from the beginning, separate 1,702-test configured root
discovery, and invariant scan; the counts above describe this fresh run. The
rejected receipt grants no Phase B or release authority, and the amended head
still requires hosted checks and fresh independent review.
