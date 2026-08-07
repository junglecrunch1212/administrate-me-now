# Change Order 004 — Settings, identity, and connection control

Status: **APPROVED — IMPLEMENTED; AC-31 AND RELEASE CANDIDATE PASS**

Proposed: 2026-08-07

Approved: 2026-08-07

Governing decisions: ADR-0000, ADR-0001, ADR-0005, CO-001–CO-003

## Outcome

Settings becomes AdministrateMe's deterministic member control plane. It
externalizes configuration, consent, runtime state, and recovery knowledge so
a member does not have to remember which identity is active, which provider is
configured, whether it is actually running, what it may read or write, whether
observation will suppress an action, or where a device bridge belongs.

Settings is not an OpenClaw operator console, a model picker, a terminal, a
secret editor, a second source of household truth, or a fifteenth projection.
The one Python runtime composes trusted session identity, reviewed config,
live adapter evidence, extension state, bridge enrollment state, observation,
workbooks, and runtime diagnostics.

## Replaced failure modes

The previous page rendered a generic capability list, two workbook rows, and
chat status. It silently truncated the capability list after twenty rows. An
enabled adapter manifest was labeled `observation_only` even when the provider
runtime remained `starting`; installed profiles and personas were labeled
healthy even when they were merely validated or active. The member shell used
the first character of an internal id and did not identify the authenticated
member or ingress method. There were no Settings commands, provider controls,
bridge workflow, privacy explanation, or deterministic fine tuning.

CO-004 supersedes that behavior with seven URL-backed sections: Overview, My
account, CoS behavior, Connections, Bridges & devices, Household & privacy,
and Safety & system.

## Truth model

Every connection reports independent axes:

1. **Setup:** setup incomplete, configured, connected, paused, disabled, or
   disconnected. Connected requires live handshake evidence; a stored reference
   alone yields configured.
2. **Runtime:** unknown, starting, running, stale, backoff, crashed, stopped, or
   not running, derived from the supervisor and adapter rather than manifest
   presence.
3. **Permission:** reviewed read/write capability enabled for this adapter
   instance, plus whether a secure credential reference exists; no reference
   value is returned. A provider-native granted scope is shown only when the
   adapter can prove it.
4. **Safety:** observation effect, member human gate, independent OpenClaw
   approval, and whether writes may execute after all gates.

Configured never means authenticated or healthy. Healthy is never inferred
from schema support. Unknown remains unknown. Optional providers without a
reviewed adapter remain visibly unavailable instead of receiving a Connect
button.

## Commands and state

The secret-free `settings-control.json` journal holds only operational desired
state: bounded member preferences, per-instance pause state, and bridge
enrollment requests. It is atomic, versioned, revision-checked, and not a
household domain projection. Every accepted mutation writes a typed private
audit receipt.

- Profile activation uses the existing stage → test → activate extension
  lifecycle and proves no authority or model-routing change.
- Fine tuning accepts only bounded integers/booleans and immediately feeds the
  existing member experience semantics used by Today and other surfaces.
- Pause/resume constructs the complete candidate adapter set before touching a
  running instance, then reconciles the real supervisor. A failed candidate
  restores prior effective configuration.
- Add Bridge creates a scoped, resumable enrollment request. No token or
  transfer package is returned by the portal. Reviewed package export,
  target-device consent, Tailscale/OS-permission verification, owner-scoped
  ingest proof, finalization, and bootstrap-material revocation remain the
  existing bridge enrollment ceremony.

OAuth/token enrollment remains a secure provider-specific activation step.
CO-004 does not put secrets or raw secure-reference paths in HTTP, events,
logs, screenshots, or diagnostics.

## Authorization

- The shell shows display name, role, Tailscale authentication method, read
  member, and action member. Internal ids are advanced diagnostic data.
- View-as is conspicuous and read-only for Settings; actions remain attributed
  to `authMember`.
- An adult controls their own private providers, bridge, profile, and
  preferences.
- A principal may request a child bridge but cannot enroll another adult's
  private device or inspect that adult's credentials.
- Provider-native sharing never grants AdministrateMe access.
- Hard privacy, authorization, observation, and governance rules are not
  toggles.

## Classification

- **PRESERVE:** Tailscale trusted ingress, `authMember`/`viewMember`, provider
  authority, secret references, append-only audit, observation, exactly
  fourteen projections, one Python runtime, one pinned OpenClaw Gateway, and
  separate per-member bridges.
- **MODERNIZE:** static diagnostics into a member control plane; truthful
  connection state; profile/fine tuning; bridge request and consent UX.
- **SUPERSEDE:** opaque initial avatar, raw/truncated capability dump,
  configuration-equals-health, one overloaded status, broad operator controls,
  arbitrary pack URL installation, and model-decided preferences.
- **DEFER:** unimplemented optional provider adapters and live OAuth/OS consent
  for accounts not yet enrolled. The Settings product, identity, status,
  authorization, runtime reconciliation, and AC-31 proof are not deferred.

## Acceptance

AC-31 proves the production composition identifies the member, preserves
view-as denial and cross-adult isolation, exposes no credential reference,
distinguishes configured from runtime state, activates validated profiles,
propagates bounded fine tuning, records a scoped child bridge request,
reconciles pause state through the actual supervisor, rejects stale revisions,
emits typed receipts, retains fourteen projections, and renders the responsive
seven-section portal without browser errors.
