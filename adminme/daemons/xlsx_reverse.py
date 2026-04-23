"""
xlsx reverse daemon — runs the xlsx reverse projector.

Implemented in prompt 07 per ADMINISTRATEME_BUILD.md §3.11 and SYSTEM_INVARIANTS.md §10.

Watches the workbook via `watchdog` and drives
`adminme.projections.xlsx_workbooks.reverse` with a 2s debounce (§10
invariant 5). Skips cycles when the forward lock is held (§10 invariant 7).

Path resolved via `adminme.lib.instance_config` (never hardcoded per
§15/D15).

Do not implement in this scaffolding prompt. Prompt 07 will fill in.
"""
