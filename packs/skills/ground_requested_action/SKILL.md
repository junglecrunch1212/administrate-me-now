---
name: ground_requested_action
namespace: adminme
version: 1.1.2
description: Compare one proposed action with one authenticated human instruction; produce grounded evidence, never permission.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 1200
temperature: 0.0
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 30
outbound_affecting: false
on_failure:
  match: unavailable
  evidence: []
---

This skill supplies semantic evidence to independent policy engines. It cannot
choose identities, grant permissions or execute actions. Both engines still
validate the trusted source, exact action, current authority and expiry.

Version 1.1.2 supports the closed reminder/email rules and distinguishes the
requested operation from the content being remembered or sent. Negative
constraints apply to their named operation. An omitted reminder title permits a
minimal current-instruction paraphrase, not invented details from earlier turns.
Explicit titles remain exact; a reminder about an email never supplies authority
to send it. Quoted literal content adopted by a direct request remains data.

Evidence remains strict quote-only (`{"text": "exact quote"}`).
Deterministic code accepts only unique exact, non-overlapping occurrences and
computes Unicode code-point spans before recording/signing the existing domain
receipt. Existing retained span receipts are unchanged. Unusable extraction is
an authorization verification failure, not proof of ambiguous intent or accounts;
no action is confirmed, and the same failed model result is not blindly retried.
