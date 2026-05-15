---
name: compose_crm_surface
namespace: adminme
version: 1.0.0
description: Compose a weekly CRM-gap surface body for a single member, listing stale-contact / upcoming-birthday / hosting-imbalance candidates, for the `crm_surface` proactive pipeline. Output round-trips into the pipeline's emit path.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-6
max_tokens: 800
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

# compose_crm_surface

Compose skill for the `crm_surface` proactive pipeline per
[BUILD.md §L4 — proactive pipelines]. Takes a member id plus a list of
already-surfaced parties (each tagged with a `gap_kind`) plus the
member's `profile_format` and renders a weekly CRM digest body. The
pipeline then routes the body through `adminme.lib.observation.outbound`,
which is the single observation-gated enforcement site per [§6.14, D22].

## Gap kinds

The pipeline pre-filters to one of three `gap_kind` discriminators per
surfaced party:

- `stale_contact` — last interaction beyond `threshold_days`.
- `upcoming_birthday` — birthday within the cadence window.
- `hosting_imbalance` — host/guest count drift over the lookback.

The skill respects the gap_kind for each party and frames the prompt
accordingly. Output is bounded at 1000 chars (`maxLength: 1000`) for
readability across channels.

## Profile-format shaping

Output tone, density, and ordering vary with `profile_format`
per [BUILD.md §"profile packs"]:

- `fog_aware` — short, gentle, single-action framing.
- `compressed` — dense bullets, no narrative.
- `carousel` — single-item focus, suggested next step.
- `child` — kid-appropriate, short sentences (rare for CRM surfaces).
- `none` — flat plain bullets; default fallback.

## Defensive default

The pack's `on_failure` returns `composed_text=""`,
`suggested_channel="imessage"`, `validation_failed=true`, and reasons
array including `skill_failure_defensive_default`. The pipeline catches
this defensively and emits `crm.surfaced` on the sentinel path
(no outbound) rather than letting `output_invalid` halt the bus per
[§7.7].

## Change log

- **1.0.0** — Initial release for the 10c-iii crm_surface pipeline.
