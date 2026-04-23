# Integration tests

Cross-module tests — end-to-end through the event log, projections, pipelines,
skill-runner wrapper, and product APIs. Per SYSTEM_INVARIANTS.md §1 invariant
10, the full pipeline suite runs against both `InProcessBus` and
`RedisStreamsBus`.

Tests that require live external services (OpenClaw, BlueBubbles, Plaid,
Google APIs, etc.) are marked `@pytest.mark.requires_live_services` and
skipped in the sandbox; they run only in the operator's lab against real
services.

Filled in by phase prompts as implementation lands.
