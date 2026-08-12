# CO-018 source-time and context-correction evidence

Date: 2026-08-12

Status: complete supported local validation passed; not independently reviewed,
merged, deployed, or run against a family Mac or live provider. No provider
action was performed.

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
  context proposals retire and member-decided proposals remain permanent.

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
and hydrates a displayed related message's exact admitting edge from outside
the display window. The tests create no canonical record or provider effect.

## Validation record

The complete supported local command set passed on Python 3.14 and Node
24.19.0:

- `poetry sync --with dev`: lock satisfied; no dependency change.
- `PYTHONPATH=bridge poetry run pytest -q`: 1,859 passed.
- `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q`: 60
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
beginning with the counts above. Pytest emitted the existing Starlette/httpx
deprecation warning. `npm ci` reported that the existing
`esbuild` and `fsevents` install scripts are not yet listed in `allowScripts`;
installation, audit, contract, typecheck, and tests still completed
successfully. Pyright reported that a newer release is available, and Poetry
reported its existing deprecated metadata-key warnings. Dependency audit
skipped only the two local, non-PyPI project packages and found no known
third-party vulnerabilities. No Phase B action is authorized by this evidence.
