# Standing order: reminder_dispatch

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Per-tick scan (default every 15 minutes per [BUILD.md §L4
reminder_dispatch]) for commitments whose `due_at` falls within the
configured lookahead window (default 60 minutes). For each due
commitment, composes an SMS-friendly reminder body via the
`compose_reminder@v1` skill and emits `reminder.dispatched` v1.
Per-commitment dedupe inline via the event log: a prior
`reminder.dispatched` for the same `commitment_id` within
`dedupe_window_minutes` (default 90) suppresses the current tick. Per
[§1] 10c-iii grounding the cohort discipline is a literal clone of
`morning_digest`'s shape with substitutions; no inheritance.

## Triggers

Scheduled (cron). Default schedule string `*/15 * * * *` per
`bootstrap/openclaw/cron.yaml`. The proactive pack's
`pipeline.yaml` carries `triggers: {schedule: "*/15 * * * *",
proactive: true}` and NO `triggers.events` — the in-process
`PipelineRunner` skips it; OpenClaw cron drives invocation.

## Approval gate

None. Reminder dispatch is a recurring routine, not a per-event ask.
Outbound delivery routes through `outbound()` per [§6.14], which
consults observation mode and records `observation.suppressed`
(active) or `external.sent` (inactive) before any external send. The
principal reviews suppressed-action logs before flipping observation
off per [§7] operating rule 7 and [D22].

## Escalation

A composition miss — skill failure on the 7-tuple
(`_SKILL_FAILURE_EXCEPTIONS` per [§7.7]), missing profile, missing
recipient routing — is absorbed by the defensive default: emit
`reminder.dispatched` with `composed_text=""`,
`validation_failed=true`, `delivered=false`. NO `outbound()` is
attempted on the sentinel path. Per-row failure does NOT abort the
tick; the next due commitment is processed independently. The
pipeline never raises into the bus.

## Execution steps

Proactive pack at `packs/pipelines/reminder_dispatch/` per [BUILD.md
§L4 reminder_dispatch]. Per-tick handler logic:

1. Load due commitments via injected loader (PM-27 constructor
   injection; default no-op returns `[]` per UT-14 carry-forward).
   Filter to `due_at - now < lookahead_window_minutes`.
2. For each candidate, dedupe inline:
   `ctx.event_log.read_recent("reminder.dispatched", since=now -
   dedupe_window_minutes)` — if a prior dispatch for the same
   `commitment_id` is present, skip.
3. Resolve recipient routing via injected profile loader; honor
   per-member overrides (`reminders_enabled`,
   `lookahead_window_minutes`, `dedupe_window_minutes`).
4. Call `compose_reminder@v1` with the commitment payload and member
   context; on failure (defensive default) carry the sentinel
   `composed_text=""` / `validation_failed=true` into step 6.
5. On valid composition call `outbound()` exactly once via the single
   import site in `deliver()` per [§6.14]. Capture `delivered: bool`
   from `OutboundResult.suppressed`.
6. Emit `reminder.dispatched` v1 with `commitment_id`,
   `owner_member_id`, `recipient_party_id`, `composed_text`,
   `channel`, `due_at`, `delivered`, `validation_failed`,
   optional `scheduled_send_at`. `causation_id=ctx.triggering_event_id`
   per the 10c-i causation pattern.

## What NOT to do

- **Do NOT call `outbound()` on the sentinel path** (validation_failed
  composition). Sentinel emits the event with `delivered=false`.
- **Do NOT modify any projection.** Per [§7.3] / [D17], proactive
  pipelines emit events; projections consume them. Dedupe is via
  event-log lookup, NOT a `last_reminded_at` column.
- **Do NOT import an LLM SDK.** Per [§8] / [D6], all skill calls go
  through `ctx.run_skill_fn` which wraps OpenClaw's gateway.
- **Do NOT raise into the bus on per-row skill failure.** The
  `_SKILL_FAILURE_EXCEPTIONS` 7-tuple is absorbed in `compose()` and
  the next candidate proceeds.
- **Do NOT cross-feed other proactive pipelines.** A
  `reminder.dispatched` does not flow into next-tick `morning_digest`
  per [§6] 10c-iii out-of-scope.
