---
name: classify_commitment_candidate
namespace: adminme
version: 3.0.0
description: Classify whether an inbound message contains a commitment a household member has made or been asked to make.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 200
temperature: 0.1
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 8
outbound_affecting: false
on_failure:
  is_candidate: false
  confidence: 0.0
  reasons:
    - skill_failure_defensive_default
---

# classify_commitment_candidate

Decide whether an inbound message implies a commitment — a promise the
receiving member has made, or one that has been requested of them.
Called once per `messaging.received` / `messaging.sent` (and future
voicemail / calendar / capture-note triggers) by the
`commitment_extraction` pipeline. The downstream `extract_commitment_fields`
skill only runs when this skill returns confidence ≥ the configured
`min_confidence`, so the safer failure mode is to under-flag — a false
negative loses one proposal; a false positive pollutes the inbox.

## What counts as a commitment

A message creates a commitment candidate when a concrete obligation can
be attributed to the receiving member or to the sender awaiting a
response from them. Examples that ARE candidates: "Can you send me the
report by Friday?" (receiving member owes a reply / deliverable);
"I'll get back to you tomorrow on this" (the sender is the receiving
member, owing a follow-up); "We owe the plumber $450 — let's settle
this week" (household payment owed); "Are you free Saturday at 2 to
walk through the kitchen?" (reply commitment, response solicited).

## What does NOT count

- **Acknowledgements with nothing owed.** "Thanks for picking up the
  dry cleaning" — gratitude only; no obligation. (Thank-you proposals
  are a separate pipeline.)
- **Informational announcements.** "Practice Thursday moved to the
  upper field — 5:30 sharp." Logistical detail, no response required.
- **Already-acknowledged exchanges.** If the receiving member has
  already replied affirming the request in the same thread, this
  message is not a fresh commitment candidate.
- **Privileged content.** The pipeline filters by party tag before
  invoking; this skill should never see attorney-client or
  opposing-counsel threads.

## Output

Returns a JSON object with:

- `is_candidate`: boolean
- `confidence`: 0.0–1.0
- `reasons`: array of short strings naming concrete signals (e.g.
  "contains scheduling proposal", "deliverable requested by date")

## Calibration notes

Confidence above 0.75 should be reserved for unambiguous obligations
("I'll send the contract by EOD Friday"); confidence in the
0.55–0.75 band signals a weak proposal worth surfacing for human
calibration. Below 0.55 the pipeline emits `commitment.suppressed`
with `reason: below_confidence_threshold` so the audit trail captures
the decision per `[REFERENCE_EXAMPLES.md §2 line 1024]`.

## Change log

- **3.0.0** — Initial release; matches BUILD.md §L4 contract for
  `classify_commitment_candidate@^3.0.0`.
