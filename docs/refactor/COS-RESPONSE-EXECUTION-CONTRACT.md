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
expose the private dispatch body as model tool output. Markdown transformations
that change confirmed text fail the final comparison rather than silently
changing the member's message.

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
The guard has separate exact binary, configuration and account checks.
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
absence of unauthorized effects. Plugin tests cover ordinary reply
cancellation, generic-tool bypass refusal and private native dispatch without
retry. Operational activation and transport pin proofs remain separate
prerequisites; code tests never claim a live sign-in or delivered message.
