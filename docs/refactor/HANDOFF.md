---
schema_version: 1
current_phase: 8
phase_name: deployment_candidate_remediated
gate: 8
gate_result: LOCAL_RELEASE_MATRIX_PASS_AWAITING_INDEPENDENT_REVIEW_AND_MACOS_CI
verified_local_date: 2026-08-04
branch: codex/administrateme-v2-runtime
candidate_commit_resolution: git rev-parse HEAD
pull_request_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
openclaw_archive_sha256: a0f1800a64ef495062da6c65e0e9a2b9a6ca0e51c3bc6197918db4eddc7292b8
next_phase: independent_review_merge_disposable_mac_gate
---

# Release handoff

## Outcome

The repository is a complete deployment candidate for independent review. The
two bootstrap gaps that reopened Gate 8 are implemented: AdministrateMe now
reconciles its five pinned OpenClaw schedules, and the central/member bridge
enrollment lifecycle has supported export, acceptance, proof, finalization,
status, disable, and revocation commands. The audit also found that OpenClaw
`2026.7.2` is absent from npm; bootstrap now builds only the exact reviewed
GitHub commit from a pinned archive checksum and records source provenance.

This handoff does not authorize installation on the family Mac. PR #248 still
requires independent review, green checks at its final head, merge, and a
disposable Apple-silicon live rehearsal. No household data, provider
credential, family identity, or family Mac was touched by this remediation.

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

## Required live and independent gates

The following cannot be honestly completed on the current Windows workstation:

1. independent review and approval of PR #248, followed by green GitHub checks
   for the exact final head and merge without bypassing protection;
2. the GitHub `macos-15` Apple-silicon job, including shellcheck, native
   OpenClaw source build, launchd plist validation, clean bootstrap, recovery,
   and the whole release-candidate suite;
3. a disposable Apple-silicon Mac rehearsal of real launchd startup, reboot,
   Tailscale Serve identity headers, OpenClaw Gateway RPC, cron list state, and
   one non-household bridge enrollment/revocation;
4. real model/channel sign-in, minimal Apple/provider grants, Safari with
   VoiceOver, encrypted off-machine restore, and the five-to-seven-day
   observation soak.

Treat any failed item as a deployment stop, not as permission to weaken a pin,
disable identity checks, hand-edit encrypted state, copy a secret into a file,
or use a family Mac as the experiment.

## Operator path

After merge, the only first-install guide is
`docs/operations/MAC-MINI-SETUP-READ-ALONG.md`. The concise pointer is
`docs/operations/FIRST-MAC-BOOTSTRAP.md`; command detail is also mirrored in
`docs/refactor/MAC-MINI-OPERATIONS.md`. `KNOWN-LIMITATIONS.md` remains the
authoritative statement of live-only and deferred behavior.

No automated merge or family deployment is authorized or performed.
