"""
Observation mode — the final-outbound-filter wrapper.

Implemented in prompt 08 per ADMINISTRATEME_BUILD.md §OBSERVATION and SYSTEM_INVARIANTS.md §6.

Observation mode is enforced at the FINAL OUTBOUND FILTER — not at the policy
layer and not at the action-decision layer (§6 invariant 13). All internal
logic runs normally; only the external side effect is suppressed.

Key rules:
- Every outbound-capable action (L5 surfaces, L4 pipelines, L1 adapters that
  can send) calls `outbound(ctx, actionFn)`. Emitting `external.sent` anywhere
  else is a bug (§6 invariant 14).
- Observation mode is PER-TENANT, not per-agent or per-skill (§6 invariant 15).
- DEFAULT-ON for new instances (§6 invariant 16, §11 invariant 4). Suppressed
  actions emit `observation.suppressed` with the full would-have-sent payload.
- Does NOT gate the xlsx forward projector — the workbook is a purely local
  artifact (DECISIONS.md §D5); forward writes emit `xlsx.regenerated` with
  `observation_mode_active` on the payload so the observation-review pane
  can show which workbook writes happened during the observation period.

Do not implement in this scaffolding prompt. Prompt 08 will fill in.
"""
