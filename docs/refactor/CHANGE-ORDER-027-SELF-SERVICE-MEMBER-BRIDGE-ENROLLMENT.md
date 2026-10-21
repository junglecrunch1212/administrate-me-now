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

Installation failures must remain diagnosable after a native dialog exits.
The helper records a private, content-free phase and terminal exit receipt;
dependency preparation retains a bounded private log before transfer-package
inspection. Dependency progress is separate from the safe enrollment preview.
Transfer codes and later enrollment output remain temporary and are deleted
on exit. A phase receipt is diagnostic evidence, never acceptance, permission,
source attestation or proof that installation completed. This MODERNIZE preserves
the existing enrollment and write-authorization gates.

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
bootstrap, bridge-ingest, Calendar, Reminders, Settings, replay, browser,
clean-macOS rehearsal, and aggregate release gates remain required.

`tests/acceptance/test_member_bridge_installer_runtime.py` adds the actual
packaged dependency-installation boundary in isolated hosted macOS: a Git-free
archive, paths containing spaces, fresh tooling and dependency caches, a real
main-only bridge environment, production preview, and recovery of an interrupted
dependency installation. Only FileVault, Tailscale and Homebrew host boundaries
are synthetic; no member device or Apple source is used as a test target. The
explicit installer job rejects empty or skipped coverage. Its exact success is
required by the existing protected macOS release gate, which runs and fails
explicitly if the prerequisite fails, is cancelled or is missing.

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
