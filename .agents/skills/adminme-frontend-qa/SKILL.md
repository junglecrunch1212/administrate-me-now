---
name: adminme-frontend-qa
description: Prove an AdministrateMe member journey through the credential-free production composition at desktop and narrow widths. Use when a member route, navigation, mode, wording, interaction, authorization, accessibility, loading/degraded state, or browser-visible semantic could change and owner-ready screenshots are required.
---

# AdministrateMe frontend QA

Close the loop on the requested owner journey without using live household accounts.

## Workflow

1. Read the outcome contract and preservation manifest; test the requested job rather than assigning a generic design score.
2. Use the existing Gate 7 fixture composition through `scripts.harness.cli browser`. Write all evidence only beneath the run's system-temp directory.
3. Exercise desktop and 390-pixel narrow behavior, exact routes and aliases, adult/child/device/ambient authorization, experience modes, view-as semantics, and observation labeling that apply.
4. Inspect keyboard naming, reduced motion, overflow, console errors, uncaught page errors, request failures, and expected degraded/denied responses.
5. Compare visible semantics, labels, order, click sequence, and important payload behavior against the preservation manifest. Treat any unexplained difference as failure.
6. Record screenshot hashes and exact browser receipts in `verification-report.json`; select one desktop and one narrow screenshot for the owner handoff.

## Boundaries

- Use fixture or disposable local state only. Do not use live cookies, household identities, installed runtime state, provider accounts, or governed release-evidence directories.
- Browser reads do not authorize writes. Do not enable providers, disable observation, enroll accounts, or mutate external state.
- Report unavailable browser proof as `UNDETERMINED`, not pass.

<!-- BEGIN GENERATED:ADMINME-HARNESS -->
## Generated harness reference

Primary artifact: `verification-report.json`.
Validate artifacts with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli validate-run <run-dir>`.
Inspect current inventories with `PYTHONPATH=bridge poetry run python -m scripts.harness.cli inventory`.
Use `UNDETERMINED` for UNKNOWN, TRACE_BREAK, missing, or unproved behavior.
<!-- END GENERATED:ADMINME-HARNESS -->
