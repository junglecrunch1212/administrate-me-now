# Standing order: reminder_dispatch

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Every 15 minutes per [BUILD.md §arch §5]. Scans the `commitments`
projection for open commitments whose `due_at` falls inside the
configured `lookahead_window_minutes` (default 60) and not closer than
`min_reminder_gap_minutes` (default 15). For each qualifying row, emits
a `reminder.dispatched` event at composition time and routes the
composed text through `outbound()` for delivery to the
recipient_party's preferred channel. Observation-mode aware via the
on-main `adminme/outbound/dispatcher.py::deliver` wrapper per [§6.13].

The reminder is a notification, not a state change to the commitment
row — the commitment's lifecycle events (proposed → confirmed →
completed / dismissed / expired) are unaffected by reminder dispatch.

## Triggers

Scheduled (cron). Default schedule string `*/15 * * * *`. The cron
entry in `bootstrap/openclaw/cron.yaml` ships with this placeholder;
bootstrap §8 may override per-member at install time.

## Trigger payload

OpenClaw cron sends the synthetic standing-order tick event
`openclaw.tick.reminder_dispatch` to the handler endpoint. The tick
event carries `{event_id, occurred_at, program_id}`; the handler treats
`event_id` as the `causation_id` for every `reminder.dispatched` event
it emits during this tick per the 10c-i proactive-pipeline causation
pattern.

## Handler endpoint

`POST http://adminme:8080/pipelines/reminder_dispatch/tick` — the
runner exposes this path; the standing-order registration call in
`ReminderDispatchPipeline.__init__` registers it with
`openclaw_client.register_standing_order(pipeline_id, schedule,
handler_url)` at process start (mocked under Phase A tests).

## Idempotency notes

No `last_reminded_at` projection field (out of scope per 10c-iii).
Dedupe rides on the event log: before emitting, the handler queries
`ctx.event_log.read_recent("reminder.dispatched", since=now() -
dedupe_window_minutes)` and skips any commitment_id already present.
Default `dedupe_window_minutes` = 90.

## Approval gate

None. Reminders are a recurring routine, not a per-event ask. Outbound
delivery routes through `outbound()` per [§6.14], which consults
observation mode and records `observation.suppressed` or
`external.sent`. The principal reviews suppressed-action logs before
flipping observation off per [§7] operating rule 7.

## Observation-mode behavior

When `InstanceConfig.observation_mode=True` per [§6.13] and [D22], the
dispatcher returns `DeliveryResult(suppressed=True)` and emits
`observation.suppressed`; the `reminder.dispatched` event is still
appended to the log so the composition is auditable. The 10c-iii
integration test asserts this round-trip.

## Failure modes

Per-row defensive default, not per-tick. If `compose_reminder` skill
raises (one of the 7 defensive-default exception types matching
`morning_digest`), the handler emits `reminder.dispatched` with
`composed_text=""` and a tone_note documenting the skill failure;
delivery is then attempted (and observation-mode suppression still
applies). One row's failure must not abort the tick — subsequent
commitments in the same tick are still processed.

The handler never raises into the bus. If the projection scan itself
fails (connection error, malformed row), the handler logs and skips
the affected rows; the tick still completes.

## Execution steps

Proactive pack at `packs/pipelines/reminder_dispatch/` with
`triggers: {schedule: "*/15 * * * *", proactive: true}` and NO
`triggers.events` — the in-process `PipelineRunner` skips it; OpenClaw
cron drives invocation via `bootstrap/openclaw/cron.yaml`. Handler
logic per 10c-iii Operating Context §"Scan source per pipeline":

1. Load config (per-member overrides supported via the same
   `_load_config` pattern as `morning_digest`).
2. Open `ctx.commitments_conn_factory()` connection. Call
   `find_open_commitments_due_before(conn, ctx.session,
   due_before=now()+lookahead_window, owner_member_id=None)`.
3. For each candidate commitment, filter:
   - `due_at - now() > min_reminder_gap_minutes`,
   - no recent `reminder.dispatched` within `dedupe_window_minutes`,
   - owner member has `reminders_enabled=True` (default true; only
     `false` overrides skip),
   - recipient_party does not carry any `skip_party_tags`.
4. Build skill input `{commitment, owner_member_profile,
   recipient_party}`; call
   `await ctx.run_skill_fn("skill:compose_reminder", inputs,
   SkillContext(session=ctx.session,
   correlation_id=ctx.correlation_id))`.
5. Emit `reminder.dispatched` v1 via
   `ctx.event_log.append(envelope, correlation_id=ctx.correlation_id,
   causation_id=tick_event["event_id"])`.
6. Call `await ctx.outbound_dispatcher.deliver(member_id=<recipient>,
   channel=<from skill output>, content=<composed_text>, ctx=ctx)`.

## What NOT to do

- **Do NOT modify the `commitments` projection.** Per [§7.3] and
  [D17], pipelines emit events; projections consume them. The reminder
  is a notification only.
- **Do NOT bypass `outbound.deliver`.** Direct channel-API calls are a
  [§7] violation; `verify_invariants.sh` catches them.
- **Do NOT abort the tick on a single-row failure.** Defensive default
  per [§7.7] is per-row, not per-tick.
- **Do NOT introduce cross-pipeline event dependencies.** A
  `reminder.dispatched` event does not flow into `morning_digest` or
  any other pipeline; each proactive pipeline scans independently.
