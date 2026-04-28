# Standing order: morning_digest

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

**STUB**: This program file ships as a metadata stub in prompt 10c-i.
Full execution steps are filled in by prompt 10c-ii alongside the
`morning_digest` pipeline pack.

## Scope

Per-member morning digest delivered around the member's wake time
(default 06:30 local in member's timezone per [BUILD.md §1202]). Gathers
today's calendar events (respecting privacy filtering), due commitments
+ tasks, due recurrences, overnight inbox count, streak status, reward
stats. Validation-guarded: every claimed calendar event / commitment /
task id is verified against projections post-composition; any
fabrication zeroes the message with sentinel "No morning brief
available; underlying data changed."

## Triggers

Scheduled per member (cron). The cron entry in
`bootstrap/openclaw/cron.yaml` ships with placeholder schedule
`0 7 * * *`; bootstrap §8 substitutes per-member configured wake times
at install time.

## Approval gate

TODO(prompt-10c-ii): document approval gate per [§6.5-6.8] and OpenClaw
`exec-approvals` interaction.

## Escalation

TODO(prompt-10c-ii).

## Execution steps

TODO(prompt-10c-ii): full execution steps shipped with morning_digest
pipeline pack.

## What NOT to do

TODO(prompt-10c-ii).
