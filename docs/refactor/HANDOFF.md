---
schema_version: 1
current_phase: 2
phase_name: runtime_spine_replacement
gate: 2
gate_result: PASS
verified_local_date: 2026-07-29
branch: codex/administrateme-v2-runtime
branch_tip_resolution: git rev-parse HEAD
gate_1_artifact_sha: c4d682c42ff9c9833e59589422bac90eec590173
gate_1_artifact_tree_sha: 5bc5e3733c456cbffbf12dcd51ba0b1744889ab6
draft_pr_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
next_phase: 3
next_phase_name: close_the_two_golden_product_slices
---

# Handoff

## State

Gate 2 passes on `codex/administrateme-v2-runtime`. The branch preserves the Gate 1 artifact and replaces the disconnected central lifecycle with one production-shaped Python composition root. Resolve the immutable Gate 2 commit with `branch_tip_resolution`; this document cannot contain the hash of the commit that contains itself.

The runtime boots credential-free from a temporary encrypted instance, reports truthful readiness, registers exactly thirteen projection capabilities, accepts bridge ingest inside the same application, drains in bounded order, and reopens the SQLCipher log after repeated shutdown. The existing draft PR remains the only PR for this refactor.

## Gate 2 implementation

- `EventSink` is the typed asynchronous producer contract. `EventWriter` atomically appends a single event or batch to SQLCipher and notifies the bus only after commit. Failed appends never notify.
- Durable delivery is at-least-once. Checkpoints advance only after successful callbacks; retry and poison metadata persist; redelivery relies on idempotent projections.
- `RuntimeApplication` owns configuration/key validation, log, writer, one bus, projections, pipelines, adapters, xlsx services, OpenClaw standing-order capability reporting, product routers, bridge ingest, health, startup unwind, and bounded shutdown.
- The explicit projection inventory is: `parties`, `interactions`, `artifacts`, `commitments`, `tasks`, `recurrences`, `calendars`, `places_assets_accounts`, `money`, `vector_search`, `xlsx_workbooks`, `member_knowledge`, and `lists`.
- `member_knowledge` now has a registered schema, projection, and owner/scope-aware queries. This establishes the runtime capability; the full member bridge authorization/search/replay product slice remains Phase 3.
- Central adapters, adapter supervisor, pipelines, governance, observation, skills, xlsx paths, and central bridge ingest use the injected writer. Legacy synchronous and duck-typed writer probes are removed. Dual-deployment member bridges adapt remote ingestion to the same typed adapter context.
- Core, Comms, Capture/Knowledge, Automation, Bridge, and System are router namespaces in one FastAPI application. Optional providers are explicitly disabled with reasons and cannot make readiness falsely healthy.
- SQLCipher connection ownership, wrong-key/startup unwind, append rollback, lock portability, bridge task draining, repeated close, and encrypted reopen are covered by tests.

## Exact verification evidence

| Command | Result |
|---|---|
| `PYTHONPATH=bridge python -m pytest -q --disable-warnings --maxfail=20` | **PASS:** 1,234 passed, 3 skipped, 2 expected Phase 3 xfailed, 3 warnings in 116.31s. |
| `PYTHONPATH=bridge python -m pytest bridge/tests -q --disable-warnings` | **PASS:** 57 passed in 38.92s. |
| `PYTHONPATH=bridge python -m pytest tests/acceptance -q --disable-warnings` | **PASS:** 1 runtime-spine acceptance passed, 2 expected Phase 3 xfailed, 2 warnings in 5.18s. |
| `PYTHONPATH=bridge python -m pytest tests/acceptance --runxfail -q --disable-warnings` | **Truthful red gate:** runtime spine passed; exactly the two named Phase 3 product slices failed (2 failed, 1 passed). |
| `python -m ruff check . --output-format concise` | **PASS.** The frozen Plaid SDK reference is explicitly excluded from F401 only; first-party code is not blanket-excluded. |
| `python -m ruff format --check <all 134 changed/new Python files>` | **PASS:** 134 files already formatted. |
| `python -m mypy adminme bridge/adminme_bridge --no-error-summary` | **PASS.** |
| `C:\Program Files\Git\bin\bash.exe scripts/verify_invariants.sh` | **PASS.** |
| `git diff --check` | **PASS.** |

The warning count is existing deprecation/configuration noise; there is no unexpected skip or xfail. The `--runxfail` result is retained as negative evidence so the Phase 3 skeletons cannot masquerade as completed behavior.

## Gate 1 evidence retained

The exact Gate 1 console reference remains unchanged: source SHA-256 `98ac268f39c0d367f5c904ab2af5e0e35389eae8ff6bffd4cede7ef8bfdd0a3b`; 12/12 interactions; 68/68 DOM assertions; zero JavaScript/page/request failures; desktop image SHA-256 `8239050463ba5dac56b5759564fec11b94eb76a3c6361182922fa830acdd2f4b`; narrow image SHA-256 `97d1fe1129cfe2f6bc94d0797037d6fb6e7464ac0218d8dd11b49f25fc597701`.

## Remaining known failures and human action

- No Gate 2 test, lint, formatting, type, invariant, replay, shutdown, registration, namespace, or process acceptance check is failing.
- The communication-to-reviewed/completed-commitment slice and the member-bridge-to-private-searchable-knowledge slice remain strict xfails by design. They are Phase 3, not Gate 2 omissions hidden as success.
- A maintainer must review the draft PR before merge. No credential, destructive action, target-family-Mac access, or other human input is required to prove Gate 2.

## Exact next phase

**Phase 3: close the two golden product slices without rebuilding the runtime spine.**

1. Make `test_communication_commitment_vertical_slice.py` pass under `--runxfail`: credential-free inbound communication through durable delivery, Party/Interaction resolution, schema-bound OpenClaw extraction provenance, proposal/review/independent approvals, guarded and observation-safe action, API/workbook visibility, completion outcome, projection feedback, changed what-now, and restart idempotency.
2. Make `test_member_bridge_knowledge_vertical_slice.py` pass under `--runxfail`: enrolled member identity, central owner stamping, private `member_knowledge`, permitted non-privileged vector representation, member/child/ambient/cross-owner denial, correction history, replay equivalence, and two-instance isolation.
3. Preserve the single writer, single bus owner, explicit thirteen-projection registry, one-process namespaces, and Gate 2 test matrix. Do not replace the product assertions with mocks or relax the strict xfails.

Do not start with visual polish, optional providers, target-family-Mac installation, or a second runtime process.
