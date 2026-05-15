# Prompt 10c-iii: Proactive pipelines — reminder_dispatch + crm_surface + custody_brief (+ 3 compose_* skill packs + 3 event schemas at v1)

**Phase:** [BUILD.md §L4] — proactive pipelines, third (final) wave of the 10c cohort.

**Branch:** `claude/10c-iii-<auto>` (harness-assigned). PR base = `main`. STACKED across c1→c4 per §8.5.

**Depends on:**
- Prompt 10c-i merged. Standing-orders infrastructure on main: `bootstrap/openclaw/programs/<six>.md` (three stubs this prompt completes), `bootstrap/openclaw/cron.yaml` carries 5 scheduled rows (3 of them placeholders for this prompt's pipelines), `bootstrap/openclaw/README.md` defines the consumption contract. `RewardReadyV1` schema at v1 in `adminme/events/schemas/domain.py`.
- Prompt 10c-ii merged. `morning_digest` + `paralysis_detection` proactive packs are on main with `compose_morning_digest@v3.0.0` + `DigestComposedV1`/`ParalysisTriggeredV1` schemas at v1 in `domain.py`. The proactive-pipeline pattern is established: structural-`Pipeline`-Protocol class with `pack_id`/`version`/`triggers` attrs + `async def handle(self, event, ctx)`; constructor-injection callable loaders per PM-27; single-site `outbound()` via `adminme.lib.observation`; emitted `*.composed` event carries `delivered: bool` for downstream surfaces.

**Quality bar:** clones `morning_digest`'s on-main proactive-pipeline shape three times with substitutions. No new infrastructure. Three pipelines are independent (no cross-pipeline event flow). Skill packs follow 09b shape. Event schemas register at v1 per [D7].

**Stop condition:** three proactive pipeline packs registered + discoverable; three `compose_*` skill packs at v1.0.0 loadable; three new event types (`reminder.dispatched`, `crm.surfaced`, `custody_brief.composed`) registered at v1 in `adminme/events/schemas/domain.py`; three standing-order program files completed (replacing 10c-i stub prose); `bootstrap/openclaw/cron.yaml` placeholder rows verified (no edit); observation-mode suppresses outbound for all three; one round-trip integration test per pipeline x 2 (obs-on + obs-off) = 6; suite green; `scripts/verify_invariants.sh` exit 0; >=27 new tests across 10c-iii.

**Estimated duration:** one Claude Code session, ~2.5 hours wall clock, 4 commits.

---

## §1 Behavioral grounding

You are writing three proactive pipelines that **literally clone** `MorningDigestPipeline`'s shape with substitutions. Cohort consistency with 10c-ii is the load-bearing requirement: the same constructor-injection-loader pattern [PM-27], the same `_SKILL_FAILURE_EXCEPTIONS` 7-tuple [§7.7], the same single-site `outbound()` discipline [§6.14], the same `causation_id=ctx.triggering_event_id` wiring on every emit. Read morning_digest before writing a line.

**Mode:** Phase A. No live OpenClaw, BlueBubbles, Plaid, or Anthropic-SDK calls in `adminme/` [§8, D6]. Observation mode default-on for new pipelines [§6.13, §6.16, D22] — all three pipelines route delivery through `adminme.lib.observation.outbound`, which gates on `ObservationManager.is_active()` and emits `observation.suppressed` (active) or `external.sent` (inactive).

**Standing-order registration is declarative, not programmatic** [D1 Corollary]. The three placeholder rows in `bootstrap/openclaw/cron.yaml` are already on main from 10c-i. Bootstrap §8 (prompt 16) consumes them via `openclaw cron add --cron "<cron>" --message "<message>"`. The pipelines do NOT call any `register_standing_order(...)` function — no such function exists on main, and none is being introduced in this prompt.

**Causation pattern** [10c-i]: every emitted `*.dispatched` / `*.surfaced` / `*.composed` event uses `causation_id=ctx.triggering_event_id` (the standing-order tick event, NOT a domain event). The `_emit_*` helpers carry `event_at_ms` + `tenant_id` + `owner_scope` + `visibility_scope` from the triggering event when present, defaulting to `int(time.time()*1000)` + `ctx.session.tenant_id` + `shared:household` otherwise.

**Anti-vision halt.** [VISION §"Proactive behavior"] and [VISION §"ADHD-aware"] frame proactive pipelines as **load-reducing**. If any pipeline's default scan window or per-row count produces noisy output (e.g., reminders firing for every commitment regardless of `due_at`, or `crm_surface` listing 50 stale parties at once), stop and report — the schedules and clamps below are calibrated for quiet defaults.

---

## §2 Read first (required, in order)

Use `sed -n '<start>,<end>p'` for `BUILD.md`/`DIAGRAMS.md`/`REFERENCE_EXAMPLES.md` ranges; do NOT full-read those files. Verify section anchors via `grep -n "^## §<N>" <file>` before reading.

1. **`packs/pipelines/morning_digest/handler.py`** — full read. Canonical proactive-pipeline shape: `class MorningDigestPipeline:` with `pack_id`/`version`/`triggers` class attrs, constructor-injection loaders with no-op defaults (PM-27), `gather`/`compose`/`validate`/`_resolve_delivery_channel`/`deliver`/`_emit_*`/`dispatch`/`handle` methods, `_SKILL_FAILURE_EXCEPTIONS` 7-tuple, inline `from adminme.lib.observation import outbound` in `deliver()`. Three new pipelines literally clone this with per-pipeline substitutions.
2. **`packs/pipelines/morning_digest/pipeline.yaml`** — full read. Manifest shape: `pack.{id,name,version,kind}`, `runtime.{language,python_version,entrypoint,class}`, `triggers.{schedule,proactive}` (no `events`), `depends_on.{skills,projections}`, `events_emitted`, `config.{schema,example}`.
3. **`packs/pipelines/morning_digest/tests/test_morning_digest_pack_load_pack_internal.py`** — pack-internal pack-load canary. F-3 rename pattern: every new pack-internal test uses a unique basename to avoid pytest's rootdir-relative-module-name collision.
4. **`tests/integration/test_pipeline_10c_ii_integration.py`** — full read. Integration template: tmp `InstanceConfig`, seeded projection DBs, mock standing-order tick event (synthesized `dict`), mock skill runner, mock `outbound` via a fake `ObservationManager`. Clone the cohort-keyed shape (`tests/integration/test_pipeline_10c_iii_integration.py`).
5. **`packs/skills/compose_morning_digest/{pack.yaml,SKILL.md,handler.py,prompt.jinja2,schemas/input.schema.json,schemas/output.schema.json,tests/}`** — full read all components. Skill-pack shape at v3.0.0; three new packs clone this at v1.0.0 with input/output schema substitutions.
6. **`adminme/pipelines/base.py`** — full read. `Pipeline` `Protocol` (`runtime_checkable`) declares `pack_id`/`version`/`triggers`/`handle()`. `PipelineContext` (`@dataclass(frozen=True)`) carries `session`/`event_log`/`run_skill_fn`/`outbound_fn`/`guarded_write`/`observation_manager`/`triggering_event_id`/`correlation_id`/`parties_conn_factory`. No `proactive.py`; no `ProactivePipeline` base class. Triggers `TypedDict` carries `events`/`schedule`/`proactive`.
7. **`adminme/lib/observation.py`** — full read. `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier) -> OutboundResult`. `OutboundResult.suppressed: bool`. ObservationManager default-on per [§6.16].
8. **`adminme/events/schemas/domain.py`** — full read. Pattern model: `DigestComposedV1` / `ParalysisTriggeredV1` shapes; `model_config = {"extra": "forbid"}`; `Field(min_length=1)` on required strings; `registry.register("<type>", 1, <Model>)` at module end. Three new types register here (NOT in a new `proactive.py` — no such module exists).
9. **`adminme/events/schemas/__init__.py`** — confirm registration is auto-loaded via `SchemaRegistry.autoload()`; no manual registry update needed beyond the `registry.register(...)` calls at the bottom of `domain.py`.
10. **`bootstrap/openclaw/cron.yaml`** — full read. Three placeholder rows already present: `reminder_dispatch / "*/15 * * * *"`, `crm_surface / "0 19 * * 0"`, `custody_brief / "0 20 * * *"`. Commit 1 verifies presence; no edit unless verification fails.
11. **`bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md`** — full read. Three on-main stubs with `**STUB**` header + `TODO(prompt-10c-iii)` markers in Approval gate / Escalation / Execution steps / What NOT to do. Commit 1 rewrites Scope sections (event-type names per §1 grounding) and fills in the four TODO sections, using `bootstrap/openclaw/programs/morning_digest.md` as the template (~70-110 lines each).
12. **`bootstrap/openclaw/programs/morning_digest.md`** — full read. Template for the four-section rewrite. Scope, Triggers, Approval gate, Escalation, Execution steps, What NOT to do.
13. **`tests/unit/bootstrap/test_openclaw_programs_loadable.py`** — read. `FULLY_POPULATED_PROGRAMS` set; `test_fully_populated_programs_have_no_todo_markers` + `test_stub_programs_carry_todo_markers`. Commit 1 moves all three (`reminder_dispatch`, `crm_surface`, `custody_brief`) from stubs → fully populated.
14. **`adminme/projections/parties/queries.py`** — read. `list_household_members(household_party_id)` and `relationships_of(party_id)` are the on-main reads. `custody_brief`'s default loader may use these via `ctx.parties_conn_factory` if the operator's bootstrap wiring provides one; the v1 default loader returns `[]` (no-op).
15. **`docs/SYSTEM_INVARIANTS.md`** — targeted: §6.13 (observation mode), §6.14 (single outbound enforcement), §6.16 (default-on), §7.3 (no projection direct writes), §7.4 (skill calls via OpenClaw), §7.7 (defensive default), §8 (no LLM SDKs in adminme/), §12.4 (tenant-identity firewall).
16. **`docs/DECISIONS.md`** — read each fully: D6 (no LLM SDK), D7 (schemas at v1), D14 (async via to_thread), D17 (proactive pipelines do not write projections directly), D22 (observation-mode default-on for new pipelines).
17. **`docs/build_log.md`** — read **Prompt 10c-ii entry** end-to-end. Carry-forwards into 10c-iii. F-3 (pack-internal pack-load test rename) is HARD.
18. **`docs/qc_rubric.md`** — full read. Job 1 (contract match), Job 2 (invariant audit), Job 4 (vision-drift) shape post-merge QC.
19. **`ADMINISTRATEME_BUILD.md` §L4 — proactive pipeline definitions.** Targeted reads: `grep -n "^#### \`reminder_dispatch\`\|^#### \`crm_surface\`\|^#### \`custody_brief\`" ADMINISTRATEME_BUILD.md` first, then `sed -n '<start>,<+15>p'` for each. Confirms cadence + default opt-in/opt-out semantics. If the line ranges have drifted since the 10c-ii build_log was written, use section labels.
20. **`WHAT-IS-ADMINISTRATEME.md`** — targeted: §"Proactive behavior" (vision-doc tiebreaker: load-reducing > additive), §"ADHD-aware" (cadence calibration).

After all reads, BEFORE writing Commit 1, write a one-paragraph scratchpad note confirming: (a) you understand the `MorningDigestPipeline` shape and will clone it three times with substitutions; (b) no inheritance from a fictional `ProactivePipeline`; (c) `outbound()` is imported inline in each pipeline's `deliver()` method; (d) three new event types register in `domain.py` alongside the existing 21; (e) constructor-injection loaders per PM-27 with no-op defaults; (f) F-3 pack-internal-test rename applies to all new pack-internal tests (pipeline AND skill).

---

## §3 Reconciliation notes (carry-forwards from 10c-ii merge)

- **F-3 HARD (10c-ii):** pack-internal pack-load tests must use unique basenames. Pattern: `packs/<kind>/<name>/tests/test_<name>_<kind>_pack_internal.py`. Applies to all 3 new pipeline packs AND all 3 new skill packs (skill collision risk if three skills all ship `test_skill.py`).
- **UT-14 OPEN (carries forward unchanged):** on-disk profile/persona/projection-loader modules don't exist; constructor-injection (PM-27) keeps three new pipelines deployable until UT-14 resolves.
- **PM-27 SOFT (graduated to convention):** every new pipeline that needs projection state takes callable loaders with no-op defaults. 10c-iii extends to three more consumers (six total: reward_dispatch + morning_digest + paralysis_detection + reminder_dispatch + crm_surface + custody_brief).
- **PM-26 SOFT:** event names that supersede a stale BUILD.md draft cite the supersession. `reward.ready` (10c-i) is canonical; the §1210 `adminme.reward.dispatched` typo is documented as superseded. 10c-iii's `reminder.dispatched` does NOT collide (different domain).

---

## §4 Assumptions to verify (against current main before Commit 1)

A1. `bootstrap/openclaw/cron.yaml` has rows for `reminder_dispatch` / `crm_surface` / `custody_brief` with the schedules quoted in §2 item 10. If absent, stop and report — the assumption that 10c-i landed placeholders was wrong.

A2. `adminme/events/schemas/domain.py` registers `DigestComposedV1` and `ParalysisTriggeredV1` at v1 (the registration-pattern template). If naming has drifted, mirror what's actually there.

A3. `ADMINISTRATEME_BUILD.md` §L4 line ranges for `reminder_dispatch` / `crm_surface` / `custody_brief` — use `grep -n "^#### " ADMINISTRATEME_BUILD.md` to locate; if the prose has drifted from what the 10c-ii build_log carry-forward assumed, prefer section-label reads and document the drift in BUILD_LOG Evidence.

A4. `parties.relationships_of` returns rows with a `label: str` column. If the column name differs (e.g. `relationship_kind`), the default `custody_brief` no-op loader stays no-op (v1 ships without coparent discovery); UT-14 resolution handles the lookup.

---

## §5 Objective

Ship three proactive pipeline packs at `packs/pipelines/{reminder_dispatch,crm_surface,custody_brief}/`, three compose skill packs at `packs/skills/{compose_reminder,compose_crm_surface,compose_custody_brief}/` at v1.0.0, three new Pydantic event-schema models registered at v1 in `adminme/events/schemas/domain.py`, and three full standing-order program files at `bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md` (replacing 10c-i stub prose). All three pipelines clone `MorningDigestPipeline`'s shape (no inheritance; structural Pipeline Protocol; constructor-injection loaders; single-site `outbound()`; defensive-default `_SKILL_FAILURE_EXCEPTIONS` catch). Observation-mode default-on per [D22]. Six standing orders now in `cron.yaml`.

---

## §6 Out of scope

- Modifying `adminme/pipelines/base.py`, `adminme/pipelines/runner.py`, `adminme/lib/observation.py`, or any file under `adminme/` other than `events/schemas/domain.py` and `events/schemas/__init__.py` (if the autoload list needs explicit update — verify it doesn't).
- New projection schema fields. `reminder_dispatch` uses event-log dedupe (recent-`reminder.dispatched` lookup), NOT a `last_reminded_at` column on `commitments`. If §L4's depth-read reveals a `last_reminded_at` field is needed for downstream §14 console-surface coherence, stop and report.
- Real on-disk loader modules (profile/persona/projection). UT-14 stays open; constructor-injection with no-op defaults bridges the gap.
- A `members` projection. There isn't one and isn't being introduced here. Member data flows through `parties` (per 10c-ii build_log) or via injected `profile_loader` callables.
- Cross-pipeline event coordination. A `reminder.dispatched` does not flow into next-tick `morning_digest`; conversely `digest.composed` does not suppress same-day `reminder.dispatched`.
- Live OpenClaw standing-order firing. Integration tests synthesize the tick event as a `dict` and pass it to `handle()` directly.
- Per-member channel selection logic (the skill outputs `suggested_channel`; the real channel decision is `adminme/lib/observation.py::outbound`'s caller territory — v1 passes the skill's suggestion through).
- Auto-confirming any commitment / state-changing the source row. Reminders, surfaces, briefs are read-side notifications.
- Subscribing to non-existent event types. Per PM-9, add `# TODO` comments only.
- Bootstrap §8 wiring of the cron rows. Out of scope; prompt 16 owns that.

---

## §7 Deliverables

### Three new event types (Pydantic models in `adminme/events/schemas/domain.py`)

| Type | Class | Required fields | Notes |
|---|---|---|---|
| `reminder.dispatched` | `ReminderDispatchedV1` | `commitment_id`, `owner_member_id`, `recipient_party_id`, `composed_text`, `channel`, `due_at` (datetime), `delivered: bool`, `validation_failed: bool` | Optional: `scheduled_send_at: datetime`. `composed_text` `min_length=1, max_length=280` SMS-friendly. Channel `Literal["imessage","sms","email","push"]`. |
| `crm.surfaced` | `CrmSurfacedV1` | `member_id`, `surfaced_party_ids: list[str]` (`min_length=1`), `gap_kind: Literal["stale_contact","upcoming_birthday","hosting_imbalance"]`, `composed_text`, `channel`, `week_of_date: str`, `delivered: bool`, `validation_failed: bool` | `gap_kind` discriminates the three subtype use-cases the stub prose described. `composed_text` `max_length=1000`. |
| `custody_brief.composed` | `CustodyBriefComposedV1` | `member_id`, `coparent_member_id`, `child_member_ids: list[str]` (`min_length=1`), `composed_text`, `channel`, `brief_date: str`, `delivered: bool`, `validation_failed: bool` | `composed_text` `max_length=1500` long-form. |

All three: `model_config = {"extra": "forbid"}`. Append three `registry.register("<type>", 1, <Model>)` calls at the bottom of `domain.py`.

### Three new compose skill packs at `packs/skills/{compose_reminder,compose_crm_surface,compose_custody_brief}/`

Each pack at v1.0.0, 09b shape — 7 files: `pack.yaml`, `SKILL.md`, `schemas/input.schema.json`, `schemas/output.schema.json`, `handler.py` (defensive `post_process`), `prompt.jinja2`, `tests/test_<skill>_skill_pack_internal.py` (F-3 rename). Per-skill input/output substitutions per the §7 table above; `provider_preferences: [anthropic/claude-haiku-4-5, anthropic/claude-opus-4-6]`, `temperature: 0.2`, `sensitivity_required: normal`, `context_scopes_required: ["member_profile_read"]`, `timeout_seconds: 12`, `outbound_affecting: false`. **NEVER** hardcode `claude-opus-4-7` or `claude-sonnet-4-7` — those aliases do not exist (per POSTMORTEM-v3-to-v4). `on_failure` shape returns `composed_text=""`, `validation_failed=true`, and the skill's other output fields at safe defaults so the pipeline's `_SKILL_FAILURE_EXCEPTIONS` catch lands on the sentinel path cleanly.

### Three new pipeline packs at `packs/pipelines/{reminder_dispatch,crm_surface,custody_brief}/`

Each pack: `pipeline.yaml`, `config.example.yaml`, `config.schema.json`, `handler.py`, `tests/test_<pack>_pack_load_pack_internal.py`. Each `handler.py` defines a class mirroring `MorningDigestPipeline`:

```
class <Pipeline>Pipeline:
    pack_id: str = "pipeline:<name>"
    version: str = "1.0.0"
    triggers: Triggers = {"schedule": "<cron>", "proactive": True}
    def __init__(self, *, <pipeline-specific loader callables, each defaulting to no-op>) -> None: ...
    async def gather(...) -> dict[str, Any]: ...
    async def compose(self, ctx, gather_result) -> dict[str, Any] | None: ...
    def validate(self, gather_result, composed) -> bool: ...
    async def deliver(self, ctx, member_id, body_text, channel) -> bool: ...
    async def _emit_<type>(self, ctx, ...) -> None: ...
    async def dispatch(self, ...) -> None: ...
    async def handle(self, event: dict, ctx: PipelineContext) -> None: ...
```

Per-pipeline differences are loader signatures, the `_SKILL_FAILURE_EXCEPTIONS` catch (same 7-tuple), the emitted-event payload shape, the absence of `validate()` for `crm_surface` (no claimed-event-id guard — `gap_kind` is the load-bearing payload field, not an id list), and the `crm_surface` per-member opt-in (default `crm_proactive_enabled=False`).

**Scan source per pipeline (loader-mediated; v1 defaults are all no-op):**
- `reminder_dispatch`: loader returns commitments where `due_at - now < lookahead_window_minutes` (default 60) AND no prior `reminder.dispatched` event in the log within `dedupe_window_minutes` (default 90) for the same `commitment_id`. Dedupe lookup inline via `ctx.event_log.read_recent("reminder.dispatched", since=now-dedupe_window)`.
- `crm_surface`: loader returns one row per member with `crm_proactive_enabled=True`; row payload includes up to `max_surfaced_per_week` (default 5) stale parties by `gap_kind`. One skill call per member; emitted `crm.surfaced` carries the list.
- `custody_brief`: loader returns one row per member with a coparent relationship set; row payload includes `child_member_ids`, next-day calendar events, handoff timing, allergen/med notes. One skill call per parent member.

**Per-member overrides config (per pipeline):**

| Pipeline | Default keys |
|---|---|
| `reminder_dispatch` | `lookahead_window_minutes: 60`, `min_reminder_gap_minutes: 15`, `dedupe_window_minutes: 90`, `skip_party_tags: ["test","fixture"]`, `per_member_overrides: {<member_id>: {reminders_enabled, lookahead_window_minutes, dedupe_window_minutes}}` |
| `crm_surface` | `threshold_days: 90`, `max_surfaced_per_week: 5`, `cadence_day_of_week: 0` (Sunday), `cadence_local_hour: 19`, `per_member_overrides: {<member_id>: {crm_proactive_enabled (default false), threshold_days}}` |
| `custody_brief` | `brief_local_hour: 20`, `per_member_overrides: {<member_id>: {custody_brief_enabled (default true), lookahead_days}}` |

### Three program-prose rewrites (stub → full) at `bootstrap/openclaw/programs/`

Each <=110 lines, per the `morning_digest.md` template: Scope (event-type names per §1 grounding), Triggers, Approval gate (None — recurring; outbound observation-gated; principal reviews observation logs), Escalation (composition miss → defensive default → emit with `validation_failed=true`, `delivered=false`; no raise into bus), Execution steps (cite §L4 numbered list), What NOT to do (no `outbound()` on sentinel path; no projection writes [§7.3]; no LLM SDK in handler [§8, D6]).

### Tests

- Unit: `tests/unit/test_pipeline_{reminder_dispatch,crm_surface,custody_brief}.py` — handler-direct cases per §8 below. ~6 cases per pipeline = 18.
- Pack-internal pack-load canaries: 3 pipeline + 3 skill = 6 files, all F-3 rename pattern.
- Schema validation: extend `tests/unit/test_event_schemas.py` (or the schemas-test file the 10c-ii F-3 walk landed on; verify path at Commit 1) with 3 new model tests = 3.
- Bootstrap programs: update `tests/unit/bootstrap/test_openclaw_programs_loadable.py`'s `FULLY_POPULATED_PROGRAMS` set to include the three; existing `test_fully_populated_programs_have_no_todo_markers` + `test_stub_programs_carry_todo_markers` then assert correctly without further edits.
- Integration: `tests/integration/test_pipeline_10c_iii_integration.py` — 6 cases (3 pipelines x 2 obs-mode paths). Each: seed `InstanceConfig` + projection DBs, mock `run_skill_fn`, mock outbound via fake `ObservationManager`, synthesize tick event dict, call `handle()`, assert event in log + dispatcher state + (on suppression) `observation.suppressed` also in log.

Floor: >=27 new tests across 10c-iii (3 schema + 6 pack-internal + 18 handler-unit + 6 integration). Overshoot welcome.

---

## §8 Commit structure (exactly four commits; messages exact)

### Commit 1 — 3 event schemas + 3 program-prose rewrites

**Files:** `adminme/events/schemas/domain.py` (append 3 BaseModel + 3 registry.register), `bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md` (full prose rewrites), `tests/unit/test_event_schemas.py` (extend; verify path), `tests/unit/bootstrap/test_openclaw_programs_loadable.py` (extend `FULLY_POPULATED_PROGRAMS`).

**Files-to-read:** 5 (`domain.py`, `morning_digest.md` template, three stubs).

**Verification:**
```
grep -F 'program_id: reminder_dispatch' bootstrap/openclaw/cron.yaml
grep -F 'program_id: crm_surface' bootstrap/openclaw/cron.yaml
grep -F 'program_id: custody_brief' bootstrap/openclaw/cron.yaml
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/test_event_schemas.py tests/unit/bootstrap/test_openclaw_programs_loadable.py -v
git add adminme/events/schemas/domain.py bootstrap/openclaw/programs/reminder_dispatch.md bootstrap/openclaw/programs/crm_surface.md bootstrap/openclaw/programs/custody_brief.md tests/unit/test_event_schemas.py tests/unit/bootstrap/test_openclaw_programs_loadable.py
git commit -m "phase 10c-iii commit 1: 3 event schemas at v1 + 3 program-file rewrites"
```

If any cron-yaml grep returns no match, **stop and report** (A1 assumption invalid). If mypy fails on new BaseModels, fix shape. Expected: mypy clean; 6 new tests pass (3 schema + 3 bootstrap-program-fully-populated checks).

### Commit 2 — 3 compose_* skill packs at v1.0.0

**Files:** `packs/skills/{compose_reminder,compose_crm_surface,compose_custody_brief}/` (7 files each = 21 files).

**Files-to-read:** 6 (all 7 of `compose_morning_digest`'s files; pack-load behavior cross-ref).

**Verification:**
```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/skills/compose_reminder/tests/ packs/skills/compose_crm_surface/tests/ packs/skills/compose_custody_brief/tests/ -v
git add packs/skills/compose_reminder/ packs/skills/compose_crm_surface/ packs/skills/compose_custody_brief/
git commit -m "phase 10c-iii commit 2: 3 compose_* skill packs at v1.0.0"
```

Expected: ~12 tests pass across the three skill packs (1 pack-load canary + 3 handler-direct cases per skill, +/-1).

### Commit 3 — 3 pipeline packs + handler-direct unit tests

**Files:** `packs/pipelines/{reminder_dispatch,crm_surface,custody_brief}/` (5 files each = 15), `tests/unit/test_pipeline_{reminder_dispatch,crm_surface,custody_brief}.py` (3).

**Files-to-read:** 6 (4 of `morning_digest`'s pack + the on-main unit-test reference for 10c-ii + base.py).

**Per-pipeline unit cases (clone the structure of `tests/unit/test_pipeline_*.py` precedents; fake event log, factory closure for loaders, `_config_override` test seam):**

- `reminder_dispatch`: emit on due commitment; no-emit outside window; no-emit on dedupe-hit; sentinel on skill-failure-defensive-default; observation-on suppression keeps event but `delivered=False`; per-member `reminders_enabled: false` skip.
- `crm_surface`: emit on opt-in member with stale parties; no-emit without opt-in; no-emit with no stale parties; clamp at `max_surfaced_per_week`; obs-on suppression; per-row skill failure does not abort tick.
- `custody_brief`: emit on member with coparent relationship; no-emit without coparent; per-member opt-out via `custody_brief_enabled: false`; emit with empty next-day calendar still composes (handoff + allergen notes); obs-on suppression; per-row skill failure does not abort tick.

**Verification:**
```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/pipelines/reminder_dispatch/tests/ packs/pipelines/crm_surface/tests/ packs/pipelines/custody_brief/tests/ tests/unit/test_pipeline_reminder_dispatch.py tests/unit/test_pipeline_crm_surface.py tests/unit/test_pipeline_custody_brief.py -v
git add packs/pipelines/reminder_dispatch/ packs/pipelines/crm_surface/ packs/pipelines/custody_brief/ tests/unit/test_pipeline_reminder_dispatch.py tests/unit/test_pipeline_crm_surface.py tests/unit/test_pipeline_custody_brief.py
git commit -m "phase 10c-iii commit 3: 3 pipeline packs + handler-direct units"
```

Expected: 3 pack-load canaries + ~18 handler-direct unit tests pass.

### Commit 4 — Integration + verify + BUILD_LOG + push

**Files:** `tests/integration/test_pipeline_10c_iii_integration.py` (6 cases), `docs/build_log.md` (append).

**Files-to-read:** 3 (existing `test_pipeline_10c_ii_integration.py` for clone template; the three pipeline `handler.py` from c3 for under-test surface; build_log tail for append point).

**Build_log Evidence template:** Append AFTER prompt 10c-ii entry under `## Build prompts`. Header: `### Prompt 10c-iii — proactive pipelines (reminder_dispatch + crm_surface + custody_brief)`. Fields: Refactored (date + prompt-file path); Session merged (PR # + 4 SHAs + merge-date); Outcome (IN FLIGHT until merge); Evidence (cite paths + invariants + test counts + observation-mode behavior + F-3 carry-forward applied + six standing orders now in cron.yaml); Carry-forward for prompt 10d / 14b/14c / 16 / 19 / soft-watch (event-log dedupe pattern extract candidate). Mirror the 10c-ii Evidence-section density.

**Verification:**
```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_pipeline_10c_iii_integration.py
git commit -m "phase 10c-iii commit 4: integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean on `adminme/`/`packs/`/`tests/`; mypy clean; suite >=+27; `verify_invariants.sh` exit 0.

**PR open:** Title `phase 10c-iii: reminder_dispatch + crm_surface + custody_brief proactive pipelines`. Body: top line "Closes the third wave of the 10c cohort per `prompts/PROMPT_SEQUENCE.md`. Three proactive pipelines literally clone `morning_digest`'s on-main shape with substitutions; three `compose_*` skill packs at v1.0.0 in 09b shape; three event schemas at v1 in `adminme/events/schemas/domain.py`; replaces 10c-i's stub prose for the three program files. No new infrastructure; no projection schema changes; observation-mode-default-on per [D22]. Notable Soft Finding (F-14 carry-forward from 10c-iii pre-authorship gate): PM-21 compliance gap — refactored prompts for 10c-i / 10c-ii not on main as of merge. Non-blocking for 10c-iii; build_log housekeeping pass scoped post-merge."

Then BUILD_LOG `Evidence` body. After PR opens, run one round of `mcp__github__pull_request_read` (status + reviews + comments). Report. **Stop.** Do not poll.

---

## §8.5 Branch strategy

**STACKED** per `docs/BRANCH-STRATEGY.md`. Per-c-band dependencies:
- c1 (schemas + program prose) branches from `main`. Self-contained; ships first.
- c2 (skill packs) branches from c1's branch. Depends on c1 because the integration tests (c4) ultimately reference both.
- c3 (pipeline packs + units) branches from c2's branch. Depends on c2 because pipeline `pipeline.yaml`'s `depends_on.skills` references `compose_*@^1.0.0`.
- c4 (integration + BUILD_LOG) branches from c3's branch. Depends on all three preceding c's.

If the Executor harness re-assigns the branch name (per universal preamble), accept and continue; the dependency order is preserved by sequential PR opens.

---

## §9 Verification (final)

Across the four-commit sequence: 4x mypy clean; >=27 new tests passing on `tests/` testpath; 6 pack-internal tests passing under `packs/`; ruff clean on `adminme/`/`packs/`/`tests/`; `scripts/verify_invariants.sh` exit 0; PR opens against `main`.

Cross-cutting [§N] / [DN] checks (enforced by `verify_invariants.sh`):
- [§6.13, §6.14, D22]: outbound is the single observation-gated enforcement point; three pipelines call `from adminme.lib.observation import outbound` exactly once each, in their `deliver()` method.
- [§7.3, D17]: no projection direct writes from any of the three handlers; only `ctx.event_log.append`.
- [§7.4, §7.7, §8, D6]: skill calls via `ctx.run_skill_fn` only; defensive catch on the same 7-tuple as `morning_digest`; no LLM SDK import anywhere under `adminme/`.
- [§12.4]: no tenant identity in any new file. Fixtures use placeholders `member-a` / `member-b` / `party-c` / etc.
- [§15, D15]: paths resolve through `InstanceConfig` in integration tests; no hardcoded literal.
- [D7]: three new types register at v1 with bare-name shape per the `domain.py` convention.
- Causation: every emitted `*.dispatched` / `*.surfaced` / `*.composed` uses `causation_id=ctx.triggering_event_id`.

---

## §10 Stop

Three proactive pipeline packs registered (`reminder_dispatch`, `crm_surface`, `custody_brief`). Three `compose_*` skill packs at v1.0.0. Three new event types (`reminder.dispatched`, `crm.surfaced`, `custody_brief.composed`) at v1 in `domain.py`. Six standing orders now in `bootstrap/openclaw/cron.yaml`. Observation-mode gating verified end-to-end. Ready for prompt 10d (pipeline/skill consistency checkpoint).
