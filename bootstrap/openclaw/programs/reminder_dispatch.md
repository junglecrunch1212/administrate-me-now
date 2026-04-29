# Standing order: reminder_dispatch

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

**STUB**: This program file ships as a metadata stub in prompt 10c-i.
Full execution steps are filled in by prompt 10c-iii alongside the
`reminder_dispatch` pipeline pack.

## Scope

Every 15 minutes per [BUILD.md §arch §5]. Queries commitments / recurrences /
tasks due within their lead-time window; emits `reminder.surfaceable`
events (observation-mode aware via the `outbound()` wrapper for any
external dispatch).

## Triggers

Scheduled (cron). Default schedule string `*/15 * * * *`.

## Approval gate

TODO(prompt-10c-iii).

## Escalation

TODO(prompt-10c-iii).

## Execution steps

TODO(prompt-10c-iii): full execution steps shipped with reminder_dispatch
pipeline pack.

## What NOT to do

TODO(prompt-10c-iii).
