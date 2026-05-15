---
name: compose_reminder
namespace: adminme
version: 1.0.0
description: Compose a short, warm reminder for a single commitment due within the lookahead window. Output feeds the reminder_dispatch pipeline's outbound delivery.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 300
temperature: 0.2
sensitivity_required: normal
context_scopes_required:
  - member_profile_read
timeout_seconds: 12
outbound_affecting: false
on_failure:
  composed_text: ""
  suggested_channel: imessage
  tone_note: skill_failure_defensive_default
---

# compose_reminder

Compose skill for the `reminder_dispatch` proactive pipeline per
[BUILD.md §L4]. Takes one due commitment plus the owning member's
profile and the recipient party's contact preferences, returns a short
reminder body (SMS-friendly, ≤280 chars) plus a suggested channel and
an optional tone note.

## What good output looks like

Short, warm, but not saccharine. Names the commitment ("Drop off
return paperwork by 5pm") without restating the recipient's whole
context. Tone matches `owner_member_profile.tone` per the F-8
carry-forward from 10c-ii: if the member's tone is `direct`, drop the
softeners; if `gentle`, add one. Never invent details that aren't in
the input — the reminder is a notification, not a summary.

The `suggested_channel` defaults to the commitment's
`preferred_channel` when present; otherwise the recipient party's
default contact channel; otherwise `imessage`. The dispatcher makes
the final channel call based on the member's outbound preferences —
this is a suggestion, not a binding choice.

## Defensive default

The pack's `on_failure` returns `composed_text=""`,
`suggested_channel="imessage"`, and `tone_note="skill_failure_defensive_default"`.
The pipeline catches this defensively and emits `reminder.dispatched`
on the sentinel path (no outbound send) rather than letting
`output_invalid` halt the bus per [§7.7].

## Change log

- **1.0.0** — Initial release per prompt 10c-iii. Clones the
  `compose_morning_digest` 09b shape with reminder-specific schema
  substitutions.
