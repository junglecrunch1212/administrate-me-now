---
name: ground_reminder_mode
namespace: adminme
version: 1.0.0
description: Compare a retained requested reminder and a fresh numbered mode answer without changing the original action.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 1600
temperature: 0.0
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 30
outbound_affecting: false
on_failure:
  match: unavailable
  original_quotes: []
  answer_quotes: []
---

This comparison supplies evidence only. It cannot authorize, choose identities,
change the retained draft, extend its lifetime, execute or release a queue.
