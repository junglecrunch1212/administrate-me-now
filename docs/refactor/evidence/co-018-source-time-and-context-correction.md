# CO-018 source-time and context-correction evidence

Date: 2026-08-12

Status: complete supported local validation passed; not independently reviewed,
merged, deployed, or run against a family Mac or live provider. No provider
action was performed.

## Implemented proof surface

- Gmail adapter v1.2.0 emits `messaging.received@v4` and
  `messaging.sent@v3`, preserving provider message time in envelope
  `occurred_at` and RFC continuity metadata.
- Noise filtering v21 and `classify_message_nature` v7 use
  `message-context-pack-v2` and `triage-context-interpretation-v2`.
- Context assembly enforces 30-day/100-source provider-thread episodes and
  strong cross-thread edges. Repeated names, addresses, participants, subjects,
  and filenames alone are excluded.
- Triage displays Message date separately from Imported / recorded time and
  offers exact wrong-context relationship/text corrections.
- Four append-only correction/reversal events rebuild into Context Fabric v2;
  the original source and member decision history remain immutable.
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

The production-composition test proves provider time differs from append time,
strong-edge admission, the member dismissal/correction batch, exact detach,
explicit relink and text reinstatement, projection state, and rebuild equality.
It creates no canonical record or provider effect.

## Validation record

The complete supported local command set passed on Python 3.14 and Node
24.19.0:

- `poetry sync --with dev`: lock satisfied; no dependency change.
- `PYTHONPATH=bridge poetry run pytest -q`: 1,674 passed.
- `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q`: 58
  passed.
- `poetry run ruff check adminme bridge/adminme_bridge` and the additional
  unscoped `poetry run ruff check .`: passed.
- `poetry run mypy adminme bridge/adminme_bridge`: 219 source files passed.
- `bash scripts/verify_invariants.sh`: passed.
- Node 24 `npm ci`: 0 vulnerabilities; `npm run check`: TypeScript and the
  pinned OpenClaw 2026.7.2 contract at
  `ce67ffb70e83e5ebb56ea02e814e577153f84f61` passed; `npm test`: 17 passed.
- Lifecycle catalog freshness, JavaScript syntax, and `git diff --check`:
  passed.

There were no test skips or failures. Pytest emitted the existing
Starlette/httpx deprecation warning. `npm ci` reported that the existing
`esbuild` and `fsevents` install scripts are not yet listed in `allowScripts`;
installation, audit, contract, typecheck, and tests still completed
successfully. No Phase B action is authorized by this evidence.
