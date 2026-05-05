# Standing order: paralysis_detection

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Per ADHD-profile member at configurable times (default 15:00 and 17:00
local per [BUILD.md §1216]). Pre-conditions: no completions in prior 2
hours, `adminme_energy_states.level <= low`, currently within the
member's fog window. **Deterministic — never invokes LLM** per
[BUILD.md §1297-1302] and operating rule 20 ([BUILD.md §124]). Picks a
template from persona's `paralysis_templates.yaml`. Emits
`paralysis.triggered` with template id + single-action framing.
Surface in v1 is inbox only (the optional-outbound branch per
[BUILD.md §1302] is deferred to a future per-profile-config-aware
prompt).

## Triggers

Scheduled per ADHD-profile member (cron). Default schedule string
`0 15,17 * * *` is placeholder; bootstrap §8 may override per
ADHD-profile member configuration at install time.

## Approval gate

None. v1 has no outbound side effect — output is an inbox event and
the member surfaces it on demand. When the optional-outbound branch
[BUILD.md §1302] lands later, it will route through `outbound()`.

## Escalation

If pre-conditions fail (recent completion, energy not low, outside fog
window, profile None, persona None, paralysis_templates empty), the
pipeline defensively skips: NO event emits and no error raises per
[§7.7]. Bus checkpoint advances normally.

## Execution steps

Proactive pack at `packs/pipelines/paralysis_detection/` with
`triggers: {schedule: "0 15,17 * * *", proactive: true}` and NO
`triggers.events` — the in-process `PipelineRunner` skips it
(`runner.py:131-138`); OpenClaw cron drives invocation. Handler logic
per [BUILD.md §1297-1302]:

1. For the ADHD-profile member, check pre-conditions:
   - zero completions in prior 2 hours (tasks + commitments),
   - energy level ≤ low (from `adminme_energy_states`),
   - now within the member's fog window (read from profile).
   If any check fails, defensively skip — do not emit.
2. Load the persona pack's `paralysis_templates` (deterministic, no
   LLM call per operating rule 20).
3. Pick a template by deterministic round-robin seeded by
   `(member_id, today_iso)` so the same day produces the same
   template for the same member but different members get different
   templates.
4. Emit `paralysis.triggered` v1 with `member_id`, `template_id`,
   `template_text`, `triggered_at`. Surface is the inbox view (a
   future console prompt consumes the event).

## What NOT to do

- **Do NOT invoke an LLM.** Template selection is a deterministic
  YAML lookup per [BUILD.md §1297-1302] + operating rule 20. Binding.
- **Do NOT call `outbound()` in v1.** [BUILD.md §1302]'s optional
  outbound branch is deferred to a future config-aware prompt.
- **Do NOT emit when pre-conditions fail.** All defensive-skip cases
  (recent completion, energy not low, outside fog window, missing
  profile, empty persona templates) silently advance the bus.
- **Do NOT modify any projection.** Per [§7.3], pipelines emit events.
