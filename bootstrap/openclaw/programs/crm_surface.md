# Standing order: crm_surface

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

**STUB**: This program file ships as a metadata stub in prompt 10c-i.
Full execution steps are filled in by prompt 10c-iii alongside the
`crm_surface` pipeline pack.

## Scope

Weekly + on-demand per [BUILD.md §1238-1245]. For each active Party,
compute contact gap (days since last meaningful interaction); if gap >
desired_contact_frequency, emit `crm.gap_detected`. Upcoming birthdays
(next 14 days) emit `crm.birthday_upcoming`. Hosting balance asymmetry
above threshold emits `crm.hosting_imbalance`. Surfaces in inbox.

## Triggers

Scheduled weekly (cron). Default schedule string `0 19 * * 0` — Sunday
19:00 local per the placeholder convention.

## Approval gate

TODO(prompt-10c-iii).

## Escalation

TODO(prompt-10c-iii).

## Execution steps

TODO(prompt-10c-iii): full execution steps shipped with crm_surface
pipeline pack.

## What NOT to do

TODO(prompt-10c-iii).
