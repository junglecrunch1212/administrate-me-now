# Change Order 029 — Governed Apple Messages Reply from Comms

Status: Phase A implementation candidate; no family-Mac activation, provider
permission, reply enablement, or live Messages effect.

Goal: `[AM-COS-TRIAGE-V1][G11] Governed Apple Messages Reply from Comms`.

## Architecture decision

This change is **MODERNIZE/HARDEN**. It preserves G10's member-private,
source-authoritative Apple Messages observation path and adds one deliberately
narrow write seam: an authenticated adult owner may approve the exact Unicode
plain-text body and exact existing observed chat for one use. AdministrateMe
owns domain authority, privacy, event lineage, projection, and reconciliation;
OpenClaw supplies the independent signed allow-once approval; the separately
supervised member bridge alone contacts the reviewed transport.

The install validator admits only the sealed G11 Cat-A bridge profile:
observation required, owner scope fixed, `communication-source` runtime, and
the single `reply` write capability. New conversation, recipient selection,
autonomous CoS send, standing auto-reply, attachments, reactions, edits,
unsend, receipts, typing, membership change, and bulk/broadcast remain absent
from manifests, authorization, APIs, and transport code.

## Settings and member control

`apple-messages-reply-v1` composes the exact member, enrollment, provider
account, protected source, BlueBubbles transport, permission evidence,
capability version, policy version, and current read-settlement health. Settings
distinguishes implementation unavailable, bridge not connected, transport
unsupported, read observation not settled, reply unsupported, local permission
missing, available but disabled, enabled ask-every-time, paused, degraded, and
revoked.

Only the authenticated adult owner can enable, pause, or revoke. View-as,
another adult, a child, an admin role, and bridge pairing grant nothing.
Each control change is a private typed event and revisioned Settings receipt;
it has zero provider effects. Pause or revoke invalidates pending approval at
both OpenClaw consumption and bridge delivery.

The G09 enrollment catalog represents reply implementation, permission, and
write ceiling, but its read-source checkbox is disabled. Reply permission and
authority are deliberately managed by this separate post-observation control;
selecting a write capability in a read-only bridge handoff is rejected.

## Exact approval and command contract

Comms renders source/account, native chat, and participant set as fixed facts.
The browser uses text-only DOM construction, preserves whitespace and Unicode,
invalidates preview on any edit, and requires a second explicit “Approve this
exact reply” action. It never calls BlueBubbles.

The member receipt binds command/idempotency identity, owner/actor/operator,
enrollment/account/source/chat/reply-target/interaction, participant and body
fingerprints, native revision, setting/capability/policy versions, expiry,
approval basis, correlation/causation, observation disposition, and final
plain-text privacy guard. The existing allowlist → governance → rate-limit
order remains intact. OpenClaw revalidates current setting, destination,
interaction revision, participants, cancellation, expiry, and its independent
signed single-use receipt before appending `external_sync.write_requested@v2`.

## Bridge and transport contract

Central delivery uses the existing authenticated protected command API and is
bound to one active member enrollment. The bridge rechecks every coordinate,
fingerprint, approval field, expiry, local capability, and transport readiness.
The adapter then freshly reads the exact chat participants and reply target,
recomputes the target revision, and refuses any stale, missing, group-ambiguous,
or changed state before the only provider mutation.

The pinned BlueBubbles Server 1.9.9 transport uses exactly
`POST /api/v1/message/text` with `chatGuid`, deterministic `tempGuid`, the exact
`message`, and `method: apple-script`. It does not expose a chat-create or any
other Messages mutation route. The bridge records a durable attempt before the
call. A restart or timeout after possible acceptance becomes `ambiguous` and is
never retried automatically. Accepted, sent, failed, and ambiguous remain
distinct and generic success is impossible without transport evidence.

## Result, observation, and reconciliation

Command acceptance is not delivery. The interactions projection records
awaiting OpenClaw approval, dispatched, suppressed, accepted/sent result,
failed, ambiguous, canceled, observed, and reconciled states. Observation mode
produces a terminal suppressed receipt with `provider_called=false` and no
bridge command.

Later `messaging.sent@v7` continues through the G10 read adapter. The G11
reconciler requires exact member/account/chat/body/participants and exact native
message ID when transport evidence provides it; otherwise exactly one
deterministic candidate may match. It appends separate
`messaging.reply.observed@v1` and `messaging.reply.reconciled@v1` facts.
Unmatched direct provider observations remain external member actions and never
cause a resend.

## Acceptance evidence

`tests/acceptance/test_apple_messages_governed_reply.py` drives the production
Settings, Comms, domain approval, signed OpenClaw approval, protected bridge
command, real bridge edge validator, adapter write seam, authenticated result
ingest, G10 outgoing observation, projection, and reconciliation with disposable
fixtures. It proves owner and cross-member isolation, exact Unicode preservation,
browser double-submit controls, missing approvals, observation suppression,
pause-during-pending, cancellation, stale/forged/cross-member commands, duplicate
results, replay/rebuild/restart lineage, unmatched direct observation, and exact
fixture-effect accounting.

Bridge transport/unit coverage proves the one exact REST payload, loopback and
Keychain boundaries, participant/chat/target revalidation, unavailable transport,
ambiguous timeout without retry, durable bridge restart ambiguity, and pack
validator confinement. Aggregate acceptance, encrypted backup/restore, tenant
isolation, secret scans, Python 3.14/Node 24 builds, and browser harness remain
release gates.

Phase B remains separately owner-authorized: deployment, real bridge enrollment,
real Messages/BlueBubbles permission, reply enablement, observation exit, and
every live message effect.
