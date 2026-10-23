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

For first member-forwarded intake activation, follow the two-release procedure
in [CO-032](CHANGE-ORDER-032-FORWARDED-INTAKE-DEPLOYMENT-READINESS.md). Activate
the immutable `rollback-prerequisite` merge first, with its own pending
attestation and fresh backup/rehearsal. Then activate the later `cutover` merge,
retaining the prerequisite for rollback. Both use the normal guarded updater;
there is no environment switch or installed-manifest edit. After cutover, the
encrypted epoch also prevents a chained rollback to an older unaware release.

An observation window's original start does not expire; its fresh audit and
review evidence do. Preserve that start when preparing new evidence. If the
owner explicitly omits a configured member's device check, the pending writer's
repeatable `--skip-member-device-check <member-id>` records that exact omission
separately from tested member IDs. At least one member must actually be tested.
Report the omitted coverage and retain every identity/privacy/isolation check.
Physical recovery evidence must remain genuine and recent.

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

If the predecessor's export lacks the explicit observation-window origin, use
the exact merged candidate's read-only exporter before the reducer:

```bash
python -m bootstrap.htr10_export \
  --instance-root "$HOME/Library/Application Support/AdministrateMe" \
  --historical-output /absolute/path/inside/backups/historical.json \
  --source-output /absolute/path/inside/backups/source-assurance.json \
  --approve-provider-binding-migration
```

Here the compatibility-named approval flag confirms review of the exact
provider-ownership audit view. The exporter prepares that view only in memory;
it neither enrolls providers nor changes configuration. An already-current
configuration produces no transition. A non-null transition digest must be
reviewed separately before it can be carried into pending activation evidence.
No provider call or runtime construction occurs. Existing outputs are never
overwritten. Run from the clean merged candidate with its supported interpreter.

Read-only collection can proceed when only deployment attestation dimensions
are blocked, while operational topology, core capabilities, safe reads,
ingestion, observation containment and current zero-effect accounting remain
healthy. It cannot turn an unobserved recovery login or device test into
evidence. Missing, unknown or broader operational degradation remains blocked.
Both exported audits must cover the original active observation boundary; the
reducer and pending writer reject a missing, mismatched or subsequently changed
origin. Active readiness and physical-check timestamps remain untouched.

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

MODERNIZE: after an authorized CoS live interval, HTR-10 also binds the current
observation window's actual `ended_at`, when present, and every retained closed
`prior_provider_effect_windows` entry. The exporter audits each half-open
`[started_at, ended_at)` interval at the same immutable event watermark and
requires zero effects within every observation interval. Authorized effects
between intervals remain visible in the all-history integrity audit. Unknown,
uncorrelated, or otherwise invalid effects still fail that audit regardless of
their time. The optional `provider_effect_windows` receipt field carries the
complete interval evidence through pending attestation and guarded promotion;
changed ends, reasons, or history invalidate that binding. Original start-only
receipts remain valid only for the same unchanged open window without retained
history. A code update never reopens a closed interval or discards its history.

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
marker, pointer chain, release coordinate and exact preferred Python, Node and
Poetry. A completed, unconsumed preparation also records a content fingerprint
of its generated dependency/build files, modes and link targets. When that
fingerprint still matches, retry reuses the prepared runtime without reinstalling
dependencies or rebuilding. Interrupted, changed or older preparations without a
fingerprint take the existing clean rebuild path; unsafe paths fail closed. The
updater checks the fingerprint again immediately before consuming preparation.
Reuse applies only to the same candidate and predecessor, never another commit
or a selected, retained or already-consumed release. Active and recursively retained releases are read-only. If preparation
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
creates a mode-0600 activation journal before backup quiescence. New pending
receipts include private relative coordinates for their existing recovery proof.
The updater may reuse that authenticated backup and rehearsal only when the
candidate, deployment binding, original freshness window, key binding and full
state inventory still match. It compares all state file paths, sizes and bytes,
then repeats the comparison after quiescence; changed, missing, unsafe or legacy
evidence takes the ordinary fresh backup/rehearsal path. No timestamp is renewed.
This preserves current state coverage instead of treating an unchanged event
watermark alone as sufficient. The journal/result records whether evidence was
reused; the legacy `fresh_*` digest fields identify the validated artifact in
either case. The updater still proves the retained predecessor's source, runtime,
OpenClaw and LaunchAgent rollback coordinates and revalidates the pending binding
before selector authority exists. Reuse does not authorize restoration or a
state replacement, and all existing recovery and post-switch checks still run.
Immediately before the first selector mutation it consumes the completed
preparation record, which can never grant retry authority again.

A successful intentional rollback does not make that consumed release
preparation reusable. The rolled-back commit remains ineligible for another
activation even when its source tree is unchanged and a new candidate-pending
attestation exists. Publish and review a new protected-main successor commit,
then repeat the preliminary rehearsal, HTR-10 review, and candidate-pending
attestation for that exact commit. Never delete or rewrite the consumed
preparation record or installed release to make a prior commit retryable.

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

### Separately selected personal Google connections

`member-google-connections-v1` MODERNIZES the CoS-only acquisition
ceiling without restoring blanket personal polling. It PRESERVES the original
bounded sample, exact member/account ownership, append-only observations,
manual communication submission, and the external-write boundaries in
`UX-BEHAVIOR-CONTRACT.md` and ADR-0005. An existing personal Google connection
may be selected independently of the CoS mailbox. The operator must have that
member's explicit authorization; another adult's consent is not a substitute.

The private `member_google_connections` policy contains exact existing adapter
IDs, member/account coordinates, explicit `access` (`read_only` or `read_write`),
`binding_digest(entry, access=...)` values and hashes of
the owner's authorization. It accepts only enrolled member-owned Google Gmail,
Calendar and Tasks OAuth adapters. Account credentials, calendar/list selection,
and all other binding fields must match the predecessor. No account is created
or reattributed. The retained test sample is unchanged and remains historical
evidence rather than the active acquisition ceiling for these selected reads.

Prepare this policy in the private `ActivationRequest.adapter_config` for the
existing guarded `bootstrap.cos_channel_activation` operation. Set only the
selected existing entries enabled. The request's authorizing member and receipt
hash must match every added/changed selection; an operator cannot remove another
member's selection. Keep existing CoS enrollment, sender/transport proofs and
recipient restrictions. The operation requires the matching merged release,
observation containment, an encrypted backup and restore proof, and settled
Doctor evidence; its journal binds the complete prepared configuration. Do not
edit active configuration by hand. A request to change Google connections does
not authorize changing the CoS sender, recipients, or any unselected provider authority.

Gmail always uses its read-only scope with writes disabled. Calendar and Tasks
use the explicitly selected read-only or read/write scope; changing access
requires a matching owner-authorized selection and binding. Read/write access
retains the existing allowlist, governance, independent approvals, rate limits,
privacy guard and observation suppression for each external effect. It does
not authorize an individual provider write. Gmail initial backfill, named
historical queries and related-participant expansion stay disabled. The
selected connections also force manual candidate
handling and disables language extraction before adapters start, including after
restart with older retained preferences. A source-attention preference cannot
relax this ceiling. Forwarding an item explicitly to the CoS remains a separate
member action. Settings show current selected read acquisition separately from
the retained sample, and do not claim a full mailbox history or healthy sync
without runtime/cursor evidence.

Reusing a credential reference is not proof that Google will accept it. Verify
its exact owner/account and effective scope through the supported provider
handshake; renewal may require the member's Google consent. On an activation
failure the existing operation restores predecessor configuration, contains
sending and pauses acquisition. A connection pause remains effective after
restart. Removing a member's exact selections through the same authorized
operation disables those reads without deleting retained facts or candidates.
Restoring an older release without this contract keeps personal adapters off.

Validate the change in isolated CI with
`tests/acceptance/test_member_google_connections.py`, the existing CoS acquisition
and forwarded-intake scenarios, and the guarded activation/recovery tests. The
Google release scope requires the complete focused production, packaged-restart,
permission/privacy, guarded-recovery and type checks. Its Git-bound selector
allows only the exact Google integration edits; every other runtime byte and
mode, including bridge code and dependencies, must remain unchanged. This CI
scope does not assert compatibility for a retained helper or waive an actual
deployment prerequisite. Member-device browser
verification is read-only acceptance after authorized activation, not a place
to test candidate code.

### CoS account Calendar and Tasks acquisition

`cos-google-workspace-v1` MODERNIZES the delegation-only service-source setup:
CoS may use her actual Google account through separate installed-app OAuth grants
for `calendar-read` and `tasks-read`. This PRESERVES the CoS service principal,
service-private facts, the existing Gmail read/send grant, member credentials,
selected member access, and the guarded activation/recovery process. It does not
impersonate a member or grant Calendar/Tasks writes. Drive, Contacts and provider
write workflows remain later deliverables; this acquisition slice is not a claim
that the complete CoS Google workspace is ready.

After deploying the protected release, enroll each required purpose with
`python -m bootstrap.google_oauth <private-client-file> --cos-service --purpose
calendar-read` (then `tasks-read`). The default `--cos-service` behavior remains
Gmail read/send. Each workspace purpose uses its own Keychain name and receipt;
it never overwrites the mail/member secret or requests incremental grant union.
The exact scopes are the corresponding `calendar.readonly` or `tasks.readonly`,
`openid`, and `https://www.googleapis.com/auth/userinfo.email`. Explicit OpenID
Connect identity scope matches Google's canonical identity response while the
resource read scope remains mandatory. Broader, identity-only, incomplete and
mismatched-account grants fail before secret storage; do not relax oauthlib's
scope check or union prior grants. Select the requested Calendar or Tasks read
permission on Google's granular consent screen: signing in alone is not consent
to that resource. A failed callback stores no new credential or enrollment receipt.
Google documents [OpenID Connect scopes](https://developers.google.com/identity/openid-connect/openid-connect#scope-param)
and [checking granted permissions](https://developers.google.com/identity/protocols/oauth2/web-server#check-granted-scopes).
Google account consent is still necessary; administrative app approval does not
replace it. Revoking an entire OAuth application at Google may affect all its
purpose grants, so recovery pauses a source rather than revoking unrelated tokens.

Verify the intended CoS browser profile and the site's actual account before
opening the private authorization URL. Never fall back to a member profile or
copy session credentials. Keep account coordinates, authorization URL, OAuth
client file, selection and output private and outside Git. The expected account
is entered locally, and the helper verifies Google's returned identity.

Prepare a private JSON `CoSGoogleSelection` with `adapter_id`, `purpose`,
`provider_account_id`, `resource_id`, `credential_reference` and the existing
owner-authorized operation's `owner_authorization_sha256`. Use the provider's
exact existing calendar/task-list ID, not `primary`, `@default`, a name or `*`.
Run `python -m bootstrap.cos_google_workspace --selection <private-selection>
--previous-config <private-adapters-copy> --output <new-private-prepared-config>`.
The command refreshes the exact purpose token, rechecks the actual Google account,
reads the exact resource metadata and at most one source item, then writes a new
0600 preparation file. Calendar ownership must be `owner`; a shared member
calendar is not relabeled CoS-owned. No resource is created, no provider write
occurs, and active configuration is untouched. Repeat using the prior prepared
file as input to add the other purpose. No source contents appear in its summary.

Use the resulting inventory as `ActivationRequest.adapter_config` for the existing
`bootstrap.cos_channel_activation` command. Its authorizing household principal
and receipt must cover new/changed sources; it admits only the exact selected
CoS adapters and retains all existing adapter records. Existing member selections,
Gmail route/send controls, observation containment, backup and rollback still
apply. Do not edit active configuration by hand. A new or changed Gmail recipient,
member account, write scope or resource cannot be smuggled into this setup.

The visible outcome is successful Calendar/Tasks acquisition with service-private
facts after activation, not just a stored token or successful preparation. Check
those configured adapter states and new source facts within the bounded activation
window. On regression use the guarded operation's recovery; to revoke acquisition,
remove the exact selection or pause its adapter through an owner-authorized
activation. Retain the adapter record and append-only history. An older runtime
will keep these unrecognized CoS sources disabled under its acquisition ceiling.

Identity and scope references: [Google userinfo](https://developers.google.com/resources/api-libraries/documentation/oauth2/v2/python/latest/oauth2_v2.userinfo.html)
and [Google OAuth scopes](https://developers.google.com/identity/protocols/oauth2/scopes).

The `cos-google-workspace` release scope reuses the existing protected Google
connections worker on both Linux and macOS. It runs separate-purpose enrollment,
credential substitution and scope rejection, guarded setup/activation, actual
Calendar/Tasks adapter polling into the encrypted event log, existing member/mail
regressions, supervisor recovery, affected pack validators and type checks. The
selector requires the component and its proof files as regular files in the head
and actual merge tree, and binds every unrelated byte and mode; dependency,
bridge, runtime, persistence or unknown-file changes retain system validation.

### CoS-owned Apple Reminders: one selected list

MODERNIZE the Apple acquisition boundary for the CoS work computer. The new
`reminders-cos-apple-eventkit` central adapter reads one existing, explicitly
reviewed list as `service-principal:chief-of-staff`; the existing member bridge
adapter and its enrollment remain unchanged. It emits owner-aware `list.*` v2
and list-item snapshot v4/deletion v3 facts into the encrypted event log. It
cannot create lists/reminders, complete tasks, send, or write provider data.
Native-context exposure is a separate consumer integration and must be verified
through the CoS context tool before claiming the list is usable by the agent.

Keep the selected native source ID, list ID, device/Unix-user binding and
source-ownership evidence in private operator files. A local source belongs to
the reviewed work-computer environment; do not invent an iCloud address. An
explicit `icloud` selection additionally requires the operator's evidence of
that actual signed-in Apple account and its selected list. EventKit's CalDAV
source type or a display name does not establish account ownership. Signing in
later does not migrate, replace or broaden the existing local selection.

The permission-bearing process is the native executable
`CoS Reminders Helper.app/Contents/MacOS/CoSRemindersHelper`, bundle identifier
`com.administrateme.cos-reminders-helper`. It embeds the selected protected
release's Python 3.14 interpreter in process, with Reminders usage descriptions
in its signed main bundle. It does not launch a generic Python child to acquire
EventKit permission. Setup and ordinary polling both launch this same app.

The earlier generic-Python setup returned not-determined status, then timed out
without a dialog or source output. Its Python bundle lacked Reminders usage
metadata; that fact alone does not prove the timeout's sole cause. The repaired
app validates its actual main bundle, usage keys, signed component, device and
release before permission handling. Its main run loop stays available while a
bounded background operation waits for EventKit callbacks. A Codex, Terminal,
member-bridge or generic-Python grant never substitutes for this app's grant.

Install the immutable component from the new protected source while the healthy
central runtime stays running. From that protected checkout, use its compatible
Python 3.14 environment and the explicit protected commit:

```text
python -m bootstrap.cos_reminders_helper_app --source <protected-helper-source> --expected-commit <protected-commit> --destination <new-absolute-helper.app>
```

Save the returned `app_path`, `artifact_sha256` and `bundle_identifier` as an
owner-only helper receipt. The installer verifies protected source and code
signing, atomically installs a new app and reuses an existing exact artifact
without recompiling or re-signing. It never updates central LaunchAgents or
system Python. The signed component contains no central release coordinates;
compatible central updates keep the same app and consent identity. Changed
helper bytes need a new reviewed artifact and may need new OS consent. Signing
is ad hoc, not a Developer ID/notarization claim; no identifier-only designated
requirement weakens the exact signature to retain permission.

The setup controller can use `--runtime-release <current-protected-release>`
to bind its native worker to the compatible already-installed release before a
central maintenance cycle. The a10 exact-list reader contract is supported;
release marker, interpreter/library digest, device and reader module origins
are verified. The helper carries its worker, so this setup does not depend on
activating the new central client. Status is read-only and cannot prompt:

```text
python -m bootstrap.cos_apple_reminders --status --helper-receipt <private-helper.json> --runtime-release <current-protected-release> --output <new-private-status.json>
python -m bootstrap.cos_apple_reminders --inspect <private-lookup.json> --helper-receipt <private-helper.json> --runtime-release <current-protected-release> --request-permission --output <new-private-resource.json>
```

The owner-only lookup contains exact `list_name` and `source_kind` (`local` or
`icloud`), optionally a previously verified `source_id` to disambiguate. Only
explicit `--request-permission` requests OS consent. Inspect the actual OS
prompt identity before accepting it; do not claim usability from a prompt or a
saved setting. A denied, timed-out or differently attributed operation produces
no activation input. Omit that flag for later read-only inspection.

Inspection reads list metadata, rejects zero/multiple matches and returns only
that source/list plus the device/Unix-user binding and pinned helper receipt.
It reads no reminder content. Review the result against actual Apple source UI;
a title or CalDAV type is not account ownership. It does not upgrade Reminders
formats, move lists or migrate a local source to iCloud. Runtime polling never
enumerates accounts/lists or uses a default list.

The private `CoSRemindersSelection` contains `native_helper` (the exact receipt),
`adapter_id`, `source_id`, `list_id`, `source_kind`, `device_identity_sha256`,
`os_uid`, `source_ownership_evidence_sha256` and `owner_authorization_sha256`.
The last two hashes identify the operator's exact private reviewed records.
Prepare and read back through the same app before enabling the client:

```text
python -m bootstrap.cos_apple_reminders --selection <private-selection.json> --previous-config <private-adapters.json> --runtime-release <current-protected-release> --output <new-private-prepared.json>
```

This writes a new private activation input only after same-app current permission
and exact existing-list read-back. The content-free evidence includes the pinned
helper artifact and item count; `item_count: 0` is a verified empty list only
when all native proof checks pass. Missing lists and nil/failed fetches fail.
Ordinary preparation/polling never prompt. Private single-use request directories,
bounded expiry and exact response nonce/operation/identity reject stale, replayed,
foreign-process and unexpected responses. Provider bodies stay in owner-only
transient IPC files and the existing encrypted event path, not command arguments
or logs. Setup failures retain only an allowlisted fixed native phase/error code
(for example `python_initialization_failed` or `PermissionError`) after IPC cleanup,
so diagnostics do not lose the failed boundary. Only after this proof should the operator deploy/enable the central
client through the normal protected updater and guarded activation.

Apple's [Reminders usage-description contract](https://developer.apple.com/documentation/bundleresources/information-property-list/nsremindersfullaccessusagedescription)
and [EventKit migration guidance](https://developer.apple.com/documentation/technotes/tn3152-migrating-to-the-latest-calendar-access-levels)
govern this affected native boundary. Hosted macOS checks prove actual embedding,
LaunchServices identity and main-loop completion plumbing without requesting
Reminders consent; real OS consent and exact-list read-back remain live proof.

Use the prepared document as `ActivationRequest.adapter_config` through the
existing journaled CoS channel activation. Only the exact selected source can
be added. Existing member, Gmail and Google workspace records remain intact;
paused adapters stay paused. A source change requires deliberate review:
revoke the old selection, retain its disabled record/history, then prepare a
new adapter ID for the new source. Do not retarget an old cursor to another list.

Acceptance is the selected list's current facts through production ingestion,
with service-private provenance and zero writes, followed by the native-context
consumer check. Permission loss, source mismatch, nil/failed fetches and invalid
snapshots do not generate deletion facts. A complete successful snapshot emits
tombstones for missing prior items; a durable metadata-only checkpoint permits
restart/retry without duplicating complete snapshots. Incomplete publication
never advances that checkpoint. Polling can resume after permission is restored.

For recovery, set this source adapter `enabled: false`, or set
`cos_apple_reminders.selection` to null, through the same guarded activation.
History and separately authorized retained-context access remain intact.
Pause the feature on a schema-aware runtime after its new OS-authentication v3
receipt has been written; an older binary is not the default recovery for that
history. Deployment alone does not enable or enroll this source.

The `cos-apple-reminders` CI scope reuses the protected connections worker on
Linux and macOS, adding protocol/permission/resource negatives, actual helper-client
loader/event-log polling, restart/tombstone/error recovery, legacy EventKit pack
regressions and schema checks. macOS additionally requires the signed native
embedding, LaunchServices, sealed production-worker status and callback tests. Only these exact component/proof paths are excluded from its
dependency fingerprint. Unrelated source changes, missing proofs, deletions,
symlinks and executable-mode drift retain the protected system fallback.
