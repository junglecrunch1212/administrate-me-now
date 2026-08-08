# CO-007 candidate evidence — capture, Triage, and immutable SSOT

Date: 2026-08-08
Branch: `codex/administrateme-v2-runtime`
Boundary: Phase A, disposable encrypted instances, credential-free adapters,
observation mode; no family-Mac deployment or real provider effect.

## Implemented event paths

Member thought:

`triage.intent.submitted@v1` → `triage.intent.features_extracted@v1` → zero or
more `triage.item.proposed@v2` records. The exact text is durable even when the
deterministic extractor produces no candidate. Candidate confidence is display
metadata only and never an eligibility threshold.

Local truth:

`triage.item.confirmed@v2` → distinct typed canonical event(s) →
`triage.item.materialized@v1`. The approval causes the effect; the effect never
retroactively causes the approval. Recent history records `confirmed_local`.

Provider path:

`member.provider_action.confirmed@v1` → signed OpenClaw allow-once resolution →
`openclaw.approval.received@v1` → `external_sync.write_requested@v2` → scoped
observation/provider result v2. Every terminal result carries the command event,
domain approval, OpenClaw approval, provider/account/operation, request
fingerprint, provider-called truth, origin proposal, correlation, causation,
owner/visibility scope, and sensitivity.

Stream path:

Gmail appends or explicitly quarantines each exact provider record before its
durable page watermark advances. Restart restores the shared cursor; duplicate
records do not re-emit; one malformed/404 record cannot hide later messages.
The message pipeline records model-returned communication features for display
but uses the shared deterministic extractor—not model candidates, scores, or
routes—to create Triage proposals.

## Safety proof

- Browser/public payloads containing `openclaw_approved` fail schema validation.
- Receipt signatures bind receipt id, tool name, exact request fingerprint,
  issue/expiry, allow-once decision, and exact OpenClaw version/commit.
- Changed content/tool, forged signature, expiration, receipt-id rebinding, and
  another member fail before the adapter.
- Atomic consumption is single-use. A same-member/same-body recovery may resume
  only the same idempotent command after an interruption; changed bindings still
  fail.
- OpenClaw source commit `944cf08` classifies
  `suppressCommandInterpretation` with system provenance as admin-only. The
  member relay omits all three fields and retains operator read/write scope.
- Observation suppression preserves the initiating private scope and records
  `provider_called=false`.

## Browser and vision evidence

The in-app browser exercised the production FastAPI member application on a
disposable instance at desktop width. Add for review showed one focused exact-
text input, an optional type hint, explicit “nothing official until confirm”
copy, and a named live result.

Natural dictation—calendar appointment, reminder, weekly recurrence, and a
commitment in one spoken-style paragraph—created four exact-span proposals.
Triage placed the recurrence in Ready and the calendar, reminder, and
commitment in Needs details. Confirming the recurrence moved it to Recent only
after `triage.item.confirmed@v2`, produced one audited materialization, and
displayed `confirmed_local`. This browser pass exposed and corrected both the
original comma/“and” clause-splitting defect and the misleading “Awaiting
OpenClaw” label for local truth.

## Executable evidence

Focused suites cover:

- signed receipt forgery/content/tool/member/expiry/recovery behavior;
- least-privileged pinned `chat.send` request shape;
- every supported deterministic intent kind, exact spans, ambiguity retention,
  and natural dictation;
- Triage chronology, replay, idempotency, source evidence, external pending
  action, local materialization, and terminal outcome linkage;
- Gmail durable cursor restart, dedupe, quarantine, later-message progress, and
  API-change degradation;
- checkpoint/lag/poison/backoff-derived surface health;
- plugin manifest, tool registration, approval hooks, and TypeScript contract.

Final candidate results:

- `PYTHONPATH=bridge poetry run pytest -q`: 1,467 passed;
- `PYTHONPATH=bridge poetry run pytest tests/acceptance --runxfail -q`: 33 passed;
- `poetry run ruff check adminme bridge/adminme_bridge`: passed, 203 source files;
- `poetry run mypy adminme bridge/adminme_bridge`: passed;
- `bash scripts/verify_invariants.sh`: passed;
- Node 24.19 `npm ci`, `npm run check`, `npm test`, `npm run build`, and
  `npm pack --dry-run`: contract/build/package passed and 14 plugin tests passed;
- architecture-negative checks: passed (no second model router, truth store,
  scheduler, or forbidden dependency direction introduced);
- production npm audit: zero vulnerabilities;
- desktop and 390×844 in-app browser: four proposal result, correct lanes,
  `confirmed_local`, zero console errors, and no horizontal overflow.

The Python suites report only the existing Starlette `TestClient` deprecation
warning. The complete npm dev tree reports one transitive
`nanoid@3.3.16` advisory below Vitest/Vite/PostCSS; production dependencies are
unaffected and CO-007 does not silently alter that unrelated toolchain lock.

Live Gateway process rehearsal, real provider read/write evidence, family-Mac
rollout, and observation exit remain separate activation gates.
