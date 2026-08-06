---
schema_version: 1
current_phase: 8
phase_name: production_activation
gate: 8
gate_result: CODE_REVIEWED_MERGED_MAIN_AWAITING_LIVE_MAC_ACTIVATION
verified_local_date: 2026-08-05
verified_github_date: 2026-08-05
branch: main
candidate_commit_resolution: git rev-parse HEAD
pull_request_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
openclaw_archive_sha256: a0f1800a64ef495062da6c65e0e9a2b9a6ca0e51c3bc6197918db4eddc7292b8
next_phase: central_observation_then_live_provider_activation
---

# Release handoff

## 2026-08-06 Triage surface and adjudication correction

This amendment **MODERNIZES** the former Inbox into the canonical Triage
surface under D26. Triage is a private, deterministic projection containing
only unresolved extracted/derived candidates; already-official tasks stay on
Today and Scoreboard. Existing commitment proposals and OpenClaw household
action proposals are aggregated during migration, while new generic
`triage.item.*` events cover task, commitment, calendar-event, reminder, list,
list-item, and CRM identifier-link targets.

Member confirmation passes through a closed typed materializer. Local targets
emit canonical events only after confirmation. External targets require an
explicit household member, provider account, and destination container before
a governed `external_sync.write_requested` event can be emitted; the provider
observation remains the fact that updates calendar/list projections. The Apple
Reminders adapter no longer consumes unconfirmed `commitment.proposed` events.
Legacy Inbox HTTP/OpenClaw bindings remain deprecated aliases but are absent
from new navigation. `tests/acceptance/test_triage_vertical_slice.py` proves
the production composition from proposal through human confirmation to one
official task.

Local release evidence on 2026-08-06: the complete root suite passed with
1,324 tests; strict acceptance passed with 22 tests under `--runxfail`; Ruff
and mypy passed across 189 production/bootstrap source files; both invariant scripts
passed; and the exact pinned OpenClaw contract, TypeScript check, 14 plugin
tests, and build passed. The Triage process proof also covers idempotent
enrichment/confirmation/dismissal, private-scope isolation, CRM visibility,
Apple Calendar routing, Google Tasks routing, replay, and the rule that an
external write command cannot fabricate a provider observation.

## 2026-08-06 personal Google OAuth activation repair

The live Mac activation proved that the Google packs' service-account-only
credential resolver could not enroll a personal Gmail account. This amendment
adds a shared secret-reference resolver for existing 1Password references and
instance-bound macOS Keychain references, installed-user OAuth factories for
Gmail and Google Tasks (matching Calendar), and a local consent helper that
stores only refresh credentials in Keychain. Gmail remains read-only; Calendar
and Tasks remain governed by observation mode and the external-write seams.
Pack-level regression coverage passes with 29 tests and Ruff is clean.

## 2026-08-06 live release-switch hardening

The first guarded Triage activation attempt rolled back automatically when
launchd retained the previous immutable release definition during a reload
race. The repair waits for the old job to disappear, then verifies the loaded
program, working directory, and repository-root environment against the new
plist before health can pass. Stable wrappers also change into the validated
active release before importing bootstrap code, and LaunchAgents now provide
the exact state directory through `ADMINME_INSTANCE_DIR`, preventing live
model-skill pipelines from degrading on an unresolved instance path. The
post-switch gate tolerates only bounded projection/Gateway/plugin/cron/listener
startup transients before requiring the complete Doctor and synthetic round
trip; structural and security failures still roll back immediately. These
changes **MODERNIZE** service switching while **PRESERVING** rollback, release
immutability, secret-free argument lists, and fail-closed readiness.

## 2026-08-05 live activation hardening

The production Mac rehearsal additionally proved and repaired three operational
edges: the managed Gateway RPC probe now receives its Keychain-backed token
without persisting it, launchd startup tolerates the documented RunAtLoad versus
kickstart race only when the job is verifiably loaded, and encrypted backup
omits only OpenClaw's generated `plugin-skills` links while continuing to reject
all other links. Quiesced backup also tolerates SQLite removing only disposable
`-wal`/`-shm` sidecars between enumeration and hashing; durable files remain
fail-closed. Launchd now proves the central runtime ready before starting the
Gateway so the local plugin can register against its backend. The complete root
suite passes with 1,314 tests.

## 2026-08-05 Mac bootstrap compatibility repair

The live Apple-silicon rehearsal exposed five release-blocking compatibility
defects and this reviewed amendment fixes them. Bootstrap now requires the
current Homebrew 6 metadata consumer, keeps pinned Poetry in an instance-local
tooling environment outside the synchronized application virtualenv, permits
only the installer-owned `app`, `bin`, and `state` scaffold at first wizard
entry, and explicitly acknowledges the reviewed local OpenClaw plugin path.
The plugin now registers its eleven declared dashboard methods during the
pinned runtime's read-only discovery mode and its nine tools during tool
discovery, without starting hooks or services outside full activation.
Every OpenClaw CLI, cron, activation, and launchd path is also bound to the
instance-local OpenClaw state/config directory so lab and production cannot
select each other's plugin paths. Bootstrap explicitly writes and validates
`gateway.mode=local` before launchd starts the pinned Gateway.
Optional unconfigured member chat remains explicitly blocked in capability
health but no longer blocks the deterministic runtime's global readiness.
Resumed installs reload existing user LaunchAgents before kickstart; the later
2026-08-06 hardening above adds the required unload wait and exact loaded-job
verification.

This **MODERNIZES** installer/package compatibility while **PRESERVING** the
reviewed-source boundary, fail-closed first-run behavior, one pinned OpenClaw
runtime, OpenClaw cron ownership, and AdministrateMe domain authority. Evidence
is recorded in `docs/refactor/evidence/mac-bootstrap-compatibility-2026-08-05.md`.

## 2026-08-05 release disposition

The operator explicitly authorized final review, amendment, green-check merge,
and installation preparation. The reviewed release adds the bidirectional
primary surfaces: central Google Calendar plus per-member Apple Calendar,
Reminders, and Notes. It also fixes production composition so
`config/adapters.json` reaches the central adapter supervisor after service
restart. Calendar and reminder sync also now requires a durable central event
ID before local cursor/state advancement, so an ingest outage retries instead
of silently losing the provider change. The exact merged `main` SHA and final
GitHub check result are supplied with the operator handoff; the Mac must refuse
any other revision.

The initial CoS Mac Mini installation remains credential-free and starts in
observation mode. Provider credentials and Apple grants are deliberately not
bootstrap inputs. The central service is installed first; each member's Apple
apps require a separate per-member bridge running under a macOS login signed
into that member's iCloud account. Google-backed calendars remain owned by the
central Google adapter and must not be duplicated through EventKit.

## Outcome

The repository is the reviewed production-activation candidate. The
two bootstrap gaps that reopened Gate 8 are implemented: AdministrateMe now
reconciles its five pinned OpenClaw schedules, and the central/member bridge
enrollment lifecycle has supported export, acceptance, proof, finalization,
status, disable, and revocation commands. The audit also found that OpenClaw
`2026.7.2` is absent from npm; bootstrap now builds only the exact reviewed
GitHub commit from a pinned archive checksum and records source provenance.

Installation is authorized only at the exact merged SHA stated in the final
operator handoff and only after its GitHub checks are green. The Mac install is
itself the live Apple-silicon rehearsal: stop on any failed release boundary,
Doctor, backup/restore, Tailscale identity, service, or isolation check. No
household data, provider credential, family identity, or family Mac was touched
while preparing this release.

## What is ready

- The central installer refuses root, Rosetta, unsupported macOS, FileVault
  off, low disk, a dirty checkout, a non-matching reviewed SHA, and production
  `--dry-run` before household mutation.
- Release extraction is atomic. Interrupted Python installs have commit
  receipts and safely retry instead of trusting a partial directory.
- OpenClaw source, package-manager lock, version, executable, and receipt are
  pinned and Doctor-checked. There is no npm, beta, or branch fallback.
- The wizard records an explicit IANA household timezone, resumes only its
  encrypted original answers, writes truthful seed follow-ups, requires the
  observation briefing, and distinguishes expected bridge follow-up from a
  fault.
- Cron reconciliation cross-checks both declarations, owns only
  `administrateme-*`, rejects duplicates, adds/edits/removes recorded stale
  jobs, leaves foreign jobs untouched, verifies the resulting state, and
  attempts rollback before reporting failure.
- Member bridge transfers are AES-GCM encrypted, expire after 30 minutes, bind
  member/role/private scope/Tailscale identity/release, keep the one-time code
  separate, and rotate Keychain material transactionally.
- Member installation verifies Apple silicon, FileVault, disk, Tailscale self
  identity, a clean exact-SHA checkout, child/pack restrictions, and an
  immutable atomic release. It rolls back a failed install and consumes the
  transfer file only after the owner-scoped enrollment proof succeeds.
- Central finalization requires the encrypted event-log proof. Revocation is
  independent of portal identity and leaves the central runtime fail closed
  if a safe restart cannot be proven.
- Stable central and member helpers resolve their archived release rather than
  depending on the Terminal's current folder. Tailscale login comparison is
  case-insensitive while duplicate household identities remain forbidden.
- The parallel operator guide includes GitHub acquisition, account/FileVault,
  Homebrew, Tailscale CLI and Serve, lab rehearsal, all wizard answers,
  OpenClaw activation, portal checks, bridge enrollment, backup/restore,
  reboot, observation, and symptom-based troubleshooting.

## Local release evidence

The 2026-08-05 primary-surface amendment passed the current release matrix on
the Windows review host before push:

- strict acceptance: 21 passed with `--runxfail`;
- root and pack suite: 1,455 passed; bridge distribution: 60 passed;
- focused primary-surface/bootstrap regression: 25 passed; three explicit
  central/bridge ingest-failure tests also passed;
- Ruff clean and 455 files formatted; mypy clean across 199 production source
  files; production Pyright: zero errors and zero warnings;
- root and bridge dependency audits found no known vulnerabilities; both lock
  checks and wheel/sdist builds passed;
- architecture-negative, credential-free clean-install, two-instance tenant
  isolation, and encrypted backup/restore process rehearsals passed;
- pinned OpenClaw contract, TypeScript, 12 tests, build, package dry run, and
  production dependency audit passed;
- performance smoke passed all budgets: 11.46-second cold start, 128.26 ms
  append/projection p95, 352.81 ms temporary-view p95, 26.53 ms member-chat
  fixture p95, 5.77 MiB peak traced memory, and 2.38 MB encrypted storage after
  100 task events;
- `git diff --check` passed. Unix `shellcheck` remains intentionally delegated
  to the final Apple-silicon GitHub job because it is not installed on the
  Windows review host.

The final PR-head Apple-silicon check is a merge prerequisite and is reported
with the exact merged SHA in the operator handoff.

All commands below passed on 2026-08-04 from the complete candidate tree:

- acceptance contract: 21 passed with `--runxfail`;
- root and pack collection: 1,446 passed, zero failures;
- bridge distribution: 60 passed; Ruff and mypy clean; wheel/sdist built;
- OpenClaw plugin: exact contract, TypeScript check, 12 tests, build, package
  dry run, and production audit passed;
- rendered portal: 78 checks and 18 screenshots, with zero console, page, or
  request failures;
- Ruff and format: 450 files clean; mypy: 195 source files clean; Pyright: zero
  errors and zero warnings;
- root and bridge lock checks/builds passed; both installed dependency audits
  report no known vulnerabilities (the two local unpublished project names
  are correctly skipped by PyPI lookup);
- architecture-negative, clean-install, two-instance tenant isolation, and
  encrypted backup/mutate/restore rehearsals passed;
- performance smoke passed every budget: 4.8-second cold start, 95.91 ms
  append/projection p95, 96.95 ms temporary-view p95, 18.34 ms member-chat
  fixture p95, 4.98 MiB peak traced memory, and 2.38 MB encrypted storage after
  100 task events;
- every new shell entrypoint passes Bash syntax, and `git diff --check` passes.

The only recurring test warning is Starlette's upstream TestClient/httpx
deprecation. Poetry also reports metadata-migration warnings. Neither affects
the locked runtime or the passing builds.

GitHub run 30964802418 also passed on the maintained Apple-silicon `macos-15`
runner. It built and verified the exact OpenClaw source, exercised the native
plugin contract, performed the credential-free bootstrap/Doctor/recovery
rehearsal, checked the static and secret-safe tree, and ran the whole release
candidate suite. The later receipt-path patch passed in that same run under
macOS's canonical `/private/var` temporary path.

## Remaining live activation gates

The following must be completed from the operator read-along on Apple hardware:

1. an Apple-silicon Mac rehearsal of real launchd startup, reboot,
   Tailscale Serve identity headers, OpenClaw Gateway RPC, cron list state, and
   one bridge enrollment/revocation before enabling provider writes;
2. real model/channel sign-in, minimal Apple/provider grants, Safari with
   VoiceOver, encrypted off-machine restore, and the five-to-seven-day
   observation soak;
3. member-by-member OAuth/EventKit/Notes consent and propagation checks, with
   calendar attendance/change and reminder completion verified as feedback.

Treat any failed item as a deployment stop, not as permission to weaken a pin,
disable identity checks, hand-edit encrypted state, copy a secret into a file,
or use a family Mac as the experiment.

## Operator path

The only first-install guide is
`docs/operations/MAC-MINI-SETUP-READ-ALONG.md`. The concise pointer is
`docs/operations/FIRST-MAC-BOOTSTRAP.md`; command detail is also mirrored in
`docs/refactor/MAC-MINI-OPERATIONS.md`. `KNOWN-LIMITATIONS.md` remains the
authoritative statement of live-only and deferred behavior.

Repository merge is authorized and completed only with green final-head checks.
No tool has remotely installed or activated a household Mac or provider.
