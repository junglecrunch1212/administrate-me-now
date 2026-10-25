# Standing order: reward_dispatch

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

This program is **reactive, not scheduled**: it has `triggers.events` declared
in its `pipeline.yaml`, so the AdministrateMe `PipelineRunner` picks it up at
product boot and OpenClaw cron does NOT invoke it. The standing-order file
exists for documentation continuity with the other five proactive standing
orders so a single `AGENTS.md` block describes all eight v1 proactive
behaviors per [§14].

## Scope

When a household task is completed, or a commitment is fulfilled, surface
positive reinforcement to the responsible member. Tier (`done` / `warm` /
`delight` / `jackpot`) is sampled per the member's profile reward
distribution. The reward toast is fanned out to the member's open console
tabs by the console's SSE layer per [CONSOLE_PATTERNS.md §8] — this
program does not deliver the toast itself.

## Triggers

- `task.completed`
- `commitment.completed`

## Approval gate

None. Rewards are non-outbound — no message is sent to a person; the only
side effect is the in-UI toast dispatched by the console SSE layer per
[CONSOLE_PATTERNS.md §8]. There is no channel transport invocation, so
OpenClaw's `exec-approvals` machinery does not gate this pipeline. The
canonical event is `reward.ready` per [BUILD.md §1620, CONSOLE_PATTERNS.md
§8]; downstream consumers (console, scoreboard) gate at their own surfaces.

## Escalation

None. A reward miss (skill failure, missing template, missing profile) is
absorbed by the defensive default per [§7.7] — emit `reward.ready` with
tier=`done` and a sentinel template — never raise.

## Execution steps

This pipeline runs in-process via `adminme.pipelines.runner.PipelineRunner`
(it has `triggers.events` declared). OpenClaw cron does NOT invoke it.
This program file exists for documentation continuity with the other five
standing orders.

The handler logic per [BUILD.md §1207-1210] + [BUILD.md §1620] +
[CONSOLE_PATTERNS.md §8]:

1. On `task.completed` or `commitment.completed`, read `member_id` from
   the event payload (`completed_by_member_id` for tasks,
   `completed_by_party_id` for commitments).
2. Load the member's profile. Read `reward_distribution` from the profile
   manifest per [BUILD.md §1884].
3. Roll tier deterministically — seed from `event_id` so the same event
   always rolls the same tier (re-processing on subscriber rewind must
   not double-toast a different reward). Compare the seeded random draw
   against the cumulative bands of the member's `reward_distribution`.
4. Load the persona pack's `reward_templates.yaml` per [BUILD.md
   §PERSONA PACKS]. Pick a template by tier; fall back to `done` tier
   if the rolled tier is missing; fall back to a default sentinel if
   `done` is also missing.
5. Emit `reward.ready` v1 with `member_id`, `tier`, `template_id`,
   `template_text`, `triggering_task_id` (None if commitment), and
   `triggering_commitment_id` (None if task). `correlation_id` propagates
   from the source event; `causation_id` = `ctx.triggering_event_id`.

## What NOT to do

- **Do NOT emit `adminme.reward.dispatched`.** The canonical event name
  is `reward.ready` per [BUILD.md §1620, CONSOLE_PATTERNS.md §8 —
  supersedes §1210 typo]. The console's SSE layer consumes by that name.
- **Do NOT call `outbound()`.** The reward toast is dispatched by the
  console's SSE layer per [CONSOLE_PATTERNS.md §8], not by this
  pipeline. There is no channel-transport invocation here.
- **Do NOT modify any projection.** Per [§7.3], pipelines emit events;
  projections consume them. A pipeline writing a projection row is a bug.
- **Do NOT auto-merge a tier.** The tier sample reads the member's
  profile manifest's `reward_distribution`. Skipping the profile lookup
  and hardcoding a tier collapses the variable-ratio reinforcement that
  [BUILD.md §1884] depends on.
- **Do NOT invoke an LLM.** Reward dispatch is deterministic — tier
  sampling is a seeded random draw and template selection is a YAML
  lookup. No skill call is needed.
