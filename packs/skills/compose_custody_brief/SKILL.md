---
name: compose_custody_brief
namespace: adminme
version: 1.0.0
description: Compose a long-form coparent custody handoff brief for a single member, covering next-day calendar events, handoff timing, and allergen/med notes, for the `custody_brief` proactive pipeline. Output round-trips into the pipeline's emit path.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-6
max_tokens: 1200
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

# compose_custody_brief

Compose skill for the `custody_brief` proactive pipeline per
[BUILD.md §L4 — proactive pipelines]. Takes a member id plus the
coparent and child member ids plus next-day calendar / handoff /
allergen / medication context, and renders a long-form custody-handoff
brief. The pipeline then routes the body through
`adminme.lib.observation.outbound`, which is the single
observation-gated enforcement site per [§6.14, D22].

## Long-form length budget

`composed_text` is bounded at 1500 chars (`maxLength: 1500`). Custody
briefs need room for handoff timing, multiple children, and allergen /
medication notes; the prompt requests structured prose rather than dense
bullets.

## Profile-format shaping

Output tone, density, and ordering vary with `profile_format`
per [BUILD.md §"profile packs"]:

- `fog_aware` — short paragraphs, single-action framing per child.
- `compressed` — structured headers + bullets, minimal narrative.
- `carousel` — single-item focus per child, suggested order.
- `child` — kid-appropriate, short sentences (rare for handoff briefs).
- `none` — flat plain text with section headers; default fallback.

## Empty-day composition

Per the prompt's §8 c3 unit-case list: even with an empty next-day
calendar the skill should still compose (handoff timing + allergen / med
notes), so the pipeline emits a useful brief rather than skipping.

## Defensive default

The pack's `on_failure` returns `composed_text=""`,
`suggested_channel="imessage"`, `validation_failed=true`, and reasons
array including `skill_failure_defensive_default`. The pipeline catches
this defensively and emits `custody_brief.composed` on the sentinel
path (no outbound) rather than letting `output_invalid` halt the bus
per [§7.7].

## Change log

- **1.0.0** — Initial release for the 10c-iii custody_brief pipeline.
