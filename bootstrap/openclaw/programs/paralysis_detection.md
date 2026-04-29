# Standing order: paralysis_detection

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

**STUB**: This program file ships as a metadata stub in prompt 10c-i.
Full execution steps are filled in by prompt 10c-ii alongside the
`paralysis_detection` pipeline pack.

## Scope

Per ADHD-profile member at configurable times (default 15:00 and 17:00
local per [BUILD.md §1216]). Pre-conditions: no completions in prior 2
hours, `adminme_energy_states.level <= low`, currently within the
member's fog window. Deterministic — never invokes LLM. Picks a template
from persona's `paralysis_templates.yaml`. Emits
`adminme.paralysis.triggered` with template id + single-action framing.

## Triggers

Scheduled per ADHD-profile member (cron). Default schedule string
`0 15,17 * * *` is placeholder; bootstrap §8 may override per
ADHD-profile member configuration at install time.

## Approval gate

TODO(prompt-10c-ii).

## Escalation

TODO(prompt-10c-ii).

## Execution steps

TODO(prompt-10c-ii): full execution steps shipped with paralysis_detection
pipeline pack.

## What NOT to do

TODO(prompt-10c-ii).
