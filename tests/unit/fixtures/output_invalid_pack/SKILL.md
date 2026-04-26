---
name: output_invalid_pack
sensitivity_required: normal
context_scopes_required: []
provider_preferences:
  - anthropic/claude-haiku-4-5
max_tokens: 50
temperature: 0.0
timeout_seconds: 5
outbound_affecting: false
---

Pack whose output.schema.json refuses any sane response. No `on_failure`
declared, so output-invalid surfaces as `SkillOutputInvalid`.
