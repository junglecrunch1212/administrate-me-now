---
name: classify_message_nature
namespace: adminme
version: 8.0.2
compatible_success_versions: [8.0.0, 8.0.1]
description: Extract typed message nature, attention signals, and human-gated household candidates.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 6000
temperature: 0.1
sensitivity_required: normal
context_scopes_required: []
timeout_seconds: 120
outbound_affecting: false
on_failure:
  classification: personal
  confidence: 0.0
  reasons:
    - skill_failure_defensive_default
  reply_signal: unknown
  automated: false
  transactional_due: false
  security_code: false
  person_to_person: false
  deadline: null
  candidates: []
---

# classify_message_nature

Tags an inbound message — email, SMS, iMessage — with one of five
categories so the `noise_filtering` pipeline can decide whether to
surface it in the authorized interactions view or suppress to the noise bucket. The
`on_failure` defaults to `personal` rather than `noise` because
mis-classifying real messages as noise (and dropping them from the
interactions view) is worse than mis-classifying noise as personal.

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
message remains in the interactions view; the safer failure mode is "human sees
something they could have ignored" rather than "human misses
something they needed to see."

## Typed features and candidates

The skill extracts language features only. It receives a bounded, private,
same-account, episode-bounded conversation context pack assembled by
deterministic code. Exact provider thread IDs establish primary conversation
membership. Cross-thread context requires an exact RFC reference, exact
attachment identity, or explicit member confirmation; participant, name,
address, subject, and filename overlap are explanation-only corroboration. The
model cannot add sources or widen member/workspace scope. `reply_signal`
describes whether the text explicitly calls for a response; unread state never
changes it.
Automation, transactional-due, security-code, person-to-person, and deadline
fields are typed evidence consumed by deterministic code. The model does not
rank messages or authorize a write.

Possible tasks, outbound member commitments, calendar events, reminders,
recurrences, list items, lists, party identifiers, new party candidates,
party relationships, and relationship facts are returned as candidates.
People output remains unresolved evidence for human review; the model never
selects an existing party or asserts identity. Direction is derived from the event type or immutable provider
`SENT` label, so another person's inbound promise is never attributed to the
member. Every admitted candidate
must carry a stable source alias and verbatim source quote; deterministic code
rejects ungrounded, completed, other-owned, or related-only
output, filters candidate fields, preserves privacy scope, and owns Triage
eligibility. They remain derived proposals until the member reviews them in
Triage. The dedicated commitment pipeline continues to own richer attributed
inbound-request semantics; this skill covers grounded outbound member promises
that the legacy pipeline intentionally suppresses without a sender identity.

Version 8.0.2 clarifies the existing candidate field vocabulary and the scope
and polarity of each action's qualifiers. Output-schema annotations guide new
inference without invalidating retained 8.0.0/8.0.1 receipts. Ordinary history
reuses those successes unchanged; a prompt correction does not request replay.
