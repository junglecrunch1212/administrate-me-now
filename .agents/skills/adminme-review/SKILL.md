---
name: adminme-review
description: Perform an exact-diff, read-only AdministrateMe review and produce a schema-valid independent review report. Use after implementation or when auditing a candidate for correctness, privacy, replay, determinism, member-surface behavior, failure paths, completeness, and release-boundary violations without modifying the repository.
---

# AdministrateMe review

Review the exact candidate as evidence; do not repair it in this pass.

## Workflow

1. Verify the run binding, task-owned path list, exact HEAD, base, and diff digest before reviewing.
2. Run in a separate ephemeral read-only Codex session or an equivalently isolated read-only job. Never write to the workspace.
3. Read the applicable instructions, outcome contract, preservation manifest, changed files, governing sources, tests, and verification receipts.
4. Trace each changed path through entrypoint, authentication, authorization, append-before-publish, projection/replay, pipeline, API/surface, observation, provider-effect boundary, terminal audit, and rollback where applicable.
5. Check both sides of every contradiction. Do not resolve `UNDETERMINED` evidence by inference.
6. Record only actionable findings with severity, category, exact path/line, evidence, and consequence. A test failure, stale binding, missing required journey, or schema-invalid result blocks approval.
7. Validate the external `review-report.json`. Conclude `APPROVE` only when no actionable finding or unresolved required evidence remains.
8. Import it with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli record-review <run-dir> <review-path>`. This command rejects another run or candidate binding before replacing canonical review evidence.

## Boundaries

- Tests remain pass/fail authority; model prose cannot overrule them.
- Read-only review cannot modify files, apply patches, push, post, merge, deploy, or touch external/live state.
- This review is candidate quality evidence only. It does not satisfy G-001 independent review unless the exact separate-task, immutable-head, hosted/local, and receipt requirements are independently met.

<!-- BEGIN GENERATED:ADMINME-HARNESS -->
## Generated harness reference

Primary artifact: `review-report.json`.
Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.
Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.
Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.
<!-- END GENERATED:ADMINME-HARNESS -->
