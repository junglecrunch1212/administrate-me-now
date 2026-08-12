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
replays a retained Gmail Sent v3 quoted-only body from a v20 checkpoint into an
explicit no-candidate assessment and append-only supersession of its seeded
untouched v20 false commitment, and hydrates a displayed related message's exact
admitting edge from outside the display window. The tests create no canonical
record or provider effect. A controlled handler interleaving appends member
enrichment after descendant inspection but before supersession; the production
projection process then proves the stale v3 receipt cannot dismiss that item.

## Validation record

The complete supported local command set passed on Python 3.14 and Node
24.19.0:

- `poetry sync --with dev`: lock satisfied; no dependency change.
- `PYTHONPATH=.:bridge poetry run pytest tests packs -q --import-mode=importlib`:
  1,867 passed. The ordinary configured root discovery
  (`PYTHONPATH=bridge poetry run pytest -q`) passed 1,698 tests under
  `pyproject.toml`'s `testpaths = ["tests"]` boundary.
- `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q`: 61
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
