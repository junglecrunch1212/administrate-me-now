---
name: compose_custody_brief
namespace: adminme
version: 1.0.0
description: Compose a nightly custody brief for a parent member with a coparent relationship — next-day calendar events for the children, handoff timing, and allergen/med reminders. Output feeds the custody_brief proactive pipeline's outbound delivery.
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
[BUILD.md §L4]. Takes the recipient member's profile, the coparent's
profile, child profiles, the next-day calendar events for those
children, handoff timing, and any allergen/med notes. Returns a
long-form brief (≤1500 chars) the pipeline delivers via outbound.

## What good output looks like

Calm, factual, and complete. Sections in order:

1. Tomorrow's schedule (calendar events grouped by child, with times).
2. Handoff timing (who picks up / drops off, where, when).
3. Allergen and medication reminders (one short line each).

Tone matches `member_profile.tone` per the F-8 carry-forward from
10c-ii — but custody briefs lean factual regardless. Never invent
schedule items, handoff windows, or medical details that aren't in the
input. If the next day has no calendar events, say so; the handoff and
allergen sections still surface.

## Defensive default

The pack's `on_failure` returns `composed_text=""` and
`suggested_channel="imessage"`. The pipeline catches this defensively
and skips the row (per-row defensive default; one parent's failure
does not abort the tick) per [§7.7].

## Change log

- **1.0.0** — Initial release per prompt 10c-iii. Clones the
  `compose_morning_digest` 09b shape with custody-brief-specific
  schema substitutions.
