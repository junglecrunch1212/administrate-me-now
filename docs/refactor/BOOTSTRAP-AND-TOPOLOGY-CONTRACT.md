# Bootstrap and topology contract

Gate 5 realization: `bootstrap/` now implements the ten-section encrypted journal, fixed operator scripts, Keychain reference boundary, user LaunchAgents, credential-free Doctor round trip, encrypted backup/restore, member-bound bridge packages, and pre-replacement upgrade validation. Windows process evidence and the macOS CI contract do not replace the guarded disposable-Apple-silicon live gate or authorize family-Mac activation.

## Deployment boundary

### Central family hub

The first supported central deployment contains exactly:

1. `adminme-runtime`: one long-lived Python process composing the event log, bus, all thirteen domain projections plus Triage and the non-authoritative Context Fabric, reactive pipelines, adapter supervisors, xlsx forward/reverse services, product router namespaces, health/Doctor, and built family assets.
2. One OpenClaw Gateway process pinned to package/source version `2026.7.2` / commit `ce67ffb70e83e5ebb56ea02e814e577153f84f61`, owning generic assistant runtime concerns.
3. Operator-owned platform services that are not AdministrateMe runtimes: Tailscale Serve, 1Password, backup tooling, and external channel servers when selected.

`adminme-runtime` binds loopback `127.0.0.1:3330`; Tailscale Serve is the only network edge. It serves `/api/core`, `/api/comms`, `/api/capture`, `/api/automation`, `/api/bridge`, `/api/system`, browser assets, and the narrow member portal. Legacy `:3333–:3337` product ports are compatibility aliases only if tests prove they are required; they do not create more processes. The runtime rejects/strips untrusted identity headers and accepts identity only from the local proxy boundary.

The Central Host is a tagged Tailscale infrastructure node
(`tag:administrateme-central`), never a member login or member bridge identity.
Its node owner and service process cannot resolve as a household member. Each
interactive member instead has one distinct reviewed trusted-ingress binding;
missing, duplicate, case-fold-colliding, or cross-member bindings block
readiness. The service runs under a dedicated Standard macOS account. A
separate administrator/recovery account, a tested FileVault unlock path, and
disabled automatic login are independent recent activation evidence; merely
being non-root is not evidence of a dedicated service account or recovery.

Node must satisfy the reviewed AdministrateMe Node 24 baseline (`>=24.15.0 <25`) and may compile plugin/frontend/profile assets during build/install. It is not a second central application server. OpenClaw listens on loopback `:18789`. Exact paths and ports are instance configuration, never string-literal instance paths in business code.

### Member bridge

Each Apple-using member may have a separate least-privilege bridge daemon on a Mac signed into that member’s account. It holds only its enrollment identity and source-specific permissions; it never holds the central event-log key or another member’s credentials. The central ingest route derives owner scope from enrolled Tailscale identity and rejects a payload that claims another scope. Kid restrictions are enforced in the enrollment manifest and central policy.

A package-exported credential is provisional: it may submit only the exact,
unexpired `bridge.enrolled@v1` bootstrap proof bound to its member, enrollment
ID, identity, private scopes, bootstrap adapter, and idempotency key. It cannot
submit a batch or any ordinary fact. Ordinary ingest requires active status.

### Communication, service identity, and OpenClaw isolation

Every enabled central provider account is bound explicitly to exactly one
reviewed owner principal: either a human member or a non-human service
principal. It also names one exact provider account, an explicit
`personal|work|service` workspace, reviewed permission scope, binding
provenance, and ownership attestation before it can ingest. Duplicate or
cross-owner provider coordinates fail closed. The currently shipped Gmail,
Google Calendar, and Google Tasks adapters remain member-owned; service-owned
accounts require a separately reviewed capable adapter. Account discovery,
address matching, possession of a token, and vendor-side sharing are never
AdministrateMe authority.

A Chief-of-Staff service principal, its provider account, addressable Endpoint,
optional Party, Tailscale infrastructure node, and OpenClaw agent/session are
separate coordinates. The planned **CoS Apple service identity** is a
household-owned, non-human channel identity and must never resolve as either
human member. Its initial state is `planned` / `account_not_created`: there is
no account reference, endpoint, enrollment, authentication, or health claim.
Future service-channel states are `planned`, `account_not_created`,
`account_created`, `channel_not_enrolled`, `observation`, `healthy`,
`degraded`, `disabled`, and `revoked`. Reserving an address cannot advance the
state. Exact provider coordinates remain encrypted instance configuration and
public readiness exposes counts and state classes only.

A future CoS-addressed fact must cross a reviewed communications adapter and
resolve through one explicit route to `member_private`, `household_shared`, or
`external_contact`; zero, multiple, or unknown routes remain `unresolved` and
outside all member-visible scopes. Human-member Apple sources remain
member-private and cannot use this service routing path. Provider and Apple
sharing do not create a route, Party, membership, or authorization.

Human-to-human provider traffic is normalized by Cat-A adapters and does not
enter an OpenClaw model session. For member-to-CoS DMs, bootstrap binds one
isolated OpenClaw agent per human member and configures account-aware DM scope
(`per-account-channel-peer`) so two people or two provider accounts cannot
share session memory. A second work agent for the same member is created only
when its tools, model, retention, or organization policy must differ—not just
to render a work filter. Provider account credentials and OpenClaw agent
credentials remain separately revocable.

The CoS may eventually send only as its own reviewed service identity through
allowlist → domain governance → rate limit → final privacy/content guard →
observation → independent OpenClaw and domain approval → terminal audit. It
cannot impersonate or auto-send as a member. Attempted, suppressed, delivered,
failed, and reconciled outcomes are terminally accounted. Apple/iMessage,
BlueBubbles, Twilio, account creation, sign-in, OS permissions, enrollment, and
live sending remain separate post-merge owner gates; this contract adds no
provider connectivity. The current external-write request/approval/dispatcher
contract remains member-owned, so service-channel configuration must reject
`sending_enabled:true` until a separately reviewed generic service-owner
command contract exists and passes observation-suppressed production proof.

## Composition lifecycle

Startup order is transactional and health-visible:

1. Resolve/validate instance config and secret references; acquire one instance lock.
2. Verify schema/migration compatibility, exact OpenClaw version and commit, Node prerequisite, native plugin manifest/build, filesystem ownership, encryption key reference, and observation default.
3. Open/migrate encrypted event log; refuse plaintext or invalid keys.
4. Open durable bus offsets and construct a single append-and-publish port so commit always precedes notification.
5. Register all fifteen projections and schemas (thirteen domain truth plus Triage and the non-authoritative Context Fabric); open/rebuild/migrate as required.
6. Register reactive pipelines and durable consumer ids; reconcile pack registry.
7. Start central adapters and xlsx services with durable watermarks/circuit state.
8. Validate/install/reconcile the one native AdministrateMe OpenClaw plugin from the exact selected immutable release, compiled workspace/SOUL, typed skill, slash commands, dashboard bindings, automation standing orders, and the pinned host's bundled `llm-task`; add the required tool without removing existing policy entries, then require exact plugin install/runtime-source identity, runtime tool registration, plugin inspect, and deep RPC health.
9. Start router namespaces/browser assets behind identity middleware; report readiness only after required capabilities are healthy or explicitly degraded by policy.
10. Emit a versioned runtime-started/health snapshot without secrets.

On SIGTERM/SIGINT: stop accepting writes; mark not-ready; drain bounded in-flight requests; stop adapters; stop trigger admission; let consumers checkpoint completed work; flush workbook/sidecar writes; close projection connections, bus offsets, then the event log; emit/retain shutdown evidence where safe. A forced timeout is surfaced at next start. Restart replays from durable offsets and reconciles ambiguous actions by idempotency/outcome, never by blind resend.

## Phase separation

- **Phase A:** development and destructive rehearsal only in Codex/cloud or a disposable macOS lab with fake accounts and credential-free fixtures. Never connect the target family Mac or real household providers.
- **Release boundary:** reviewed PR, passing release contract, signed/versioned artifacts, backup/rollback plan, exact OpenClaw and dependency pins.
- **Phase B:** install only the reviewed release on the family Mac. Bootstrap is the installer and evidence recorder, not an experimentation shell. Real-provider enrollment is least-privilege and observation-first.

## Ten-section bootstrap

Each section has `not_started|in_progress|complete|failed|rolled_back`, versioned inputs, an idempotency key, preflight, actions, verification, rollback, emitted events, and a secret-safe report. Encrypted answers and the event log support resume at the first incomplete/invalidated section. Re-running a completed version is a no-op unless a declared input changed.

1. **Environment preflight:** supported disposable macOS lab, FileVault, ownership/permissions, disk, time sync, Tailscale, Python runtime, a Node version satisfying the exact source engine, exact OpenClaw source commit, workspace, Homebrew/git/gh/rclone/LibreOffice/1Password as selected, backup target. Abort before mutation on failure.
2. **Assistant persona:** select/fork the instance-level persona, run `npm --prefix openclaw/administrateme run workspace:compile`, install the generated `SOUL.md`, `IDENTITY.md`, `USER.md`, and `AGENTS.md` at the pinned workspace location, reload and verify. Member profiles remain per-member overlays. No authority or model policy comes from persona.
3. **Household composition:** create tenant/household Parties, principals/children/helpers/ambient members, Memberships/Relationships, time zone and address through typed events; verify projection and two-member isolation.
4. **Profiles:** assign one of five built-ins or validated custom profiles; disclose behavior diff; emit assignment; verify route/engine policy for adult/child/ambient roles.
5. **Credential references:** generate a random 24+ character `ADMINME_OPENCLAW_SHARED_SECRET` for both local processes, store only its secret reference, inject its Keychain-resolved value explicitly into both the OpenClaw transport and AdministrateMe skill runtime, and configure exact `ADMINME_OPENCLAW_ACTOR_IDENTITIES_JSON` (`channel|account|actor -> member`) plus `ADMINME_OPENCLAW_MEMBER_ROLES_JSON` separately. Never require the long-lived runtime to rediscover that credential from ambient process state. Every mapped member requires an explicit interactive role and, for enabled external DMs, a distinct member agent/account binding under account-aware DM scope. Every enabled provider adapter requires exactly one explicit member or service-principal owner, provider account, workspace, capability consent, ownership attestation, and provenance. Collect other values through local secure UI/CLI, validate, write values to 1Password/OpenClaw secret storage, persist references only, and redact logs/reports. The transport secret is not member authorization; provider LLM keys go to OpenClaw only. Apple, Google Workspace, Google Cloud, Cloudflare, Twilio, Tailscale, OpenClaw, and AdministrateMe credentials and recovery paths stay independently revocable; a CoS mailbox is never the sole administrative recovery root.
6. **Plaid:** sandbox first, consent/link, read-only minimum scopes, initial sync/reconciliation, no production flip during bootstrap/observation.
7. **Seed data:** tenant-supplied household/CRM/place/asset/account/recurrence/project data becomes typed events; skipped optional data creates an explicit follow-up, never exemplar defaults.
8. **Central integrations:** pair selected OpenClaw channels and central Cat-A/B/C/E adapters; set `ADMINME_LIVE_GATE=1` and `OPENCLAW_SOURCE_DIR` only in the disposable lab; enable only the exact pinned bundled `llm-task`, preserve existing `tools.alsoAllow` entries while adding it, validate with OpenClaw's config writer, and require Doctor to prove it runtime-registered; run the live AdministrateMe plugin link/inspect/restart/deep-RPC gate; reconcile the compiled skill, commands, dashboard, and `automations.json`; seed selected shared lists; test enabled adapters with observation-safe actions. Do not proceed on a version/commit mismatch.
9. **Observation briefing:** enforce active observation, explain the documented 5–7 day trust review, perform a would-have-happened test that is suppressed/audited, show review/disable prerequisites, backup status, member portal, and emergency stop.
10. **Bridge enrollment and capability:** seal reviewed pack/account/container/
permission/provenance coordinates; create one short-lived, member-bound package
at a time; verify the target member and Tailscale identity; accept with zero
enabled provider packs; finalize only the exact owner-scoped bootstrap proof;
and revoke transfer material. A later explicit member action may enable one
sealed pack and encounter its macOS permission prompt. Ordinary provider facts
and commands remain denied until the bridge emits an exact
`adapter.authenticated@v2` receipt and a separate central `bridge-attest`
operation promotes only that pack. Readiness distinguishes transport-active
from provider `not_attested`, `partial`, `attested`, or `not_required` without
publishing provider identifiers.

Bootstrap completes only when a secret-scanned report lists versions, checks, events, installed capabilities, skipped optionals, observation start/end-not-before, authenticated backup/restore proof, retained rollback predecessor, tagged-host and member-ingress acceptance, Standard-account/recovery proof, provider-binding evidence, OpenClaw account/agent/session isolation, and rollback status. `bootstrap.completed` is not emitted if any required section is incomplete. Service channels that are explicitly optional may remain planned or disabled without blocking the current central release, but their state is never promoted by unrelated readiness.

## Post-bootstrap Settings reconciliation

After bootstrap, the member portal may change only the operational state that
ADR-0005 assigns to the Settings control plane. Bounded preferences and
adapter pause state use an atomic secret-free desired-state journal and typed
receipts. Pause/resume reconciles the existing supervisor; it does not create a
new daemon or mutate provider data. Add Bridge may create a scoped enrollment
request, but the reviewed bootstrap ceremony still owns configuration sealing,
short-lived package export, target-member acceptance, owner-scoped transport
proof, finalization, and bootstrap-material revocation. Provider enablement/OS
permission and central capability attestation are later, separate operations;
transport state never substitutes for them. OAuth grants and scope expansion
remain provider-specific secure activation steps. No password, token, provider
account, or secure-reference path is returned to browser Settings.

## Observation trust period

Observation begins before any connector/action becomes live and remains active for at least five full days and normally seven. Disable requires a principal, healthy backup, reviewed suppression history, zero unresolved privacy/governance/identity failures, reconciled connector watermarks, passing Doctor, explicit confirmation, and an audit event. Time alone never flips it. Inbound facts, internal reasoning, projections, workbooks, and local UI continue. The final external seam stores the complete redacted-for-view but audit-complete would-have-happened record and performs no side effect.

## Bootstrap acceptance evidence

Release requires fresh install, interrupted resume at every section boundary, repeat no-op, changed-input reconciliation, per-section rollback, corrupted-state fail-closed, secret scan, observation proof, backup and bare-machine restore, uninstall/disable without data destruction, one Apple-member bridge and kid-restriction rehearsal, and a two-instance parallel canary. Production composition must also prove tagged infrastructure identity cannot authenticate as a member, exact A/B ingress isolation, no-enrollment bridge denial plus the narrow provisional proof, transport-only provider denial, exact capability attestation and stale/wrong-coordinate denial, zero central construction of member Apple adapters, global provider and bridge-account ownership validation, private-by-default shared-resource routing, explicit envelope-consistent service-channel routing/unresolved denial, current OpenClaw topology rather than configured assertions, Settings/readiness agreement, host/config/release-bound rollback evidence, durable terminal-effect integrity, and zero true/unknown/unsuppressed effects in the current attested observation window. The clean rehearsal is on a disposable macOS host; Windows results do not substitute.

The preliminary rehearsal also binds and revalidates the full retained pointer
chain plus fixed host, instance, service-account, and configuration
fingerprints. A separate owner-only command may use that exact strengthened
receipt, encrypted artifact, reviewed HTR-10 watermark/window receipt, clean
non-retained candidate commit, and recent typed HTR observations to write one
append-only mode-0600 `candidate-deployment-readiness-v1` artifact under
`app/readiness/candidate-pending/`. It has no mutable pending selector or
promotion API, says the candidate is inactive and unauthorized, and leaves
updater backup, rollback compatibility, crash recovery, live Doctor, and
post-switch acceptance not evaluated. The active readiness loader reads only
`state/config/deployment-readiness.json` with contract
`deployment-readiness-v1`; runtime, Settings, Doctor, and provider admission
never inspect the pending directory. The crash-journaled updater must be given
one explicit pending artifact ID. It revalidates that artifact before backup
and again before selection, creates and rehearses its own exact fresh artifact,
proves the retained predecessor, and derives active evidence only inside its
selection/start transaction after the candidate pointer is durable and before
candidate service start.

## Gate 5 implementation status

The central runtime foundation remains unchanged. Gate 5 adds a real installer
and operations layer without another central application process. Section 1
fails before instance mutation; completed section versions/input digests are
idempotent; interrupted sections resume from encrypted state. Doctor checks
exact pins, architecture, loopback ports, SQLCipher/migrations, fifteen
projections, adapter categories, launchd, mappings, observation, disk/backup,
the required `llm-task` enabled/allowed/runtime-registered state, and the
canonical production-composition proof. AdministrateMe-plugin discovery alone
is not language-seam health.

Backup authenticates every staged file and proves affinity-safe,
fixed-watermark replay plus genuine workbook/sidecar regeneration before
replacement. The separate non-replacing rehearsal uses one guardian around
distinct extraction and replay workers and never targets active state. The
guardian and each active worker retain a shared cleanup-visible activity lease
during work; after worker terminality the guardian must acquire the exclusive
form before deleting plaintext, and stale cleanup likewise requires exclusive.
On success it emits a content-free receipt only after verified
plaintext-payload and control-stage deletion. On a supervised timeout or
controller disconnect the guardian reaps the worker and removes the plaintext
payload; a later rehearsal removes validated inactive control residue, with
payload removed before ownership markers.

Before staging plaintext, rehearsal also binds the active pointer to its
retained source identity. An exact mode-0600 source marker is sufficient; a
markerless retained release using the legacy compatibility path is accepted
only when every tracked regular file and executable bit matches the pointer
commit retained by the clean candidate repository and all other
source-adjacent entries fit one explicit generated-runtime policy. A malformed
or mismatching present marker never falls back. This source-tree result does
not validate the excluded virtual environment, Node dependencies,
native-plugin build output, full release immutability, rollback compatibility,
or automatic rollback eligibility.

New updater releases are materialized from the fixed Git commit into private
staging, verified, marked before publication, and atomically renamed; ignored
worktree files cannot become marker-attested source. An unprovably reaped
materializer leaves its private stage and a `publishing_unreaped` record in
place without cleanup, retry, backup, or activation authority. Before staging,
the updater durably records candidate, controller, instance, pointer-chain,
nonce, path, and generated-policy ownership in a parent-side `publishing`
record whose preparation-activity coordinate is explicitly unbound. After
atomic source publication, and before any generated-root cleanup or runtime
preparation, it creates the private preparation-activity inode, acquires it
exclusively, binds that exact coordinate in the record, and transitions to
`preparing`. The controller retains exclusive ownership through cleanup,
downgrades to a shared cleanup-visible lease while every active preparation
worker holds its own shared lease, and reacquires exclusive ownership after
worker terminality for completion. Controller disconnect, command timeout,
activity-coordinate replacement, or an unreaped command fails closed without
granting cleanup authority.

Runtime preparation occurs only at that stable, non-selected final path, only
while the record remains pending, and only after deleting all allowlisted
generated roots without following links. Retry revalidates the unchanged
source, marker, pointer chain, and release coordinate, then rebuilds those
roots from scratch and verifies exact Python, Node, and Poetry pins. Every
active or recursively retained predecessor is read-only. A cooperative
instance operation lock covers the supported fixed-commit activate launcher
through controller cleanup, while direct rollback acquires the same canonical
manager lock. The preparation record is consumed immediately before the first
selector mutation and cannot authorize preparation again. The marker,
preparation record, and activity lease attest source/preparation mechanics,
not full immutability, rollback eligibility, or deployment readiness. The
preliminary receipt is backup/replay evidence, not full live Doctor,
rollback-eligibility, or activation evidence. Live provider success is never
inferred from fixture state.

Activation-transaction amendment: the cooperative lock is still not crash
recovery by itself, but the updater now adds a separate mode-0600 active
transaction journal before backup quiescence. It binds exact predecessor
pointer/readiness bytes and running state, exact pending HTR-08 identity, fresh
backup/rehearsal hashes, and the mechanical rollback proof; records intent
before every active selector/service mutation; promotes active readiness only
after pointer selection and before service start; and recovers the predecessor
on failure or the next guarded invocation after controller death. Explicit
rollback requires committed activation history and is journaled by the same
rule. This establishes activation-bound HTR-05/HTR-09 mechanics only when an
owner-created pending artifact validates. It does not execute human HTR rows,
authorize deployment, promote fixture evidence, or complete post-switch
privacy/browser/effect acceptance.
