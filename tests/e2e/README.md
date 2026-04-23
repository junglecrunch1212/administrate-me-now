# End-to-end tests

Full-stack smoke tests: Node console at :3330 → Python product APIs at
:3333–:3336 → event log → projections. Per ADMINISTRATEME_BUILD.md §STACK,
"End-to-end smoke tests on Node console" covers this directory.

Tests that require live external services are marked
`@pytest.mark.requires_live_services` and skipped in the sandbox.

Filled in by prompt 19 and prompts 13a/13b/14a-c as implementation lands.
