---
name: multi_provider_pack
sensitivity_required: normal
context_scopes_required: []
provider_preferences:
  - anthropic/claude-opus-4-7
  - anthropic/claude-sonnet-4-6
  - anthropic/claude-haiku-4-5
max_tokens: 50
temperature: 0.0
timeout_seconds: 5
outbound_affecting: false
---

Three providers — exercises wrapper provider-fallback iteration.
