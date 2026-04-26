---
name: outbound_affecting_pack
sensitivity_required: normal
context_scopes_required: []
provider_preferences:
  - anthropic/claude-haiku-4-5
max_tokens: 50
temperature: 0.0
timeout_seconds: 5
outbound_affecting: true
on_failure:
  ok: false
---

Pack with outbound_affecting=true so the observation-mode short-circuit
fires. `on_failure` doubles as the suppress-mode defensive default.
