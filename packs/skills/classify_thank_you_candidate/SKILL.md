---
name: classify_thank_you_candidate
namespace: adminme
version: 1.3.0
description: Decide whether a principal should send a thank-you note to a party after a recent interaction or favor.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 400
temperature: 0.2
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 12
outbound_affecting: false
on_failure:
  is_candidate: false
  confidence: 0.0
  reasons:
    - skill_failure_defensive_default
---

# classify_thank_you_candidate

Decide whether a principal should send a thank-you note to a party after a
recent interaction or favor.

## What counts as a candidate

- **Hosted hospitality.** The party hosted the household — hosted for
  dinner, let the kids sleep over, had the household at their beach
  house, etc. Formal ("fancy dinner") and casual ("they had us for
  pizza Friday") both qualify.
- **Substantial favor.** Watching the kids for an evening. Driving
  someone to the airport. Picking up groceries during a flu week.
  Returning a lost phone. Fixing a plumbing emergency at cost.
- **Significant gift.** Birthday, housewarming, baby, graduation.
  Handwritten-note-warranting, not "they brought a $12 bottle of wine
  to our open house" level.
- **Professional kindness.** A doctor who stayed late to fit you in.
  A contractor who did something above-scope without billing. A
  neighbor who shoveled your sidewalk.

## What does NOT count

- **Transactional exchanges.** Paying someone for a service. Reciprocal
  meet-ups between close friends ("we had coffee"). Standard helpful
  replies to asked questions.
- **Already-reciprocated interactions.** If the household has already
  said thanks in the thread, don't propose a separate note.
- **Interactions where the member has negative affect.** If the principal
  is visibly frustrated in the thread (detected via sentiment signals
  in the message history), no thank-you proposal — the relationship
  dynamic needs a human to decide.
- **Coparent/coparenting exchanges.** Coparenting logistics — kid
  handoffs, school pickups, medical-appointment coordination — do not
  generate thank-you proposals regardless of cordiality. Emotional
  texture is too load-bearing.
- **Professional-transaction providers.** Standard service provider
  interactions (the dentist's scheduler, the pool guy, the accountant)
  don't generate thank-you proposals unless there's a specific
  above-scope favor.

## Output

Returns a JSON object with:

- `is_candidate`: boolean
- `urgency`: `'within_24h' | 'this_week' | 'within_month' | 'no_rush'`
- `suggested_medium`: `'text' | 'email' | 'handwritten_card' | 'small_gift'`
  (handwritten implied for significant gifts and hosting; text for
  small favors; email for professional kindness.)
- `reasons`: array of short strings, each a concrete signal
- `confidence`: 0.0-1.0

## Calibration notes

The skill is tuned for tier-2-and-above parties (close friends and above).
Acquaintances may trigger false positives in the "small gift" branch. The
`commitment_extraction` pipeline upstream already filters by party tier
before calling this skill, so the skill itself doesn't need to re-check
tier.

## Change log

- **1.3.0** — Added `suggested_medium` and handwritten-card preference
  for hosting-hospitality cases.
- **1.2.0** — Added coparent skip rule.
- **1.1.0** — Initial tier-2 tuning.
