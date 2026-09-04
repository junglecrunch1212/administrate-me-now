# Mac mini bootstrap and operations

This is the non-programmer operator contract for the dedicated Apple-silicon
Mac mini. Gate 5 builds and tests this package; it does **not** authorize use on
the future family Mac. Phase A uses a disposable Mac and fake identities.

## Supported host and fixed interfaces

- Apple-silicon `arm64`; Rosetta execution is rejected.
- macOS 14, 15, or 26 on officially supported hardware; macOS 26 is preferred
  for a new Mac mini.
- A dedicated non-administrator-at-runtime account with FileVault enabled.
- Python `>=3.14,<3.15`, Node `>=24.15,<25`, SQLCipher `>=4.12,<5`
  through the locked `sqlcipher3==0.6.2` Apple-silicon wheel,
  Homebrew `>=6,<7`, Poetry `2.4.1`, and npm `>=11.6,<12`.
- OpenClaw `2026.7.2`, plugin API `2026.7.2`, and source commit
  `ce67ffb70e83e5ebb56ea02e814e577153f84f61` exactly. Because that version is
  unpublished on npm, the installer verifies its GitHub archive SHA-256 and
  builds with the source-declared `pnpm@11.15.1`. Bun is not required.

The machine-readable source is `bootstrap/versions.json`. These names are the
stable central/operator surface:

ADR-0006 governs migration from an existing Python 3.11/Node 22 release. The
new formulae are installed side by side and candidate checks do not relink
Node, rewrite LaunchAgents, move `current-release.json`, or restart the active
services. A live cutover is a later explicit operation using
`--approve-runtime-migration` after the complete rehearsal.

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
never prints it. The runtime receives the exact instance-state directory as
`ADMINME_INSTANCE_DIR`; model skills must not infer it from a developer
checkout. OpenClaw binds `127.0.0.1:18789`; the one AdministrateMe process
binds `127.0.0.1:3330`. Tailscale Serve remains the sole future edge.

The selected household IANA timezone drives the five OpenClaw standing orders.
The reconciler owns only `administrateme-*` names, rejects declaration drift or
duplicates, leaves foreign jobs untouched, verifies the final state, and rolls
back partial changes. Selected member bridges remain an explicit action-required
state until their 30-minute encrypted package is accepted on the matching
Tailscale member Mac and finalized from a real owner-scoped event, or revoked.
Finalization proves transport only and starts no provider pack. Seal provider
coordinates before export with `bridge-configure`; after finalization, the
member separately invokes `bridge-enable-pack` and reviews any macOS permission
prompt. Ordinary provider facts and commands remain blocked until the exact
`adapter.authenticated@v2` event is retained centrally and `bridge-attest`
promotes that pack. Never treat `active` transport as provider health.

## macOS permissions and OAuth

Permission prompts are per integration and should appear only after the
operator selects that integration:

- Calendar: permits that member bridge to read/write the selected calendars.
- Reminders: permits only the selected member's reminders and shared lists.
- Contacts: permits contact synchronization selected during enrollment.
- Notes: Apple has no general server API; the member bridge uses the documented
  local mechanism, retains exact account/folder/revision provenance, and must
  never infer AdministrateMe access from an Apple-native share. The Notes
  surface is self-only unless the owner records one explicit non-privileged
  adult grant. Evernote, Obsidian, Otter, Fathom, Zoom, and Notion are disabled
  until their own reviewed adapter and member consent are configured.
- Microphone/Voice Memos: required only for the optional Voice Memos bridge.

Contact activation is source- and member-specific. Apple Contacts runs only
on an enrolled member bridge after that member grants macOS Contacts access;
the central service account does not acquire a personal Apple ID. Google
People uses the exact enrolled Google account, Microsoft Graph uses delegated
mailbox scopes, and CardDAV uses one discovered address-book collection. Begin
with a read-only observation reconciliation and verify member, account,
container, record id, revision/etag, group, tombstone, and permission state.
An observed message or calendar endpoint is not a contact or Party. A provider
share is not an AdministrateMe grant. Do not activate contact create/update/
delete/group/photo operations until the exact operation has passed the normal
approval, privacy, revision, observation, and terminal-reconciliation review;
provider deletion always receives a separate confirmation.

Calendar, Reminders, and Contacts configuration uses content-free provider
account references distinct from member and enrollment IDs. It also seals the
personal workspace, exact container allowlist, least permission set,
`member-bridge-enrollment` provenance, and ownership attestation. Native Apple
sharing does not promote AdministrateMe visibility; only an explicit central
shared-container grant may do so. Contacts writes remain limited to the D23
`update_party_identifier` correction seam. A future central service-owned Apple
source is a different adapter/schema review; the planned CoS identity cannot
reuse these human-member packs.

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
Python environment from that release's working directory; it cannot import a
developer checkout merely because the operator invoked the wrapper there.
Upgrade/start waits for the previous launchd definition to unload and verifies
the newly loaded program, working directory, and repository-root environment
against the candidate plist before accepting health. Post-switch validation
starts the exact OpenClaw Gateway and proves its local health response before
starting the Python runtime, because reactive language replay begins during
runtime startup. The native plugin's authenticated AdministrateMe health
report therefore reconciles on bounded backoff until runtime readiness; a
single expected startup refusal cannot require a manual health nudge. It
may retry only the bounded projection/Gateway/plugin/cron/listener startup
checks; it then requires the complete Doctor, including the canonical
synthetic round trip. Every other failed check causes immediate rollback.
The same cutover removes only stale AdministrateMe plugin registrations, links
the plugin from the selected immutable release, and proves both its install
path and loaded runtime module resolve inside that release. While both services
are stopped, that selection also replaces only AdministrateMe-owned agents and
bindings with the exact member topology derived from the already-selected
adapter configuration, preserves unrelated OpenClaw rows, and validates the
result before restart. Crash recovery restores the journaled predecessor
adapter bytes before reconciling the predecessor plugin and derived topology;
rollback does the same in its target direction. Neither path invents an
account, member, agent, binding, or DM scope outside the reviewed configuration.
When a retained predecessor manifest predates the managed-topology fields,
recovery removes only candidate-owned AdministrateMe agent/binding rows and
those unsupported plugin fields before the predecessor installer performs its
own schema validation, then verifies the same condition after installation;
unrelated OpenClaw configuration and the global session policy are preserved.

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
member/bridge mappings, pack manifests, and workbook/sidecar state. Generated
OpenClaw plugin-discovery links, agent-local `codex-home/{tmp,.tmp}` launcher
cache, and `codex-home/logs_*.sqlite{,-wal,-shm}` launcher telemetry are
recreated by their owning runtimes and are not durable backup state. Durable
OpenClaw/Codex sessions, workspace state, and every household database remain
included; every other state-tree link remains a hard failure. The archive never
includes the Keychain values themselves.

Backup format v2 streams the tar archive through AES-256-GCM into a private
temporary ciphertext file and atomically publishes it only after the final
authentication tag and filesystem sync succeed. Source files are hashed during
inventory and again while streamed, so drift fails without publishing an
artifact. Restore authenticates the complete ciphertext before extraction and
removes any failed plaintext stage. The v2 path is not constrained by the
one-shot AEAD API's 2 GiB plaintext ceiling; the existing 8 GiB encrypted
artifact and 16 GiB unpacked-state safety bounds still apply. Restore retains
read compatibility with version 1 artifacts.

Before a candidate switch, run the non-replacing rehearsal from that exact
clean candidate's Python 3.14 environment. The candidate path must be the same
checkout that supplies `bootstrap.backup_restore`; do not run this command from
the active release and point it at unrelated code:

```bash
cd /absolute/path/to/clean-reviewed-candidate
ADMINME_REPOSITORY_ROOT="$PWD" poetry run python -m bootstrap.cli \
  --instance-root "$HOME/Library/Application Support/AdministrateMe" \
  rehearse-restore \
  "$HOME/Library/Application Support/AdministrateMe/backups/administrateme-YYYYMMDDTHHMMSSZ.ambackup" \
  "$PWD"
```

The rehearsal reads the two required keys from the instance's existing
Keychain boundary without printing them. One guardian and each active worker
retain shared cleanup-visible activity leases. One worker authenticates and
strictly extracts the exact manifest inventory, and a distinct worker opens
only the staged encrypted event log, rebuilds all 15 projections at a fixed
watermark, and regenerates both workbooks and all 17 sidecars. The guardian
reaps each worker, upgrades to the exclusive cleanup lease, and removes the
plaintext payload before reporting success. The controller reaps the guardian,
removes the validated control stage, and only then writes one new mode-0600,
content-free receipt under `backups/`. On controller disconnect or a supervised
timeout, the guardian reaps the active worker and removes payload; guardian
death leaves the worker lease in force, and a later run requires an exclusive
lease and removes payload before validated inactive control residue. The
rehearsal never constructs the service manager, replaces active state, changes
the release pointer or rollback tree, connects a provider, or appends an event.

Before creating plaintext staging, the rehearsal also validates the pointer's
retained source identity. The normal proof is a UID-owned, mode-0600 source
marker that exactly names the pointer commit. A markerless retained release
using the legacy compatibility path may instead compare every tracked regular
file and executable bit with the pointer commit
retained by this same clean candidate repository. It also rejects every
source-adjacent entry outside the explicit generated-runtime policy for the
release virtual environment, native-plugin dependencies/build output, and
narrow Python bytecode caches. A malformed, public, symlinked, or mismatching
present marker fails without fallback. The receipt records counts and digests,
not paths or contents. This establishes tracked-source identity only: excluded
runtime/build artifacts, full release immutability, rollback compatibility,
and automatic rollback eligibility remain `not_evaluated`. Never add a marker
to the installed predecessor to make this check pass.

The receipt's offline check is deliberately not full Doctor. It records live
checks and activation eligibility as `not_evaluated`/false, and it records
rollback compatibility and automatic rollback eligibility as `not_evaluated`.
It also reports projection divergence as `not_evaluated`; the logical digests
are content-free replay evidence, not a comparison with the backed-up derived
databases. The guarded updater must later create and rehearse its exact fresh
backup and bind predecessor compatibility after backup quiescence and before
any active candidate or OpenClaw selector, plugin registration, or
release-pointer mutation; full live Doctor is post-switch. Do not mark all of
HTR-05 or deployment readiness complete from the preliminary receipt alone.
During that post-switch proof, source-assurance replay may temporarily block
the derived `observation_effects` readiness dimension, and the pinned OpenClaw
Gateway may briefly leave the `openclaw` dimension blocked until its first
topology heartbeat reaches the already-live candidate. The updater may wait
within its bounded settlement window only when the blocking dimensions are a
non-empty subset of those two known startup states while safe reads and event
ingestion remain live. Any other blocked readiness dimension, unsafe
read/event state, or non-source capability degradation still triggers
immediate recovery.
HTR-03 acceptance covers exactly the members with configured trusted-ingress
identities; household records whose identities are explicitly deferred do not
become unknown acceptance rows. The live singleton topology proof counts the
two unique running launchd jobs and their live PIDs, rather than matching
mutable process command-line text.
The selected runtime derives its current release, instance, host, and
configuration binding through the same strict deployment snapshot as pending
and promoted readiness, including the prepared adapter bytes. The exact
provider-binding transition may combine additive legacy attribution with one
uniquely discovered principal-bound personal Google grant. Enrollment adds only
Gmail read-only with writes disabled, Calendar read-only, and the default Google
Tasks list read-only; it preserves the Keychain credential, requires observation
mode, and is journaled for byte-exact recovery. A legacy configuration digest
is not comparable evidence and must fail closed.

An installed-user OAuth item created by an enrollment interpreter is not yet a
runtime-readable item. Before HTR-10, and only after separate exact authorization
for this live Keychain/configuration preparation, copy exactly the uniquely
discovered read-only grant into one signed-client envelope and add its one alias:

```bash
poetry run python -m bootstrap.provider_runtime_alias \
  --instance-root "$HOME/Library/Application Support/AdministrateMe" \
  --authorize-live-runtime-alias
```

The command preserves the OAuth document, scopes, token, existing aliases, and
observation mode; it makes no provider call and verifies signed-client reads
before publishing the alias map. If activation did not select the prepared
adapters, the same separately authorized command with `--rollback` removes only
that unused alias and copied envelope. Rollback refuses while active adapter
configuration still references the logical credential.
Legacy-plugin recovery accepts OpenClaw's validated post-install entry with no
optional plugin `config` object; malformed entries or non-object configuration
still fail closed.
The candidate-pending contract and journaled consumer are implemented, but no
household HTR-08 evidence is authorized or recorded and HTR-09 has not been
requested. Repository fixtures prove mechanics only; they do not authorize an
artifact, a service stop, or activation.

Restore only a file inside the configured backup directory:

```bash
./bootstrap/restore.sh "$HOME/Library/Application Support/AdministrateMe/backups/administrateme-YYYYMMDDTHHMMSSZ.ambackup"
```

Restore decrypts and authenticates into a staging directory, rejects links and
path traversal, verifies every hash, opens the SQLCipher log, rebuilds all 15
registered projections (13 domain projections plus Triage and Context Fabric)
and workbooks from
the event log, then and only then replaces active state. The previous state
moves to the `rollback` directory. If post-restore Doctor fails, the previous
state is automatically put back.

## Upgrade and rollback

An upgrade is not topology repair. Before invoking it, require the Central
Host to be a tagged non-member infrastructure node, the runtime user to be a
dedicated Standard account with a separately tested recovery administrator,
every interactive member to pass a distinct trusted-ingress acceptance, every
enabled provider account to have an explicit owner/workspace/scope/provenance
binding, and current authenticated backup/restore/rollback evidence. Runtime,
Settings, Doctor, and `/api/system/ready` must agree on the shared content-free
readiness dimensions. Do not edit installed releases or live configuration to
turn a blocked dimension green.

The planned CoS Apple service identity is not part of a central upgrade. It
remains `planned` / `account_not_created` unless the owner separately
authorizes each account, recovery, host, permission, transport, enrollment,
observation, and sending gate. It never grants member Apple-source access.

### Candidate-pending readiness (reference only)

After a separate owner request completes HTR-01 through HTR-04, HTR-06,
HTR-07, HTR-10, and the preliminary HTR-05 rehearsal against the final merged
candidate and configuration, record HTR-08 from that exact clean candidate:

First save the authenticated `/api/system/historical-attribution` result and
the exact `source-assurance-v1` details from the same `/api/system/ready`
watermark as separate owner-only mode-0600 JSON files inside the configured
backup directory. Review the pseudonymized attribution groups and terminal
effect counts, then reduce them without copying group rows or event IDs:

```bash
python -m bootstrap.htr10_review \
  --instance-root "$HOME/Library/Application Support/AdministrateMe" \
  --historical-attribution /absolute/path/inside/backups/historical.json \
  --source-assurance /absolute/path/inside/backups/source-assurance.json \
  --observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-reviewed
```

The reducer requires both audits to be healthy at one watermark, zero
unattributed sources, terminal-effect integrity, and zero true, unknown, or
unsuppressed effects in the already configured observation window. It reads
that window start from active readiness and cannot reset it. Its append-only
`htr-10-review-v1` receipt contains only counts and hashes. A fixture or an
unreviewed export is not HTR-10 completion.

```bash
python -m bootstrap.candidate_readiness \
  --instance-root "$HOME/Library/Application Support/AdministrateMe" \
  --candidate /absolute/path/to/exact-clean-candidate \
  --expected-candidate-commit 0123456789abcdef0123456789abcdef01234567 \
  --preliminary-rehearsal-receipt /absolute/path/inside/backups/rehearsal.json \
  --preliminary-backup-artifact /absolute/path/inside/backups/backup.ambackup \
  --htr-10-review /absolute/path/inside/backups/htr-10-review.json \
  --confirm-htr-01 --htr-01-observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-htr-02 --htr-02-observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-htr-03 --htr-03-observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-htr-04 --htr-04-observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-htr-06 --htr-06-observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-htr-07 --htr-07-observed-at 2026-01-01T00:00:00+00:00 \
  --confirm-htr-10 \
  --approve-provider-binding-migration
```

The timestamps above are placeholders, not evidence. Use the actual aware UTC
observation times only after the named human checks occur. The HTR-10 input is
an owner-reviewed, self-hashed, content-free `htr-10-review-v1` receipt bound
to the fixed event watermark, historical attribution result, source-assurance
result, and original provider-effect observation-window start; it is not a
generic checkbox and cannot reset that window.

The command holds the canonical cooperative deployment-operation lock, checks
the full active/retained pointer chain, hashes fixed configuration coordinates,
streams the exact encrypted backup digest, validates the strengthened
`backup-rehearsal-v1` receipt, and writes one append-only mode-0600
`app/readiness/candidate-pending/<attestation-id>.json`. It never replaces
`state/config/deployment-readiness.json`, selects the candidate, stops a
service, touches Keychain values, calls a provider, constructs the updater, or
promotes the pending artifact. Runtime, Settings, and Doctor ignore it. There
is deliberately no mutable "current pending" pointer: a future journaled
updater must receive one explicit attestation ID, revalidate every binding and
freshness predicate, and derive active readiness only inside its recoverable
selection/start transaction. A command result is not HTR completion when its
inputs are fixtures. The compatibility-named
`--approve-provider-binding-migration` flag approves the exact reviewed
transition recorded in HTR-10; it does not authorize broader provider scopes,
credential changes, or writes.

Never run a floating OpenClaw upgrade. The commands in this section are
reference-only until this implementation is merged, an owner has separately
completed HTR-08, every bound fact remains current, and the owner explicitly
requests HTR-09 for the exact protected-main SHA. Prepare and review that
candidate checkout, then run with the exact append-only HTR-08 artifact ID:

```bash
./bootstrap/update.sh \
  --pending-attestation-id=0123456789abcdef0123456789abcdef \
  --approve-provider-binding-migration \
  /absolute/path/to/reviewed-candidate
```

For a separately rehearsed Python/Node baseline change, the reviewed command
is instead:

```bash
./bootstrap/update.sh \
  --pending-attestation-id=0123456789abcdef0123456789abcdef \
  --approve-runtime-migration \
  /absolute/path/to/clean-reviewed-candidate
```

Both forms refuse a dirty or non-Git candidate and materialize the reviewed
commit's controller source into a private fixed-coordinate workspace with a
commit-versioned tooling venv. A same-baseline update
uses the active release's Python and Node only as the controller runtime; a
separately approved baseline migration uses the side-by-side candidate runtime.
Neither form runs the new controller inside, or synchronizes, the active
release's venv. The launcher rechecks the candidate commit and clean worktree
after dependency preparation, attests the loaded upgrade module against that
fixed commit materialization, and passes the exact commit to the manager. A missing,
mismatched, or active-release controller fails before backup, service stop, or
pointer mutation.

Do not reset the login Keychain or select **Allow all applications** when a
Python-boundary migration encounters an access prompt. The explicit migration
launcher preserves the existing items for rollback. Under the active
interpreter it reads each currently effective value, transfers it only through
an anonymous pipe to the candidate interpreter, and has that interpreter create
and verify a runtime-bound copy. Long values are represented as bounded,
versioned Keychain fragments plus a checksummed root manifest committed last;
no fragment crosses the Apple prompt reader's input limit. Only then does it atomically publish
`state/config/keychain-runtime-aliases.json`, which contains references and
release metadata but no values. The candidate probes every configured logical
reference through the alias-aware production store with an eight-second bound and refuses the migration
before release preparation, backup, service stop, or pointer mutation if a
source is missing, the copy cannot be verified, an alias is invalid, or an ACL
prompt remains. An older rollback release ignores the alias map and keeps using
the original untouched items.

Compatibility validation happens before backup, service stop, release publication,
or pointer replacement. The same-runtime shell gate independently refuses a
Python/Node compatibility range, preferred version, or formula change before
executing candidate code. A different
OpenClaw version, undeclared successor commit, or plugin API is refused. A
compatible candidate first passes the cheap source/runtime compatibility
checks. Before staging, it fsyncs a private parent-side `publishing` record bound
to the candidate/controller commits, instance, full recursive pointer-chain
digest, stage nonce, final path, and generated-policy version, with the
preparation-activity coordinate explicitly unbound. The fixed Git tree and
private exact source marker are verified, fsynced, and atomically published
together at the final commit path; ignored or untracked worktree files are not
copied. An unprovably reaped materializer leaves a private
`publishing_unreaped` record and stage for reviewed recovery and grants no
cleanup, retry, backup, or activation authority. After successful source
publication, and before generated-root cleanup or runtime preparation, the
updater creates the exact private preparation-activity inode, acquires it
exclusively, binds that coordinate, and transitions the record to `preparing`.
The controller retains exclusive ownership through retry cleanup, downgrades to
a shared cleanup-visible lease while each active command guardian/worker holds
its own shared lease, and reacquires exclusive ownership after worker
terminality for completion. Controller disconnect, timeout,
activity-coordinate replacement, or an unreaped command fails closed. Only
then is the isolated Python environment and native-plugin build prepared at the
stable, non-selected final path. Preparation authority comes from that pending
record, never from the marker alone. Every retry revalidates the record, source,
marker, pointer chain, and release coordinate; deletes every allowed generated
root without following links; rebuilds from scratch; and verifies exact
preferred Python at both the formula and venv coordinates plus exact Node and
Poetry. Active and recursively retained releases are read-only. If preparation
fails, the verified source remains inactive and no backup, service stop,
selector, plugin registration, or pointer mutation has occurred. One private
cooperative operation lock covers the supported fixed-commit activate launcher
from the first active-deployment read through disposable controller cleanup;
direct rollback acquires the same canonical manager lock. A loser fails before
tooling, backup, or selector effects.

The disposable controller keeps its own private `HOME`, while production
LaunchAgent and log coordinates come from the login home resolved through the
runtime account database. The signed Apple `security` subprocess receives that
same login-home coordinate. This lets backup quiescence and activation manage
the durable user services and find already-bound runtime Keychain aliases while
controller caches remain isolated. Secret values stay out of arguments,
environment variables, files, and logs; no Keychain item or ACL is changed.

Inside that lock the updater revalidates the explicit pending artifact, then
creates a mode-0600 activation journal before backup quiescence. It creates and
rehearses one exact fresh encrypted artifact, proves the retained predecessor's
source, prepared runtime, OpenClaw, and LaunchAgent rollback coordinates, and
revalidates the pending binding again before selector authority exists.
Immediately before the first selector mutation it consumes the completed
preparation record, which can never grant retry authority again.

The journal stores exact predecessor pointer, readiness, adapter, and Settings
control bytes plus the prior running state. Settings control is part of the
rollback boundary because a newer runtime may migrate that file before Doctor
can adjudicate the candidate. It fsyncs intent before OpenClaw selection,
service quiescence, plugin selection, pointer selection, readiness promotion,
required-tool and LaunchAgent selection, restart, and Doctor. Candidate-bound
active readiness is published only after the candidate pointer and before
candidate services start.
An ordinary failure invokes the same recovery path immediately; SIGKILL or
host death leaves the journal for the next guarded activate/rollback invocation,
which performs recovery and returns without beginning new work. Recovery
restores the predecessor selectors and exact owner-evidence control bytes, then
mechanically refreshes only the configuration fingerprint after the final
plugin and required-tool configuration selectors, then restores the prior
running/stopped state. This keeps the
retained physical attestations bound to the exact configuration the recovered
runtime will load without inventing a new human confirmation. A markerless
retained predecessor is checked against the clean Git object database supplied
to that guarded invocation (which may be a later protected-main commit), while
the journal's captured predecessor and candidate commits remain the exact
ancestry and byte-proof coordinates. The
temporary materialized controller tree is never treated as a Git repository.
Failure to converge leaves services stopped and the journal in place. A
committed activation history is also the sole authority
for journaled explicit rollback, including restoration of the predecessor's
readiness evidence and the same post-selector configuration-fingerprint
refresh. These mechanics do not execute HTR-08 or HTR-09 and do not substitute
for post-switch browser/privacy/isolation/effect acceptance.

OpenClaw reconciliation resolves the instance-owned `bin/openclaw` selector
directly and revalidates it against the reviewed source receipt. It does not
depend on the disposable controller's ambient `PATH` after installation or
during journal recovery. Plugin selection and adapter-derived member-topology
selection are one stopped-service operation; recovery restores predecessor
control bytes before running that operation. The plugin CLI has a bounded
five-minute window for cold integrity scans. Reconciliation captures the exact
private OpenClaw configuration before its first mutation and restores those
bytes atomically if uninstall, install, topology selection, validation, or
runtime-source proof fails.

An additive projection change must also be exercised against an encrypted
database from the prior schema. A clean event-log rebuild is necessary but does
not prove existing-database startup ordering: schema statements run before the
projection migration hook, so indexes over newly introduced columns belong in
that hook.

For central services and post-switch Doctor, the candidate manifest's
formula-specific Node bin precedes `/opt/homebrew/bin`; this keeps an older
side-by-side global Node installation out of the candidate runtime.

The lower-level rollback command is:

```bash
python -m bootstrap.cli rollback \
  --recovery-repository /absolute/path/to/exact-clean-protected-main-checkout
```

Production rollback requires that explicit clean Git checkout before selector
work begins. It supplies the trusted object database used to verify and recover
the retained current release if rollback is interrupted or fails. Test-mode
fixtures may omit it.

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
