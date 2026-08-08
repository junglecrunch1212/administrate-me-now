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

The bridge ignores every ordinary conversation turn. It forwards content only
when the configured channel is allowlisted and host metadata includes an
explicit `{administrateme: {consent: true, eventKind, sensitivity}}` marker.
Privileged sensitivity is not accepted. Model hooks record only provider/model
metadata for tracked `adminme:` sessions and never subscribe to `llm_input` or
`llm_output`.

## Build and fixture test

```text
npm ci
npm run workspace:compile
npm run check
npm test
npm run build
npm pack --dry-run
```

AdministrateMe's reviewed runtime baseline requires Node `>=24.15.0 <25`. The current
Windows development host is intentionally fixture-only. In a disposable Mac
lab with the exact OpenClaw checkout, set `ADMINME_LIVE_GATE=1` and
`OPENCLAW_SOURCE_DIR`, then run `npm run test:live:mac`. That gate links the
plugin, inspects its live runtime, restarts the Gateway, and requires deep RPC
health. Do not run it on the family Mac before review and merge.

Bootstrap must copy the compiled workspace files into the per-instance
OpenClaw workspace and reconcile `automations.json` through OpenClaw's current
automation commands. Per-member profile additions belong in per-member
overlays; `SOUL.md` remains per-instance identity.
