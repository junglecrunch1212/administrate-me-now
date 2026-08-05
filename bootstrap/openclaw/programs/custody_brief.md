# Standing order: custody_brief

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Daily per-parent-member custody brief (default 20:00 local per
[BUILD.md §L4 custody_brief]) for members with a coparent
`Relationship` set. The injected loader returns one row per opt-in
parent member; row payload carries `coparent_member_id`,
`child_member_ids`, next-day calendar events, handoff timing,
allergen / med notes. One skill call per parent composes the
long-form brief; emits `custody_brief.composed` v1. Per [§1] 10c-iii
grounding the cohort discipline is a literal clone of
`morning_digest`'s shape with substitutions; no inheritance.

## Triggers

Scheduled daily (cron). Default schedule string `0 20 * * *` per
`bootstrap/openclaw/cron.yaml`. The proactive pack's `pipeline.yaml`
carries `triggers: {schedule: "0 20 * * *", proactive: true}` and NO
`triggers.events`; OpenClaw cron drives invocation.

## Approval gate

None. The daily custody brief is a recurring routine, not a per-event ask.
AdministrateMe persists a private, sensitive notification signal and its policy
decision; OpenClaw owns the send and reports its outcome. The per-member
opt-out is `custody_brief_enabled=false`.

## Escalation

A composition miss — skill failure on the 7-tuple
(`_SKILL_FAILURE_EXCEPTIONS` per [§7.7]), missing coparent
relationship, missing child-member roster — is absorbed by the
defensive default: emit `custody_brief.composed` with
`composed_text=""`, `validation_failed=true`, `delivered=false`. NO
notification is raised on the sentinel path. Per-row failure does
NOT abort the tick; the next parent member is processed
independently. The pipeline never raises into the bus.

## Execution steps

Proactive pack at `packs/pipelines/custody_brief/` per [BUILD.md §L4
custody_brief]. Per-tick handler logic:

1. Load eligible parent members + custody rows from scoped Parties,
   Relationships, Members, and Calendars read models. Constructor injection
   supplies deterministic fixtures in isolated tests. Each row carries
   `member_id`, `coparent_member_id`,
   `child_member_ids`, next-day calendar events, handoff timing,
   allergen / med notes.
2. Honor per-member overrides: `custody_brief_enabled` (default
   true for members with a coparent relationship), `lookahead_days`
   (override default).
3. For each eligible parent member call `compose_custody_brief@v1`
   with the row payload; empty next-day calendar still composes
   (handoff + allergen notes carry). On skill failure (defensive
   default) carry the sentinel
   `composed_text=""` / `validation_failed=true` into step 5.
4. On valid composition persist one private, sensitive notification signal and
   policy decision. OpenClaw sends only a route decision and reports outcome.
5. Emit `custody_brief.composed` v1 with `member_id`,
   `coparent_member_id`, `child_member_ids`, `composed_text`,
   `channel`, `brief_date`, `delivered`, `validation_failed`.
   `causation_id=ctx.triggering_event_id` per the 10c-i causation
   pattern.

## What NOT to do

- **Do NOT raise a notification on the sentinel path.** Sentinel emits
  the event with `delivered=false`.
- **Do NOT claim delivery before `notification.delivery.result`.**
- **Do NOT modify any projection.** Per [§7.3] / [D17], proactive
  pipelines emit events; projections consume them. Coparent /
  child-roster lookups are read-only via the loader.
- **Do NOT import an LLM SDK.** Per [§8] / [D6], all skill calls go
  through `ctx.run_skill_fn` which wraps OpenClaw's gateway.
- **Do NOT raise into the bus on per-row skill failure.** The
  `_SKILL_FAILURE_EXCEPTIONS` 7-tuple is absorbed in `compose()` and
  the next parent proceeds.
- **Do NOT auto-confirm or state-change the source rows.** Custody
  briefs are read-side notifications per [§6] 10c-iii out-of-scope.
