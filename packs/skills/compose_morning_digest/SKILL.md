---
name: compose_morning_digest
namespace: adminme
version: 3.0.0
description: Compose the per-member morning digest body from gathered projection state, in a profile-format-appropriate shape. Output round-trips into the validation guard.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 800
temperature: 0.2
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 12
outbound_affecting: false
on_failure:
  body_text: ""
  claimed_event_ids: []
  validation_failed: true
  reasons:
    - skill_failure_defensive_default
---

# compose_morning_digest

Compose skill for the `morning_digest` proactive pipeline per
[BUILD.md §1289] and [BUILD.md §2242] (cataloged as
`compose_morning_digest@v3`). Takes an aggregated projection-state
payload (today's calendar events, due commitments + tasks, due
recurrences, overnight inbox count, streak status, reward stats) plus
the member's `profile_format` and renders the digest body the member
will see at their wake time.

## Profile-format shaping

The output's tone, density, and ordering vary with `profile_format`
per [BUILD.md §"profile packs"]:

- `fog_aware` — short paragraphs, single-action framing, minimal lists.
- `compressed` — dense lists, bullet-only, no narrative.
- `carousel` — one item at a time, suggested order with rationale.
- `child` — kid-appropriate language, short sentences.
- `none` — flat plain bullets; default fallback.

## Validation-guard discipline

The pipeline runs a validation guard on every claimed projection-side
id mentioned in `body_text` per [BUILD.md §1289]. The skill MUST emit
`claimed_event_ids` listing every calendar event id, commitment id,
task id, or recurrence id it referenced. The pipeline rejects the
composition (and emits the sentinel "No morning brief available;
underlying data changed.") if any claimed id is absent from the input
payload. Fabrication zeroes the message.

This is non-negotiable: do NOT reference an id that is not in the
input. If the input is sparse, return a shorter body.

## Defensive default

The pack's `on_failure` returns `body_text=""`,
`claimed_event_ids=[]`, `validation_failed=true`, and reasons array
including `skill_failure_defensive_default`. The pipeline catches this
defensively and emits `digest.composed` on the sentinel path
(no outbound) rather than letting `output_invalid` halt the bus.

## Change log

- **3.0.0** — Initial release for AdministrateMe v1, cataloged at
  `compose_morning_digest@v3` per [BUILD.md §2242]. Output keys
  designed for round-trip into the validation guard:
  `claimed_event_ids` is the load-bearing field that lets the guard
  verify every referenced id against the input projection payload.
