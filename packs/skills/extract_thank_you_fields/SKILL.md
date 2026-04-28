---
name: extract_thank_you_fields
namespace: adminme
version: 1.0.0
description: Given a message classified as a thank-you candidate, extract structured fields suitable for round-tripping into a `commitment.proposed` event with `kind=other`.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 400
temperature: 0.1
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 8
outbound_affecting: false
on_failure:
  recipient_party_id: ""
  suggested_text: ""
  urgency: no_rush
  confidence: 0.0
  reasons:
    - skill_failure_defensive_default
---

# extract_thank_you_fields

Second-stage skill in the `thank_you_detection` pipeline. Runs only
after `classify_thank_you_candidate@1.3.0` has returned
`is_candidate=true` with confidence above the configured
`min_confidence`. Extracts the structured fields that populate the
proposed thank-you's `commitment.proposed` payload (which the pipeline
emits with `kind="other"` per `BUILD.md §1150`'s default disposition —
the kind Literal is NOT extended for thank-yous in v1).

## What good output looks like

The extractor produces a short, ready-to-send draft note plus a
recipient pointer. `recipient_party_id` is typically the upstream
sender (the party who hosted, gave the gift, or did the favor).
`suggested_text` is a 1–4 sentence draft the principal can edit and
send — concrete enough to acknowledge the specific kindness, short
enough that handing it off to a handwritten card or a quick text
doesn't feel forced. The urgency value drives the inbox surface's
sort order: `today` for a same-day acknowledgement (e.g. "they had us
over last night"); `this_week` is the default for hosting
hospitality; `this_month` for non-time-sensitive significant gifts;
`no_rush` when the relationship doesn't have a tight social clock.

The output schema is designed to round-trip directly into
`CommitmentProposedV1` (in `adminme/events/schemas/domain.py`) without
coercion drift. `urgency`'s enum matches `CommitmentProposedV1.urgency`'s
`Literal["today", "this_week", "this_month", "no_rush"]` exactly.
**Note the urgency-vocabulary asymmetry with the upstream classifier:**
`classify_thank_you_candidate@1.3.0`'s urgency Literal is
`within_24h | this_week | within_month | no_rush` — it does NOT match
`CommitmentProposedV1.urgency`'s Literal. The pipeline therefore lets
the extractor produce the canonical urgency value rather than
round-tripping the classify-side hint; the classify-side urgency is a
hint to the extractor only and need not be passed through.

## Defensive default

If the model returns a non-dict shape, an unknown `urgency`, or omits
the required fields, the handler coerces to `urgency="no_rush"`,
`confidence: 0.0`, and a reasons array including
`skill_failure_defensive_default`. The pipeline catches this
defensively at the integration point and emits `commitment.suppressed`
rather than a malformed `commitment.proposed`.

## Change log

- **1.0.0** — Initial release; second-stage extractor for
  `thank_you_detection` pipeline. Output keys aligned to
  `CommitmentProposedV1` v1 schema.
