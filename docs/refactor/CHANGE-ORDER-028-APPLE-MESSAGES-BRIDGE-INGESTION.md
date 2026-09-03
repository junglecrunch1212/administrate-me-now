# Change Order 028 — Apple Messages Bridge Ingestion

Status: Phase A implementation candidate; no family-Mac activation or provider enrollment.

Goal: `[AM-COS-TRIAGE-V1][G10] Apple Messages Bridge Ingestion and Triage Integration`.

## Architecture decision

This change is **MODERNIZE**. It replaces the historical assumption that every
Cat-A adapter must run centrally with one narrowly sealed
`communication-source` protocol for a separately enrolled member bridge. It
preserves all stronger boundaries: OpenClaw still owns channels and outbound
conversation execution; AdministrateMe owns typed household truth, source
policy, Triage, Comms, provenance, and outcomes; the central application remains
one Python runtime; and a personal Messages source never becomes a direct CoS
channel.

The bridge protocol has no `send` or `apply_write` method. The pack manifest has
an empty write-capability set, bridge authorization has no write operation, and
the central command dispatcher never discovers a Messages target. Reply, new
message, reaction, edit, unsend, read-state mutation, chat creation, and
attachment send remain G11-or-later work requiring a separate review.

## Selected transport

The reviewed transport is BlueBubbles Server `1.9.9` at source commit
`ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1`. The Apple-silicon DMG is
`300676880` bytes with SHA-256
`fafd650c883f52e7494a6625e45249f2144d197378a4d57143ccf6198bb2e862`.
The [release](https://github.com/BlueBubblesApp/bluebubbles-server/releases/tag/v1.9.9),
[source commit](https://github.com/BlueBubblesApp/bluebubbles-server/commit/ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1),
[authentication middleware](https://github.com/BlueBubblesApp/bluebubbles-server/blob/ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1/packages/server/src/server/api/http/api/v1/middleware/authMiddleware.ts),
and [route table](https://github.com/BlueBubblesApp/bluebubbles-server/blob/ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1/packages/server/src/server/api/http/api/v1/httpRoutes.ts)
are the implementation evidence.

The artifact is Developer-ID signed but not notarized, and the tagged source
package metadata still says `1.9.8`. Product installation is therefore not
implemented. Compatibility remains conditional until the exact binary passes a
clean, disposable macOS Apple-silicon rehearsal. The wizard detects the local
app, requires the exact runtime version, and guides the member; it never fetches
or installs a floating latest release.

## Transport and privacy contract

The connector uses outbound REST polling only. It accepts exactly an
`http://127.0.0.1:<port>` origin, ignores environment proxy configuration,
resolves the password from a member-Mac Keychain reference for each request,
and exposes only reviewed GET/message-query calls. Exceptions are sanitized so
the credential-bearing query URL cannot become an error cause.

BlueBubbles listens on all interfaces and its password authorizes write routes
too. A loopback client is not sufficient deployment proof. Activation requires
independent host-firewall containment, LAN mode, Private API off, and Firebase,
Cloudflare, zrok, ngrok, Dynamic DNS, webhook, and other remote paths disabled.
The connector reports degraded until both containment claims are verified.

Webhooks are deliberately excluded: the selected server sends unsigned JSON
without acknowledgement, durable retry, sequence, or cursor. Socket.IO is also
excluded from G10 because post-connect authentication and global broadcasts do
not meet the selected risk envelope. REST polling provides truthful
`bounded_best_effort` coverage, not complete history or lossless mutation
delivery.

Credentials never cross the bridge. Central configuration contains only an
opaque Keychain reference. Attachments retain bounded metadata and a protected
opaque reference; content, local paths, downloads, OCR, and transcription are
excluded. Provider participant endpoints remain source observations until
exact identity evidence resolves an existing Party.

## Supported semantics

- Incoming and outgoing iMessage observations when chat/handle evidence proves
  the transport; SMS observations when the selected payload proves `SMS`.
- Stable member/enrollment/device/connector/account/source/chat/message
  coordinates, direction, timestamps, group shape, current participant
  snapshot, reply reference, body text, subject, delivery state, and bounded
  attachment metadata.
- Boundary-before-history acquisition, durable member/device-namespaced cursor,
  live polling, bounded reconciliation, native-message plus revision-fingerprint
  deduplication, restart recovery, quarantine, backoff, and content-free source
  accounting.
- Private production-path ingestion into the encrypted event log, deterministic
  interaction projection, source policy, pinned OpenClaw `llm-task` seam,
  deterministic candidate eligibility, reconciliation, Triage, and read-only
  Comms.

## Unsupported or qualified semantics

- No delete stream, complete edit history, stable upstream revision, provider
  replay cursor, snapshot-isolated pagination, or proof of complete global
  history.
- Reactions, edits, retractions, system records, and out-of-order/conflicting
  mutations are quarantined rather than fabricated.
- Attachment bodies, RCS identification, reliable SMS-versus-MMS distinction,
  historic group membership, per-recipient group delivery/read state, and a
  complete reply graph are unavailable.
- The standard selected serializers do not prove per-message account identity;
  the enrolled protected source coordinate is the account boundary.
- The bridge-local cursor database is not part of the central encrypted backup.
  Loss of that database may cause safe re-observation, while central
  idempotency prevents duplicate canonical facts. It is not claimed as cursor
  restore.

## Wizard and lifecycle

Apple Messages read is an available G09 wizard capability bound to
`messaging-bluebubbles`; Apple Messages reply remains `planned_g11`. Transport
absent, incompatible, installed, authentication required, authenticated,
permission required/denied, observation starting, settling, active, degraded,
paused, and revoked remain separate states. The authentication probe is
content-free and the first admitted source observation is the only settlement
proof.

## Acceptance evidence

`tests/acceptance/test_apple_messages_bridge_ingestion.py` drives the real
connector normalizer through authenticated `/api/bridge/ingest` into the
production event log, bus, pipelines, projections, source policy, Triage, and
Comms. It covers member isolation, direction, group endpoint scope, actionable
request and outbound promise review, verification-code and chatter
non-dominance, read-only capability truth, malformed/oversized/forged inputs,
protected attachment metadata, duplicate idempotency, revocation, interaction
rebuild equivalence, and zero provider effects. Connector fixtures additionally
cover authentication failure, unavailable transport, history/live dedupe,
cursor restart, truncated coverage, unsupported records, and out-of-order
mutation quarantine.

Phase B remains separately owner-authorized: deployment, real bridge enrollment,
Full Disk Access, Keychain entry, firewall containment, real Messages
observation, and every Messages write.
