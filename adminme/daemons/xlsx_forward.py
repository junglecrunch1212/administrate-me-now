"""
xlsx forward daemon — runs the xlsx forward projector.

Implemented in prompt 07 per ADMINISTRATEME_BUILD.md §3.11 and SYSTEM_INVARIANTS.md §10.

Subscribes to the event bus and drives
`adminme.projections.xlsx_workbooks.forward` with a 5s debounce (§10
invariant 5). Runs unconditionally regardless of observation mode
(DECISIONS.md §D5).

Path resolved via `adminme.lib.instance_config` (never hardcoded per
§15/D15).

Do not implement in this scaffolding prompt. Prompt 07 will fill in.
"""
