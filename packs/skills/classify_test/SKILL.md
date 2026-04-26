---
name: classify_test
namespace: adminme
version: 0.1.0
description: Trivial test classifier — decide whether a string "is a thing".
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
max_tokens: 200
temperature: 0.0
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 5
outbound_affecting: false
---

# classify_test

Trivial classifier used only by the AdministrateMe skill-runner wrapper's
unit tests. Returns `{is_thing, confidence}` for an arbitrary input string.

This pack is **not** intended for production use; it exists so the wrapper's
loop can be exercised end-to-end against a mocked OpenClaw gateway.
