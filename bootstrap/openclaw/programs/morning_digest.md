# Standing order: morning_digest

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

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

None. The digest is a recurring routine, not a per-event ask. Outbound
delivery routes through `outbound()` per [§6.14], which consults
observation mode and records `observation.suppressed` or
`external.sent`. The principal reviews suppressed-action logs before
flipping observation off per [§7] operating rule 7.

## Escalation

A composition miss (skill failure, missing profile, missing persona)
is absorbed by the defensive default per [§7.7] — emit
`digest.composed` with `validation_failed=true` and the sentinel body
"No morning brief available; underlying data changed." NO outbound is
attempted on the sentinel path. The pipeline never raises into the bus.

## Execution steps

Proactive pack at `packs/pipelines/morning_digest/` with
`triggers: {schedule: "0 7 * * *", proactive: true}` and NO
`triggers.events` — the in-process `PipelineRunner` skips it
(`runner.py:131-138`); OpenClaw cron drives invocation via
`bootstrap/openclaw/cron.yaml`. Handler logic per [BUILD.md §1289]:

1. Gather projection state: today's calendar events
   (privacy-filtered), due commitments, today's tasks, due
   recurrences, inbox count, streak status, reward stats.
2. Resolve member's `profile_format` from the profile loader.
3. Call `compose_morning_digest@v3` with the gathered payload.
4. Validation guard: every id in the skill's `claimed_event_ids`
   MUST appear in the gather payload. Any miss → sentinel path
   (emit `digest.composed` with sentinel body, `validation_failed=true`,
   `delivered=false`; DO NOT call outbound).
5. Otherwise call `outbound()` exactly once with the composed body.
6. Emit `digest.composed` v1 with `member_id`, `body_text`,
   `profile_format`, `validation_failed`, `delivered`, `today_iso`.

## What NOT to do

- **Do NOT skip the validation guard.** Any claimed id absent from the
  gather payload zeroes the message per [BUILD.md §1289].
- **Do NOT call `outbound()` on the sentinel path.**
- **Do NOT modify any projection.** Per [§7.3], pipelines emit events;
  projections consume them.
