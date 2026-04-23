# Shared test fixtures

Fixtures shared across unit, integration, and e2e test suites.

Per SYSTEM_INVARIANTS.md §12 invariant 5: fixtures are subject to the
no-hardcoded-identity rule EXCEPT when a fixture line is explicitly flagged
with `# fixture:tenant_data:ok`. The identity-scan canary exempts those
lines.

Filled in by phase prompts as implementation lands.
