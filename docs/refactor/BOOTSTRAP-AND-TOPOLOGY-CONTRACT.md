# Bootstrap and topology contract

## Deployment boundary

### Central family hub

The first supported central deployment contains exactly:

1. `adminme-runtime`: one long-lived Python process composing the event log, bus, all thirteen projections, reactive pipelines, adapter supervisors, xlsx forward/reverse services, product router namespaces, health/Doctor, and built family assets.
2. One OpenClaw Gateway process pinned to `v2026.7.1` / release commit `2d2ddc43d0dcf71f31283d780f9fe9ff4cc04fe4`, owning generic assistant runtime concerns.
3. Operator-owned platform services that are not AdministrateMe runtimes: Tailscale Serve, 1Password, backup tooling, and external channel servers when selected.

`adminme-runtime` binds loopback `127.0.0.1:3330`; Tailscale Serve is the only network edge. It serves `/api/core`, `/api/comms`, `/api/capture`, `/api/automation`, `/api/bridge`, `/api/system`, browser assets, and the narrow member portal. Legacy `:3333–:3337` product ports are compatibility aliases only if tests prove they are required; they do not create more processes. The runtime rejects/strips untrusted identity headers and accepts identity only from the local proxy boundary.

Node 22 may compile frontend/profile assets during build/install. It is not a second central application server. OpenClaw listens on loopback `:18789`. Exact paths and ports are instance configuration, never string-literal instance paths in business code.

### Member bridge

Each Apple-using member may have a separate least-privilege bridge daemon on a Mac signed into that member’s account. It holds only its enrollment identity and source-specific permissions; it never holds the central event-log key or another member’s credentials. The central ingest route derives owner scope from enrolled Tailscale identity and rejects a payload that claims another scope. Kid restrictions are enforced in the enrollment manifest and central policy.

## Composition lifecycle

Startup order is transactional and health-visible:

1. Resolve/validate instance config and secret references; acquire one instance lock.
2. Verify schema/migration compatibility, exact OpenClaw pin, filesystem ownership, encryption key reference, and observation default.
3. Open/migrate encrypted event log; refuse plaintext or invalid keys.
4. Open durable bus offsets and construct a single append-and-publish port so commit always precedes notification.
5. Register all thirteen projections and schemas; open/rebuild/migrate as required.
6. Register reactive pipelines and durable consumer ids; reconcile pack registry.
7. Start central adapters and xlsx services with durable watermarks/circuit state.
8. Validate/install/reconcile the one OpenClaw plugin, typed skills, slash commands, and standing orders against the pinned host.
9. Start router namespaces/browser assets behind identity middleware; report readiness only after required capabilities are healthy or explicitly degraded by policy.
10. Emit a versioned runtime-started/health snapshot without secrets.

On SIGTERM/SIGINT: stop accepting writes; mark not-ready; drain bounded in-flight requests; stop adapters; stop trigger admission; let consumers checkpoint completed work; flush workbook/sidecar writes; close projection connections, bus offsets, then the event log; emit/retain shutdown evidence where safe. A forced timeout is surfaced at next start. Restart replays from durable offsets and reconciles ambiguous actions by idempotency/outcome, never by blind resend.

## Phase separation

- **Phase A:** development and destructive rehearsal only in Codex/cloud or a disposable macOS lab with fake accounts and credential-free fixtures. Never connect the target family Mac or real household providers.
- **Release boundary:** reviewed PR, passing release contract, signed/versioned artifacts, backup/rollback plan, exact OpenClaw and dependency pins.
- **Phase B:** install only the reviewed release on the family Mac. Bootstrap is the installer and evidence recorder, not an experimentation shell. Real-provider enrollment is least-privilege and observation-first.

## Ten-section bootstrap

Each section has `not_started|in_progress|complete|failed|rolled_back`, versioned inputs, an idempotency key, preflight, actions, verification, rollback, emitted events, and a secret-safe report. Encrypted answers and the event log support resume at the first incomplete/invalidated section. Re-running a completed version is a no-op unless a declared input changed.

1. **Environment preflight:** supported macOS, FileVault, ownership/permissions, disk, time sync, Tailscale, Python runtime, build-only Node, pinned OpenClaw, workspace, Homebrew/git/gh/rclone/LibreOffice/1Password as selected, backup target. Abort before mutation on failure.
2. **Assistant persona:** select/fork persona, validate/compile tenant-neutral sources to derived SOUL material, install at the pinned OpenClaw-supported location, reload and verify. No authority or model policy comes from persona.
3. **Household composition:** create tenant/household Parties, principals/children/helpers/ambient members, Memberships/Relationships, time zone and address through typed events; verify projection and two-member isolation.
4. **Profiles:** assign one of five built-ins or validated custom profiles; disclose behavior diff; emit assignment; verify route/engine policy for adult/child/ambient roles.
5. **Credential references:** collect through local secure UI/CLI, validate, write values to 1Password/OpenClaw secret storage, persist references only, redact logs/reports. Optional credentials stay optional; provider LLM keys go to OpenClaw only.
6. **Plaid:** sandbox first, consent/link, read-only minimum scopes, initial sync/reconciliation, no production flip during bootstrap/observation.
7. **Seed data:** tenant-supplied household/CRM/place/asset/account/recurrence/project data becomes typed events; skipped optional data creates an explicit follow-up, never exemplar defaults.
8. **Central integrations:** pair selected OpenClaw channels and central Cat-A/B/C/E adapters; install/verify plugin, skills, commands, standing orders; seed selected shared lists; test Apple/Google calendar/contacts/Home Assistant where enabled using observation-safe actions.
9. **Observation briefing:** enforce active observation, explain the documented 5–7 day trust review, perform a would-have-happened test that is suppressed/audited, show review/disable prerequisites, backup status, member portal, and emergency stop.
10. **Bridge enrollment:** create one short-lived, member-bound package at a time; verify target Apple identity, Tailscale identity, permissions, allowed Cat-B/D adapters and kid restrictions; enroll centrally; revoke bootstrap material; verify owner-scoped ingest and bridge health.

Bootstrap completes only when a secret-scanned report lists versions, checks, events, installed capabilities, skipped optionals, observation start/end-not-before, backup/restore proof, and rollback status. `bootstrap.completed` is not emitted if any required section is incomplete.

## Observation trust period

Observation begins before any connector/action becomes live and remains active for at least five full days and normally seven. Disable requires a principal, healthy backup, reviewed suppression history, zero unresolved privacy/governance/identity failures, reconciled connector watermarks, passing Doctor, explicit confirmation, and an audit event. Time alone never flips it. Inbound facts, internal reasoning, projections, workbooks, and local UI continue. The final external seam stores the complete redacted-for-view but audit-complete would-have-happened record and performs no side effect.

## Bootstrap acceptance evidence

Release requires fresh install, interrupted resume at every section boundary, repeat no-op, changed-input reconciliation, per-section rollback, corrupted-state fail-closed, secret scan, observation proof, backup and bare-machine restore, uninstall/disable without data destruction, one Apple-member bridge and kid-restriction rehearsal, and a two-instance parallel canary. The clean rehearsal is on a disposable macOS host; Windows results do not substitute.

## Gate 2 implementation status

The central runtime now owns one SQLCipher event log, one durable bus, one typed commit-before-notify writer, thirteen projections, reactive pipelines, adapter/xlsx lifecycles, product namespaces, bridge ingest, and system readiness/liveness. Startup unwinds partially opened resources on failure. Shutdown first rejects new writes, then drains producers and consumers within bounds before closing projection stores, bus state, and the encrypted log. The ten-section installer, macOS rehearsal, browser assets, and full Doctor remain later gates; they are not falsely reported as runtime-ready capabilities.
