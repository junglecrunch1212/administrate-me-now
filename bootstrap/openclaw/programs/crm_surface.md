# Standing order: crm_surface

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Weekly per-member CRM signal surface (default Sunday 19:00 local per
[BUILD.md §L4 crm_surface]). For each member with
`crm_proactive_enabled=true`, the injected loader returns up to
`max_surfaced_per_week` (default 5) stale parties grouped by
`gap_kind` ∈ {`stale_contact`, `upcoming_birthday`,
`hosting_imbalance`}. One skill call per surfaced member composes the
weekly CRM nudge; emits `crm.surfaced` v1 carrying the list. Per [§1]
10c-iii grounding the cohort discipline is a literal clone of
`morning_digest`'s shape with substitutions; no inheritance.

## Triggers

Scheduled weekly (cron). Default schedule string `0 19 * * 0` —
Sunday 19:00 local per `bootstrap/openclaw/cron.yaml`. The proactive
pack's `pipeline.yaml` carries `triggers: {schedule: "0 19 * * 0",
proactive: true}` and NO `triggers.events`; OpenClaw cron drives
invocation.

## Approval gate

None. The weekly CRM surface is a recurring routine, not a per-event
ask. Outbound delivery routes through `outbound()` per [§6.14], which
consults observation mode and records `observation.suppressed`
(active) or `external.sent` (inactive) before any external send. The
principal reviews suppressed-action logs before flipping observation
off per [§7] operating rule 7 and [D22]. The per-member opt-in default
is `crm_proactive_enabled=false` — silence is the default, surfacing
is the explicit override.

## Escalation

A composition miss — skill failure on the 7-tuple
(`_SKILL_FAILURE_EXCEPTIONS` per [§7.7]), empty surfaced-party list,
missing member profile — is absorbed by the defensive default: emit
`crm.surfaced` with `composed_text=""`, `validation_failed=true`,
`delivered=false`. NO `outbound()` is attempted on the sentinel path.
Per-row failure does NOT abort the tick; the next opt-in member is
processed independently. The pipeline never raises into the bus.

## Execution steps

Proactive pack at `packs/pipelines/crm_surface/` per [BUILD.md §L4
crm_surface]. Per-tick handler logic:

1. Load opt-in members + stale-party rows via injected loader (PM-27
   constructor injection; default no-op returns `[]` per UT-14
   carry-forward). Each row carries `member_id`, `gap_kind`, and up to
   `max_surfaced_per_week` party ids by gap type, computed against
   the configured `threshold_days` (default 90).
2. Honor per-member overrides: `crm_proactive_enabled` (default
   false), `threshold_days` (override default).
3. For each opt-in member with a non-empty surfaced-party list, call
   `compose_crm_surface@v1` with the row payload; on failure
   (defensive default) carry the sentinel
   `composed_text=""` / `validation_failed=true` into step 5.
4. On valid composition call `outbound()` exactly once via the single
   import site in `deliver()` per [§6.14]. Capture `delivered: bool`
   from `OutboundResult.suppressed`.
5. Emit `crm.surfaced` v1 with `member_id`, `surfaced_party_ids`,
   `gap_kind`, `composed_text`, `channel`, `week_of_date`,
   `delivered`, `validation_failed`.
   `causation_id=ctx.triggering_event_id` per the 10c-i causation
   pattern.

## What NOT to do

- **Do NOT call `outbound()` on the sentinel path.** Sentinel emits
  the event with `delivered=false`.
- **Do NOT modify any projection.** Per [§7.3] / [D17], proactive
  pipelines emit events; projections consume them. The `parties`
  projection's stale-contact computation is read-only here.
- **Do NOT import an LLM SDK.** Per [§8] / [D6], all skill calls go
  through `ctx.run_skill_fn` which wraps OpenClaw's gateway.
- **Do NOT raise into the bus on per-row skill failure.** The
  `_SKILL_FAILURE_EXCEPTIONS` 7-tuple is absorbed in `compose()` and
  the next opt-in member proceeds.
- **Do NOT exceed `max_surfaced_per_week`.** The loader clamps the
  list; the handler does not re-expand it. Per the anti-vision halt
  ([§1] 10c-iii) noisy CRM output is the failure mode to avoid.
