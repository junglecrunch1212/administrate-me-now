# Mac mini bootstrap and operations

This is the non-programmer operator contract for the dedicated Apple-silicon
Mac mini. Gate 5 builds and tests this package; it does **not** authorize use on
the future family Mac. Phase A uses a disposable Mac and fake identities.

## Supported host and fixed interfaces

- Apple-silicon `arm64`; Rosetta execution is rejected.
- macOS 14, 15, or 26 on officially supported hardware; macOS 26 is preferred
  for a new Mac mini.
- A dedicated non-administrator-at-runtime account with FileVault enabled.
- Python `>=3.11,<3.12`, Node `>=22.22.3,<23`, SQLCipher `>=4.12,<5`
  through the locked `sqlcipher3==0.6.2` Apple-silicon wheel,
  Homebrew `>=6,<7`, Poetry `2.2.1`, and npm `>=10.9,<11`.
- OpenClaw `2026.7.2`, plugin API `2026.7.2`, and source commit
  `944cf08f94c334cff2b0fdf7e01187a9efa42715` exactly. Because that version is
  unpublished on npm, the installer verifies its GitHub archive SHA-256 and
  builds with the source-declared `pnpm@11.15.1`. Bun is not required.

The machine-readable source is `bootstrap/versions.json`. These names are the
stable central/operator surface:

```text
bootstrap/bootstrap-macos.sh
bootstrap/install.sh
bootstrap/doctor.sh
bootstrap/start.sh
bootstrap/stop.sh
bootstrap/status.sh
bootstrap/backup.sh
bootstrap/restore.sh
bootstrap/activate-openclaw.sh
bootstrap/bridge-status.sh
bootstrap/bridge-export.sh
bootstrap/bridge-finalize.sh
bootstrap/bridge-revoke.sh
bootstrap/install-member-bridge.sh
bootstrap/member-bridge.sh
```

`install.sh` is the compatibility name and delegates to
`bootstrap-macos.sh`. `update.sh` is an additional guarded upgrade helper, not
a replacement for any fixed name.

## Clean credential-free rehearsal

From a reviewed checkout on the disposable Mac, run exactly:

```bash
./bootstrap/install.sh --fixture two-adult-one-child --instance-root "$HOME/Library/Application Support/AdministrateMe Lab" --expected-commit "$APPROVED_SHA"
```

This uses only fake `.invalid` identities and fake-provider contracts. It
creates an encrypted isolated lab instance, starts the real user-level service
composition around credential-free boundaries in observation mode, and exits
nonzero unless Doctor proves the canonical
message-to-review-to-dashboard/workbook-to-completion-to-what-now path. It
does not prove Gmail, Apple, Plaid, Home Assistant, Tailscale, or any other
live provider.

The `--dry-run` flag is test-harness-only and production bootstrap refuses it;
it previously implied a safety property the macOS path did not provide. For the
later reviewed Phase-B install, omit `--fixture` but keep `--expected-commit`.
The terminal wizard asks for household structure and verified Tailscale login
names. An exact OpenClaw channel/account/actor mapping may be recorded; it is
safe to leave channels unmapped while a principal uses private portal chat,
and an unmapped actor has no household authority.
It explicitly tells the operator never to paste passwords, tokens, or OAuth
codes. A local non-secret answer document based on
`bootstrap/answers.example.json` may instead be supplied with `--answers`.

## What the installer does

Preflight is read-only and occurs before instance mutation. It verifies the
OS, native architecture, FileVault, dedicated non-root account, disk space,
Command Line Tools, Homebrew, and required commands. A missing dependency or
unsupported architecture produces a specific remediation and no partial
instance.

After preflight it installs only the two required Homebrew formulas when
needed, creates a release-specific Python environment from `poetry.lock`,
downloads only the exact reviewed OpenClaw source archive, verifies its pinned
SHA-256/package manager/version, builds its UI and CLI, writes an instance-local
executable and provenance receipt, builds and checks the native plugin, creates
isolated app/state/log/backup directories, and runs all ten wizard sections.
The answers and section journal are AES-GCM encrypted. The encryption,
transport, backup, and enrollment bootstrap values live in macOS Keychain;
configuration stores references only.

The installer writes two user LaunchAgents:

- `com.administrateme.openclaw`
- `com.administrateme.runtime`

Neither runs as root. Their argument lists contain no secret. The launcher
retrieves a required value from Keychain into process memory/environment and
never prints it. OpenClaw binds `127.0.0.1:18789`; the one AdministrateMe
process binds `127.0.0.1:3330`. Tailscale Serve remains the sole future edge.

The selected household IANA timezone drives the five OpenClaw standing orders.
The reconciler owns only `administrateme-*` names, rejects declaration drift or
duplicates, leaves foreign jobs untouched, verifies the final state, and rolls
back partial changes. Selected member bridges remain an explicit action-required
state until their 30-minute encrypted package is accepted on the matching
Tailscale member Mac and finalized from a real owner-scoped event, or revoked.

## macOS permissions and OAuth

Permission prompts are per integration and should appear only after the
operator selects that integration:

- Calendar: permits that member bridge to read/write the selected calendars.
- Reminders: permits only the selected member's reminders and shared lists.
- Contacts: permits contact synchronization selected during enrollment.
- Notes: Apple has no general server API; the bridge uses the documented local
  mechanism and member ownership rules.
- Microphone/Voice Memos: required only for the optional Voice Memos bridge.

OAuth opens the provider's own page. Confirm the expected provider and least
privilege before granting access. OpenClaw owns assistant-provider credentials.
Plaid is sandbox-only during bootstrap. Home Assistant and optional providers
remain disabled until explicitly selected. A skipped noncritical subsection
becomes a Triage follow-up; it never becomes false completion.

## Daily operations

```bash
./bootstrap/status.sh
./bootstrap/start.sh
./bootstrap/doctor.sh
./bootstrap/stop.sh
```

The status vocabulary is strict: `healthy`, `degraded`, `stopped`, or `failed`
for the deployment, with each process separately reported as `running`,
`observation_only`, `degraded`, `stopped`, or `failed`. Repeated start and stop
are safe. Launchd restarts a crashed process with a throttle; persistent crash
loops surface as failed/degraded rather than green. Every stable wrapper reads
the validated active-release pointer and invokes that release's isolated
Python environment; it does not depend on an IDE or a developer checkout venv.

For a bug report, create a secret-redacted machine report:

```bash
./bootstrap/doctor.sh --export "$HOME/Desktop/administrateme-diagnostic.json"
```

The export replaces the instance root, includes no credential value, and is
marked `safe_to_attach`. Review it anyway before sharing because capability
names and timestamps are operational metadata.

## Backup and restore

Create an encrypted, versioned backup:

```bash
./bootstrap/backup.sh
```

The command briefly quiesces the two user services, hashes the allowlisted
state tree, encrypts the archive with the Keychain backup key, then restores
the prior service state. It includes the event log/checkpoints, projections,
configuration metadata and secret references, OpenClaw workspace metadata,
member/bridge mappings, pack manifests, and workbook/sidecar state. It never
includes the Keychain values themselves.

Restore only a file inside the configured backup directory:

```bash
./bootstrap/restore.sh "$HOME/Library/Application Support/AdministrateMe/backups/administrateme-YYYYMMDDTHHMMSSZ.ambackup"
```

Restore decrypts and authenticates into a staging directory, rejects links and
path traversal, verifies every hash, opens the SQLCipher log, rebuilds all 14
registered projections (13 domain projections plus Triage) and workbooks from
the event log, then and only then replaces active state. The previous state
moves to the `rollback` directory. If post-restore Doctor fails, the previous
state is automatically put back.

## Upgrade and rollback

Never run a floating OpenClaw upgrade. Prepare and review a candidate checkout,
then run:

```bash
./bootstrap/update.sh /absolute/path/to/reviewed-candidate
```

Compatibility validation happens before backup, service stop, release copy,
or pointer replacement. A different OpenClaw version, commit, or plugin API is
refused. A compatible candidate receives an isolated Python environment and
native-plugin build before service replacement, then a pre-upgrade encrypted
backup, atomic release pointer switch, launchd reconciliation, restart, and
Doctor. Failure restores both the previous pointer and its launchd
definitions. The lower-level rollback command is:

```bash
python -m bootstrap.cli rollback
```

Do not remove the prior release or rollback state until the full trust period
and a fresh backup/restore rehearsal have passed.

## Observation and emergency stop

Observation starts on. It records complete would-have-happened evidence but
performs no external side effect. Trust cannot be declared until the Section 9
briefing exists, five full days have elapsed (normally review at seven), the
suppression history is reviewed, identity/privacy findings are resolved,
connector watermarks and backup are healthy, Doctor passes, and a principal
explicitly confirms. Time alone never disables observation.

For an emergency, run `./bootstrap/stop.sh`. This unloads only the two named
user agents and does not delete data, backups, Keychain items, or bridge
enrollments.

## Live-Mac rehearsal checklist

CI cannot grant real macOS permissions or provider consent. Before the family
Mac is touched, use a disposable supported Apple-silicon Mac and record:

1. Clean dedicated account, FileVault, 10 GiB free, current system time, and a
   tested external backup target.
2. `macos-bootstrap-rehearsal.sh` passes, including every interruption/resume
   boundary, idempotent rerun, plist lint, secret scan, and backup/restore.
3. The exact OpenClaw source checkout passes `npm run test:live:mac` with
   `ADMINME_LIVE_GATE=1` and `OPENCLAW_SOURCE_DIR` set for that one command.
4. Start, stop, restart, forced crash, login restart, and network outage each
   produce the documented status and recovery.
5. One adult bridge and one kid bridge use separate fake Apple accounts and
   Tailscale identities; cross-owner claims and kid Obsidian access fail.
6. Calendar, Reminders, Contacts, Notes, and microphone prompts are explained
   and denied once to prove graceful degraded states before granting them.
7. Plaid remains sandbox. Gmail/Home Assistant/other providers use disposable
   accounts only. No live-family-success claim is recorded.
8. Diagnostic export and all logs pass the secret scan; launchd arguments and
   GitHub artifacts contain no credentials.
9. Observation remains active, the briefing and five-to-seven-day workflow are
   visible, and the synthetic attempted action is suppressed.
10. Sign the bootstrap report and retain its Git SHA, versions, hashes, test
    outputs, and explicit human grants outside the repository.

Official platform references reviewed for this matrix: Apple’s macOS 26 Mac
compatibility list, Apple’s Apple-silicon identification guide, Homebrew’s
installation requirements, and the Node.js release schedule. Exact URLs are
recorded in the Gate 5 evidence.
