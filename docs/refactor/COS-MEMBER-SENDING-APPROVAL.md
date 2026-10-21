# CoS member sending approval

Classification: **MODERNIZE** the member portal relay under
ADR-0001-OPENCLAW-ADMINISTRATEME-BOUNDARY.md and UX-BEHAVIOR-CONTRACT.md.
**PRESERVE** independent OpenClaw and AdministrateMe approvals, private member
scope, observation suppression, exact content, recipient policy, immutable
terminal audits, and no automatic provider retries.

Before a reply, Settings checks the member-private delegation described in
COS-RESPONSE-EXECUTION-CONTRACT.md. How my CoS works exposes explicit owner-only
permission setup, scope and expiry review, and revocation; channel enrollment
alone never grants these permissions. Missing permission is shown before a new
text approval, and an uncertain grant result is reconciled through its exact
authenticated request identity without automatic retry.

The member then approves the exact plain-text reply in Settings → CoS replies.
“Continue to sending approval” opens their existing private conversation and
asks CoS to submit that exact approved reply to the existing response tool.
This button does not approve OpenClaw execution. The conversation separately
shows the approved channel, recipient, subject and text, with “Allow this reply
once” and “Don’t send”. Neither a chat message nor a timeout is a decision.
These are approval actions on the member's device. The actual sender remains
the enrolled CoS account; iMessage transport runs on the central Mac, regardless
of the operating system used to view the member dashboard.

The response plugin records a short-lived binding at `before_tool_call`, using
the host's exact tool call, agent and session plus the domain approval ID and
request fingerprint. Its service uses the pinned public
`openclaw/plugin-sdk/approval-runtime.createChannelNativeApprovalRuntime`
with `eventKinds: ["plugin"]`; it handles only `administrateme_cos_response`
requests matching that binding. The SDK subscription and native
`plugin.approval.resolve` remain owned by OpenClaw. Its supported local
operator-approval client fallback obtains runtime authority inside the Gateway
process. No runtime token, raw provider arguments, or unrelated approvals reach
the dashboard, and the Python chat client retains read/write scopes.

The authenticated member APIs independently require the principal's own view
and enabled private chat. They derive the expected agent/session, read the
encrypted approval event, verify ownership, fingerprint, expiry and current
recipient policy, then return the approved content. A sending decision requires
a fresh native pending record and exact revision. The backend mints an
eight-second HMAC ticket only for that explicit action. Tickets bind operation,
member, session, agent, full native ID, revision, decision, nonce and expiry.
The plugin rejects tampered, expired, reused or mismatched tickets and consumes
the pending record before native resolution. Neither disconnect nor restart
replays an approval. Other tools and exec approvals are outside this relay.

The pinned host invokes `onResolution` without awaiting its promise. The CoS
tool therefore waits, for at most ten seconds, for its exact independent receipt
publication before entering the existing backend execution path. This is not
approval synthesis or retry: absent/failed publication still fails closed.

Protocol evidence is the official source at commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`:

- `src/gateway/server-methods/chat-origin-routing.ts` sets the initiating
  surface to internal `webchat` for `chat.send` with `deliver: false`.
- `src/infra/exec-approval-surface.ts` recognizes that Web UI as approval-capable.
- `src/agents/agent-tools.before-tool-call.approval.ts` creates the real native
  plugin request with tool-call, session and agent coordinates, and runs its
  non-awaited resolution callback.
- `src/plugin-sdk/approval-runtime.ts`,
  `src/infra/approval-native-runtime.ts`, and
  `src/gateway/operator-approvals-client.ts` define the supported subscription
  and local approval-runtime fallback.
- `packages/gateway-protocol/src/schema/plugin-approvals.ts` and
  `src/gateway/server-methods/plugin-approval.ts` define exact pending and
  allow-once/deny resolution payloads.

Hosted tests cover authenticated production routes with encrypted state and
the pinned protocol boundary, plugin correlation and ticket handling, deferred
receipt publication, browser interactions, and the real public SDK/native
registry. These fixtures never enroll or contact household provider accounts.
