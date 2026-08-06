# Comms cockpit evidence — 2026-08-06

## Disposition

**MODERNIZE COMPLETE FOR PRODUCT CORE; OPTIONAL PROVIDER EDGES DISABLED.**

D28 adds Comms as the ninth stable adult surface after Triage. The production
composition uses `deterministic-comms-cockpit-v1`; no model ranks, suppresses,
archives, sends, or creates household truth. The combined lens is a query-time
union over independently authorized member/provider-account/workspace facts,
not a second mailbox or generic OpenClaw transcript.

## Executable proof

- `tests/unit/test_comms.py` proves stable explained Focus/noise ordering,
  exact member binding, personal/work separation, disabled-account exclusion,
  read-only capability denial, and the deterministic final content/privacy
  guard.
- Interaction projection tests prove additive schema migration, v3/v2 event
  replay, body/participant/account state, local snooze/quiet state, and
  provider command reconciliation.
- Adapter/supervisor tests prove exact member/account routing for reply and
  provider-state commands and cross-member denial.
- `tests/acceptance/test_gate7_household_experience.py` boots the production
  composition and proves private Comms read, child denial, deterministic
  composition, stale-write refusal, local snooze, independent reply approval,
  observation suppression, terminal command evidence, and no fixture
  delivery.
- The retained browser harness passes 92/92 assertions across 21 captures,
  including desktop and 390-pixel Comms, all nine adult routes, keyboard
  focus, reduced motion, no horizontal page overflow, and zero console, page,
  or request failures. Evidence is under `visual/`.
- The pinned OpenClaw package registers nine domain dashboard reads plus
  health and passes its exact source contract, TypeScript check, 14 tests, and
  build.

## Provider evidence and ceilings

- Gmail/Google Workspace: messages, threads, labels, history, modify, and send
  are official Gmail API surfaces. Push notifications require Pub/Sub watch
  renewal and history reconciliation. Read-only consent remains the default;
  `--enable-gmail-writes` explicitly adds modify/send consent.
  <https://developers.google.com/workspace/gmail/api/reference/rest>,
  <https://developers.google.com/workspace/gmail/api/guides/push>,
  <https://developers.google.com/workspace/gmail/api/guides/sync>
- Outlook/Microsoft 365: Microsoft Graph supports message/folder operations,
  per-folder delta, change/lifecycle notifications, drafts/replies/send,
  read-state, flags, and importance. Shared/delegated mailbox subscription
  limitations remain a distinct permission path.
  <https://learn.microsoft.com/en-us/graph/outlook-mail-concept-overview>,
  <https://learn.microsoft.com/en-us/graph/api/message-delta>,
  <https://learn.microsoft.com/en-us/graph/outlook-change-notifications-overview>
- Apple Messages: EventKit is not a Messages-history API. Public Messages and
  MessageUI APIs do not provide arbitrary personal history ingestion. A future
  edge therefore requires a reviewed, explicitly trusted local bridge such as
  BlueBubbles and must disclose its Full Disk Access/automation implications.
  <https://developer.apple.com/documentation/messages>,
  <https://developer.apple.com/documentation/messageui>,
  <https://github.com/BlueBubblesApp/bluebubbles-server>
- Twilio: two-way SMS/MMS and WhatsApp use inbound webhooks and provider
  delivery evidence. WhatsApp free-form replies are constrained to the
  24-hour service window; approved templates apply outside it.
  <https://www.twilio.com/docs/messaging/api/message-resource>,
  <https://www.twilio.com/docs/usage/webhooks/messaging-webhooks>,
  <https://www.twilio.com/docs/whatsapp/api>
- OpenClaw: human-to-human provider messages bypass model sessions.
  Member-to-CoS external DMs use one isolated agent per member and
  account-aware peer scope; an additional work agent is justified only by a
  real tool/model/retention/policy boundary.
  <https://docs.openclaw.ai/concepts/multi-agent>,
  <https://docs.openclaw.ai/channels/channel-routing>,
  <https://docs.openclaw.ai/gateway/config-channels>

## Live boundary

The live Gmail account is explicitly member-bound in the instance config and
remains read-only. A fresh consent using the opt-in write flag is required
before reply/read/archive actions can appear. Microsoft Graph, BlueBubbles,
Twilio SMS, and Twilio WhatsApp packs are not installed and remain truthful
disabled catalog edges. Attachment metadata is retained and rendered; content
download is not claimed. Real-provider watch renewal, cursor reconciliation,
write observation, and delivery feedback remain activation evidence, never a
fixture inference.
