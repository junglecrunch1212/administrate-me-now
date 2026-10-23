# CoS response execution

Classification: **MODERNIZE** the service-owned channel boundary; **PRESERVE**
the independent approvals, observation filter, tenant isolation and immutable
audit required by `BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md`, `ACCEPTANCE-CONTRACT.md`
and `UX-BEHAVIOR-CONTRACT.md`. `COS-RESPONSE-ALLOWLIST-CONTRACT.md` governs the
recipient ceiling. An allowed recipient is never disclosure or send authority.

The first supported response is one exact plain-text message to the approving
member's own endpoint. Gmail uses the enrolled service mailbox. iMessage uses
the exact dedicated OpenClaw account and international phone recipient.
No group, reply-all, CC/BCC, attachments, HTML, alias expansion, member
impersonation or another member's private context is authorized.

## Confirmation and execution

**MODERNIZE — visible member permission setup.** Account enrollment and live
channel activation do not establish a member-private service delegation.
Settings → How my CoS works lets an authenticated principal explicitly choose
their own private-context reading and external-action preparation permissions,
review their scope and expiry, grant them, and revoke their own grant. It never
grants another adult's consent or replaces an existing active grant silently.
The Settings creation command requires no active private grant. The server
serializes private creation per member through append and projection settlement,
so a stale second tab receives a definitive conflict instead of overlapping
authority. Under that lock, the parties projection must reach the current durable
checkpoint of its subscribed event types before checking for an active grant.
Unrelated events do not advance that subscriber's checkpoint. A failed settlement returns
an uncertain status without allowing another creation against stale state.
Only an explicit pre-append conflict clears the UI's pending change;
an uncertain transport result still requires exact readback.
Private-context reading concerns AdministrateMe's authorized private context;
it does not enroll, reconnect, or resume personal inbox acquisition. Preparation
permission is not provider execution authority and is not limited to one reply:
it remains effective until its displayed expiry or revocation.

The reply options report current delegation readiness before text approval.
Email requires `prepare_external_actions`; the full native iMessage path also
requires `read_context` for its private result lookup. Forwarded iMessage intake
independently requires `read_context`. Missing, expired, revoked, ambiguous, or
insufficient grants prevent a new reply approval with an actionable explanation.
Execution and final result lookup retain their independent existing permission
checks. An idempotent readback of an earlier exact text approval never refreshes
its expiry, restores revoked permission, or authorizes execution.

After an uncertain grant-creation response, the member may reconcile the exact
request through the read-only delegation listing's `request_id` query. This
looks up that authenticated member's immutable request, not a similar grant's
permissions. An absent record is not proof that an in-flight request failed;
the UI keeps the operation unresolved and never automatically repeats it.

The authenticated member confirms exact account, destination, subject and text
through `/api/member/v1/cos/responses/approvals`. A private encrypted
`cos.response.approved` fact records a ten-minute expiry, content fingerprint
and current recipient-policy event. A policy edit invalidates pending
approvals. Settings exposes the composer and labels its result as awaiting
independent OpenClaw approval, never as sent.

`administrateme_cos_response` executes the approval event through the mapped
member's private CoS delegation. It requires an independent exact allow-once
OpenClaw receipt and the normal allowlist, governance and rate-limit sequence.
A durable `cos.response.attempted` precedes contact. There is at most one
contact attempt per approval event, including after restart, cancellation,
timeout, provider error or missing terminal evidence.

The member device supplies the authenticated review and approval surface.
It is not the sending identity or iMessage transport: the enrolled CoS account
and guarded OpenClaw transport on the central Mac perform the actual send.

At the final boundary the application rechecks exact service ownership,
content, member-scoped recipient policy, expiry, observation and separate
channel-activation authority. Approval must be newer than operational
activation. Old suppressed work is never released by disabling observation.
Gmail policy dispatch shares the tenant's Settings policy lock; completed
revocation prevents later dispatch from using the prior policy.

Gmail's actual `send()` requires a process-local exact-response permit.
Generic service Gmail sends fail closed. Service OAuth grants have their own
owner binding; historical unbound and member grants do not qualify. CoS Gmail
uses only read and send scopes. Service mailbox read/archive mutations are not
part of the response contract.

## Native iMessage boundary

OpenClaw remains the channel and native transport owner. The plugin uses its
pinned public outbound adapter to send the admitted plain text. It does not
expose the private dispatch body as model tool output. That adapter has no
raw-text bypass: it normalizes tables, inline directives and Markdown.
iMessage approvals therefore accept a conservative single-line literal subset
without formatting syntax, control characters or boundary spaces. Email text
is unchanged. The member receives an actionable validation message before any
approval is recorded. The final claim still compares the entire confirmed
body; the syntax restriction does not replace exact content enforcement.

Every dedicated account uses the reviewed `cos-imsg-guard.mjs` executable as
its actual CLI path. The wrapper forwards only supported read RPC methods and
refuses mutations unless an exact unclaimed response admission exists.
Admission binds account, normalized phone and full text, expires after thirty
seconds and is atomically claimed once. Generic messages can consume only an
identical already-approved effect, never change its recipient or content.
The claim marks dispatch as in flight. The provider settlement holds the same
tenant policy lock as Settings revocation, so a completed revocation cannot
race a previously admitted response. An in-flight revocation waits for the
bounded native result; it does not recall an already submitted message.

The final claim opens the existing observation wrapper, which waits for the
guard's terminal callback. Provider success is audited only after that callback.
An absent, ambiguous or timed-out native result is unknown; it is never retried.
If the sole invocation returns or its admission expires while still unclaimed,
result collection and expiry retire it under the same lock used by native claim and
records `denied` with zero effects. A late claim cannot use the retired
admission; claims recheck their lifetime after awaited final checks. Claimed
admissions and missing process state never support this zero-effect conclusion.
The guard has separate exact binary, configuration and account checks.
Its provider child receives only basic OS environment variables; Gateway
credentials, credential aliases and runtime injection variables are excluded.
Hook cancellation and generic-tool blocking are defense in depth: the pinned
OpenClaw hook alone is insufficient because some direct sends bypass it and
hook errors fail open.

## Results and proof

Every authorized response resolves to a private `cos.response.result`:
`sent`, `suppressed`, `denied`, or `unknown`. Unknown has no invented effect
count. The native wrapper does not expose message bodies or credentials in
diagnostics. `external.sent` is emitted only by the existing observation seam.
For Gmail, that seam attributes the effect to the CoS service principal while
retaining the approving member's private scope and exact approval chain.
The adapter supervisor's shared device identity is never presented as the sender.

**MODERNIZE — historical attribution.** A CoS attempt is an internal
`openclaw-plugin` / `cos-response` command, not a provider-source account.
The deployment audit attributes only that exact command to its member's private
workspace after matching the immutable approval's tenant, member, actor, scopes
and correlation fields. Invalid internal coordinates remain unattributed even
if a provider binding happens to match. Exported evidence remains content-free;
the separate source-assurance audit still validates recipient policy, independent
approvals, exact content and every terminal provider effect.

Production-composition coverage is in
`tests/acceptance/test_cos_response_execution.py`; actual Gmail send and
observation code run against a credential-free provider double. Native tests
exercise exact claim, revocation, terminal callback, one-use authority and
absence of unauthorized effects. `test_cos_response_text.py` covers early
validation, unchanged email text and the claimed/unclaimed result distinction.
The hosted source-install gate runs a synthetic Unicode, spacing, directive,
escape and formatting corpus through the exact pinned OpenClaw sender's pure
formatting functions. This checks the supported literal subset and specific
regressions; it does not prove arbitrary Markdown preserves exact content.
**MODERNIZE — normal Gateway startup proof:** the hosted exact-source approval
probe starts the pinned Gateway with an empty provider/channel configuration
and an enabled scheduler containing no jobs. It explicitly removes the
channel/provider/cron skip overrides, requires bounded `/readyz` readiness and
real read-scoped `cron.status`/`cron.list` responses, and waits for the relay's
signed read seam to become usable before exercising every existing approval
boundary. Auxiliary browser, canvas and Gmail watcher services remain disabled;
the probe performs no model invocation or provider contact. This strengthens
the production-composition requirement in `ACCEPTANCE-CONTRACT.md`: deferred
sidecar startup is not evidence that the ordinary service startup can finish.
Plugin tests cover ordinary reply
cancellation, generic-tool bypass refusal and private native dispatch without
retry. Operational activation and transport pin proofs remain separate
prerequisites; code tests never claim a live sign-in or delivered message.

**MODERNIZE — approval relay lifecycle:** the generic service start hook returns
immediately while the pinned public SDK establishes its native approval
connection. Signed approval listing and resolution fail closed until startup
completes within thirty seconds. Failure, expiry or shutdown clears pending
approvals and rejects callbacks from retired connection generations. Shutdown
waits at most five seconds for the SDK's cleanup; a replacement connection is
refused until cleanup succeeds. The relay never automatically retries startup
or an uncertain approval resolution. These bounds preserve OpenClaw's ownership
of native approval authority under `BOOTSTRAP-AND-TOPOLOGY-CONTRACT.md` without
allowing a stalled approval connection to block the ordinary Gateway lifecycle.

## Standing replies to the owner

**MODERNIZE**, owner-directed 2026-10-21: an explicit standing self-reply grant
permits ordinary final answers to that owner's incoming private iMessages
without asking them to approve each answer. The historical recipient ceiling
alone does not create this grant. The guarded channel activation request's
`allow_owner_self_replies: true` records exact owner/agent/account/recipient
binding hashes in `cos.channels.activated` schema 2 and independently in the
OpenClaw plugin configuration. Omission retains only existing matching grants
in both places; `false` removes the authorizing owner's bindings. Existing
grants for other members are retained only while their exact routes and
recipient policy still match. An observation pause retains permission while
suppressing sending. Routine reactivation therefore preserves
standing permission while revoked or changed destinations cannot inherit it.
Existing schema 1 activations and exact manual approvals retain their behavior.

The plugin binds the trusted `message_received` native message ID to its durable
private intake receipt and the matching `agent_end` user transcript transport
metadata. The pinned dispatcher does not populate the inbound run ID despite
its optional SDK declaration. A host-collected followup uses its protected
collection marker and exact pending source text in the same private session.
When an inbound run ID is present it must also match. It awaits intake even when that hook is still completing, isolates queued
runs in the same conversation, excludes analysis/tool/progress/silence output,
and consumes the run before attempting delivery. No model tool can grant the
permission. The internal self-reply route verifies the exact principal, current
private delegation, agent, session, native source message, recipient ceiling,
active service channel, domain grant and independently enabled host binding.
The incoming receipt must be newer than activation and at most one hour old.
Every check is repeated at the guarded native boundary; observation still
suppresses the effect. Generic delivery and third-party sends remain blocked.

A standing reply records a schema 2 `cos.response.approved` fact with the CoS
actor and the real standing authority/source references, followed by a distinct
`cos.reply.openclaw_authorized` receipt. It never fabricates a human exact-content
confirmation or OpenClaw allow-once decision. Existing attempt, claim, native
terminal, privacy guard, rate limit and source/historical assurance paths remain
mandatory. Duplicate incoming receipts and restart cannot resend an attempted
part; unknown outcomes are not automatically retried.

Answers are flattened to plain-text lines, split into at most sixteen 3,500
UTF-16-unit parts, and ASCII punctuation is escaped before the pinned native
renderer. Admission binds the exact resulting text. A later part requires proof
that the preceding part was sent. The pinned source-install formatter check
covers this production escape function, Unicode, links, punctuation, directives
and long responses. `test_cos_self_replies.py` covers production API composition,
revocation, duplicate delivery, restart, observation, ownership and assurance;
`self-replies.test.ts` covers real plugin hook wiring and concurrent run binding.

Impact map: channel activation and rollback -> activation/reconciliation tests;
private intake/session -> forwarded-intake and standing-reply tests; external
send authority and terminal audit -> response execution, source and historical
assurance; native formatting -> authenticated pinned formatter corpus. Gmail
and unrelated acquisition behavior are unchanged. This touches shared external
write governance and event schemas, so the existing protected system release
gates remain required after focused development checks. No test authorizes
provider contact or substitutes for observed live delivery.

Integration findings: the pinned inbound dispatcher omits its optional run ID,
and Codex reasoning mirrors lack a phase tag. Regression cases use the actual
pinned inbound mapper, retained native transport metadata, collected followups,
and mirror identity to avoid silent correlation failure or disclosure of
reasoning. Source assurance loads the exact standing grant and input references
plus activation changes; metadata-only collection otherwise loses the authority
chain even after a valid native send.

## Requested private timed reminders

MODERNIZE the existing private self-reply seam: an explicit member request may
bind one exact text and aware future time to that member's own enrolled iMessage
endpoint. `administrateme_self_reminder` composes encrypted intent, an OpenClaw
one-shot script timer and the existing guarded sender. It does not create a
second scheduler, permit arbitrary scripts, enable generic `message`, or extend
the transient one-hour reply window. A provider reminder/calendar alert alone
is not completion of a requested timed text.

Future self reminders are opt-in. The supported channel activation request's
`allow_owner_self_reminders` field changes only that owner's exact existing
reply bindings. `scheduledSelfReminderBindings` is the independent host ceiling;
the durable authenticated member request supplies the exact domain intent.
Existing recipient policy, current delegation, channel activation, observation,
final native claim, terminal audit and no-automatic-resend behavior remain.
Routine activation preserves the host ceiling only for retained reply bindings.
The stored request survives a restart and ordinary reactivation; current revoked
permissions still prevent its effect. Cancellation withdraws the request itself.

The timer stores an opaque event reference, never the message or recipient. Its
single permitted callback resolves only the bound member/agent/job/session and
may deliver from the due time through ten minutes late. A missed window stays
unsent. Unknown contact is never retried. Domain cancellation happens before
best-effort timer cleanup. Confirmation requires native enabled-job readback;
a prepared record, registration timeout or callback proposal is not scheduled.

Validate the production API through request, restart, due-time enforcement,
revocation, cancellation, exact native admission and terminal history. The
isolated pinned-Gateway probe checks actual scoped timer creation, persistent
restart, script tool context and one completion without a model or provider.
It is not proof of a live iMessage delivery. The `cos-reminders` release scope
runs these affected contracts, existing reply/recipient/identity/approval/audit
checks, schema checks and static validation through the unchanged protected
status. Its closed source boundary is checked across base, head and test merge;
changed storage, credentials, dependencies or unrelated runtime/test inputs use
the system path. The macOS worker also requires the pinned native timer and
sender checks. Drafts and missing, failed or stale workers cannot approve release.

Rollout has two bounded stages in one protected artifact: deploy with future
reminders disabled and verify existing replies, then explicitly enable the
owner's requested reminder permission. Before new reminder records exist, the
ordinary predecessor remains the binary rollback. After use, retain the new
schema-aware artifact and withdraw the new permission through supported channel
activation (`allow_owner_self_reminders: false`); cancel pending requests before
restoring the permission. Do not blindly downgrade a live log containing v3
response approvals to a predecessor that cannot audit that authorization method.
Verify existing reply behavior in the permission-off configuration with retained
reminder receipts before treating that configuration as the recovery checkpoint.
