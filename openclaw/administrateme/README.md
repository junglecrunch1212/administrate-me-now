# AdministrateMe native OpenClaw plugin

This is the only OpenClaw plugin package for AdministrateMe v2. It targets the
official-contract snapshot for OpenClaw `2026.7.2` at commit
`ce67ffb70e83e5ebb56ea02e814e577153f84f61`. The repository fixture validates
the used SDK surface; it is not a claim that a live Gateway ran on Windows.

## Trust boundary

The plugin talks only to the authenticated AdministrateMe loopback API under
`/api/openclaw/v1/`. Put a random value of at least 24 characters in the
environment variable named by `sharedSecretEnv` (default
`ADMINME_OPENCLAW_SHARED_SECRET`) for both processes. The value is never a
household credential and is never logged. AdministrateMe independently maps
the exact `channel|account|actor` tuple from
`ADMINME_OPENCLAW_ACTOR_IDENTITIES_JSON` to a member with an explicit role in
`ADMINME_OPENCLAW_MEMBER_ROLES_JSON`, then enforces member role,
scope, observation, approval, concurrency, and idempotency.

OpenClaw operator scopes and plugin permission decisions cannot grant household
access. Side-effect tools are optional and request one OpenClaw `allow-once`
approval; AdministrateMe then applies its own authorization. Dashboard calls
also require `client.authenticatedUserId`; an operator bearer by itself is not
an actor identity.

The production-v2 Google Tasks surface is deliberately three narrow tools:
prepare one exact member-approved `google_tasks_create` command, execute that
same command once, and read its terminal/observation/reconciliation state. It
is not a generic Google Tasks writer. The execute tool's allow-once receipt is
bound to the command, envelope, account, pre-existing task list, content
fingerprint, expiry, and effect ceiling; AdministrateMe independently rechecks
the domain approval and consumes a scoped observation admission.

The bridge forwards selected content from a reviewed dedicated
`forwardIntakeAccounts` endpoint, `/adminme-forward`, or an allowlisted channel
with an explicit host `{administrateme: {consent: true, eventKind, sensitivity}}`
marker. A dedicated intake endpoint treats all messages sent there as selected
material because the transport cannot reliably distinguish a native forward
from another message. Other ordinary conversation turns are ignored. Privileged
or unknown sensitivity is not accepted. Model hooks record only provider/model
metadata for tracked `adminme:` sessions and never subscribe to `llm_input` or
`llm_output`.

The backend service channel's `openclaw_channel_id` must exactly match the
reviewed plugin entry's `channelId` (`imessage` or `bluebubbles`). Its account
reference must be `provider-account:` plus SHA-256 of the entry's exact,
case-sensitive `accountId`; no trimming or lowercasing is applied. A same-named
account on the other transport is a different endpoint. Each member still
requires an exact transport/account/actor mapping and isolated direct-peer
agent binding. The inert
[`cos-imessage-intake.example.yaml`](../../config/cos-imessage-intake.example.yaml)
shows the matching coordinates; enabling a real provider remains a separate
reviewed enrollment step.

## Build and fixture test

```text
npm ci
npm run workspace:compile
npm run check
npm test
npm run build
npm pack --dry-run
```

The selected AdministrateMe/OpenClaw runtime requires Node `>=24.22.1 <25`.
Older retained releases may have a lower floor. The current Windows development
host is intentionally fixture-only. In a disposable Mac
lab with the exact OpenClaw checkout, set `ADMINME_LIVE_GATE=1` and
`OPENCLAW_SOURCE_DIR`, then run `npm run test:live:mac`. That gate links the
plugin, inspects its live runtime, restarts the Gateway, and requires deep RPC
health. Do not run it on the family Mac before review and merge.

Bootstrap must copy the compiled workspace files into the per-instance
OpenClaw workspace and reconcile `automations.json` through OpenClaw's current
automation commands. Per-member profile additions belong in per-member
overlays; `SOUL.md` remains per-instance identity.

## Service work entry and revocation

After the supported installer selects this release, the non-member CoS agent
has its own immutable instruction workspace and four service tools. It can keep
internal work/notes/scratchpad and read canonical task/commitment facts covered
by current standing grants. This is internal processing authority; source
confidentiality and separate disclosure/provider-write rules still apply.

An authorized local operator starts one bounded native run with the normal
Gateway connection (no member mapping or manually derived session credential):

```sh
openclaw gateway call administrateme.service.run --params '{"message":"Read currently granted work, keep a sourced internal note, and verify its saved state.","request_id":"service-work-example-001"}' --json
```

The method accepts only `message` and `request_id`, requires `operator.admin`,
seals the configured service agent and a request-specific signed session inside
the plugin, and invokes the
pinned native `runtime.subagent.run` with `deliver:false`. It returns admission
and `run_id`, not completion or a transcript. Use native `agent.wait` with that
run ID to observe settlement; the service's own state tool verifies persistence.
Each admission attempt signs a fresh nonce; continuity comes from the authorized
record tools, so revoked source grants cannot survive through an earlier run
transcript. Reusing the same request ID uses native admission idempotency while its cache
is valid. After expiry or restart, even that same ID starts fresh model context.
Operator access does not supply member authority: the domain checks each source
against current service grants. No grants or external writes are enabled here.
The pinned runtime labels an internal non-delivery turn `webchat`; only that
sentinel without recipient/account/thread/sender coordinates is accepted with
the exact service binding. External and mapped-member contexts fail closed.

To revoke this feature while preserving the schema-aware runtime and history:

```sh
openclaw config set plugins.entries.administrateme.config.serviceWorkEnabled false --strict-json
openclaw gateway restart
```

The installer preserves this flag. On restart the four tools, their pre-call
hook and the run entry all deny. Use the supported activation/operator restart
for the installed instance; do not edit its release or state. A binary downgrade
after new persistent history requires independently proved schema compatibility.

Hosted `check-service-work-gateway.mjs` exercises the pinned Gateway's real
operator RPC, native agent context, hook and production tools against synthetic
loopback model/domain fixtures. It does not establish live model availability,
member grants, provider permissions or actual deployment usability.
