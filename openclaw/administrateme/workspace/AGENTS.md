# AdministrateMe standing orders

Generated deterministically from version-controlled program sources. Scheduling remains in OpenClaw automations; these instructions do not create a second scheduler.

<!-- source: bootstrap/openclaw/programs/crm_surface.md -->
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

None. The weekly CRM surface is a recurring routine, not a per-event ask.
AdministrateMe persists the notification signal and policy decision; OpenClaw
owns the send and reports its outcome. The per-member opt-in default is
`crm_proactive_enabled=false` — silence is the default.

## Escalation

A composition miss — skill failure on the 7-tuple
(`_SKILL_FAILURE_EXCEPTIONS` per [§7.7]), empty surfaced-party list,
missing member profile — is absorbed by the defensive default: emit
`crm.surfaced` with `composed_text=""`, `validation_failed=true`,
`delivered=false`. No notification is raised on the sentinel path.
Per-row failure does NOT abort the tick; the next opt-in member is
processed independently. The pipeline never raises into the bus.

## Execution steps

Proactive pack at `packs/pipelines/crm_surface/` per [BUILD.md §L4
crm_surface]. Per-tick handler logic:

1. Load opt-in members + stale-party rows from the scoped parties read model.
   Constructor injection supplies deterministic fixtures in isolated tests.
   Each row carries `member_id`, `gap_kind`, and up to
   `max_surfaced_per_week` party ids by gap type, computed against
   the configured `threshold_days` (default 90).
2. Honor per-member overrides: `crm_proactive_enabled` (default
   false), `threshold_days` (override default).
3. For each opt-in member with a non-empty surfaced-party list, call
   `compose_crm_surface@v1` with the row payload; on failure
   (defensive default) carry the sentinel
   `composed_text=""` / `validation_failed=true` into step 5.
4. On valid composition persist one typed notification signal and policy
   decision. OpenClaw sends only a route decision and reports the outcome.
5. Emit `crm.surfaced` v1 with `member_id`, `surfaced_party_ids`,
   `gap_kind`, `composed_text`, `channel`, `week_of_date`,
   `delivered`, `validation_failed`.
   `causation_id=ctx.triggering_event_id` per the 10c-i causation
   pattern.

## What NOT to do

- **Do NOT raise a notification on the sentinel path.** Sentinel emits
  the event with `delivered=false`.
- **Do NOT claim delivery before `notification.delivery.result`.**
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


<!-- source: bootstrap/openclaw/programs/custody_brief.md -->
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


<!-- source: bootstrap/openclaw/programs/morning_digest.md -->
# Standing order: morning_digest

Per [D1] Corollary — workspace-prose program file, version-controlled.
Bootstrap §8 (prompt 16) concatenates this file into `~/Chief/AGENTS.md`.

## Scope

Per-member morning digest delivered around the member's wake time
(default 06:30 local in member's timezone per [BUILD.md §1202]). Gathers
today's calendar events (respecting privacy filtering), due commitments
+ tasks, due recurrences, overnight Triage count, streak status, reward
stats. Validation-guarded: every claimed calendar event / commitment /
task id is verified against projections post-composition; any
fabrication zeroes the message with sentinel "No morning brief
available; underlying data changed."

## Triggers

Scheduled per member by OpenClaw. The versioned `0 7 * * *` installation
default is replaced by a configured member wake time when present.

## Approval gate

None. The digest is a recurring routine, not a per-event ask. AdministrateMe
persists `notification.signal.raised` and `notification.decision.made`;
OpenClaw sends only a `route` decision and reports the actual outcome through
`administrateme_notification_result`.

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
   recurrences, Triage count, streak status, reward stats.
2. Resolve member's `profile_format` from the profile loader.
3. Call `compose_morning_digest@v3` with the gathered payload.
4. Validation guard: every id in the skill's `claimed_event_ids`
   MUST appear in the gather payload. Any miss → sentinel path
   (emit `digest.composed` with sentinel body, `validation_failed=true`,
`delivered=false`; do not raise a notification).
5. Otherwise raise one typed notification signal and policy decision. OpenClaw
   owns the external send and delivery-result callback.
6. Emit `digest.composed` v1 with `member_id`, `body_text`,
   `profile_format`, `validation_failed`, `delivered`, `today_iso`.

## What NOT to do

- **Do NOT skip the validation guard.** Any claimed id absent from the
  gather payload zeroes the message per [BUILD.md §1289].
- **Do NOT raise a notification on the sentinel path.**
- **Do NOT claim delivery before `notification.delivery.result`.**
- **Do NOT modify any projection.** Per [§7.3], pipelines emit events;
  projections consume them.


<!-- source: bootstrap/openclaw/programs/paralysis_detection.md -->
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
`paralysis.triggered` with template id + single-action framing. The event is an
Today/activity surface; it is not an external delivery request.

## Triggers

Scheduled per ADHD-profile member by OpenClaw. `0 15,17 * * *` is the explicit
installation fallback; profile fog-window overrides win when configured.

## Approval gate

None. This program has no outbound side effect; output is a scoped Today/activity
activity event surfaced on demand.

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
   `template_text`, `triggered_at`. Surface is the scoped Today and activity
   feed.

## What NOT to do

- **Do NOT invoke an LLM.** Template selection is a deterministic
  YAML lookup per [BUILD.md §1297-1302] + operating rule 20. Binding.
- **Do NOT convert the scoped activity event into an implicit message.**
- **Do NOT emit when pre-conditions fail.** All defensive-skip cases
  (recent completion, energy not low, outside fog window, missing
  profile, empty persona templates) silently advance the bus.
- **Do NOT modify any projection.** Per [§7.3], pipelines emit events.


<!-- source: bootstrap/openclaw/programs/reminder_dispatch.md -->
# Standing order: reminder_dispatch

## Scope

Every 15 minutes, ask AdministrateMe for commitments due inside the configured
lookahead window (60 minutes by default). For each eligible commitment, invoke
`compose_reminder@v1`. AdministrateMe records the compatibility
`reminder.dispatched` fact with `delivered=false` and the canonical typed chain:

1. `notification.signal.raised`
2. `notification.decision.made`
3. `notification.delivery.result`

The domain policy may route, suppress, hold, or retry. Only a route decision
delegates a delivery attempt to OpenClaw. Only OpenClaw may emit the delivery
result; AdministrateMe never claims delivery from a no-op callback.

## Triggers

OpenClaw owns the schedule declared in `bootstrap/openclaw/cron.yaml`. The
proactive pack has no event trigger and is never scheduled by the in-process
`PipelineRunner`.

## Policy and deduplication

- Deduplicate a commitment inside `dedupe_window_minutes` (90 by default).
- Honor per-member enablement, lookahead, dedupe, profile, quiet-hour, and
  observation settings.
- Missing approval produces `hold`; observation mode produces `suppress`;
  quiet hours or `not_before` produce `retry`; three failed attempts open the
  delivery circuit.
- The signal and decision retain member scope, correlation, causation, and
  commitment provenance.

## Execution

1. Load due commitments from the scoped commitment read model.
2. Remove recent duplicates and opted-out members.
3. Compose through the pinned OpenClaw `llm-task` skill seam.
4. On validation failure, emit only the non-delivered compatibility sentinel
   and continue with the next commitment.
5. On success, persist signal and policy decision through the real EventSink.
6. If the decision is `route`, OpenClaw performs the channel action and posts a
   typed `notification.delivery.result` back through its authenticated plugin
   seam. Suppressed, held, and retry decisions perform no channel action.

## Boundaries

- Do not import provider SDKs or create an AdministrateMe scheduler.
- Do not mutate projections directly.
- Do not use `external.sent` as a speculative delivery receipt.
- Do not expose reminder content across owner or visibility scope.
- A per-row skill or delivery failure must not abort the remaining cohort.


<!-- source: bootstrap/openclaw/programs/reward_dispatch.md -->
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
