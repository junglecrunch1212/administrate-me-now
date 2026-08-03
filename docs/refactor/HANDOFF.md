---
schema_version: 1
current_phase: 5
phase_name: mac_mini_bootstrap_and_operations
gate: 5
gate_result: PASS
verified_local_date: 2026-08-03
branch: codex/administrateme-v2-runtime
branch_tip_resolution: git rev-parse HEAD
draft_pr_url: https://github.com/junglecrunch1212/administrate-me-now/pull/248
openclaw_version: 2026.7.2
openclaw_commit: 944cf08f94c334cff2b0fdf7e01187a9efa42715
evidence_level: credential_free_process_and_reproducible_macos_contract
next_phase: 6
next_phase_name: member_bridge_private_searchable_knowledge
---

# Handoff

## State

Gate 5 passes at installer-readiness level. Gates 1–4 remain committed and
unchanged. The branch now contains the Mac mini package that can preflight,
install, configure, start, stop, diagnose, back up, restore, and guard updates
for the exactly pinned OpenClaw plus one AdministrateMe process. It has not
been installed on the future family Mac.

The stable operator names are `bootstrap/bootstrap-macos.sh`, `install.sh`,
`doctor.sh`, `start.sh`, `stop.sh`, `status.sh`, `backup.sh`, and `restore.sh`.
All are Bash-syntax and ShellCheck clean; `install.sh` delegates to the
constitutional compatibility entrypoint.

## Gate 5 implementation

- `bootstrap/versions.json` pins the supported macOS majors, Apple-silicon
  architecture, Python, Node/npm, SQLCipher binding/runtime, Homebrew, Poetry,
  exact OpenClaw version/commit/API, ports, and the fact that Bun is unused.
- Shell preflight rejects root, Rosetta/x86_64, unsupported macOS, FileVault
  off, missing Command Line Tools/Homebrew, unsafe roots, and low disk before
  instance mutation. Required formulas and a release-specific Python
  environment are installed only after preflight and from exact lockfiles.
- `bootstrap/wizard.py` implements all ten constitutional sections with
  version/input-digest idempotency and independent encrypted status. A failed
  section records failure and resumes; Section 1 failure creates no instance.
  The operator may use the fake fixture, a local non-secret answer document,
  or the terminal questionnaire.
- macOS Keychain stores bootstrap/event/backup/OpenClaw/bridge material. The
  journal is AES-GCM encrypted. Plists, config, reports, arguments, logs, and
  diagnostic exports contain references only. The test-file provider is
  guarded by `ADMINME_TEST_MODE=1` and never used in production.
- Two user LaunchAgents call Python launchers; neither service runs as root.
  The runtime is one Python process at loopback `:3330`; OpenClaw is exact-pinned
  at loopback `:18789`. Start/stop/status are idempotent and distinguish
  healthy/degraded/stopped/failed plus per-process state. The runtime resolves
  the shared transport credential from Keychain and authorizes only explicit
  OpenClaw channel/account/actor-to-member mappings.
- Section 10 creates one encrypted package per member with Tailscale identity,
  owner scope, central stamping, and explicit cross-owner denial. Child
  packages prohibit Obsidian and retain kid restrictions. A non-Apple
  household explicitly skips the section.
- Doctor validates platform, configuration, secure references, SQLCipher and
  migrations, all 13 projection stores/checkpoints, five adapter categories,
  plugin compatibility, launchd/process state, loopback ports, member/bridge
  mapping, observation workflow, disk/backup, and the real isolated canonical
  message/review/dashboard/workbook/completion/what-now/rebuild round trip.
  The export is machine-readable and secret-scanned.
- Backup quiesces services, hashes an allowlisted state tree, and encrypts the
  versioned archive. Restore authenticates in staging, rejects unsafe paths,
  verifies all hashes, opens the event log, rebuilds all 13 projections and
  workbooks, preserves a rollback copy, then replaces active state. A failed
  post-restore Doctor automatically restores the prior state.
- Upgrade validation compares the candidate's OpenClaw version, commit, API,
  plugin package, and lockfile before backup or service replacement. A
  compatible switch builds its isolated environment first, then uses a backup,
  rollback pointer, and transactional launchd reconciliation; floating
  OpenClaw is prohibited.

## Evidence and limits

The local process evidence is recorded in
`docs/refactor/evidence/gate-5-mac-mini-operations.md`. The macOS contract is
`.github/workflows/macos-bootstrap.yml`, with reproducible scripts under
`scripts/acceptance/`. The live disposable-Mac checklist is in
`MAC-MINI-OPERATIONS.md`.

No Gmail, Apple, Plaid, Home Assistant, member bridge, OAuth, Tailscale grant,
or other live-provider success is claimed. The fake Plaid state is sandbox;
the canonical action is observation-suppressed and performs zero external
side effects. The Windows host cannot prove FileVault, real Keychain UI,
actual user launchd recovery, Apple permissions, or a live pinned Gateway.

Before any family-Mac deployment, a maintainer must review the draft PR and
run the documented checklist on a disposable supported Apple-silicon Mac,
including `npm --prefix openclaw/administrateme run test:live:mac` with the
exact source checkout and explicit lab guard. That is a genuine required human
action, not a hidden Gate 5 fixture claim.

## Exact credential-free command

```bash
./bootstrap/install.sh --dry-run --fixture two-adult-one-child --instance-root "$HOME/Library/Application Support/AdministrateMe Lab"
```

## Exact next phase

**Phase 6 is only the member-owned bridge-to-private-searchable-knowledge
slice.**

Make `tests/acceptance/test_member_bridge_knowledge_vertical_slice.py` pass
without weakening it: enrolled member identity, central owner stamping,
private `member_knowledge`, permitted non-privileged vector representation,
member/child/ambient/cross-owner denial, correction history, replay
equivalence, and two-instance isolation.

Do not reopen Gate 5 paths, replace fixed script names, add a central process,
activate live providers, redesign the console, or broaden OpenClaw conversation
mirroring unless Phase 6 exposes a concrete prerequisite defect.
