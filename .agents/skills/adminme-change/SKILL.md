---
name: adminme-change
description: Implement or clean up an AdministrateMe code change behind an outcome contract, preservation manifest, characterization tests, production-composition proof, and escalating deterministic checks. Use for repository changes that must preserve event-sourced behavior, member surfaces, privacy, replay, observation, OpenClaw boundaries, or current release safety.
---

# AdministrateMe change

Build the smallest complete change while keeping the existing product behavior explicit.

## Workflow

1. Read the instruction chain, current-state index, outcome contract, evidence inventory, and preservation manifest.
2. Pin branch, HEAD, base, tracked changes, untracked files, runtimes, and task-owned paths without cleaning or overwriting owner state.
3. Trace the affected behavior from live entrypoint through authorization, event append, projection, pipeline, output, observation, and every failure path that applies. Replace the generic evidence-inventory, preservation-manifest, and implementation-plan `UNDETERMINED` placeholders with those exact task-specific sources, boundaries, checks, and choices before claiming checkpoint readiness.
4. Write characterization tests before a behavior-preserving cleanup. A defect fix requires a failing regression test and a governing `PRESERVE`, `MODERNIZE`, `SUPERSEDE`, or `DEFER` classification.
5. Implement one bounded batch. Keep old active paths until replacement parity passes.
6. Run static, focused, and production-journey tiers. Run browser proof when `scripts.harness.autonomous_release.browser_required` classifies the changed behavior as member-visible. Treat an unexplained semantic, authorization, console, network, accessibility, desktop, or narrow difference as a regression.
7. Run `PYTHONPATH=bridge poetry run python -m scripts.harness.cli review-ready <run-dir>`. Stop and close its gaps until it reports `READY`, then request the independent exhaustive adversarial review before starting breadth or release-candidate work.
8. If review finds a defect, add its failing regression first, repair one bounded batch, refresh the binding, and return to step 6. Once the exact head is approved, run breadth, release-candidate, applicable macOS, and hosted gates without changing code.
9. Refresh exact diff bindings and validate every run artifact. For a release candidate, evaluate the versioned G-001/G-002 lane; never infer autonomous authority from a green test alone.

## Failure and rollback

- Stop adding changes after a failed batch; preserve diagnostics and reproduce the failure.
- Repair ordinary technical failures autonomously. Change approach after repeated failure instead of weakening a gate.
- Reverse only task-owned lines with a reviewed patch after checking concurrent overlap.
- Never reset, checkout, clean, stash, force-push, rewrite history, weaken tests, or touch an installed release or household state.
- Report missing proof as `UNDETERMINED`; never convert a skipped or stale check into a pass.

## Authority

- Keep deterministic code authoritative for truth, eligibility, ranking, routing, scope, authorization, and pass/fail.
- Keep all language work behind the pinned OpenClaw `llm-task` seam.
- Before G-002 is effective and enrolled, do not commit, push, open or modify a PR, merge, deploy, begin Phase B, mutate providers, alter Keychain/permissions, or disable observation unless a later publication/release task has separate explicit authority under effective G-001.
- After G-002 is effective and enrolled, an owner-requested build task may commit only its bound paths, push normally, open a draft PR, and create one immutable local queue entry without a second owner phrase only when the committed exact-head cheap checkpoint is `READY` and `queue-g002` accepts the outcome-bound impact classification. That standing publication authority never covers a manual path, high-risk impact, merge, deployment, live mutation, provider action, observation change, or G-001 candidate. The scheduled operator prompt is the only standing merge/release authority.

<!-- BEGIN GENERATED:ADMINME-HARNESS -->
## Generated harness reference

Primary artifact: `preservation-manifest.json`.
Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.
Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.
Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.
<!-- END GENERATED:ADMINME-HARNESS -->
