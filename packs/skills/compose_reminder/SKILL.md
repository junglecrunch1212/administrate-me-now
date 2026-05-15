---
name: compose_reminder
namespace: adminme
version: 1.0.0
description: Compose a single-commitment reminder body for the reminder_dispatch proactive pipeline. Output is SMS-friendly (<=280 chars), tone-matched to the owner's member_profile.tone, and channel-suggested per the recipient party preferences.
input_schema: ./schemas/input.schema.json
output_schema: ./schemas/output.schema.json
provider_preferences:
  - anthropic/claude-haiku-4-5
  - anthropic/claude-opus-4-7
max_tokens: 300
temperature: 0.2
sensitivity_required: normal
context_scopes_required:
  - member_profile_read
timeout_seconds: 12
outbound_affecting: false
on_failure:
  composed_text: ""
  suggested_channel: imessage
  tone_note: skill_failure_defensive_default
---

# compose_reminder

Compose skill for the `reminder_dispatch` proactive pipeline per
[BUILD.md §L4 `reminder_dispatch`]. Takes one commitment row, the
owner's member profile, and the recipient party row; renders the body
the recipient will receive when the commitment is within the
configured lookahead window.

## What good output looks like

The body is short and warm but not saccharine: a single sentence
mentioning what the commitment is and roughly when it is due (relative
phrasing — "tomorrow morning", "in about an hour" — is preferable to
raw timestamps for human readers). Tone matches the owner's
`member_profile.tone` per the 10c-ii F-8 carry-forward — `direct`
voices the reminder plainly, `warm` adds a small relational softener,
`playful` allows a light touch but never at the expense of clarity.

The suggested_channel respects the commitment's `preferred_channel`
when set; otherwise the recipient party's primary channel. If the
model returns an unknown channel string, the post-process handler
coerces to `imessage` and appends a tone_note explaining the coercion
per `[§7.7]`'s defensive-default discipline.

## Defensive default

The pack's `on_failure` returns `composed_text=""`,
`suggested_channel="imessage"`, and `tone_note="skill_failure_defensive_default"`.
The pipeline catches this defensively and still emits
`reminder.dispatched` on the sentinel path so the audit trail records
that the reminder fired (even if the send is suppressed at the
dispatcher layer) per `[§6.13]`.

## Change log

- **1.0.0** — Initial release for the 10c-iii reminder_dispatch
  pipeline. Output keys `composed_text` + `suggested_channel` map
  directly into `outbound.dispatcher.deliver(...)`; `tone_note` is
  audit-only and not used at delivery time.
