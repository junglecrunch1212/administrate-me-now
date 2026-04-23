"""
Authority gate + action_gates — the governance layer of guardedWrite.

Implemented in prompt 08 per ADMINISTRATEME_BUILD.md §AUTHORITY and SYSTEM_INVARIANTS.md §6.

Governance gate values: `allow` / `review` / `deny` / `hard_refuse`.

Key rules:
- `hard_refuse` items (send_as_principal, auto-answer unknown coparent,
  reference privileged medical/legal in outbound) are NEVER overridable
  (§6 invariant 7).
- `review` emits a `review_request` event and returns 202 `held_for_review`;
  the action executes later only after explicit operator approval
  (§6 invariant 8).
- This is the second layer in guardedWrite's strict order: agent allowlist →
  governance action_gate → sliding-window rate limit. First layer to refuse
  short-circuits and records which layer refused on the denial event
  (§6 invariants 5-6).

OpenClaw's approval gates (tool-execution boundary, host-local) and this
AdministrateMe governance gate (HTTP API boundary) are INDEPENDENT gates —
both must pass; neither substitutes for the other (§8 invariant 7).

Do not implement in this scaffolding prompt. Prompt 08 will fill in.
"""
