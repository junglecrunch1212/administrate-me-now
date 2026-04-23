"""
xlsx forward projector — event-driven workbook regeneration.

Implemented in prompt 07 per ADMINISTRATEME_BUILD.md §3.11 and SYSTEM_INVARIANTS.md §10.

Subscribes to forward-trigger event families (tasks, recurrences, commitments,
parties, list_items, money_flows, accounts, assumptions, plaid.sync, etc.) and
debounces 5s on bursts before regenerating the workbook.

Key rules:
- Writes computed values, not Excel formulas, for reproducibility + audit +
  round-trip safety (§10 invariant 6).
- Writes sidecar `.xlsx-state/<workbook>/<sheet>.json` in the same lock as
  the xlsx write so the reverse daemon can tell a user edit from a
  forward-regeneration (§10 invariant 2).
- Runs UNCONDITIONALLY regardless of observation mode (DECISIONS.md §D5) —
  the workbook is a purely local artifact. Every forward write emits
  `xlsx.regenerated` with `observation_mode_active: true|false` on the payload.
- Path resolved via `adminme.lib.instance_config` (never hardcoded per §15/D15).

Do not implement in this scaffolding prompt. Prompt 07 will fill in.
"""
