---
name: scope_required_pack
sensitivity_required: normal
context_scopes_required:
  - private:does_not_exist
provider_preferences:
  - anthropic/claude-haiku-4-5
max_tokens: 50
temperature: 0.0
timeout_seconds: 5
outbound_affecting: false
---

Scope-required test pack — used to exercise the wrapper's scope-check branch.
