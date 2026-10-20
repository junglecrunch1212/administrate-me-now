# CoS response recipient contract

Date: 2026-10-20.

This extends the [CoS Settings contract](CHANGE-ORDER-026-COS-SETTINGS-CAPABILITY-CATALOG.md)
and the [member-forwarded intake contract](CHANGE-ORDER-031-MEMBER-FORWARDED-OBLIGATIONS.md).
It controls the recipient ceiling for the service principal's email and
iMessage responses. It does not enable a provider, enroll an account, change
observation mode, or grant send approval.

| Classification | Governing behavior |
| --- | --- |
| MODERNIZE | CO-026 Settings adds the URL-backed **CoS replies** section with exact recipient, channel and household-member association. |
| PRESERVE | CO-031 forwarding produces review candidates; it does not authorize provider effects. |
| PRESERVE | Domain approval, independent OpenClaw approval, service account ownership, final privacy guard, rate limiting, observation and terminal audit remain independent requirements. |
| PRESERVE | Adult private context is isolated. A household-wide recipient ceiling grants no access to another member's conversation. |

## Exact recipient ceiling

The initial list is empty and denies every response. A list entry contains
`member_id`, `channel` (`email` or `imessage`) and one bare `address`. Email
domains are lowercase; local parts remain exact, including case, dots and
plus tags. There is no provider-specific alias equivalence. iMessage accepts
an exact email or an international number in E.164 form. Display names,
lists, comments, group syntax, whitespace, control characters, non-ASCII
addresses and wildcards are rejected. One channel endpoint cannot belong to
two members. A recipient's member must exist in the configured household;
infrastructure identities cannot be recipients.

Every actual To/Cc/Bcc or resolved group participant must match an entry for
the active member context. Transports may impose the stricter single-recipient,
no-group contract. The configured service principal and account must also be
independently verified by the transport; listing an endpoint cannot substitute
for that binding. A mismatched account, member, recipient or malformed policy
fails closed.

## Settings and storage

Only an authenticated household principal in their own view can read or
replace the service recipient configuration. Other roles and view-as cannot
retrieve the addresses. This is household service configuration; it does not
expose member-owned source credentials or provider content.

`GET /api/member/v1/settings/cos-replies` replays the tenant's encrypted
`settings.cos_response_allowlist_changed@1` events. `PUT` replaces the full
list with an expected policy revision and idempotent request ID. Concurrent
edits require refresh. A retry returns the original receipt without changing
the current policy. Reusing a request ID with different content is rejected.
An empty replacement revokes all recipients.

The append-only encrypted event is authoritative. Contact details are never
written into the plaintext Settings journal. Restart/replay preserves the
policy and revocations. An invalid sequence or event fails closed. No CoS
recipient policy is installed from built-in household contacts.

The same section offers an exact-content response approval form. Options
contain only the active member's listed endpoints and independently bound CoS
accounts. Email has a subject; the initial guarded iMessage transport supports
phone destinations and no subject. A saved approval says it is awaiting
independent sending approval, includes its expiry, and never claims delivery.
The recipient ceiling can contain iMessage email handles, but the initial
transport cannot send to them and the form excludes them.

Dispatch reloads the current policy immediately before the provider effect.
It shares the tenant policy lock with mutation so an accepted revocation
cannot race a later dispatch using the former list. Exact-content approvals
bind the policy revision and event ID; a policy edit invalidates those prior
approvals rather than silently widening them.

## Evidence

The credential-free production-composition acceptance test is
`tests/acceptance/test_cos_response_allowlist_settings.py`. It covers
principal/self authorization, view-as and child exclusion, encrypted receipt
replay, optimistic conflicts, retry idempotency, immediate empty-list
revocation, cross-member rejection, absence of contacts in the Settings
journal, and zero provider effects from Settings changes.

`tests/unit/test_cos_response_policy.py` covers normalization, malformed
recipients, every-recipient matching, alias rejection, duplicate endpoint
ownership, tenant isolation and fail-closed replay. These tests run only in
isolated CI or the authorized disposable lab. Live member browser acceptance
uses the documented member-device workflow after activation.

The disposable browser harness `tools/console-reference-harness/cos-reply-controls.mjs`
exercises the actual Settings form, save/reload/revoke behavior, thirteen-section
navigation, labeled controls, keyboard accessibility and desktop/narrow reflow.
Narrow browser coverage does not establish physical mobile-device acceptance.
