---
name: compose_custody_brief
namespace: adminme
version: 1.0.0
description: Compose the nightly custody brief for the custody_brief proactive pipeline — next-day calendar events for the child(ren), custody-handoff timing, and allergen/medication reminders, tone-matched to the parent's member_profile.tone.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 600
temperature: 0.2
sensitivity_required: normal
context_scopes_required:
  - member_profile_read
timeout_seconds: 12
outbound_affecting: false
on_failure:
  composed_text: ""
  suggested_channel: imessage
---

# compose_custody_brief

Compose skill for the `custody_brief` proactive pipeline per
[BUILD.md §L4 `custody_brief`]. Takes the parent member's profile,
the coparent member's profile, the child profiles, next-day calendar
events for those children, the configured handoff_timing pattern, and
any allergen/medication notes; renders the nightly custody brief.

## What good output looks like

The body opens with the handoff timing (when the children are with
which parent the next day), then walks the next-day calendar in time
order, and closes with any allergen/medication reminders if present.
Tone matches the parent's `member_profile.tone` per the 10c-ii F-8
carry-forward: `direct` voices it plainly, `warm` adds a small
relational softener, `playful` allows a light touch but never at the
expense of clarity. The output is long-form (up to 1500 characters)
because a custody brief is a load-bearing daily reference for the
coparenting parent.

If `calendar_events` is empty the brief still composes — handoff
timing and allergen/med reminders alone are enough content. Empty
brief is suppressed before emit at the pipeline layer, not at the
skill layer.

## Defensive default

The pack's `on_failure` returns `composed_text=""` +
`suggested_channel="imessage"`. The pipeline catches this defensively
and still emits `custody_brief.composed` on the sentinel path so the
audit trail records that the brief fired (even if the send is
suppressed at the dispatcher layer) per `[§6.13]`.

## Change log

- **1.0.0** — Initial release for the 10c-iii custody_brief pipeline.
  Output keys `composed_text` + `suggested_channel` map directly into
  `outbound.dispatcher.deliver(...)`.
