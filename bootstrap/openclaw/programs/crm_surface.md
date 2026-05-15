# Standing order: crm_surface

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Weekly per [BUILD.md §1238-1245]. Per member with
`crm_proactive_enabled=True` (opt-in per [D22], default false), scans
the `parties` projection for parties whose `last_contact_at` exceeds
`threshold_days` (default 90, per-member-overridable). Up to
`max_surfaced_per_week` (default 5) parties are gathered into a single
weekly CRM-surface composition delivered to the member. Emits
`crm_surface.composed` at composition time and routes the composed
text through `outbound()` per [§6.13].

## Triggers

Scheduled weekly (cron). Default schedule string `0 19 * * 0` (Sunday
19:00, placeholder convention). Bootstrap §8 may substitute member-
local times at install time. The cron entry in
`bootstrap/openclaw/cron.yaml` is the canonical schedule source.

## Trigger payload

OpenClaw cron sends the synthetic standing-order tick event
`openclaw.tick.crm_surface` to the handler endpoint. The tick event
carries `{event_id, occurred_at, program_id}`; the handler uses
`event_id` as `causation_id` for every `crm_surface.composed` event it
emits during this tick per the 10c-i proactive-pipeline causation
pattern.

## Handler endpoint

`POST http://adminme:8080/pipelines/crm_surface/tick` — the runner
exposes this path; standing-order registration in
`CrmSurfacePipeline.__init__` calls
`openclaw_client.register_standing_order(pipeline_id, schedule,
handler_url)` at process start (mocked under Phase A tests).

## Idempotency notes

The Sunday cadence is the dedupe boundary; no per-tick event-log
dedupe is needed. A single Sunday tick produces at most one
`crm_surface.composed` event per opt-in member with stale parties.
Members with no stale parties produce no event.

## Approval gate

None. The weekly surface is a recurring routine, not a per-event ask.
Per-member opt-in via `crm_proactive_enabled` is the consent gate;
default false per [D22] explicit opt-in for CRM surfacing.

## Observation-mode behavior

When `InstanceConfig.observation_mode=True` per [§6.13] and [D22], the
dispatcher returns `DeliveryResult(suppressed=True)` and emits
`observation.suppressed`; the `crm_surface.composed` event is still
appended to the log so the composition is auditable. The 10c-iii
integration test asserts this round-trip.

## Failure modes

Per-row (per-member) defensive default, not per-tick. If
`compose_crm_surface` skill raises for one member (one of the 7
defensive-default exception types matching `morning_digest`), the
handler skips that member's emit and continues to the next member.
A single member's skill failure must not abort the tick.

The handler never raises into the bus. If the `parties` projection
scan itself fails for a particular member, the handler logs and skips
that member; other members in the same tick still complete.

## Execution steps

Proactive pack at `packs/pipelines/crm_surface/` with
`triggers: {schedule: "0 19 * * 0", proactive: true}` and NO
`triggers.events` — the in-process `PipelineRunner` skips it; OpenClaw
cron drives invocation via `bootstrap/openclaw/cron.yaml`. Handler
logic per 10c-iii Operating Context §"Scan source per pipeline":

1. Load config (per-member overrides supported via the same
   `_load_config` pattern as `morning_digest`).
2. Resolve the opt-in member set: members with
   `crm_proactive_enabled=True` per
   `get_member_profile_flags(conn, ctx.session, member_id)`.
3. For each opt-in member, open `ctx.parties_conn_factory()`
   connection and call `find_parties_with_stale_contact(conn,
   ctx.session, threshold_days, owner_member_id=member_id)`. Clamp the
   result to `max_surfaced_per_week` (default 5).
4. If the clamped list is empty, skip the member (no emit).
5. Build skill input `{member_profile, surfaced_parties: list}`; call
   `await ctx.run_skill_fn("skill:compose_crm_surface", inputs,
   SkillContext(session=ctx.session,
   correlation_id=ctx.correlation_id))`.
6. Emit `crm_surface.composed` v1 via
   `ctx.event_log.append(envelope, correlation_id=ctx.correlation_id,
   causation_id=tick_event["event_id"])`.
7. Call `await ctx.outbound_dispatcher.deliver(member_id=member_id,
   channel=<from skill output>, content=<composed_text>, ctx=ctx)`.

## What NOT to do

- **Do NOT emit for members without `crm_proactive_enabled=True`.** The
  CRM-surface opt-in is explicit per [D22]; default false.
- **Do NOT modify the `parties` projection.** Per [§7.3] and [D17],
  pipelines emit events; projections consume them.
- **Do NOT bypass `outbound.deliver`.** Direct channel-API calls are
  a [§7] violation.
- **Do NOT abort the tick on a single-member failure.** Defensive
  default per [§7.7] is per-row, not per-tick.
- **Do NOT exceed `max_surfaced_per_week`.** The clamp protects the
  member's attention budget per [VISION §"Proactive behavior"].
