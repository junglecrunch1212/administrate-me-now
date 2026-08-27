# ADR-0005 — Member Settings and operational control-plane boundary

Status: Accepted, 2026-08-07

Scope: CO-004

## Decision

AdministrateMe owns a narrow deterministic Settings control plane inside the
one central Python runtime. It composes, but does not duplicate, identity,
connection, member-experience, bridge, privacy, observation, backup, and
runtime evidence.

Operational desired state is not household truth and is not a projection. A
secret-free, atomic, revisioned journal may retain pause state, bounded member
preferences, and bridge enrollment requests. Typed append-only events record
accepted transitions. Member experience overrides feed existing authorized
surface composition; adapter pause/resume feeds the existing supervisor.

Connection status is a product contract with four axes—setup, runtime,
permission, and safety. A manifest, config row, credential reference, provider
handshake, cursor, policy, and observation mode are different evidence. No
single state may collapse them or promote missing runtime evidence to healthy.

Credentials remain with macOS Keychain, 1Password, the provider OAuth grant,
or OpenClaw when OpenClaw owns the provider. The control plane sees only a
boolean/redacted receipt and the reviewed capability enabled on the adapter
instance. It may label a provider-native scope as granted only when the adapter
returns proof of that grant. It does not return raw tokens or secure-reference
paths.

OpenClaw continues to own channels, generic conversation, model credentials,
model routing, sessions, scheduling, generic tool approval, device nodes, and
runtime administration. Settings may show redacted OpenClaw health and a
handoff; it may not expose the operator console, model picker, provider-key
editor, or another model router.

Per-member bridges remain separate edge processes. The central control plane
may request and display enrollment, but activation still requires target
member consent, Tailscale identity verification, OS permission verification,
owner-scoped evidence, and bootstrap-material revocation. Adult private-source
consent is self-owned; principals may govern child bridges only.

## Consequences

- The authenticated identity is visible throughout the member shell; view-as
  is explicit and cannot authorize Settings mutations.
- A shared connection registry becomes the status source for Settings and the
  affected member surfaces, replacing duplicated account heuristics over time.
- The adapter supervisor gains an atomic reconcile seam and a secret-free
  inventory that combines supervisor and adapter-native evidence.
- Profiles and personas remain validated data. Fine tuning is range-constrained
  and deterministic; an LLM cannot select or infer it.
- Exactly fifteen projections (thirteen domain truth projections plus Triage
  and the non-authoritative Context Fabric) and the one-runtime topology remain
  unchanged.
- AC-31 owns process, isolation, redaction, reconciliation, and browser proof.
