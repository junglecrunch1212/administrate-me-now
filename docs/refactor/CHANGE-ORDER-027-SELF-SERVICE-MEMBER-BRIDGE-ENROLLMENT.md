# CO-027 — Self-service member bridge enrollment

Status: **IMPLEMENTED IN PHASE A; LIVE ENROLLMENT AND DEPLOYMENT DEFERRED**

Date: 2026-09-02

Governing decisions: ADR-0000, ADR-0001, ADR-0005, ADR-0006, ADR-0008,
CO-004, CO-019, CO-023, CO-026, AC-57

## Objective and boundary

The retained-data CoS deployment admits only the explicit Calendar, Reminders
and Notes exception in
[`COS-CHANNEL-ACTIVATION-CONTRACT.md`](COS-CHANNEL-ACTIVATION-CONTRACT.md#explicit-member-bridge-sources).
This MODERNIZE uses the existing member-owned request as read authority; it
does not require or manufacture another consent event. Settings must refuse
unavailable selections before creating a handoff, and the read-only bridge
ceiling remains effective when independent CoS channel replies are live.

G09 modernizes the existing bridge request, encrypted transfer, provisional
`bridge.enrolled@v1` proof, Tailscale binding, finalization, connector-pack
authorization, and revocation ceremony into a member-owned Settings wizard. It
does not introduce another enrollment database or transport. The authoritative
bridge record remains `bridges/member-mapping.json`; the encrypted append-only
event log remains the source for wizard replay; and each member bridge remains
one separate edge process.

This is credential-free Phase A work. It does not deploy, contact an Apple
account, connect a family bridge, alter a family Keychain or LaunchAgent, change
macOS permission, start provider observation, authorize a write, or perform a
provider effect.

## Settings and target-device flow

An authenticated adult in their own Settings context can review ownership,
privacy, observation behavior, local permission requirements, the authenticated
read/action member, This Mac versus Another Mac, and the exact supported
capabilities. The review names data categories, local access, read ceiling,
write ceiling, revocation, and the fact that transport pairing starts no
source. Apple Messages read stays planned for G10 and reply stays planned for
G11.

The central runtime creates a 30-minute AES-GCM-encrypted, HMAC-signed handoff
bound to tenant/instance, member, request, enrollment, Tailscale identity,
device class, exact release commit, exact capability-set digest, and expiry.
For an installed central release, that commit comes from the captured deployment
binding and must match the safe, owner-only source marker in the exact release
root; missing, mismatched, or unsafe evidence cannot fall back to environment
variables, Git discovery, or a directory name. The packaged member installer
likewise verifies its archive-bound commit against the installed source marker.
Package bytes are downloaded with no-store headers. The separately copied
one-time code is never rendered, placed in a URL, written into ordinary
Settings JSON, stored in the bridge index, or retained on the target after
acceptance.

The reviewed macOS helper bundle owns the `.adminmebridge` document type. It
contains the exact Git-archived release and a checksum-bound reviewed installer,
so the target member does not need a source checkout or terminal. It passes the
transfer code through an owner-only temporary file rather than argv, previews
only safe binding metadata, obtains explicit local acceptance, verifies
Tailscale and release identity, and routes signed wizard packages through the
shared helper service. The completed bundle receives a final local ad-hoc seal
and strict signature verification before distribution; this is neither
Developer ID signing nor notarization and grants no Gatekeeper waiver. Native
dialogs then carry every selected capability through its macOS Settings
location, grant-or-deny continuation, local container
or folder selection, content-free test, signed adapter evidence, central
attestation, and connector start. It cannot click or bypass TCC, run arbitrary
central code, install an updater or remote shell, or start a connector merely
because transport paired. The legacy unsigned bootstrap package remains
accepted only by the established legacy path.


### First-run delivery (MODERNIZE, 2026-10-23)

Authenticated Settings provides the reviewed helper for the **exact active
central release**, separately from a member's short-lived handoff. The supported
publisher is `python -m bootstrap.member_bridge_distribution --repository
<CLEAN_PROTECTED_MAIN_CHECKOUT> --instance-dir <INSTANCE_DIR> --expected-commit
<MERGE_SHA>`. Prepare this immutable artifact before activating that central
release. It lives outside installed releases and encrypted state under the
instance parent's `app/bridge-helper-downloads/<MERGE_SHA>` directory. Keep the
predecessor artifact for rollback. Publication cannot enroll a member or grant
Apple access. Missing, unsafe, oversized, mismatched or damaged artifacts return
an actionable refusal; neither server nor browser substitutes another release.

The member downloads and opens the helper, chooses **Prepare This Mac**, and
completes native prerequisite prompts before creating a pairing request. The
helper checks the supported Apple-silicon/macOS version, FileVault, developer
SDK, Tailscale app/identity availability and storage, then installs its reviewed
runtime. Homebrew's official 7.0.10 macOS package is downloaded over HTTPS and
checked against its pinned SHA-256 and macOS signature assessment before opening
Installer. The native installation and account consent remain the member's.
See [Homebrew's installer documentation](https://docs.brew.sh/Installation.html).
The helper can use Tailscale's ordinary application executable; a terminal
shortcut is not required. A blocked prerequisite explains its own next step and
can resume without a pairing code. Installation does not start a daemon.

Settings asks the member to confirm **Mac Preparation Complete**, then creates
the existing fresh 30-minute handoff. This checkbox is a workflow aid, not
permission or device attestation. Opening a handoff before preparation finishes
preparation without reading its secret and directs the member back to Settings.
The existing local consent, exact identity, expiry, source selection, replay,
unknown-result and write guards still decide enrollment. Completion points to
source-specific sync status, Apple write permissions and private CoS permissions.
Selecting reading during enrollment grants no writing and does not turn on the
system-wide observation switch. An empty selected calendar is valid; members
must select their actual desired Apple or Google-backed calendar in EventKit.

Recovery: interrupted preparation retains diagnostic receipts and retries the
same immutable runtime; interrupted pairing retains the existing request and
accepted source steps. Revoke or repair through Settings, never silently create
a replacement after an unknown response. A bad central delivery is rolled back
through the guarded updater to its known-working predecessor; its matching
helper remains available. No provider writes are part of preparation/acceptance.

Installation failures must remain diagnosable after a native dialog exits.
The helper records a private, content-free phase and terminal exit receipt;
dependency preparation retains a bounded private log before transfer-package
inspection. Dependency progress is separate from the safe enrollment preview.
Transfer codes and later enrollment output remain temporary and are deleted
on exit. A phase receipt is diagnostic evidence, never acceptance, permission,
source attestation or proof that installation completed. This MODERNIZE preserves
the existing enrollment and write-authorization gates.

Native dependency preparation must use an SDK compatible with the selected
Apple developer tools. An unqualified `xcrun` SDK lookup can select a newer SDK
than those tools support. Without an explicit SDK override, the installer uses
the selected developer directory's canonical `MacOSX.sdk` and validates it.
The locked native dependency build verifies compilation and linking. Explicit
SDK or compiler selections are retained or fail with an actionable diagnostic;
they are never silently replaced. Build-specific environment settings stay
inside dependency preparation and do not reach enrollment or the running bridge.
No system developer selection, SDK installation or symlink is changed. A complete
existing environment and local continue/uninstall do not acquire a new build
prerequisite. This MODERNIZE preserves the pinned dependencies and runtime.

## Compatible helper component releases

The owner-directed risk-based release policy in AC-21 **MODERNIZES** helper
packaging without changing enrollment authority. A reviewed helper/installer
component can be delivered separately while retaining the exact deployed
runtime archive. The default build still uses one commit. The compatible build
records two distinct immutable identities: the helper component source and the
retained runtime. Existing `release_commit`, installed source markers, target
runtime paths and handoff equality checks keep their runtime meaning. New
helper/installer bytes remain outside that runtime archive and installed tree.

The builder must prove compatibility from fixed Git objects, including paths,
contents and executable modes. The versioned policy includes every tracked
runtime input by default and excludes only explicit helper packaging files and
identified non-runtime test/CI/prose paths. Dependency locks, package metadata,
enrollment interfaces, schemas, runtime Markdown and unknown paths remain
included. An addition, deletion or mode/content change to an included input
refuses the compatible build. Component source must match its clean immutable
commit, including bytes/modes hidden by Git index flags. The retained runtime
must be an ancestor. Provenance does not replace protected PR approval.

The sealed manifest binds both identities, compatibility policy and fingerprint,
installer/launcher hashes and runtime archive hash. Verify the complete archive
inventory against the fixed runtime tree and the supplied previously verified
archive hash; Git attributes cannot silently change retained source. The new
installer installs or resumes that retained runtime through the existing
supported path. It cannot relabel changed runtime bytes as an old release,
relax a transfer guard or overlay a patch onto an installed release.

Final component validation covers the actual combined package on the affected
macOS toolchain: Git-free installation, interrupted recovery, production preview,
local management, integrity and secret cleanup, plus negative incompatible,
dirty/tampered source, archive, mode and identity cases. Changes to scope/CI
policy require their focused selection and fail-closed gate tests. A compatible
helper-only release does not need unrelated full application suites or central
redeployment. New runtime/interface interactions require fresh evidence even
when unchanged installer/toolchain checks can be reused with matching bindings.

After the component's protected-main merge, build and verify the sealed bundle,
retain the known-working sealed predecessor, and install through the supported
helper replacement procedure. Perform bounded installation/use checks on the
authorized device. On regression, restore that predecessor bundle and document
association; preserve runtime files, enrollment state, permissions and attempt
history. Enrollment itself still requires a fresh valid handoff and actual
member acceptance/permission gates. This component path neither grants those
permissions nor requires maintenance of the unaffected central runtime.

The supported component delivery procedure is:

1. Verify the component's immutable protected-main identity and passing selected
   checks. Read the runtime identity and previously verified archive SHA-256
   from the retained helper's verified manifest. Build in a clean checkout with
   `python -m bootstrap.member_bridge_helper_app`, a new `.app` destination,
   `--expected-commit` for the component, `--runtime-commit` for the retained
   runtime and `--expected-runtime-archive-sha256` for that archive. Verify the
   resulting manifest, hashes and strict code seal before distribution.
2. On the authorized device, confirm no helper run or dialog is active. Record
   the original bundle location and document association, retain the exact
   original sealed bundle with its metadata and hash receipt, and stage and
   verify the replacement before replacing the bundle at that location. Register
   the verified replacement's document association through macOS's supported
   application-registration path. Do not modify any installed runtime files.
3. Verify the installed bundle's manifest/hashes/seal and actual launch identity,
   then use the ordinary fresh-handoff enrollment or existing local management
   flow. A permission dialog remains a real member action; installation is not
   enrollment, source readiness or permission evidence.
4. If the component regresses, close its helper run, restore the exact preserved
   bundle at the original location and restore/register its document association.
   Recheck hashes/seal and launch identity. Preserve runtime files, enrollment,
   permissions, diagnostics and attempts. If the predecessor was already stalled,
   report restoration of that pre-change state, not successful onboarding.

Packaging tests prove that the verified predecessor remains intact; they are
not a claim that an actual device replacement or rollback was performed.

### Permission recovery and concurrent source settlement

**MODERNIZE:** a sealed helper-owned permission controller may use a retained,
unchanged runtime through an explicit compatibility adapter. The controller is
an exact component resource with a manifest and launcher hash; it is not copied
into the installed runtime or imported by the central application. Compatibility
binds the expected runtime source identity and the exact interface implementation
used by the adapter. A changed or unknown interface fails closed. Excluding this
helper resource from the runtime fingerprint does not exclude shared bridge
code, central policy, schemas or dependencies from release validation.

Permission recovery also depends on the central enrollment protocol. Source
observations can arrive while another source's native dialog is open, or before
the helper receives confirmation that its daemon started. Central progress must
serialize validation, proof consumption, index changes and durable progress
with observation settlement and member controls. An observation arriving while
its source is `observation_starting` remains durably admitted. After the genuine
helper reports `observing`, central catch-up finds the first admitted observation
after that source's exact authorization boundary and appends the existing
`settling`/`settled` progress. It preserves the helper command's acknowledgement.
No new centrally reported `observing` fact is introduced: the predecessor's
historical auditor and existing transition semantics remain compatible.
Configuring another source preserves the aggregate attestation derived from all
configured sources; an already attested source must remain admitted while the
new source awaits its own proof. This uses the existing index commit procedure
and never gives the new source authority before attestation.

The controller's restrictive state-vector hash precondition covers every selected
capability as well as the aggregate state. A source can settle while the aggregate
remains `settling`; checking only that aggregate would accept a stale helper
vector and lose the reconciliation base. This header grants no authority and
does not replace the retained signed body or any identity check.

The explicit builder option `--runtime-compatibility-contract
bridge-permission-recovery-v1` supports this mixed central/helper repair. It permits
only regular-file modifications of the fixed central enrollment router and
the exact reviewed old/new source pair for the central coordinator fix, plus the
closed `cos-standing-replies-457-hooks-458-v1` central overlay. The overlay is a single
coherent group of 24 exact path/presence/mode/hash pairs from retained public
release `043ac68915b185be4ada771f6893a4d69c3f994b` to the reviewed PR457 central
sources plus the PR458 native reply-hook correction at
`6035a997614b52f537bb9ce91d24d01464d878a2`. Its five added central
files must be absent from the retained archive. Partial mixtures, unknown bytes,
mode drift and any other dependency or edge-contract change fail closed. This
does not establish a generic central-code exclusion or change release scope.
The PR458 revision replaces only the three exact changed CoS source coordinates
and adds the grant-derived native hook policy module; no updated central bytes
enter the retained edge archive. Current helper builds require the complete
updated group. Older immutable helpers retain their own versioned manifests.
Focused compatibility checks reject unknown bytes on either side and incomplete
hook-policy groups before packaged clean/interrupted installation is accepted.
A separate `hosted-gateway-startup-probe-456-v1` exact regular-file hash pair
covers the hosted approval probe's pinned disabled-heartbeat startup check.
It is recorded independently of the CoS source group, remains a runtime
input for default compatibility and CI, and is never copied over the retained
archive. Its corrected bytes execute only in the isolated hosted probe.
The exact coordinator
pair changes only the body of `BridgeEnrollmentCoordinator.configure_capability`;
the edge-used definitions remain unchanged. Unknown shared-module bytes fail
closed. The manifest records both hashes and retains every byte of the paired
archive. The controller accepts only the exact
reviewed retained/current interface hashes. Default compatibility and the CI
runtime fingerprint remain strict. The
launcher binds the expected central release independently of the paired edge
release; the authenticated protocol must match before any permission action.
The real packaged installation gate uses the complete `043` archive, whose
SHA-256 is `12eaab9cd6325200b0fa795236fd5da917f221282a582cc8a8767e94e6847df3`,
and verifies every retained and candidate overlay coordinate. It checks fresh
and interrupted installs and loads the sealed current controller through the
retained interpreter. Central reply schemas remain confined to the central
runtime; bridge enrollment, source-observation envelopes and provider authority
continue to use their unchanged contracts. The authenticated current-central
composition tests remain necessary alongside the archive/import smoke. This
evidence does not assert real macOS permission grants or member-device usability.

The adapter reuses the retained helper's signed progress, central validation and
acknowledged local persistence, and its read-only `_identity()` device probe.
Current Tailscale login and node must match both the local binding and central
member/request evidence. Its narrowly documented protected `_report` seam supports a fresh `permission_required` transition for an explicitly selected
blocked source after local retry consent. It does not manufacture a permission,
attestation, member identity or provider action. The same request and original
failure history remain intact. Paused, revoked, unexpected or mismatched current
bindings stop before source access; settled sources remain untouched. An
authenticated, exact-bound central snapshot may reconcile only proven monotonic
observation progress through the helper's supported persistence path. It cannot
grant permission, configure a source, establish an attestation or resolve an
unknown command. A typed stale-state refusal may be retried only when the central
protocol proves it preceded nonce consumption and every durable effect, and only
for that individual report. Lost responses and ambiguous local persistence remain
unknown and stop; a new nonce never substitutes for missing command evidence.

Retry eligibility is limited to initial permission failures that never reached
adapter attestation or observation startup. The retained adapter-authentication
idempotency contract does not establish fresh authority for a later same-source
reauthorization. Such a source is reported as unsupported before new permission,
configuration or proof mutations; the helper must not attempt it and fail later.
A separate initial-start continuation preserves an already verified source whose
first local daemon start rolled back. It is not permission retry or active-source
reauthorization. The central snapshot must prove one original signed permission,
attestation and observation-start chain ending in the exact degraded receipt,
unchanged source authority and coordinates, no prior observation or activation,
and no intervening owner control. The recovery POST rechecks that proof under the
source-admission lock before consuming its nonce or appending any event; an
observation arriving after the snapshot invalidates it even without wizard drift.

The sealed helper requires the entire local selection stopped and absent from
launchd, no surviving daemon, no enabled pack, and no unresolved command journal.
It rechecks these prerequisites after explicit native continuation consent,
continues the single failed start first, and retains that source's permission and
configuration. Multiple ambiguous start recoveries, an already enabled source,
prior activation, revocation and unknown outcomes remain blocked. The retained
runtime and original signed progress writer are unchanged. The helper-owned
launch adapter synchronizes unloading, startup and confirmed-stop rollback;
unknown cleanup never restores configuration underneath a possibly live process.
Actual admitted observations and projection settlement still establish usability.

Cancelling a native permission dialog or source picker stops subsequent prompts
without recording a false missing-prerequisite or degraded-source fact.

Calendar and Reminders selection uses EventKit metadata and verifies the selected
identifier through the same EventKit lookup used by the daemon. Scripting IDs are
not assumed to be EventKit identifiers. Calendar read setup requires macOS Full
Calendar access, not Add Only access; macOS has no separate read-only grant.
AdministrateMe still denies provider writes throughout this setup. Notes retains
its native scripting permission and content-free availability check. Native
permission choices are made by the member; the helper never clicks TCC dialogs.
Purpose strings explain the access used by these selected-source checks.

Diagnostics contain only enumerated capability, stage and result codes. Native
source output, raw exception text, container labels/coordinates, account data and
credentials are not retained. Blocked or uncertain results must not show a
successful enrollment message. The helper's permission status and successful
local probe do not prove the separately launched daemon can observe: actual
central settlement and the ordinary device health check remain required.

Validation includes all three permission recovery paths through
production enrollment/progress/attestation, private manual source admission,
historical attribution and readiness; cancellation, denied and partial states;
restart, stale binding, revocation and unknown outcomes; observations interleaved
with daemon startup and remaining native prompts; packaged invocation,
resource integrity and secret cleanup. Hosted native ABI, authorization-status
and error checks are distinct from synthetic granted-access composition. No
native grant is fabricated to turn a hosted limitation into a successful source
read. Actual member permission and daemon observation remain deployment
acceptance requirements.

## Independent lifecycle and capability gates

The wizard uses the one exact lifecycle:
`not_requested`, `requested`, `package_preparing`, `package_ready`,
`awaiting_transfer`, `package_opened`, `instance_verified`,
`member_confirmed`, `device_identity_verified`, `bridge_paired`,
`awaiting_permissions`, `capabilities_testing`, `settling`,
`active_observation`, `partially_active`, `degraded`, `paused`, `revoked`,
`expired`, and `failed`. Invalid jumps fail before transport or capability
mutation. Content-free signed progress uses nonce replay protection and is
stored as private typed events.

Each selected capability independently uses `requested`,
`implementation_available`, `prerequisite_missing`, `permission_required`,
`permission_denied`, `permission_verified`, `adapter_authenticated`,
`observation_starting`, `observing`, `settling`, `settled`, `degraded`,
`paused`, `revoked`, or `unsupported`. A pack can start only after the exact
member/enrollment/source/account/container configuration, local permission
evidence, compatible release, exact adapter-authenticated event, and central
attestation all agree. Calendar, Reminders, Contacts, Notes, Voice Memos, and
Obsidian all retain a zero write ceiling during enrollment and observation.
The first centrally admitted observation advances `observing` through
`settling` to `settled`, so browser state becomes active or partially active
without a second operator or target-device ceremony.

## Recovery and revocation

Browser state re-composes from the secret-free Settings cache and replayable
private events. Target restart resumes from its protected receipt and derived
device proof key without the transfer code. Central restart replays the wizard
event family. Pause removes enabled connectors while retaining the exact
attested set for resume; every non-terminal capability records and restores its
prior state. Revocation clears connector execution and authorization, blocks
ingest, removes the handoff and protected proof key, and supports a bridge-owned
local uninstall. Expiry deletes transfer material and permits only a fresh
request ID. Duplicate, replayed, wrong-member, wrong-device, wrong-instance,
wrong-release, wrong-capability-set, altered, missing, and ambiguous operations
fail closed. Request identifiers remain bound to their original member even
after failure, revocation or artifact cleanup. A different member cannot reuse
one to replace a handoff or proof key. Historical proof lookup also includes the
member coordinate, preserving valid retained histories with overlapping client
request identifiers without allowing future collisions.

The explicit CoS source exception appends a typed, encrypted
`bridge.source_authorized@1` receipt with the exact device, selected source
configuration and earlier enrollment/permission evidence. Historical attribution
uses those immutable event-time coordinates, including selected containers, and
never substitutes the current bridge index for historical authority. Later
pause or revocation closes future admission while preserving earlier facts.

For a deployed instance, supported self-service index mutations share the
existing operation lock and a narrowly authenticated readiness transition. The
transaction changes only the exact authorized enrollment and resulting bridge
configuration fingerprint, preserving original physical attestation dates and
all unrelated deployment evidence. Startup resolves an authenticated interrupted
transition before loading bridge mappings; unexplained drift or tampered
transition evidence fails closed. This MODERNIZE preserves the release-bound
readiness contract across enrollment, restart and later guarded maintenance.

## Constitutional disposition

- **PRESERVE:** encrypted append-only truth, deterministic replay, exact adult
  privacy and consent, existing provisional enrollment and bridge transport,
  per-member edge processes, connector authorization, observation mode, one
  Python runtime, fifteen projections, one pinned OpenClaw Gateway, and
  provider-effect accounting.
- **MODERNIZE:** Settings now completes package preparation and owner controls;
  the target helper provides reviewed document-opening package acceptance,
  signed progress, independent capability states, restart recovery, and local
  cleanup. Installed release identity uses the deployment-bound source marker,
  and the completed helper bundle is sealed and verified before distribution.
- **SUPERSEDE:** operator-only enrollment, terminal/YAML/LaunchAgent editing by
  members, pairing-as-provider-authority, Tailscale-as-consent, all-pack
  activation, secret-bearing Settings payloads, and ambiguous lifecycle state.
- **DEFER:** G10 Apple Messages observation, G11 Messages reply, deployment,
  family-Mac enrollment, Apple permissions/accounts, live observation, write
  enablement, provider effects, and unrelated runtime migration or repair.

## Evidence

`tests/acceptance/test_self_service_member_bridge_enrollment.py` exercises the
production composition with two adults, package generation/transfer/opening,
target install and LaunchAgent fixture, signed progress, exact attestation,
partial success, restart/resume, pause/resume, expiry/regeneration, abuse,
revocation, isolation, Git-free installed release handoffs, failed source proof,
and zero provider effects. `tests/unit/test_bridge_release_identity.py` covers
binding precedence, unsafe markers, helper/installer agreement, and the retained
development-checkout identity path.
`tests/unit/bootstrap/test_member_bridge_helper_app.py` verifies the compiled
document handler, portable exact-release archive, installer binding, resumable
native per-capability workflow, protected secret transport, and absence of
updater, remote-shell, arbitrary-code, TCC bypass, and write authority. Existing
bootstrap, bridge-ingest, Calendar, Reminders, Settings, replay, browser and
clean-macOS coverage remains required when those contracts change. Final
validation and protected aggregates select the actual affected scope under
AC-21; unchanged central behavior can be reused for a compatible helper release.

`tests/acceptance/test_member_bridge_installer_runtime.py` adds the actual
packaged dependency-installation boundary in isolated hosted macOS: a Git-free
archive, paths containing spaces, fresh tooling and dependency caches, a real
main-only bridge environment, production preview, and recovery of an interrupted
dependency installation. Both setup-python and actual Homebrew interpreters are
covered. An older Apple linker with a newer SDK must reproduce the specific
architecture-parser failure; the matching SDK must then support the real
packaged installation and recovery. Compiler, linker and resolved SDK evidence
distinguish these cases. FileVault and Tailscale host boundaries are synthetic;
the setup-python case also substitutes only the Homebrew interpreter lookup.
No member device or Apple source is used as a test target. The
explicit installer job rejects empty or skipped coverage. Its exact success is
required by the protected macOS aggregate for a helper component or full-system
release. The aggregate fails explicitly if required evidence fails, is
cancelled, skipped or missing. Combined component/runtime packaging requires
its additional compatibility and actual installation proof.

`tests/acceptance/test_cos_member_bridge_acquisition.py` covers the explicit
read-only source exception through that same production composition with the
retained dataset and CoS overlay present. Its policy unit tests exercise exact
request/device/capability boundaries; edge command tests ensure the sealed
read-only ceiling cannot be lost when installing the member daemon.

`tests/acceptance/test_member_bridge_historical_attribution.py` proves immutable
source ownership and refuses missing, future, contradictory or cross-member
history. `tests/acceptance/test_bridge_readiness_transition.py` covers the guarded
index/readiness transaction, exact restart recovery and refusal of unrelated
configuration drift. These remain isolated hosted proofs, not live enrollment
or member-device permission evidence.


### Contained EventKit runtime repair (2026-10-22)

MODERNIZE the Apple Reminders native color boundary: register Quartz before
reading the EventKit color and use its explicit component count. Unsized PyObjC
arrays must never be enumerated. Isolated macOS acceptance exercises real Quartz
colors through the production list reader, without querying or writing a store.

PRESERVE the existing enrollment, source configuration, proof key, cursor state,
private ownership and read-only ceiling during a contained runtime repair.
`install-member-bridge.sh --prepare-runtime-only` prepares a new immutable release
and dependencies without consuming a handoff or starting a service. The prepared
release's `bootstrap.member_bridge_runtime_repair` command requires the exact
stopped, disabled predecessor and an unchanged edge/dependency/state interface.
It switches the launch definition and release pointer under the existing helper
and launch locks, observes one stable process for 30 seconds, and records the
original enrollment release separately from the new execution release.

The operation retains private predecessor snapshots and transition history.
Known failures roll back to the original **stopped** state. The same prepared
command with `--rollback-operation <operation-id> --expected-candidate <commit>
--bridge-root <root>` restores that state after a later regression. Unknown
launch outcomes retain the selected candidate and the existing recovery block;
they never authorize a second start. Original receipts, configuration and events
are not replaced by a new enrollment. A successful process start is not source
acceptance: require actual permitted Calendar, Reminders and Notes acquisition
through the member surface after activation. Provider-write tests remain separate.

The bounded `bridge-runtime` PR scope checks the exact repair, installer, native
color reader, bridge integration, privacy/authorization and launch/recovery
contracts. Unlisted paths, dependency changes and mode changes select the system
gate. Native installation tests now package the candidate runtime, because a
color-reader change cannot honestly claim the retained runtime is unchanged.

### Selected Reminders acquisition repair (2026-10-22)

PRESERVE the enrolled read ceiling before native acquisition and all source
emissions. The Reminders bridge resolves only approved list identifiers and
fetches their reminders, so an unrelated list cannot abort the permitted sync
with a central container denial. Unselected list metadata and reminder content
stay on the member device. The central admission rules remain unchanged.

The contained runtime repair also supports a successor to an already repaired
runtime while preserving the original enrollment identity. Only the reviewed
Reminders adapter and native shim may differ; source configuration, dependency,
credential, history and all other edge interfaces remain fixed. Both the original
and already-deployed CoS schema overlay are recognized for the predecessor.
The same preparation, stopped replacement, settlement and exact-operation
rollback contract applies. An intentionally empty selected calendar is not
proof of a read failure; report zero records without inventing a provider write.
