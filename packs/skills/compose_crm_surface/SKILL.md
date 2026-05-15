---
name: compose_crm_surface
namespace: adminme
version: 1.0.0
description: Compose the weekly CRM-surface digest for the crm_surface proactive pipeline — a tone-matched summary of relationships the opted-in member hasn't touched in a while, plus a suggested display order for the surfaced party ids.
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
---

# compose_crm_surface

Compose skill for the `crm_surface` proactive pipeline per
[BUILD.md §L4 `crm_surface`]. Takes the opted-in member's profile and
the list of stale-contact parties surfaced by the pipeline's scan;
renders the weekly CRM digest the member receives.

## What good output looks like

The body lists the surfaced parties in a coherent suggested order
(closer relationships first, or oldest-stale first — the model
chooses one rule and applies it consistently within one composition).
Tone matches the member's `member_profile.tone` per the 10c-ii F-8
carry-forward: `direct` voices it plainly ("you haven't talked to X
in 4 months"), `warm` adds light relational framing, `playful` allows
a light touch but never at the expense of clarity. The output is
under 1000 characters; longer than a reminder because it's a digest
of several relationships, but still skim-friendly.

The `sort_order` output field is an array of `party_id` strings in
the suggested display order, so the receiving member's surface can
re-order without re-parsing prose. Order MUST be a subset/reorder of
the input `surfaced_parties` ids — fabrication zeroes the message.

## Defensive default

The pack's `on_failure` returns `composed_text=""` +
`suggested_channel="imessage"`. The pipeline catches this defensively
and still emits `crm_surface.composed` on the sentinel path so the
audit trail records that the surface fired (even if the send is
suppressed at the dispatcher layer) per `[§6.13]`.

## Change log

- **1.0.0** — Initial release for the 10c-iii crm_surface pipeline.
  Output keys `composed_text` + `suggested_channel` map into
  `outbound.dispatcher.deliver(...)`; `sort_order` is audit-only and
  consumed by the console-surface in a later prompt (14b/14c).
