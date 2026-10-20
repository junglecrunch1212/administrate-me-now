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
