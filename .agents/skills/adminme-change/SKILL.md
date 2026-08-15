---
name: adminme-change
description: Implement or clean up an AdministrateMe code change behind an outcome contract, preservation manifest, characterization tests, production-composition proof, and escalating deterministic checks. Use for repository changes that must preserve event-sourced behavior, member surfaces, privacy, replay, observation, OpenClaw boundaries, or current release safety.
---

# AdministrateMe change

Build the smallest complete change while keeping the existing product behavior explicit.

## Workflow

1. Read the instruction chain, current-state index, outcome contract, evidence inventory, and preservation manifest.
2. Pin branch, HEAD, base, tracked changes, untracked files, runtimes, and task-owned paths without cleaning or overwriting owner state.
3. Trace the affected behavior from live entrypoint through authorization, event append, projection, pipeline, output, observation, and every failure path that applies.
4. Write characterization tests before a behavior-preserving cleanup. A defect fix requires a failing regression test and a governing `PRESERVE`, `MODERNIZE`, `SUPERSEDE`, or `DEFER` classification.
5. Implement one bounded batch. Keep old active paths until replacement parity passes.
6. Run static, focused, production journey, breadth, and release-candidate tiers as applicable. Use the harness check selector and retain exact receipts in the temp run.
7. Run browser proof for every touched member path. Treat an unexplained semantic, authorization, console, network, accessibility, desktop, or narrow difference as a regression.
8. Refresh exact diff bindings and validate every run artifact.

## Failure and rollback

- Stop adding changes after a failed batch; preserve diagnostics and reproduce the failure.
- Repair ordinary technical failures autonomously. Change approach after repeated failure instead of weakening a gate.
- Reverse only task-owned lines with a reviewed patch after checking concurrent overlap.
- Never reset, checkout, clean, stash, force-push, rewrite history, weaken tests, or touch an installed release or household state.
- Report missing proof as `UNDETERMINED`; never convert a skipped or stale check into a pass.

## Authority

- Keep deterministic code authoritative for truth, eligibility, ranking, routing, scope, authorization, and pass/fail.
- Keep all language work behind the pinned OpenClaw `llm-task` seam.
- Do not commit, push, open or modify a PR, merge, deploy, begin Phase B, mutate providers, alter Keychain/permissions, or disable observation.

<!-- BEGIN GENERATED:ADMINME-HARNESS -->
## Generated harness reference

Primary artifact: `preservation-manifest.json`.
Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.
Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.
Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.
<!-- END GENERATED:ADMINME-HARNESS -->
