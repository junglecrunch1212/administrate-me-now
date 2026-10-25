# CoS email and iMessage operation

Date: 2026-10-20.

The owner directs members to send or forward selected obligations to dedicated
CoS email and iMessage accounts. The accounts supply authenticated submissions;
extraction produces Triage candidates. Members confirm those candidates before
they become accepted household work. Personal inbound channels are not scanned
for obligations.

This extends CO-031 and the [recipient contract](COS-RESPONSE-ALLOWLIST-CONTRACT.md).

| Classification | Governing change |
| --- | --- |
| PRESERVE | CO-031 selected submissions, private sender routing, candidate provenance and member confirmation. |
| SUPERSEDE | The bounded personal-mail acquisition window is retained as historical evidence and disabled for new acquisition. A separately authorized CoS-only acquisition overlay controls new reads. |
| MODERNIZE | Dedicated CoS Gmail read/send OAuth and guarded iMessage permit separately activated operation after deployment. |
| PRESERVE | Independent domain and OpenClaw approval, recipient limits, privacy, rate limits, durable attempts and terminal audit. |
| PRESERVE | One Python runtime and one pinned OpenClaw Gateway; OpenClaw owns the iMessage channel. The provider and guard are child processes, not additional application servers. |

## Enrollment and activation

Browser sign-in verifies the visible account. It does not grant Gmail API
access or prove the iMessage provider is ready.

`bootstrap.google_oauth --cos-service` requests exactly Gmail read and send,
verifies the returned account with Gmail's profile endpoint, and stores the
refresh credential in Keychain under a separate service-owner binding.
Personal grants cannot be relabeled as the CoS. No Calendar, Tasks, mailbox
modification or full-mail scope is added by this operation.

`bootstrap.cos_imessage --install-pinned-provider` stages the official imsg
0.17.0 archive after verifying its SHA-256, then verifies the executable hash.
It excludes the optional private-API helper. Ordinary Messages automation and
read permission are the only provider permissions needed; SIP is unchanged.
The dedicated account uses iMessage exclusively, AppleScript transport,
explicit mapped direct senders, no groups, attachments, reactions, read
receipts, catch-up or private-API actions.

Deploy the immutable merged release using the existing guarded updater with
observation active. Then use `bootstrap.cos_channel_activation` with a private
operation document to publish the CoS-only acquisition and channel
configuration and end observation. The operation verifies fresh sender and
transport evidence, current nonempty recipient policy, encrypted backup and
restore rehearsal. It journals the prior configuration, settles both services,
runs Doctor and restores observation and prior configuration on failure.
There is no manual edit of an installed release or active configuration.

Before maintenance journals or stops the healthy predecessor, a metadata-only
storage admission budgets the remaining pause, update and normal-restoration
backups, the rehearsal's simultaneous authenticated TAR and extracted state,
and a conservative release/build allowance. Requirements sharing a filesystem
are added before retaining the existing 5 GiB recovery reserve. Updater checks
again before publication and after preparation; normal restoration checks its
remaining backup/rehearsal budget before stopping services. A refused admission
leaves that operation's runtime state, grants, journal and release pointer
unchanged. It reports required/free bytes without household paths or contents;
free space through separately authorized operations, then retry. It does not
delete backups, change retention or replace encrypted restore proof. A standalone
enter-observation operation reserves only its own backup/rehearsal, without
requiring space for an unrequested future release. Estimates
cannot prevent another process consuming disk later, so existing guarded
recovery remains mandatory. This modernizes admission after the observed
backup-rehearsal disk failure; backup format and recovery checks are preserved.

The original bounded acquisition selection and lifetime ledger remain intact.
Non-CoS adapters remain stopped. Ending observation never releases previously
suppressed sends: every response must have a new exact approval after the
recorded activation boundary.

## Final transport boundary

Gmail service sends require an in-process, single-use permit carrying the
verified account, member, exact recipient/content, current policy and activation.
Generic adapter calls and mailbox-management operations cannot send as CoS.

OpenClaw's ordinary outgoing-message hook is supplemental. The dedicated
iMessage account's actual `cliPath` is the packaged `cos-imsg-guard.mjs`.
Enrollment and final authorization require that exact file inside the selected
immutable release; a replacement wrapper and a matching supplied hash are refused.
The guard relays only enumerated read RPCs. Every other mutation and every CLI
send is denied. A plain-text direct send to one E.164 recipient must match a
short-lived, independently approved pending admission in the Python runtime.
The claim atomically consumes that admission before the provider receives
the request. No response token or private dispatch record is exposed to the
model. Member agents have only the AdministrateMe and pinned LLM task tools
plus memory reads, so generic shell/filesystem/message tools cannot bypass
the guard.

The guard repeats account, recipient, content and activation checks at
dispatch. Changed content, unsupported formatting, extra RPC keys, group IDs,
attachments, other transports and unlisted recipients are refused. It records
a terminal receipt before reporting success. A missing provider receipt,
disconnect, timeout or uncertain result is `unknown`, with no automatic retry.
Restart never resends an earlier attempted approval.

## Verification

Hosted production-composition tests exercise intake, policy replay, revocation,
privacy, independent approval, native claim/terminal callbacks, observation and
provider ambiguity. The Node guard tests use a disposable provider child and
loopback backend; they never contact Messages or family accounts. Bootstrap
tests prove exact configuration, pinning, backup and recovery. The protected
release checks and credential-free macOS rehearsal remain required.

After deployment, authenticated member-device acceptance covers Settings and
identity alignment. Account enrollment, API readiness and any actual provider
delivery are reported separately. One member device does not establish another
member's access. No test message is sent without an explicit recipient and
purpose authorized by the owner.
