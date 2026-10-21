# CO-031: Member-forwarded obligations

Date: 2026-10-08. Scope: source implementation and credential-free acceptance.
Activation requires a separate owner deployment request.

[CO-032](CHANGE-ORDER-032-FORWARDED-INTAKE-DEPLOYMENT-READINESS.md) supplies the
staged, runnable rollback prerequisite and durable-policy downgrade guard. Its
prerequisite release withholds historical queue cutover until the later cutover
release; the forwarding product decision below remains unchanged.

## Product decision

Refactor the existing platform. The production flow is **Member → Forward to
CoS → Extraction → Triage → Governed push to member platforms**. A personal
communication that the member does not submit creates no obligation proposal.
Forwarding asks for interpretation and review; it is neither confirmation nor
permission to write to a provider.

This order governs communication-obligation admission where it conflicts with
CO-008/011/013/014/017/018/021/023/030. Those contracts continue to govern
source acquisition, privacy, context, historical evidence, and unrelated
product capabilities. The [original proposal](PROPOSAL-MEMBER-FORWARDED-OBLIGATIONS.md)
records the inspected baseline and design rationale.

| Classification | Behavior and governing basis |
|---|---|
| SUPERSEDE | Passive personal email/message/SMS obligation identification and its recovery, revision, currentness, and saved-publication paths; replaces the communication admission portions of CO-011/021/030. |
| MODERNIZE | CO-007/008 explicit capture and CO-023 service direct intake share one durable interpreter, bounded evidence, exact route provenance, and visible incomplete states. |
| MODERNIZE | CO-009/025 generic Triage delivery retains exact command/native-object observation receipts and refuses automatic replay of uncertain central creation attempts. |
| PRESERVE | All six product functions, thirteen projection capabilities, five adapter categories, six pack kinds, five profiles, four personas, separate domain models, workbook round trips, encrypted event history, member authorization and privacy, and OpenClaw ownership in the v2 reconciliation. |
| PRESERVE | Personal Comms retention, authorized context, explicit replies, canonical Calendar/Tasks/Reminders synchronization, Notes capture, People, finance, household signals, existing accepted work, and governed external writes. |

There is no replacement repository, second model router, central Node runtime,
provider SDK, mailbox-wide forwarder, or new implicit write authorization.

## Admission and existing queue

Production defaults to `explicit-member-submission-v1`. Startup records
`communication_intake.policy_applied` before pipeline replay. Personal
`messaging.received`, `messaging.sent`, and `telephony.sms_received` events
receive verified `communication_intake.source_skipped` receipts with
`not_selected`, zero candidates, and no obligation-model call. Retired
recovery/revision/currentness/publication commands receive terminal receipts,
so settlement does not keep treating deliberate exclusion as a missing job.

Startup selects old proposals using immutable source and producer provenance.
It appends `triage.item.superseded@5` with the dedicated
`explicit_member_submission_required` reason and a content-revision compare
and apply condition. Untouched passive candidates leave active Triage.
Member edits, management decisions, confirmations, explicit submissions,
materialized objects, and original source facts remain intact. A projection
policy barrier rejects late legacy proposal publication. No event-log deletion,
cursor reset, or blanket queue deletion is part of this change.

Historical tests may explicitly select a credential-free compatibility
fixture. Production environment/configuration cannot enable that switch, and
a fixture cannot reopen legacy extraction on a policy-cutover instance.

## Forwarded evidence and durable extraction

`triage.intent.submitted@2` stores a member-private evidence snapshot, submission
kind, channel/source links, bounded full text and completeness issues. MIME parts
and bounded attachment bytes remain in the encrypted service-private source
event. The 2,000-character title/display field is not the extraction input.
The shared `member-submissions:interpretation` subscriber serves manual capture,
CoS Gmail and OpenClaw submissions through the existing pinned `llm-task` seam.
It persists interpretation before proposals; replay reuses the recorded result.
Missing evidence, ambiguity, failed extraction, and held publication stay
visible for clarification. Successfully published children replace the parent
decision burden. Extraction cannot choose a different member or grant access,
approval, or a provider destination.

MODERNIZE historical attribution for a service-forwarded member cue: its
`triage.intent.submitted@2` ownership is reconciled only through the original
service-private `messaging.received@8` fact, exact native-message receipt,
reviewed service-account binding, and resolved `direct_intake.routed@1` receipt
inside the same audit watermark. The route follows the cue in the append-only
log. PRESERVE frozen historical ownership, private scope, tenant isolation,
content-free audit exports, and independent source/effect integrity checks;
malformed linkage never falls back to a member/provider account match. This
implements the CO-023 direct-intake provenance and CO-031 forwarding boundary
above without re-resolving a later route configuration or authorizing a send.

MODERNIZE historical attribution for native OpenClaw submissions: a CoS
transport account alias is not a personal provider account. Reconcile each
`openclaw-submission` source through its exact private
`openclaw.tool_call.recorded` receipt for `submissions.forward` within the audit
watermark. The immutable source, member, tenant, account, result linkage,
receipt fingerprint and member/service identity axes must agree; native bridge
receipts must also reproduce the source's channel/account/sender/message
digest. A completed duplicate request may supply the receipt. Missing or
contradictory receipts cannot fall back to provider-account matching. PRESERVE
the original events, historical owner evidence after later grant/configuration
changes, content-free exports, and independent current authorization and effect
checks. This applies to ownership audit only and never enables acquisition or
sending.

MODERNIZE historical ownership auditing when CoS activation disables personal
acquisition: retain reviewed member/account ownership through a separate
historical-only binding inventory. Disabled entries contribute only after the
same exact ownership, workspace and provenance validation and joint uniqueness
checks as active entries. Unattested optional placeholders contribute nothing;
malformed or conflicting retained claims fail the audit. This inventory carries
no acquisition, dispatch, scope or capability authority. PRESERVE active-only
operational bindings, disabled personal workers, immutable source facts and
content-free historical exports. Both HTR-10 and the runtime historical audit
consume the ownership view without changing provider configuration or events.

Gmail accepts a reviewed exact sender route only with trusted receiver
authentication and domain alignment, or the more restrictive configured exact
DKIM identity policy. Receiver domain authentication plus a reviewed sender is
reported honestly as that assurance, not cryptographic proof of a person.
Quoted authentication headers and original senders never establish membership.
Unresolved routes remain service-private. Replays cannot copy a receipt into
a new member scope after route remapping.

Plain text, HTML, attached email and calendar text use bounded parser paths.
Calendar evidence is preserved as evidence, including UID/sequence/recurrence,
timezone and cancellation details; uncertain semantics require clarification.
Unsupported binary documents/images, missing attachments, oversized parts and
incomplete extraction are explicitly marked. This version does not claim OCR
or PDF decoding. A forward of an invitation is not an RSVP or an update to an
existing provider object.

For CoS iMessage, a reviewed OpenClaw `forwardIntakeAccounts` channel/account
pair plus backend `intake_mode: member_submission` makes messages delivered to
that dedicated CoS endpoint submissions. The provider cannot reliably identify
native forwards, so **all messages to that designated intake endpoint are
selected material**. Other CoS channels remain conversation; `/adminme-forward`
is the explicit capture gesture. Both paths require signed actor mapping and an
active service-owned account binding. Attachment metadata alone does not count
as decoded attachment content. Software support does not enroll an Apple
account, grant macOS permissions, or prove live channel delivery.

Each CoS messaging service channel must name its reviewed transport in
`openclaw_channel_id`, exactly `imessage` or `bluebubbles`. It must equal the
plugin's `forwardIntakeAccounts[].channelId` and the transport in each reviewed
`channel|account|actor` member mapping. There is no fallback between these two
transport namespaces, even when they have the same account ID. Planned records
may omit the field, but cannot resolve as an active intake endpoint.

The configured provider account reference is `provider-account:` followed by
SHA-256 of the **exact, case-sensitive OpenClaw account ID**, with no trimming or
lowercasing. The plugin's `forwardIntakeAccounts[].accountId` and member mapping
must use that exact account string. Only that uniquely enrolled dedicated intake
account may serve multiple reviewed member actors; ordinary member accounts
still cannot be shared. Per-member agent bindings retain exact direct-peer
matching and per-account/channel/peer session isolation. The inert
[`cos-imessage-intake.example.yaml`](../../config/cos-imessage-intake.example.yaml)
shows the matching backend and plugin coordinates; it is not live enrollment.

## Triage, destinations and verification

Triage explains member selection and shows submission status. Settings keeps
source access/context/alert controls while removing passive obligation tuning
from the active communication workflow. Retired items remain auditable.

Existing typed Triage routing selects the exact member account and calendar or
list. Member-domain confirmation and independent OpenClaw approval remain
separate. Allowlist → governance → rate limit, the final content/privacy guard,
observation mode, and terminal auditing retain their existing order.

Google/Apple Calendar creation and Google Tasks/Apple Reminders creation retain
their native destination semantics. Apple Reminders list creation remains the
implemented new-list path; this order does not advertise Google list creation
or general CoS proactive sending. No unconfigured destination is invented or
silently selected.

`external_sync.write_reconciled` binds a successful Triage command/result to an
independent source observation using member, tenant, account, container and
native object identity. Similar text and provider-write echoes are insufficient.
Matched, drifted, missing and incomplete observations are distinct. A provider
response alone remains “sent; waiting to verify.” Older replay cannot downgrade
a newer observation. Reconciliation performs no provider effects.

The central dispatcher reuses existing terminal results. Before non-idempotent
Google creation it durably records `external_sync.write_attempted`; a restart
with an attempt but no terminal refuses automatic creation retry and records
the ambiguity. Observation-suppressed actions are not reported as observed
provider objects. Apple bridge retry handling remains in its existing queue.

## Acceptance and deployment boundary

Required production-composition proofs are in:

- `tests/acceptance/test_explicit_communication_intake_policy.py`: no passive
  model work, audited exclusion, selective queue retirement and replay.
- `tests/acceptance/test_forwarded_channel_intake.py`: real Gmail producer
  fixtures, OpenClaw channel identity, isolation and duplicate delivery.
- `tests/acceptance/test_forwarded_submission_extraction.py`: full submitted
  evidence, ambiguity, restart, Triage, dual approval, observation suppression
  and exact provider reconciliation.
- `tests/acceptance/test_provider_creation_replay.py`: durable attempt boundaries
  before and after provider contact, terminal reuse and no blind creation retry.
- `tests/unit/bootstrap/test_communication_intake_upgrade_safety.py`: candidate
  and rollback compatibility, including interrupted-update recovery.

Run those in the credential-free hosted suites together with the complete
protected Python/Node checks, process gates, and macOS clean-install rehearsal.
Historical compatibility tests do not establish production-default coverage.
Live browser evidence from the design pass is baseline evidence only; candidate
browser coverage belongs in the isolated harness. New behavior needs fresh
authenticated member-device acceptance after separately authorized deployment.

Before activation, produce a private retirement count preview, verify encrypted
backup and rollback prerequisites, retain observation mode, and account for the
current acquisition restriction that can exclude CoS sources from polling.
Any required acquisition change, provider enrollment, runtime/dependency
migration, permission or write enablement needs its own owner authorization.

**A pre-CO-031 release is not a safe rollback target after cutover.** It can
ignore the policy epoch and resume passive extraction. The guarded updater
enforces the immutable release manifest's default and supported intake policies
before activation, rollback and interrupted-update recovery. It refuses an
unsupported rollback predecessor; first activation therefore requires a
separately prepared and tested policy-aware rollback baseline. No live updater, release,
state, provider configuration or account is changed by this source PR.
