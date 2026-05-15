---
name: compose_reminder
namespace: adminme
version: 1.0.0
description: Compose a per-commitment reminder body addressed to the recipient party, SMS-friendly (<=280 chars) for the `reminder_dispatch` proactive pipeline. Output round-trips into the pipeline's emit path.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-6
max_tokens: 400
temperature: 0.2
sensitivity_required: normal
context_scopes_required:
  - member_profile_read
timeout_seconds: 12
outbound_affecting: false
on_failure:
  composed_text: ""
  suggested_channel: "imessage"
  validation_failed: true
  reasons:
    - skill_failure_defensive_default
---

# compose_reminder

Compose skill for the `reminder_dispatch` proactive pipeline per
[BUILD.md §L4 — proactive pipelines]. Takes a single due commitment plus
the owner's `profile_format` and renders a short reminder body
addressed to the recipient party. The pipeline then routes the body
through `adminme.lib.observation.outbound`, which is the single
observation-gated enforcement site per [§6.14, D22].

## Profile-format shaping

Output tone, density, and ordering vary with `profile_format`
per [BUILD.md §"profile packs"]:

- `fog_aware` — short, gentle, single-action framing.
- `compressed` — bullet-style, minimal narrative.
- `carousel` — single-item focus, suggested next step.
- `child` — kid-appropriate, short sentences.
- `none` — flat plain text; default fallback.

## SMS-friendly length budget

`composed_text` is bounded at 280 chars to fit single-segment SMS and
iMessage delivery. The output schema enforces `maxLength: 280`; the
prompt nudges toward shorter copy.

## Channel suggestion

The skill returns a `suggested_channel` enum
(`imessage|sms|email|push`); the pipeline's `_resolve_delivery_channel`
treats this as a hint and may override based on per-member overrides.

## Defensive default

The pack's `on_failure` returns `composed_text=""`,
`suggested_channel="imessage"`, `validation_failed=true`, and reasons
array including `skill_failure_defensive_default`. The pipeline catches
this defensively and emits `reminder.dispatched` on the sentinel path
(no outbound) rather than letting `output_invalid` halt the bus per
[§7.7].

## Change log

- **1.0.0** — Initial release for the 10c-iii reminder_dispatch pipeline.
