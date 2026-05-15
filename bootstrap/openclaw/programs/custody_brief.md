# Standing order: custody_brief

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Nightly at 20:00 local per [BUILD.md §1233-1236]. Per parent member
whose profile carries a `coparent_relationship_member_id` AND whose
per-member override leaves `custody_brief_enabled=True` (default true
when a coparent relationship exists per [D22] opt-out). Composes a
brief summary of the next day's calendar events for each child
(privacy-filtered), the custody handoff timing
(`coparent_handoff_pattern`), and allergen/med reminders
(`child_health_flags`). Emits `custody_brief.composed` at composition
time and routes the composed text through `outbound()` per [§6.13].

## Triggers

Scheduled nightly (cron). Default schedule string `0 20 * * *`.
Bootstrap §8 may substitute member-local times at install time. The
cron entry in `bootstrap/openclaw/cron.yaml` is the canonical schedule
source.

## Trigger payload

OpenClaw cron sends the synthetic standing-order tick event
`openclaw.tick.custody_brief` to the handler endpoint. The tick event
carries `{event_id, occurred_at, program_id}`; the handler uses
`event_id` as `causation_id` for every `custody_brief.composed` event
it emits during this tick per the 10c-i proactive-pipeline causation
pattern.

## Handler endpoint

`POST http://adminme:8080/pipelines/custody_brief/tick` — the runner
exposes this path; standing-order registration in
`CustodyBriefPipeline.__init__` calls
`openclaw_client.register_standing_order(pipeline_id, schedule,
handler_url)` at process start (mocked under Phase A tests).

## Idempotency notes

The nightly cadence is the dedupe boundary; no per-tick event-log
dedupe is needed. A single nightly tick produces at most one
`custody_brief.composed` event per qualifying parent member. Parents
without coparent relationships, or with the per-member opt-out, produce
no event.

## Approval gate

None. The nightly brief is a recurring routine, not a per-event ask.
The per-member `custody_brief_enabled` override (default true when
coparent relationship exists) is the consent gate per [D22].

## Observation-mode behavior

When `InstanceConfig.observation_mode=True` per [§6.13] and [D22], the
dispatcher returns `DeliveryResult(suppressed=True)` and emits
`observation.suppressed`; the `custody_brief.composed` event is still
appended to the log so the composition is auditable. The 10c-iii
integration test asserts this round-trip.

## Escalation

A composition miss for one parent (skill failure, missing profile,
empty child set) is absorbed by the per-row defensive default per
[§7.7] — the handler skips that parent's emit and continues to the
next. The pipeline never raises into the bus and sibling parents in
the same tick are still processed.

## Failure modes

Per-row (per-parent) defensive default, not per-tick. If
`compose_custody_brief` skill raises for one parent (one of the 7
defensive-default exception types matching `morning_digest`), the
handler skips that parent's emit and continues to the next parent.
A single parent's skill failure must not abort the tick.

The handler never raises into the bus. A brief with no children is
suppressed BEFORE emit at the pipeline boundary — the
`CustodyBriefComposedV1` schema rejects `child_member_ids=[]`, so the
handler validates child set presence before constructing the payload.

## Execution steps

Proactive pack at `packs/pipelines/custody_brief/` with
`triggers: {schedule: "0 20 * * *", proactive: true}` and NO
`triggers.events` — the in-process `PipelineRunner` skips it; OpenClaw
cron drives invocation via `bootstrap/openclaw/cron.yaml`. Handler
logic per 10c-iii Operating Context §"Scan source per pipeline":

1. Load config (per-member overrides supported via the same
   `_load_config` pattern as `morning_digest`).
2. Open `ctx.members_conn_factory()`. Call
   `find_members_with_coparent_relationship(conn, ctx.session)` to
   resolve qualifying parent members.
3. For each parent member, check
   `custody_brief_enabled` override; skip if explicitly false.
4. Gather `child_profiles` (via the same members projection),
   `calendar_events` for each child via
   `find_events_for_child_next_day(conn, ctx.session, child_member_id,
   anchor_date=tomorrow_local)`, `handoff_timing` from
   `member_profile.coparent_handoff_pattern`, and `allergen_med_notes`
   from `member_profile.child_health_flags`.
5. If `child_profiles` is empty, skip the parent (no emit; schema
   rejects empty list).
6. Build skill input `{member_profile, coparent_profile,
   child_profiles, calendar_events, handoff_timing,
   allergen_med_notes}`; call
   `await ctx.run_skill_fn("skill:compose_custody_brief", inputs,
   SkillContext(session=ctx.session,
   correlation_id=ctx.correlation_id))`.
7. Emit `custody_brief.composed` v1 via
   `ctx.event_log.append(envelope, correlation_id=ctx.correlation_id,
   causation_id=tick_event["event_id"])`.
8. Call `await ctx.outbound_dispatcher.deliver(member_id=parent_id,
   channel=<from skill output>, content=<composed_text>, ctx=ctx)`.

## What NOT to do

- **Do NOT emit for parents without `coparent_relationship_member_id`
  set.** Custody briefs presuppose a coparent relationship.
- **Do NOT emit a brief with empty `child_member_ids`.** Schema
  rejects; handler must validate before constructing payload.
- **Do NOT modify the `members` or `calendar` projections.** Per [§7.3]
  and [D17], pipelines emit events; projections consume them.
- **Do NOT bypass `outbound.deliver`.** Direct channel-API calls are
  a [§7] violation.
- **Do NOT abort the tick on a single-parent failure.** Defensive
  default per [§7.7] is per-row, not per-tick.
- **Do NOT include other members' children.** Only children whose
  relationship resolves to the qualifying parent appear in the brief
  per [§12 tenant isolation].
