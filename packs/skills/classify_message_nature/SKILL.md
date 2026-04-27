---
name: classify_message_nature
namespace: adminme
version: 2.0.0
description: Classify an inbound message as noise, transactional, personal, professional, or promotional.
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
  classification: personal
  confidence: 0.0
  reasons:
    - skill_failure_defensive_default
---

# classify_message_nature

Tags an inbound message — email, SMS, iMessage — with one of five
categories so the `noise_filtering` pipeline can decide whether to
surface it in the inbox or suppress to the noise bucket. The
`on_failure` defaults to `personal` rather than `noise` because
mis-classifying real messages as noise (and dropping them from the
inbox) is worse than mis-classifying noise as personal.

## What classifies as each

**noise** — unsolicited bulk marketing, list-style cold outreach,
generic newsletters the principal has previously filtered or
unsubscribed from, automated digest mailers, alert noise from
already-snoozed monitors. The signal is "this message did not pick
*me* specifically; the sender does not care whether I read it." High
confidence here usually requires obvious bulk structure (unsubscribe
footer, list-unsubscribe header pattern, tracking-pixel CTA blocks)
plus content addressed to "Hello" / "Dear customer" rather than the
recipient by name.

**transactional** — receipts, shipping notifications, appointment
confirmations, two-factor codes, password-reset prompts, calendar
invites, account statements, invoices, and similar machine-generated
acknowledgements of a thing the principal already did. The signal is
"this message contains state about a transaction the principal
initiated, and may need to be retained for records, but does not
require a reply." Promotional content embedded in an otherwise
transactional message (e.g. a receipt with a "you might also like"
section) does not bump the message to promotional — the dominant
purpose wins.

**personal** — messages from friends, family, neighbors, household
contacts; SMS or iMessage threads with people the principal knows;
informal email exchanges. **professional** — work-context emails or
messages from coworkers, clients, vendors, scheduling notes about
work. **promotional** — solicited marketing the principal opted into
(brand newsletters, store loyalty offers) that aren't pure noise but
also aren't transactional. Low-confidence outputs (anywhere the
classifier is genuinely uncertain) default to `personal` so the
message lands in the inbox; the safer failure mode is "human sees
something they could have ignored" rather than "human misses
something they needed to see."
