---
name: handler_raises_pack
sensitivity_required: normal
context_scopes_required: []
provider_preferences:
  - anthropic/claude-haiku-4-5
max_tokens: 50
temperature: 0.0
timeout_seconds: 5
outbound_affecting: false
on_failure:
  is_thing: false
  confidence: 0.0
---

Pack whose handler.py raises so we can exercise the handler-failure path.
`on_failure` is the defensive default the wrapper returns.
