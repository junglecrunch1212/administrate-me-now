---
name: extract_commitment_fields
namespace: adminme
version: 2.1.0
description: Given a message classified as a commitment candidate, extract structured commitment fields suitable for round-tripping into a `commitment.proposed` event.
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
  kind: other
  confidence: 0.0
  reasons:
    - skill_failure_defensive_default
---

# extract_commitment_fields

Second-stage skill in the `commitment_extraction` pipeline. Runs only
after `classify_commitment_candidate` has returned confidence above the
configured `min_confidence`. Extracts the structured fields that
populate `commitment.proposed`'s payload.

## Output keys

The output schema is designed to round-trip directly into
`CommitmentProposedV1` (in `adminme/events/schemas/domain.py`) without
coercion drift:

- `kind` — one of `reply | task | appointment | payment |
  document_return | visit | other`. Matches `CommitmentProposedV1.kind`'s
  Literal exactly.
- `owed_by_member_id` — the household member who owes the commitment.
- `owed_to_party_id` — the party owed (typically the message sender).
- `text_summary` — concise (≤500 char) description of what is owed.
- `suggested_due` — ISO-8601 datetime or `null` when none can be
  inferred.
- `urgency` — `today | this_week | this_month | no_rush`. Matches
  `CommitmentProposedV1.urgency`'s Literal exactly.
- `confidence` — extractor-side confidence in the structured fields,
  separate from the upstream classifier's confidence.

## Defensive default

If the model returns a non-dict shape, an unknown `kind`, or omits the
required fields, the handler coerces to `kind: "other"`, `confidence:
0.0`, and a reasons array including `skill_failure_defensive_default`.
The pipeline catches this defensively at the integration point and
emits `commitment.suppressed` rather than a malformed
`commitment.proposed`.

## Change log

- **2.1.0** — Initial release; matches BUILD.md §L4 contract for
  `extract_commitment_fields@^2.1.0`. Output keys aligned to
  `CommitmentProposedV1` v1 schema.
