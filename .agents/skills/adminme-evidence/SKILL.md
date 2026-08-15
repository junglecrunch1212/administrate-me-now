---
name: adminme-evidence
description: Validate AdministrateMe harness artifacts and turn exact deterministic, browser, and review receipts into a concise nontechnical owner handoff. Use at a candidate checkpoint to state what visibly works, what did not change, one task to try, unresolved proof, and exact not-released/candidate/released status without inventing a green result.
---

# AdministrateMe evidence

Translate receipts into an owner-ready result while preserving every uncertainty.

## Workflow

1. Refresh bindings and validate the complete temporary run.
2. Confirm every required check ran against the bound candidate. Keep fixture, local candidate, hosted CI, and live evidence distinct.
3. Derive readiness from deterministic receipts. A failed, missing, stale, schema-invalid, or wrong-head gate remains `FAIL` or `UNDETERMINED`.
4. Confirm browser evidence includes desktop and narrow screenshots plus console, page, request, accessibility, route, authorization, and observation outcomes for touched member paths.
5. Confirm the independent review is read-only, exact-diff, schema-valid, and free of unresolved actionable findings.
6. Run `PYTHONPATH=bridge poetry run python -m scripts.harness.cli finalize-owner <run-dir> --what-now-works <summary> --task <task> --expected-result <result>`. The command produces `owner-handoff.metadata.json` and renders `owner-handoff.md` only after current-bound deterministic, production-journey, browser, and independent-review proof passes.
7. Report owner-autonomy metrics without inventing historical baselines.

## Boundaries

- Do not expose stack traces, credentials, household data, or backend choices in the owner handoff.
- Do not turn semantic review into test truth or treat `NOT_CONFIGURED` hosted review as pass.
- Prepare release evidence only. Do not commit, push, post, open a PR, merge, deploy, enable provider writes, alter observation, or claim live behavior without an exact live receipt.

<!-- BEGIN GENERATED:ADMINME-HARNESS -->
## Generated harness reference

Primary artifact: `owner-handoff.metadata.json`.
Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.
Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.
Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.
<!-- END GENERATED:ADMINME-HARNESS -->
