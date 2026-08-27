# CO-019 Central Host topology-readiness evidence and Phase B packet

Date: 2026-08-26

Phase A baseline: protected `main`
`d44f77060263eab0cd459659f47bf0faeca1f1c4`

Live baseline observed read-only: immutable active release
`7ed10ac599e08dc1e616f6b9c3f0ab3b11dfb9b7`

This record is deliberately content-free. It contains no household names,
login names, email addresses, hostnames, domains, phone numbers, Apple
identifiers, provider tokens, recovery details, or message content. It records
only counts, booleans, classifications, reviewed constant labels, and Git
commits. Phase 0 did not write to the family host, provider, Tailscale,
Keychain, permission database, account, bridge, or event log.

## Phase 0 read-only reconciliation

The observation used process/listener inspection, Tailscale status/Serve
status, macOS account/group and security posture, immutable release metadata,
content-free runtime/Doctor/Settings diagnostics, adapter configuration shape,
bridge inventory, OpenClaw health, backup inventory, and source-assurance
aggregates. Browser access was authenticated and read-only. No event payload or
provider content was opened.

| Dimension | Intended topology | Read-only finding | Phase 0 classification | Phase A correction | Remaining human gate |
|---|---|---|---|---|---|
| Central processes | One Python runtime and one pinned OpenClaw Gateway | One of each observed | MATCH | Singleton counts enter readiness | Re-prove after switch |
| Network edge | Loopback runtime; Tailscale Serve sole private HTTPS edge | One Serve edge to loopback observed | MATCH | Read-only exact predicate; Funnel must be off | Re-prove after host identity correction |
| Central Tailscale identity | Tagged infrastructure node, never member | Node online but untagged; owner coordinate collides with a member login | BLOCKED | Required tag and member-collision predicate | Convert through reviewed Tailscale admin change; verify ACL/Serve and member denial |
| Member ingress | Two distinct exact member logins traverse trusted edge | One member path observed; second path not proved | UNKNOWN / BLOCKED | Exact role/identity uniqueness and content-free acceptance receipt | Test both principals independently from their own sessions |
| macOS service account | Dedicated Standard user plus separate tested recovery admin | Runtime user is in administrator group; another admin exists; FileVault on and autologin off, but unlock/recovery unproved | BLOCKED | Preflight rejects admin service user; readiness requires recent recovery evidence | Migrate only through reviewed account/runbook gate and physically prove unlock/recovery |
| Provider ownership | Each enabled account has exact owner, account, workspace, scopes, provenance, attestation | Three enabled central rows; one explicit personal workspace, two workspace omissions; ownership/scope/provenance evidence absent | BLOCKED | Global fail-closed binding validator; no routing defaults | Review each account in provider UI and record protected config evidence |
| Member bridges | Optional, exact active enrollment only | Zero enrolled bridges | NOT SELECTED | No-enrollment resolver denies; health reports endpoint separately | None unless a member separately authorizes a bridge |
| OpenClaw member topology | Unique member agent/account/actor bindings and account-aware session scope | Gateway healthy; two chat-enabled members; zero actor bindings in observed report | BLOCKED | Exact topology reconciliation and plugin health contract | Reconcile in candidate and test both real member paths |
| Observation | Active; writes suppressed and terminally audited | Active; trusted false | MATCH | Strict readiness keeps writes denied | Keep active through trust review |
| Historical source settlement | Every obligation terminal | 65,719 obligations, zero missing/pending, `content_exposed:false` | MATCH for source settlement | Existing assurance retained | Revalidate at activation watermark |
| Historical provider effects | Zero true or unknown provider calls with exact terminals/correlations | Existing aggregate could not answer | UNKNOWN / BLOCKED | Metadata-only effect accounting added | Candidate must recompute zero true/unknown before switch |
| Backup/restore/rollback | Authenticated encrypted backup, disposable restore, retained predecessor, automatic rollback eligible | Recent backup and retained releases observed; authentication/restore not proved | UNKNOWN / BLOCKED | Shared readiness requires recent evidence | Authenticate backup and rehearse restore before switch |
| Settings/Doctor | Same readiness truth as runtime | Existing surfaces could promote weaker evidence | BLOCKED | One `deployment-readiness-v1` object | Verify exact agreement in browser after candidate start |
| CoS Apple service identity | Separate non-human channel identity, no member Apple access | No account or channel was claimed or activated | PLANNED / SAFE | Generic principal/channel/routing schemas; built-in `planned` state | All Apple/channel gates remain separately unauthorized |

The old Doctor result is not activation evidence because its test set lacked
the blocked dimensions above. A healthy process remains useful service
liveness, not corrected deployment readiness.

## Phase A implementation evidence

### Fail-closed production composition

- Portal identity resolution requires one exact case-fold-unique configured
  login and explicit role. Unknown view targets and the Central Host identity
  deny.
- An empty bridge-enrollment registry installs a deny-all resolver. Health
  distinguishes route availability from enrollment/readiness.
- Strict production settings sample the Central Host without exposing the node
  owner, hostname, member login, or address. A tag, singleton processes,
  Serve-only edge, Standard service account, and no member collision are
  required.
- Provider bindings are validated globally before adapter construction.
  Missing owner, provider account, workspace, permission scope, provenance, or
  ownership attestation denies startup. Duplicate/cross-owner coordinates
  deny. Settings has no `personal`, shared-household, or first-sorted-identity
  fallback.
- The OpenClaw reconciliation retains unrelated agents while requiring one
  unique AdministrateMe member agent and exact account/actor bindings per
  enabled member. Readiness compares the plugin heartbeat with the current
  OpenClaw agents, bindings, plugin topology, and DM-scope configuration.
- Runtime, readiness endpoint, Settings, and Doctor share one secret-safe
  readiness object. Safe reads remain separate from activation and writes.
- Source assurance now reports terminal effect counts including true, false,
  unknown, missing, duplicate, malformed, orphan, and approval-correlation
  results. Unknown or structurally ambiguous results block durable integrity;
  the current attested observation window separately requires zero provider
  calls and zero unsuppressed/unknown outcomes. An authorized later live call
  remains audited without permanently poisoning integrity.
- Historical attribution reads only envelope metadata and emits keyed stable
  aggregates. It never returns content or raw provider/member identifiers and
  reports `content_exposed:false`.
- Physical recovery and backup claims enter readiness only through the
  owner-only content-free attestation writer after the operator has actually
  performed every check. The receipt is bound to the release, host, instance,
  and protected routing configuration and starts a fresh observation-effect
  window. The writer itself grants nothing and changes no provider or
  permission.

### Dedicated CoS Apple future path

The current built-in catalog exposes only the generic label **CoS Apple service
identity**. It is `planned`, has no account or Endpoint reference, is not
enrolled, cannot send, and is not a prerequisite for the existing central
runtime. Exact future configuration may use only encrypted references shaped
like `cos-apple-account:<stable-hash>`,
`cos-channel-account:<stable-hash>`, or
`provider-account:<stable-hash>` outside public reports.

The v5 communication schemas preserve exactly one human member or service
principal owner and an explicit route. Member owners can route only to their
own private scope. Service owners may route to one reviewed member-private,
household-shared, or external-Party scope; ambiguity remains unresolved and is
not member-visible. Payload routes are cross-checked against envelope owner,
visibility, and provider-account metadata before append. The v5 sent fact can
represent a future service-owned observation and cannot impersonate a member,
but the current request/approval/dispatcher spine remains member-only and
rejects `sending_enabled:true`. This is a data/readiness boundary, not a
working Apple, iMessage, BlueBubbles, or Twilio adapter.

## Human Task Register

Every row below requires a new, explicit owner request after the Phase A PR is
merged. Rows are not authorized by this document and must not be combined with
an unrelated deployment request. Store exact identities and evidence only in
the encrypted instance configuration or the relevant provider/admin console.

The executable dependency order is: HTR-06 before HTR-02, using a separate
authenticated pre-migration safety backup; HTR-01 before HTR-03; HTR-04 before
HTR-10; then final HTR-07/configuration, the preliminary HTR-05 rehearsal under
the final service-account boundary, and HTR-08 as the last pre-switch
attestation. HTR-09 must rehearse its own exact fresh artifact and establish
rollback eligibility before any active selector mutation. A later relevant
configuration change invalidates and repeats HTR-05 and HTR-08 as applicable.

| ID | Human-owned action | Prerequisites | Completion evidence | Abort condition | Recovery |
|---|---|---|---|---|---|
| HTR-01 | Decide and apply the dedicated Central Host Tailscale service/tag identity | Merged release; reviewed ACL/tagOwner/Serve plan; console access from a separate admin session | Read-only sample says tagged infrastructure, online, no member collision, one Serve edge; member API denies host identity | ACL ambiguity, loss of admin path, Serve widens, Funnel enables, or any member mapping | Revert only the bounded Tailscale policy/node change from the separate admin session; do not activate release |
| HTR-02 | Migrate the runtime from the current administrator account to a dedicated Standard macOS account | HTR-06 complete; independently authenticated pre-migration safety backup; retained release; reviewed paths/ownership/LaunchAgent plan | Standard group check; exact paths and LaunchAgents owned correctly; FileVault unlock after reboot; autologin off; service restarts | Missing admin/recovery path, Keychain item inaccessible, permission widening, path mismatch, or service fails before switch | Restore prior LaunchAgent/account ownership using the separate admin and retained evidence; keep old release pointer |
| HTR-03 | Perform real Principal A and Principal B trusted-ingress acceptance independently | Correct tagged host; exact protected member mapping; two separate signed-in Tailscale sessions | Two accepted member IDs, distinct-identity boolean true, unknown/cross-view denial, recent timestamp; no raw login in report | Either member maps incorrectly, one session can view the other without authorized view-as, or host authenticates as member | Stop; restore last reviewed mapping/ACL; do not attest |
| HTR-04 | Review each enabled provider account's owner, exact account, workspace, permission scope, provenance, and ownership | Provider console access; current adapter inventory; no permission expansion | Protected adapter config validates globally; public inventory counts only; duplicate/cross-owner checks pass | Account ownership unclear, account shared between owner principals, workspace unknown, or requested scopes exceed adapter need | Leave adapter disabled; revoke only a grant explicitly authorized for revocation |
| HTR-05 | Authenticate an encrypted backup and complete the two-stage recovery proof | Candidate SHA fixed; exact candidate controller; retained predecessor; final service-account/key boundary | **Pre-switch prerequisite:** a mode-0600 `backup-rehearsal-v1` receipt records successful completion of the candidate's activity-lock-owning guardian path with distinct extraction/replay worker PIDs, exact manifest-member authentication, fixed-watermark rebuild of all 15 projections, two workbooks and 17 sidecars, zero source append, a rejecting event sink, no constructed ServiceManager, and destroyed outside-root payload/control staging. Before plaintext staging, it binds the pointer to either its exact mode-0600 source marker or, only for a markerless retained release using the legacy compatibility path, a content-free all-tracked-file/mode Git-tree digest plus the versioned generated-runtime residue policy. A bad present marker cannot fall back. The receipt explicitly records external effects, candidate execution immutability, full predecessor immutability, live checks, and rollback eligibility as `not_evaluated`. **Activation-bound completion:** after the updater creates its fresh backup, it must rehearse that exact artifact and prove predecessor rollback compatibility after backup quiescence and before any active candidate/OpenClaw selector, plugin-registration, or release-pointer mutation; full live Doctor remains post-switch. The preliminary receipt never asserts the activation-bound evidence. | Wrong key, tamper, missing/extra/colliding file, markerless predecessor tracked-source drift or unavailable Git object, SQLCipher failure, replay/artifact failure, guardian/worker/staging leak, any live-coordinate change, or unavailable rollback evidence | Reap the guardian and active worker and destroy only the exact marked disposable payload/control stage; preserve live state, any already-written evidence, backup, and predecessor; investigate before activation |
| HTR-06 | Prove separate administrator and FileVault recovery/unlock path | Physical access, keyboard/display, recovery material available outside CoS systems | Successful reboot/unlock/login evidence; autologin remains off; CoS mailbox not sole recovery root | Any uncertainty about credentials, recovery key, enabled users, or remote-only access | Stop before logout/reboot where possible; use the existing separate admin/recovery process |
| HTR-07 | Reconcile exact OpenClaw member agents/accounts/actors and DM scope | Correct real channel account choices; observation active | Unique agents/accounts, exact member sets, `per-account-channel-peer`, current plugin health | Shared account, missing actor, extra member, stale heartbeat, or topology mismatch | Restore reviewed OpenClaw config backup; keep member chat/write admission blocked |
| HTR-08 — **OWNER-GATED / NOT EXECUTED** | Record the last pre-switch, content-free candidate deployment attestation after HTR-01 through HTR-04, HTR-06 through HTR-07, HTR-10, and preliminary HTR-05 are complete | Every confirmation actually observed; protected instance root; final OpenClaw/config coordinates; retained active predecessor; exact clean candidate absent from the recursive pointer chain; exact HTR-10 review receipt and observation-window start; exact strengthened preliminary rehearsal receipt and encrypted artifact. | Append-only mode-0600 `candidate-deployment-readiness-v1` at `app/readiness/candidate-pending/<attestation-id>.json`; candidate/predecessor/pointer-chain/host/instance/service-account/config/rehearsal/artifact/watermark fingerprints match; all prerequisite timestamps are recent and expiry is no later than the earliest 31-day bound; `candidate_active:false`, `activation_authorized:false`, later updater backup/rollback/replacement/live-Doctor/post-switch fields `not_evaluated`; no secret/content. The active `state/config/deployment-readiness.json` inode and bytes remain unchanged, and runtime/Settings/Doctor ignore the pending path. | Active predecessor writer is invoked, any confirmation is assumed, candidate is active/retained, any file/coordinate/digest changes, private ownership/mode/link safety fails, evidence is stale, or the operation lock is held | Leave the active attestation untouched. A post-link drift may retain only the immutable artifact by ID; it is unselected, authorizes nothing, and must never be consumed without full revalidation. Repeat HTR-05/HTR-08 after final configuration or evidence changes. |
| HTR-09 — **EXECUTABLY BLOCKED** | Authorize and run guarded candidate activation | HTR-01 through HTR-04, HTR-06 through HTR-08, HTR-10, and preliminary HTR-05 complete; merged immutable SHA; green checks; observation active; no separate provider/channel mutation. The future updater must verify pending HTR-08, rehearse the exact fresh artifact, establish predecessor/rollback eligibility, and durably stage mechanical plus human evidence. Under the operation lock it must consume the source-preparation record and journal every later selector/recovery transition; the source-preparation record and cooperative lock are not readiness or crash-recovery evidence. | Future guarded-updater report, exact active SHA, exact newly created backup rehearsal, predecessor compatibility, crash-recoverable switch, all readiness dimensions healthy, settlement/browser/backup/rollback/effects proof | Current updater path, or any prerequisite/readiness/Doctor/source-assurance/browser/privacy/backup/effect regression | Do not invoke the current updater; after implementation, require automatic rollback and use the documented explicit rollback only from separate admin access if necessary |
| HTR-10 | Review metadata-only historical attribution and terminal-effect integrity | HTR-04 bindings validated; fixed event watermark; no content access | Every historical source is matched or explicitly classified; terminal ledger has zero missing/duplicate/malformed/orphan/unknown/correlation failures; raw identifiers/content absent | Any row needs content inference, ownership is ambiguous, or an effect cannot be accounted | Keep activation blocked. Correct only protected binding metadata under a separate reviewed request; a legacy terminal that inherently lacks effect evidence requires a separately reviewed additive reconciliation design and cannot be attested away here. |

No member bridge is required when none is selected. If a member later requests
one, configuration sealing plus export/accept/finalize proves transport only
and starts no provider pack. The member's separate `bridge-enable-pack` action
may request the exact OS permission; ordinary provider facts and commands stay
denied until `adapter.authenticated@v2` binds enrollment, configuration digest,
provider account, permission reference, and exact scopes and the central owner
runs `bridge-attest`. Revocation remains independent. This repository proof
uses fakes only and authorizes no Apple account, permission, enrollment, or
provider effect on a real device.

## Future CoS Apple/channel authorization register

These gates are deliberately prepared but **not authorized or executed**.
They must remain serial enough that each result can be reviewed before the next
authority is granted.

| Gate | Required decision/evidence | State after a successful gate | Stop/rollback boundary |
|---|---|---|---|
| COS-APPLE-01 Account type and ownership | Decide household ownership, non-human purpose, terms/compliance, billing, administrative owner, and independent recovery roots | Still `account_not_created` | No account action until decision is recorded |
| COS-APPLE-02 Account creation and recovery | Create only after explicit request; configure at least one recovery path not rooted solely in the CoS mailbox/system | `account_created` | Revoke/secure account through Apple; do not sign in to host |
| COS-APPLE-03 Apple Business Manager/domain | Decide applicability and domain/federation effects separately | Unchanged unless reviewed configuration succeeds | Abort on member identity/domain/recovery collision; reverse only through vendor-admin plan |
| COS-APPLE-04 Dedicated channel host | Decide whether the Central Host or a distinct least-privilege Mac runs the transport | `channel_not_enrolled` | No installation before topology/privacy review |
| COS-APPLE-05 Apple sign-in and OS permissions | Explicit host and least-privilege permission approval | `channel_not_enrolled` | Sign out/revoke permissions if scope differs; never grant member source access |
| COS-APPLE-06 Reviewed iMessage transport | Select and security-review BlueBubbles or another transport; pin version; verify loopback/private edge and secret storage | `channel_not_enrolled` | Uninstall/disable reviewed transport without changing member bridges |
| COS-APPLE-07 CoS contacts/address-book scope | Define CoS-only contacts and deterministic Endpoint/Party/routing policy | `channel_not_enrolled` | Any member address-book inheritance or ambiguous default blocks enrollment |
| COS-APPLE-08 iMessage enrollment | Bind one pseudonymous account reference and one service principal; verify independent revocation | `observation` | Disable/revoke enrollment on owner/member collision or unexplained access |
| COS-APPLE-09 Observation-only receipt test | Receive a neutral synthetic CoS-addressed fact; prove exact route, provenance, isolation, no shared memory, and no provider content in diagnostics | `observation` | Unresolved is acceptable and invisible; wrong visibility disables channel |
| COS-APPLE-10 Governed service-send contract and observation-suppressed test | First add and review a generic service-owner request/approval/terminal schema through the production command spine, without provider code; then use a neutral synthetic attempt through every guard and prove `suppressed:true`, `provider_called:false`, exact approvals/correlation, and one terminal | Remains `observation`; `sending_enabled` remains false | Any provider call, member-only fallback, missing terminal, impersonation, or ambiguity disables channel and triggers audit |
| COS-APPLE-11 Explicit live-send enablement | New owner request after trust period, implemented reviewed transport, healthy backup/readiness, reviewed recipients/content/rate limits, and independent approvals | `healthy` with sending explicitly enabled | Any unexpected effect, privacy failure, or reconciliation ambiguity disables sending/channel and invokes provider-specific rollback |

Equivalent Twilio/SMS/WhatsApp or future service identities require their own
provider-specific account, recovery, grant, transport, enrollment, observation,
and send gates. A common namespace does not let one gate authorize another.

## Post-merge Phase B activation packet

This packet is **reference-only and not currently executable**. Owner
authorization is necessary but insufficient: the candidate-pending contract
exists, but no owner HTR-08 artifact has been authorized or recorded, and the
updater still cannot consume it, rehearse its exact fresh backup, or recover a
crash across replacement. After those remaining paths are implemented and
merged, this packet becomes usable only when an owner explicitly requests
deployment of the exact merged commit. Substitute only reviewed absolute paths
and the 40-character merged SHA. Do not paste credentials or identities into
shell history, issues, or reports.

### 1. Preconditions — no switch

1. From the separate administrator path, verify the candidate checkout is
   clean, on protected `main`, and exactly `APPROVED_SHA`.
2. Confirm the active release and rollback predecessor are immutable and
   retained.
3. Complete HTR-06 before HTR-02 using the separate pre-migration safety
   backup; complete HTR-01 before HTR-03 and HTR-04 before HTR-10; finish
   HTR-07/final configuration; run preliminary HTR-05; then record HTR-08 as
   the last pre-switch attestation. Do not infer a checkbox from a successful
   process-health result. The updater must complete HTR-05 against its newly
   created backup after backup quiescence and before any active candidate or
   OpenClaw selector, plugin-registration, or release-pointer mutation.
4. Keep observation active and write admission false. Do not combine provider
   enrollment, bridge enrollment, runtime migration, permission changes, or
   CoS channel work unless the owner separately authorized that exact row.
5. Authenticate the encrypted backup and run the exact-candidate non-replacing
   guardian-supervised extraction/replay rehearsal. Require its content-free
   receipt, then separately require candidate-bound rollback
   compatibility/eligibility and full live Doctor at the guarded updater
   boundaries. Never describe the offline replay check as full Doctor.
6. Run the full release contract on the exact candidate using Python 3.14 and
   Node 24. Confirm GitHub protected checks are green for the same SHA.
7. Recompute source assurance at a fixed current watermark. Require zero
   missing/pending/failed required obligations, healthy all-history terminal
   integrity, and zero true/unknown/unsuppressed effects in the attested
   observation window.

If any precondition is absent, stale, contradictory, or cannot be independently
verified, stop before invoking the updater.

### 2. Guarded switch — only with a separate owner request

From the dedicated service context, use the existing reviewed launcher:

```bash
./bootstrap/update.sh /absolute/path/to/clean-reviewed-candidate
```

If and only if the candidate changes the supported Python/Node runtime and the
owner separately authorizes that migration, use the documented migration form:

```bash
./bootstrap/update.sh --approve-runtime-migration /absolute/path/to/clean-reviewed-candidate
```

The updater must attest controller provenance, recheck the clean exact commit,
prepare side-by-side runtimes, preserve the active release, authenticate backup
evidence, keep observation, quiesce safely, switch the immutable pointer,
restart both services, and run post-switch Doctor. Its built-in failure path
must restore the predecessor and continue rollback cleanup even if one cleanup
step fails.

### 3. Bounded verification

Within the updater's settlement window, require all of the following from the
new current process:

- exact active merge SHA and exact pinned OpenClaw version/commit/plugin source;
- one Python runtime, one Gateway, loopback listeners, one Serve-only edge,
  correct infrastructure tag, and no host/member collision;
- service running as Standard user and recent recovery/backup evidence;
- both members authenticate through distinct real Tailscale paths; host,
  unknown, duplicate, and cross-member paths deny;
- all fifteen projections and required subscribers settle at the current
  watermark with deterministic replay/isolation proof;
- every enabled central provider binding and active bridge provider capability
  validates with no identifier exposed; transport-only/not-attested bridges
  remain blocked rather than false-green;
- zero selected bridges reports not applicable; any selected bridge proves one
  exact enrollment, cross-member denial, zero central Apple construction, and
  exact attestation for every enabled provider pack;
- exact OpenClaw member agents, account bindings, actors, DM scope, current
  heartbeat, and no shared session/account;
- `/api/system/ready`, runtime diagnostics, Doctor, and authenticated Settings
  show the same dimension states and blockers;
- CoS Apple service identity remains `planned`, unconfigured, unenrolled, and
  unable to send;
- authenticated browser acceptance for changed Settings/readiness behavior at
  desktop and narrow width, with no content, identifier, or secret exposure;
- observation remains active; write admission remains false; historical and
  new terminal accounting reports zero provider calls and zero unknowns;
- backup remains authenticated and predecessor remains rollback-eligible.

### 4. Abort conditions

Abort/roll back immediately on any new or hidden degradation, including:

- dirty/wrong candidate, pin/provenance mismatch, or unexpected runtime;
- host/member identity collision, missing member path, widened network edge,
  Funnel, or an untrusted identity header accepted;
- admin service account, missing recovery path, Keychain/permission drift, or
  inability to restart after FileVault unlock;
- provider ownership/workspace/scope/provenance ambiguity;
- bridge fallback without active enrollment;
- OpenClaw agent/account/actor/session mismatch or stale heartbeat;
- Settings/readiness disagreement, missing dimension, source-settlement drift,
  replay/privacy/tenant-isolation failure;
- backup authentication/restore failure or missing rollback predecessor;
- any provider-called true/unknown or unsuppressed result in the attested
  observation window, missing/duplicate terminal, missing approval
  correlation, or observation disabled;
- any CoS Apple account, endpoint, enrollment, authentication, permission, or
  health claim appearing without its separate gate.

Do not repair the installed release, active virtual environment, LaunchAgent,
live encrypted state, provider config, Keychain, or permissions in place to
make activation pass.

### 5. Rollback

The guarded updater should roll back automatically. If an explicit owner
request is required after a failed or later degraded switch, use the retained
reviewed command:

```bash
python -m bootstrap.cli rollback
```

After rollback, verify the active pointer and processes belong to the retained
predecessor, loopback/Serve boundaries are restored, the encrypted log remains
append-only and readable, observation is still active, provider-effect counts
remain zero/known, and the candidate failure is retained content-free. Do not
delete the candidate, prior release, backup, rollback state, or failure
evidence until the owner has reviewed the incident. A rollback does not undo a
separately performed external provider/account/permission mutation; those use
their own provider-specific revocation plan.

## Phase A verification record

Populate this table on the PR head. A result is final only when the exact PR
commit is recorded and the required protected GitHub check is green.

| Gate | Result |
|---|---|
| Python full suite | PENDING on the exact PR commit |
| Acceptance with `--runxfail` | PENDING on the exact PR commit |
| Ruff / format / Pyright | PENDING on the exact PR commit |
| Mypy | PENDING on the exact PR commit |
| Invariant/identity/package scans | PENDING on the exact PR commit |
| Node 24 install/check/test/build/pack | PENDING on the exact PR commit |
| Clean install, performance/stress, isolation, backup/restore, browser | PENDING on the exact PR commit |
| Adversarial exact-diff review | PENDING on the exact PR commit |
| Protected PR check | GitHub control-plane result; must be green before handoff |
| Live mutation/provider effect | None authorized; none performed |
