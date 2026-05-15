---
name: compose_crm_surface
namespace: adminme
version: 1.0.0
description: Compose a weekly CRM surface for a member, summarizing parties whose last contact has grown stale. Output feeds the crm_surface proactive pipeline's outbound delivery.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 500
temperature: 0.2
sensitivity_required: normal
context_scopes_required:
  - member_profile_read
timeout_seconds: 12
outbound_affecting: false
on_failure:
  composed_text: ""
  suggested_channel: imessage
  sort_order: []
---

# compose_crm_surface

Compose skill for the `crm_surface` proactive pipeline per
[BUILD.md §L4]. Takes a member profile plus the list of parties whose
last contact has grown stale (per `desired_frequency_days`) and
returns a short digest the pipeline can deliver via outbound.

## What good output looks like

A short, scannable list (≤1000 chars). One line per surfaced party:
name + how long since last contact + (optionally) a one-clause
prompt-of-reason. Tone matches `member_profile.tone` per the F-8
carry-forward from 10c-ii. Never invent details that aren't in the
input — if a party has no notes, the line just states "last contact N
days ago."

The `sort_order` array gives the pipeline a hint about the recommended
display order (party ids in the order they appear in
`composed_text`). The pipeline does not re-rank — it just records the
recommendation alongside the composed text in the
`crm_surface.composed` event.

## Defensive default

The pack's `on_failure` returns `composed_text=""`,
`suggested_channel="imessage"`, and `sort_order=[]`. The pipeline
catches this defensively and skips the row (per-row defensive default;
one member's failure does not abort the tick) per [§7.7].

## Change log

- **1.0.0** — Initial release per prompt 10c-iii. Clones the
  `compose_morning_digest` 09b shape with CRM-specific schema
  substitutions.
