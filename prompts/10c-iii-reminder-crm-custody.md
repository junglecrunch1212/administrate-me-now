# Prompt 10c-iii: Proactive pipelines — reminder_dispatch + crm_surface + custody_brief (+ 3 compose_* skill packs + 3 event schemas at v1)

**Phase:** `BUILD.md` §L4 — proactive pipelines, third (final) wave of the 10c cohort.

**Depends on:**
- Prompt 10c-i merged. Standing-orders infrastructure is live: `bootstrap/openclaw/programs/<six>.md` exist (six prose stubs, three of which this prompt completes); `bootstrap/openclaw/cron.yaml` is canonical; `bootstrap/openclaw/programs/README.md` defines the program-file shape; `adminme/pipelines/proactive.py::ProactivePipeline` is the base class; `reward_dispatch` is the one fully-implemented proactive pipeline on main; the `reward.ready` event type is registered at v1. The OpenClaw standing-order registration helper `adminme/lib/skill_runner/openclaw_client.py::register_standing_order(pipeline_id, schedule, handler_url)` is on main.
- Prompt 10c-ii merged. `morning_digest` and `paralysis_detection` proactive pipelines are on main with their `compose_morning_digest` + `compose_paralysis_nudge` skill packs and their `digest.composed` + `paralysis.nudge_composed` event schemas at v1. The proactive-pipeline pattern is established: schedule-triggered handler → scan projection state → compose via skill → emit composed event → `outbound()` delivery (which observation-mode-gates per `[§6.13]`).
- Prompt 10b-ii-β merged. Two-skill-chain pattern with binary-classifier-first is precedent; this prompt's `reminder_dispatch` reuses the chain shape (scan-then-compose) but skips the classifier step (every due commitment is reminded; scanning is the upstream filter).
- The `sequence-update-10c-split` PR row exists in `prompts/PROMPT_SEQUENCE.md` line 113. This prompt's slug is `10c-iii-reminder-crm-custody.md`.

**Quality bar:** literally clones `morning_digest`'s on-main proactive-pipeline shape three times with substitutions. No new infrastructure. The three pipelines are independent of each other; no cross-pipeline event flow. Skill packs follow the 09b shape. Event schemas register at v1 per `[D7]`.

**Stop condition:** Three proactive pipeline packs registered and discoverable; three `compose_*` skill packs at v1.0.0 loadable; three new event types (`reminder.dispatched`, `crm_surface.composed`, `custody_brief.composed`) registered at v1 in `adminme/events/schemas/`; three matching standing-order programs at `bootstrap/openclaw/programs/<name>.md` complete (replacing 10c-i's stub prose); `bootstrap/openclaw/cron.yaml` lists all six standing orders with their schedules; observation-mode suppresses outbound for all three; one round-trip integration test per pipeline; suite green; `scripts/verify_invariants.sh` exit 0; ≥18 new tests across 10c-iii.

---

## Universal preamble

> **Phase + repository + documentation + sandbox discipline.**
>
> You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that require those are marked `@pytest.mark.requires_live_services` and skipped. If a prompt is ambiguous about which phase it belongs to, the answer is always Phase A.
>
> **Sandbox egress is allowlisted.** `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `x-deny-reason: host_not_allowed` from Anthropic's proxy. A 403 does not mean the site is down — it means the sandbox won't reach it. If a prompt tells you to WebFetch a non-GitHub URL and you get 403, that's expected; document the gap and move on per prompt 00.5's pattern.
>
> **Session-start sequence (required):**
>
> ```
> git checkout main
> git pull origin main
> git checkout -b phase-10c-iii-reminder-crm-custody
> ```
>
> The harness may auto-reassign you to `claude/<random>` regardless of the `-b` name. Work on whatever branch you actually get — do not fight it. Do NOT `git pull` again during the session. Do NOT push to `main`. You open a PR at the end; James reviews and merges.
>
> **Poetry install as needed.** If `pytest` fails with `ModuleNotFoundError: No module named 'sqlcipher3'` (or similar), run `poetry install 2>&1 | tail -5` and retry. Sandbox warm-state quirk; do not fix in code.
>
> **Read before acting.** When a prompt tells you to read something, READ IT — do not skim, do not assume, do not infer from training. Use targeted line ranges (`sed -n '<start>,<end>p'`) for large files; never full-read `ADMINISTRATEME_BUILD.md`, `ADMINISTRATEME_CONSOLE_PATTERNS.md`, `ADMINISTRATEME_REFERENCE_EXAMPLES.md`, `ADMINISTRATEME_DIAGRAMS.md`, or `ADMINISTRATEME_CONSOLE_REFERENCE.html`. If a file or range listed in a prompt's "Read first" block does not exist in the repo, stop and report — do not proceed.
>
> **External documentation is mirrored.** When a prompt says "per OpenClaw docs" or any external-doc reference, read from `docs/reference/<section>/` (the local mirror populated by prompt 00.5). Do NOT use WebFetch to pull these docs live. If a referenced file is missing, either the mirror is incomplete (stop and finish prompt 00.5) or the content is a documented gap (check `docs/reference/_gaps.md`).
>
> **Four-commit discipline.** Every prompt structures its work as four incremental commits — typically schema/plumbing, first-module build, second-module build, then integration + verification + BUILD_LOG append + push. If a turn times out mid-commit, stop; James re-launches. Commit 4 includes appending the structured entry to `docs/build_log.md` (template in the prompt's Commit 4 block) — this is not a separate PR, it's part of Commit 4's changeset.
>
> **Cross-cutting invariant verification** is `bash scripts/verify_invariants.sh` — one line in Commit 4's verification block. It checks `[§8]`/`[D6]` (no LLM/embedding SDK imports in `adminme/`), `[§15]`/`[D15]` (no hardcoded instance paths), `[§12.4]` (no tenant identity in platform code), `[§2.2]` (projections emit only allowed system events, only from allowed files), and pipeline → projection direct writes. Exits non-zero on any violation and prints the offending lines. Do not duplicate its checks inline in the prompt.
>
> **Schema conventions.** New event types register at `v1` per `[D7]`. Migrations (and upcasters when the schema shape changes) compose forward only. Projection schemas use SQLite `CHECK` constraints on closed-enum columns and NOT on open columns. Composite PK `(tenant_id, <entity_id>)` for multi-tenant projection tables. Cross-DB FK references are documentation-only comments; SQLite cannot enforce FKs across separate projection DBs per `[§2.3]`.
>
> **Tenant-identity firewall.** Platform code under `adminme/` must not reference "James", "Laura", "Charlie", "Stice", "Morningside", or any other specific tenant name per `[§12.4]`. These names live in `tests/fixtures/` only, with `# fixture:tenant_data:ok` on the relevant line if ambiguity warrants. If a prompt's illustrative example uses a name, it's illustrative — the shipped code stays tenant-agnostic. The verify script catches violations.
>
> **Citation discipline.** When a decision or invariant shapes code, cite it in the code comment or docstring. Formats: `[§N]` for `SYSTEM_INVARIANTS.md` section N, `[DN]` for `DECISIONS.md` entry N, `[arch §N]` for `architecture-summary.md` section N, `[cheatsheet Qn]` for `openclaw-cheatsheet.md` question n, `[VISION §X]` for `WHAT-IS-ADMINISTRATEME.md` section X. BUILD_LOG entries under Evidence cite the invariants that shaped the implementation.
>
> **Observation-mode discipline.** All `outbound()` calls in this prompt's handlers MUST route through `adminme/outbound/dispatcher.py::deliver` (the on-main wrapper) which gates on `InstanceConfig.observation_mode` per `[§6.13]` and `[DIAGRAMS §9]`. Direct channel-API calls from a pipeline handler are a `[§7]` violation — `verify_invariants.sh` catches them. When observation mode is on, the dispatcher returns a `delivery.suppressed` result and emits `observation.suppressed` per `[§6.13]`; the pipeline's emitted `*.composed` event is still appended (the composition happened; only the send was suppressed).
>
> **Async-subscriber test discipline.** When a test appends an event and then reads a projection, it MUST call `notify(event_id)` on the bus and then `_wait_for_checkpoint(bus, subscriber_id, event_id)` before the read assertion. For "event NOT landing" tests, append a follow-up innocuous event after the one under test, notify + wait-for-checkpoint on the follow-up, THEN assert the original's absence.
>
> **Failure-mode handler-direct discipline.** When a test wants to assert "a malformed write does not land" (CHECK-constraint failure, IntegrityError, schema-validation reject), call the handler or sheet-builder function directly with a test connection — do not route through the bus + subscriber.
>
> **Mypy preflight for new libraries.** If a prompt adds an import from a library not already in the codebase, run `poetry run mypy adminme/ 2>&1 | tail -10` before Commit 1. If mypy complains about missing stubs, add the library to the `[[tool.mypy.overrides]]` block in `pyproject.toml` with `ignore_missing_imports = true` as part of Commit 1.
>
> **PR creation with gh/MCP fallback.** After pushing your branch, try `gh pr create` first. If `gh` returns `command not found` or a GitHub API permission error, fall back to `mcp__github__create_pull_request` with `base=main`, `head=<your branch>`, `owner=junglecrunch1212`, `repo=administrate-me-now`, title + body from the prompt's template. If the MCP tool also fails: report the exact error and stop.
>
> **Post-PR: one check, then stop.** After the PR opens, the MCP tool returns a webhook-subscription message. Do ONE round of `mcp__github__pull_request_read` with `method=get_status`, `get_reviews`, and `get_comments`. Report whatever is returned. Then STOP. Do not poll again. Do not respond to webhook events that arrive after the stop message. Do not merge the PR yourself.

---

## Read first (required)

Use `sed -n '<start>,<end>p'` for the `BUILD.md` / `DIAGRAMS.md` / `REFERENCE_EXAMPLES.md` ranges. Do NOT full-read those files. Read every section listed before producing Commit 1.

1. **`prompts/10c-i-standing-orders-infra-and-reward-dispatch.md`** — full read. This is the proactive-pipeline-infra reference. Note especially the §"Operating Context" definition of `ProactivePipeline.handle_tick(tick_event, ctx)`, the `bootstrap/openclaw/programs/<name>.md` shape, and the `cron.yaml` schedule format. The six stub programs at `bootstrap/openclaw/programs/{morning_digest,paralysis_detection,reminder_dispatch,reward_dispatch,crm_surface,custody_brief}.md` were created with one-paragraph placeholder prose; THIS PROMPT replaces the prose for the latter three. Do not modify `reward_dispatch.md` or the two completed by 10c-ii.
2. **`prompts/10c-ii-morning-digest-and-paralysis.md`** — full read. This is your literal copy-the-shape reference. Replicate its section ordering, four-commit decomposition, Read-first discipline, Operating Context, BUILD_LOG template, PR title/body shape. The differences for 10c-iii: three pipelines instead of two, no ADHD-profile branch (only `crm_surface` looks at member profile flags), and the standing-order schedules differ.
3. **`packs/pipelines/morning_digest/{pipeline.yaml,handler.py,config.example.yaml,config.schema.json}`** — full read all four. This is the on-main shape `reminder_dispatch`, `crm_surface`, and `custody_brief` clone with substitutions. The handler's `_load_config`, `_resolve_member_recipients`, `_compose_then_deliver` helpers are reusable. The schedule wiring (`triggers.schedule:` in `pipeline.yaml`) and the standing-order registration call in `__init__` are the proactive-pipeline pattern.
4. **`packs/pipelines/morning_digest/tests/test_pack_load.py`** — full read. Pack-load canary template.
5. **`tests/unit/test_pipeline_morning_digest.py`** — full read. Handler-direct unit-test patterns: fake event log, factory closure, seeded projection DBs (members, commitments, parties), `_config_override` test seam, observation-mode suppression case. Clone the structure literally.
6. **`tests/integration/test_pipeline_morning_digest_integration.py`** — full read. Integration test pattern: tmp `InstanceConfig`, seeded projection DBs, mock standing-order tick event, mock skill runner, mock outbound dispatcher. Clone literally.
7. **`packs/skills/compose_morning_digest/{pack.yaml,SKILL.md,schemas/input.schema.json,schemas/output.schema.json,handler.py,prompt.jinja2,tests/test_skill.py}`** — full read all seven. This is the on-main `compose_*` skill-pack shape. The three new skills in this prompt (`compose_reminder`, `compose_crm_surface`, `compose_custody_brief`) clone this shape with substitutions.
8. **`adminme/events/schemas/proactive.py`** — full read. Confirms the on-main `DigestComposedV1` and `ParalysisNudgeComposedV1` Pydantic models. The three new event types in this prompt register here at v1, alongside the existing two.
9. **`adminme/events/schemas/__init__.py`** — full read. Confirms the registration pattern. Update the `EVENT_TYPE_REGISTRY` dict to register the three new event types at v1 in Commit 1.
10. **`adminme/pipelines/proactive.py`** — full read. Confirms `ProactivePipeline` base class is on main from 10c-i. `handle_tick(tick_event, ctx)` is the entry point for schedule-triggered handlers. No changes here.
11. **`adminme/pipelines/runner.py`** — read only `__init__` signature + `_make_callback` + `_register_proactive_pipelines`. Confirms proactive-pipeline registration loop is on main from 10c-i. No changes here.
12. **`adminme/outbound/dispatcher.py`** — full read. Confirms `deliver(member_id, channel, content, ctx) -> DeliveryResult` is on main and gates on `InstanceConfig.observation_mode`. All three new pipelines call this; no direct channel calls.
13. **`adminme/lib/skill_runner/openclaw_client.py`** — full read. Confirms `register_standing_order(pipeline_id, schedule, handler_url)` is on main from 10c-i. The three new pipelines call this at registration time (mocked under tests).
14. **`adminme/projections/commitments/queries.py`** — read the `find_open_commitments_due_before(conn, ctx.session, due_before, owner_member_id=None)` function. `reminder_dispatch` uses this to scan due commitments.
15. **`adminme/projections/parties/queries.py`** — read the `find_parties_with_stale_contact(conn, ctx.session, threshold_days, owner_member_id=None)` function. `crm_surface` uses this to scan stale parties.
16. **`adminme/projections/members/queries.py`** — read the `find_members_with_coparent_relationship(conn, ctx.session)` and `get_member_profile_flags(conn, ctx.session, member_id)` functions. `custody_brief` uses the former; `crm_surface` uses the latter to gate on `crm_proactive_enabled`.
17. **`adminme/projections/calendar/queries.py`** — read the `find_events_for_child_next_day(conn, ctx.session, child_member_id, anchor_date)` function. `custody_brief` uses this to compose the brief.
18. **`ADMINISTRATEME_BUILD.md` §L4 — proactive pipeline definitions.**
   - Verify line ranges by `grep -n "^#### \`reminder_dispatch\`\|^#### \`crm_surface\`\|^#### \`custody_brief\`" ADMINISTRATEME_BUILD.md` first.
   - `sed -n '<reminder_dispatch start>,<+15>p'` — confirms scan cadence (every 15 min), the due-window definition (commitments with `due_at` within `reminder_lookahead_minutes`, default 60), and the per-member opt-out (`reminders_enabled: bool` profile flag).
   - `sed -n '<crm_surface start>,<+15>p'` — confirms cadence (weekly, default Sunday 18:00 local member time), the staleness threshold (`desired_frequency_days` per party, default 90), per-member opt-in via `crm_proactive_enabled: bool` (default false — opt-in not opt-out for CRM).
   - `sed -n '<custody_brief start>,<+15>p'` — confirms cadence (nightly 20:00 local member time per member with `coparent_relationship_member_id` set), the brief content (next-day calendar events for the child, custody-handoff timing, allergens/meds reminders), and the per-coparent-member opt-out (`custody_brief_enabled: bool`, default true when coparent relationship exists).
19. **`ADMINISTRATEME_DIAGRAMS.md` §9 — observation mode.** Targeted read: `grep -n "^## §9\|^### §9\." ADMINISTRATEME_DIAGRAMS.md` first, then read the §9 range (typically ~20 lines). Confirms the suppress-on-observation-mode pattern: the composed event is still appended (the composition is auditable); only `outbound.deliver` returns a suppressed result.
20. **`WHAT-IS-ADMINISTRATEME.md` §"Proactive behavior" and §"ADHD-aware".** Targeted reads. The vision-doc tiebreaker for ambiguity: proactive pipelines exist to **reduce family-member cognitive load**; if the on-the-fly composition is too noisy or too frequent, the pipeline is misaligned with vision. The default schedules in §18 above are calibrated to be quiet (one digest, one nudge, one brief, plus reminders only when commitments are due — never all three firing in the same minute). Do not tune these schedules in this prompt; they're carried forward from BUILD.md `[VISION §"Proactive behavior"]`.
21. **`docs/SYSTEM_INVARIANTS.md`** — §6.13 (observation mode), §7 (pipelines), §8 (no LLM SDKs), §8.9 (skill-runner-only outbound), §12 (tenant isolation). Targeted reads.
22. **`docs/DECISIONS.md`** — D6, D7, D14 (async via to_thread), D17 (proactive pipelines do not write projections directly), D22 (observation-mode default-on for new pipelines until per-member opt-in). Read each fully.
23. **`docs/build_log.md`** — read the **prompt 10c-ii entry** (search for `### Prompt 10c-ii`) end to end. Carry-forwards into 10c-iii are listed there. Especially the F-8 carry-forward: `compose_*` skill prompts that reference per-member personality should use the `member_profile.tone` field, which 10c-ii standardized.
24. **`docs/qc_rubric.md`** — full read. Job 4 (vision check) flags if your composed event payloads carry data that doesn't survive the round-trip into the outbound delivery (i.e., the event audits more than the send delivers, which is fine, OR less than the send delivers, which is a leak). All three new event types include the same `composed_text` field that the `outbound.deliver` call uses verbatim.

After all reads complete, BEFORE running mypy preflight, write a one-paragraph orientation comment to your scratchpad confirming: (a) you understand the on-main `morning_digest` shape and that you are NOT modifying `adminme/pipelines/proactive.py` or `adminme/pipelines/runner.py`, (b) you understand the observation-mode gate is at `adminme/outbound/dispatcher.py::deliver` and that all three new pipelines call through it without bypass, (c) you understand the three new event schemas register at v1 in `adminme/events/schemas/proactive.py` and via the registry in `adminme/events/schemas/__init__.py`, (d) you have NOT introduced any cross-pipeline event dependency — each of the three pipelines independently scans projection state, composes, and delivers, (e) you understand the F-8 carry-forward (use `member_profile.tone` in compose_* prompts). If any of (a)–(e) is shaky, re-read the relevant file before proceeding.

---

## Operating context

- **No new infrastructure.** `ProactivePipeline` base class is on main from 10c-i. The runner's proactive-pipeline registration loop is on main from 10c-i. Each pipeline registers a standing order with OpenClaw at startup via `openclaw_client.register_standing_order(pipeline_id, schedule, handler_url)` (mocked in Phase A integration tests).
- **Three new event types at v1, registered in `adminme/events/schemas/proactive.py`:**
  - `reminder.dispatched` — payload: `{commitment_id, owner_member_id, recipient_party_id, composed_text, channel, due_at, scheduled_send_at}`. Emitted at composition time (before `outbound.deliver`); the dispatcher's result is logged via a separate `delivery.*` event from `outbound/dispatcher.py` per its existing contract.
  - `crm_surface.composed` — payload: `{member_id, surfaced_party_ids: list[str], composed_text, channel, week_of_date}`.
  - `custody_brief.composed` — payload: `{member_id, coparent_member_id, child_member_ids: list[str], composed_text, channel, brief_date}`.
  - All three follow `DigestComposedV1`'s Pydantic-model shape from 10c-ii: `tenant_id`, `correlation_id`, `causation_id`, `composed_at`, plus the payload fields above. Register at `v1` per `[D7]`.
- **Three new skill packs (09b shape):**
  - `compose_reminder` — input: `{commitment, owner_member_profile, recipient_party}`; output: `{composed_text, suggested_channel, tone_note}`.
  - `compose_crm_surface` — input: `{member_profile, surfaced_parties: list}`; output: `{composed_text, suggested_channel, sort_order}`.
  - `compose_custody_brief` — input: `{member_profile, coparent_profile, child_profiles, calendar_events, handoff_timing, allergen_med_notes}`; output: `{composed_text, suggested_channel}`.
  - All three: `provider_preferences: [anthropic/claude-haiku-4-5, anthropic/claude-opus-4-7]`, `max_tokens` per content type (300 / 500 / 600 respectively), `temperature: 0.2`, `sensitivity_required: normal`, `context_scopes_required: ["member_profile_read"]`, `timeout_seconds: 12`, `outbound_affecting: false`, `on_failure: { composed_text: "", suggested_channel: "imessage", tone_note: "skill_failure_defensive_default" }` (with `tone_note` only on `compose_reminder`).
- **Three new pipeline packs at `packs/pipelines/{reminder_dispatch,crm_surface,custody_brief}/`.** Each:
  - Subclasses `ProactivePipeline` per 10c-i's base class.
  - `handle_tick(tick_event, ctx)` is the entry point. Body:
    1. Load config (per-member overrides supported via the same `_load_config` pattern as `morning_digest`).
    2. Scan projection state (see per-pipeline §"Scan source" below) inside `with ctx.commitments_conn_factory() as conn:` / `with ctx.parties_conn_factory() as conn:` / `with ctx.calendar_conn_factory() as conn:` as appropriate. Each conn-factory is on main from earlier prompts.
    3. For each scan hit, build skill input + call `await ctx.run_skill_fn("skill:compose_<name>", inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`. Wrap in `try/except _SKILL_FAILURE_TYPES` and on raise emit `*.composed` with `composed_text=""` + `tone_note="skill_failure_defensive_default"` (`reminder_dispatch` only) OR skip the row (per-row defensive-default, not per-tick; one row's failure must not abort the tick).
    4. Emit the `*.composed` event via `ctx.event_log.append(envelope, correlation_id=ctx.correlation_id, causation_id=tick_event["event_id"])`. `causation_id` is the standing-order tick event, NOT a domain event. This is the proactive-pipeline causation pattern from 10c-i.
    5. Call `await ctx.outbound_dispatcher.deliver(member_id=<recipient>, channel=<from skill output>, content=<composed_text>, ctx=ctx)`. Dispatcher gates on observation mode per `[§6.13]`; if observation-mode-on, dispatcher returns `DeliveryResult(suppressed=True)` and emits `observation.suppressed`; the `*.composed` event is still in the log.
  - **Scan source per pipeline:**
    - `reminder_dispatch` — `find_open_commitments_due_before(conn, ctx.session, due_before=now()+lookahead_window, owner_member_id=None)` against the `commitments` projection. One scan-iteration row per commitment. The `last_reminded_at` field (added by this prompt to the `commitments` projection? — NO, per Out-of-Scope: no projection schema changes; use idempotency by querying for un-reminded commitments at scan time. Specifically: filter where `due_at - now() < lookahead_window` AND `due_at - now() > min_reminder_gap_minutes` AND no prior `reminder.dispatched` event in the log within `dedupe_window_minutes` for the same `commitment_id`. The dedupe check is implemented inline via `ctx.event_log.read_recent("reminder.dispatched", since=now()-dedupe_window)`.).
    - `crm_surface` — `find_parties_with_stale_contact(conn, ctx.session, threshold_days, owner_member_id=member_id)` per member that has `crm_proactive_enabled=true`. Gather up to `max_surfaced_per_week` (default 5) per member. One scan-iteration row per member (not per party); the surface list is the input to a single `compose_crm_surface` call.
    - `custody_brief` — `find_members_with_coparent_relationship(conn, ctx.session)` → for each, gather `child_profiles`, `calendar_events` (next-day, via `find_events_for_child_next_day`), `handoff_timing` (from `member_profile.coparent_handoff_pattern`), `allergen_med_notes` (from `member_profile.child_health_flags`). One scan-iteration row per parent member (not per child); all children's data goes into one brief.
- **Per-member overrides** ride on `config.example.yaml` per the same shape `morning_digest` uses. Per-pipeline keys:
  - `reminder_dispatch`: `lookahead_window_minutes` (default 60), `min_reminder_gap_minutes` (default 15), `dedupe_window_minutes` (default 90), `per_member_overrides` (allows per-member `reminders_enabled: bool`, `lookahead_window_minutes` override, `dedupe_window_minutes` override), `skip_party_tags`.
  - `crm_surface`: `threshold_days` (default 90), `max_surfaced_per_week` (default 5), `cadence_day_of_week` (default 6, Sunday), `cadence_local_hour` (default 18), `per_member_overrides` (allows `crm_proactive_enabled: bool` — default-false enable, `threshold_days` override).
  - `custody_brief`: `brief_local_hour` (default 20), `per_member_overrides` (allows `custody_brief_enabled: bool` — default-true disable, `lookahead_days` override).
- **`[ADR-0002]`:** skills invoked via `await ctx.run_skill_fn(skill_id, inputs, SkillContext(session=ctx.session, correlation_id=ctx.correlation_id))`.
- **`[§7.3]` / `[§7.4]` / `[§7.7]` / `[§8.9]` / `[D6]` / `[D7]` / `[D17]` / `[D22]`** all enforced. Defensive-default exception tuple is the same 7 types as `morning_digest`. **`[D17]` is especially load-bearing for this prompt: none of these three pipelines write to any projection table directly. All state mutations happen via emitted events that the existing projection subscribers consume.**
- **Observation-mode default for new pipelines per `[D22]`:** all three pipelines deliver through `outbound.deliver` which gates on `InstanceConfig.observation_mode`. In Phase A the default is `observation_mode=on`, so integration tests assert `delivery.suppressed=True` in the result and `observation.suppressed` in the event log. A separate "observation off" integration test per pipeline (3 tests) confirms the non-suppressed path also works.

### What's NOT in scope for this prompt

- Modifying `adminme/pipelines/proactive.py`, `adminme/pipelines/runner.py`, `adminme/outbound/dispatcher.py`, or `adminme/lib/skill_runner/openclaw_client.py`. All four are on main from 10c-i and used as-is.
- New projection schema fields. The `last_reminded_at` field on `commitments` is OUT — `reminder_dispatch` uses event-log dedupe instead (recently-emitted `reminder.dispatched` rows are the dedupe source). If §L4's depth-read reveals `last_reminded_at` is required for downstream §14 console-surface coherence, stop and report — James scopes a follow-up `commitments` projection migration. Default disposition: skip the field.
- Cross-pipeline event coordination. The three pipelines are independent. A `reminder.dispatched` event does not flow into `morning_digest`'s next tick; conversely, a `digest.composed` event does not suppress that day's `reminder.dispatched`. If §L4 implies coordination is desired ("morning digest should mention upcoming reminders inline"), that's a `compose_morning_digest` skill-prompt change, not a cross-pipeline wiring change — and it's still out of scope for this prompt.
- Live OpenClaw standing-order firing. The integration tests mock the tick by constructing a synthetic `openclaw.tick.<pipeline_id>` event and feeding it to `handle_tick` directly. The `register_standing_order` call is mocked at test boundary.
- Per-member channel selection logic (the skill outputs `suggested_channel`; the dispatcher decides actual channel based on member's channel preferences). That's `adminme/outbound/dispatcher.py` territory — on main from 10c-i.
- Composition templates / persona-pack reward_templates (those are reward_dispatch territory from 10c-i, and don't apply to these three reminder/CRM/brief pipelines).
- Auto-confirming any commitment events. The reminder is a notification, not a state change to the commitment row.
- Subscribing to non-existent event types. Per PM-9, add TODO comments inside the `triggers.events:` block if any aspirational subscriptions surface during depth-reads.

---

## Deliverables

### Commit 1 — 3 event schemas at v1 + program file prose

**File: `adminme/events/schemas/proactive.py`** (extend) — add three Pydantic models alongside the on-main `DigestComposedV1` and `ParalysisNudgeComposedV1`:

- `ReminderDispatchedV1` — fields per the Operating Context spec above; all required except `scheduled_send_at` (optional `datetime`).
- `CrmSurfaceComposedV1` — fields per spec; `surfaced_party_ids` is `list[str]` with `min_length=1`.
- `CustodyBriefComposedV1` — fields per spec; `child_member_ids` is `list[str]` with `min_length=1` (a brief with no children is suppressed before emit, not allowed past the schema).

**File: `adminme/events/schemas/__init__.py`** (extend) — register the three new types in `EVENT_TYPE_REGISTRY` at `v1` per the existing pattern.

**File: `bootstrap/openclaw/programs/reminder_dispatch.md`** (replace 10c-i stub) — full program-file prose per the 10c-i template. Sections: §"Pipeline ID and version", §"Schedule" (the cron string + lookahead semantics), §"Trigger payload" (the OpenClaw tick event shape), §"Handler endpoint" (HTTP path the runner exposes), §"Idempotency notes" (the dedupe-via-event-log pattern), §"Observation-mode behavior", §"Failure modes" (defensive-default per-row, not per-tick). ~80-110 lines.

**File: `bootstrap/openclaw/programs/crm_surface.md`** (replace 10c-i stub) — same template; per-pipeline specifics. ~80-110 lines.

**File: `bootstrap/openclaw/programs/custody_brief.md`** (replace 10c-i stub) — same template; per-pipeline specifics. ~80-110 lines.

**File: `bootstrap/openclaw/cron.yaml`** (extend) — add three rows alongside the existing three (`morning_digest`, `paralysis_detection`, `reward_dispatch`):

```yaml
- pipeline_id: pipeline:reminder_dispatch
  schedule: "*/15 * * * *"          # every 15 min
  handler_url: "http://adminme:8080/pipelines/reminder_dispatch/tick"
  enabled: true

- pipeline_id: pipeline:crm_surface
  schedule: "0 18 * * 0"            # weekly Sunday 18:00 (member-local resolution at handler time)
  handler_url: "http://adminme:8080/pipelines/crm_surface/tick"
  enabled: true

- pipeline_id: pipeline:custody_brief
  schedule: "0 20 * * *"            # nightly 20:00 (member-local resolution at handler time)
  handler_url: "http://adminme:8080/pipelines/custody_brief/tick"
  enabled: true
```

**File: `tests/unit/test_event_schemas_proactive.py`** (extend) — add three test cases (one per new model): well-formed instantiation passes; missing required field raises `ValidationError`; the `composed_at` field auto-defaults to `datetime.utcnow()` (if Pydantic's default-factory is used, per the on-main `DigestComposedV1` pattern).

**Commit 1 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest tests/unit/test_event_schemas_proactive.py -v
git add adminme/events/schemas/proactive.py adminme/events/schemas/__init__.py bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md bootstrap/openclaw/cron.yaml tests/unit/test_event_schemas_proactive.py
git commit -m "phase 10c-iii commit 1: 3 event schemas + 3 program files + cron rows"
```

Expected: mypy clean, 3 new schema-validation tests pass.

### Commit 2 — 3 compose_* skill packs

**Directory: `packs/skills/compose_reminder/`** — full 09b-shape skill pack. Files mirror `packs/skills/compose_morning_digest/`:

- `pack.yaml` — `pack.id: skill:compose_reminder`, `version: 1.0.0`, `kind: skill`. Documentation-only `model:` block.
- `SKILL.md` — frontmatter per Operating Context spec above. After frontmatter, a 2-paragraph "What good output looks like" prose section: short, warm-but-not-saccharine reminder text; tone matches `member_profile.tone` per F-8 carry-forward; channel suggestion respects the commitment's `preferred_channel` if set.
- `schemas/input.schema.json` — JSON Schema: required `{commitment: object, owner_member_profile: object, recipient_party: object}`. `additionalProperties: false`.
- `schemas/output.schema.json` — JSON Schema: required `{composed_text, suggested_channel}`. `composed_text` `minLength: 1, maxLength: 280` (SMS-friendly). `suggested_channel` enum `["imessage", "sms", "email", "push"]`. Optional `tone_note: string`. `additionalProperties: false`.
- `prompt.jinja2` — short Jinja template; mirrors `compose_morning_digest/prompt.jinja2`'s shape but tuned for single-commitment reminder phrasing.
- `handler.py` — `post_process(raw, inputs, ctx)`: defensive coercion. Non-dict input → on_failure shape. Unknown channel → coerce to `imessage` with note appended.
- `tests/test_skill.py` — pack-loader canary (1 test) + 3-4 handler-direct cases (well-formed pass-through, channel coercion, non-dict input).

**Directory: `packs/skills/compose_crm_surface/`** — same shape, substituted for CRM:
- `composed_text` `maxLength: 1000` (longer; this is a digest of multiple stale contacts).
- Input requires `surfaced_parties: array[object]` with `minItems: 1`.
- Output adds `sort_order: array[string]` (party IDs in suggested display order).
- 3-4 handler-direct test cases.

**Directory: `packs/skills/compose_custody_brief/`** — same shape, substituted for custody:
- `composed_text` `maxLength: 1500` (long-form brief).
- Input requires `child_profiles: array[object]` with `minItems: 1`, `calendar_events: array` (may be empty for a low-event day), `handoff_timing: object`, `allergen_med_notes: array`.
- Output mirrors `compose_morning_digest` shape: `composed_text` + `suggested_channel` only.
- 3-4 handler-direct test cases.

**Commit 2 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/skills/compose_reminder/tests/ packs/skills/compose_crm_surface/tests/ packs/skills/compose_custody_brief/tests/ -v
git add packs/skills/compose_reminder/ packs/skills/compose_crm_surface/ packs/skills/compose_custody_brief/
git commit -m "phase 10c-iii commit 2: 3 compose_* skill packs at v1.0.0"
```

Expected: mypy clean, ~12 tests pass (4 per skill × 3 skills, give or take coverage cases).

### Commit 3 — 3 pipeline packs + handler-direct unit tests

**Directory: `packs/pipelines/reminder_dispatch/`:**

- `pipeline.yaml` — pack manifest. Same shape as `morning_digest/pipeline.yaml`:

```yaml
pack:
  id: pipeline:reminder_dispatch
  name: Reminder dispatch
  version: 1.0.0
  kind: pipeline
runtime:
  language: python
  python_version: ">=3.11"
  entrypoint: handler.py
  class: ReminderDispatchPipeline
triggers:
  schedule: "*/15 * * * *"
  proactive: true
depends_on:
  skills:
    - compose_reminder@^1.0.0
  projections:
    - commitments
    - parties
    - members
events_emitted:
  - reminder.dispatched
config:
  schema: config.schema.json
  example: config.example.yaml
```

- `config.example.yaml` — keys per Operating Context spec; defaults: `lookahead_window_minutes: 60`, `min_reminder_gap_minutes: 15`, `dedupe_window_minutes: 90`, `skip_party_tags: ["test", "fixture"]`, empty `per_member_overrides`.
- `config.schema.json` — JSON Schema with the above keys + per-member-overrides shape.
- `handler.py` — class `ReminderDispatchPipeline(ProactivePipeline)`. Body per Operating Context §"Scan source per pipeline → `reminder_dispatch`" above. Inline helpers: `_load_config`, `_resolve_dedupe_key`, `_iter_due_commitments`, `_emit_dispatched`. Cite `[§7.3]`, `[§8.9]`, `[D17]`, `[D22]` in module docstring.
- `tests/test_pack_load.py` — pack-loader canary (1 test).

**Directory: `packs/pipelines/crm_surface/`:**

- `pipeline.yaml` — same shape; `triggers.schedule: "0 18 * * 0"`.
- `config.example.yaml` — keys per Operating Context spec.
- `config.schema.json` — corresponding JSON Schema.
- `handler.py` — class `CrmSurfacePipeline(ProactivePipeline)`. Per-member loop; opt-in via `crm_proactive_enabled` flag.
- `tests/test_pack_load.py` — pack-loader canary.

**Directory: `packs/pipelines/custody_brief/`:**

- `pipeline.yaml` — same shape; `triggers.schedule: "0 20 * * *"`.
- `config.example.yaml` — keys per Operating Context spec.
- `config.schema.json` — corresponding JSON Schema.
- `handler.py` — class `CustodyBriefPipeline(ProactivePipeline)`. Per-coparent-member loop; default-true opt-out via `custody_brief_enabled` flag.
- `tests/test_pack_load.py` — pack-loader canary.

**File: `tests/unit/test_pipeline_reminder_dispatch.py`** — handler-direct unit cases. Clone `tests/unit/test_pipeline_morning_digest.py`'s structure (fake event log, factory closures, seeded projection DBs, `_config_override` test seam, observation-mode suppression case). Cases:
- `test_due_commitment_emits_dispatched` — seed one open commitment with `due_at` 30 min from now; assert one `reminder.dispatched` emitted with the commitment_id.
- `test_outside_lookahead_window_no_emit` — `due_at` 3 hours away; assert no emit.
- `test_dedupe_hit_no_emit` — seed event log with a recent `reminder.dispatched` for the same commitment_id; assert no second emit.
- `test_skill_failure_emits_dispatched_with_empty_composed_text` — defensive-default per-row.
- `test_observation_mode_suppresses_delivery_but_event_landed` — assert `delivery.suppressed=True` in dispatcher mock; `reminder.dispatched` still in event log.
- `test_per_member_reminders_disabled` — override `reminders_enabled: false` for member; assert no emit.

**File: `tests/unit/test_pipeline_crm_surface.py`** — handler-direct unit cases. Cases:
- `test_member_with_stale_parties_emits_composed` — seed parties with `last_contact_at` > 90 days; member opt-in true; assert `crm_surface.composed` emitted with surfaced_party_ids.
- `test_member_without_opt_in_no_emit` — `crm_proactive_enabled` false (default); assert no emit.
- `test_no_stale_parties_no_emit` — opt-in true but no stale parties.
- `test_max_surfaced_clamp` — seed 12 stale parties, `max_surfaced_per_week=5`; assert exactly 5 in surfaced_party_ids.
- `test_observation_mode_suppresses_delivery_but_event_landed`.
- `test_skill_failure_skips_row_not_tick` — one member's skill fails; another member's brief still composes.

**File: `tests/unit/test_pipeline_custody_brief.py`** — handler-direct unit cases. Cases:
- `test_member_with_coparent_relationship_emits_composed` — seed member with `coparent_relationship_member_id`, seed two child members, seed calendar; assert `custody_brief.composed` emitted with both child_member_ids.
- `test_member_without_coparent_relationship_no_emit` — no coparent set; assert no emit.
- `test_opt_out_via_per_member_override` — `custody_brief_enabled: false` override.
- `test_empty_calendar_still_emits` — no events next day; brief composes anyway with handoff timing + allergen reminders only.
- `test_observation_mode_suppresses_delivery_but_event_landed`.
- `test_skill_failure_skips_row_not_tick`.

**Commit 3 verification:**

```
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest packs/pipelines/{reminder_dispatch,crm_surface,custody_brief}/tests/ tests/unit/test_pipeline_{reminder_dispatch,crm_surface,custody_brief}.py -v
git add packs/pipelines/reminder_dispatch/ packs/pipelines/crm_surface/ packs/pipelines/custody_brief/ tests/unit/test_pipeline_{reminder_dispatch,crm_surface,custody_brief}.py
git commit -m "phase 10c-iii commit 3: 3 pipeline packs + handler-direct units"
```

Expected: mypy clean, 3 pack-load canaries + ~18 handler-direct unit tests pass.

### Commit 4 — Integration round-trip + verification + BUILD_LOG + push

**File: `tests/integration/test_pipeline_10c_iii_integration.py`** — round-trip integration tests against the live runner (mirror `tests/integration/test_pipeline_morning_digest_integration.py`). Cases (3 — one per pipeline, observation-mode-on path):

1. `test_reminder_dispatch_round_trip_observation_on` — seed `InstanceConfig(observation_mode=True)`; seed commitments DB with one open commitment due in 30 min; mock the standing-order tick; mock `run_skill` to return composed_text; mock `outbound_dispatcher.deliver` to return `DeliveryResult(suppressed=True)`. Append the tick event. Notify + wait-for-checkpoint. Assert event log contains `reminder.dispatched` with `causation_id == tick_event.event_id`. Assert `observation.suppressed` also in the log (from dispatcher).
2. `test_crm_surface_round_trip_observation_on` — analogous; seed one member with `crm_proactive_enabled=true` and 3 stale parties.
3. `test_custody_brief_round_trip_observation_on` — analogous; seed one member with `coparent_relationship_member_id` set and 2 child members.

Plus 3 additional cases (observation-mode-off path), one per pipeline:

4. `test_reminder_dispatch_round_trip_observation_off_delivers` — `observation_mode=False`; dispatcher mock returns `DeliveryResult(suppressed=False, delivery_id="dlv_test_001")`. Assert `reminder.dispatched` in log AND dispatcher was called with the right args. No `observation.suppressed`.
5. `test_crm_surface_round_trip_observation_off_delivers` — analogous.
6. `test_custody_brief_round_trip_observation_off_delivers` — analogous.

**File: `docs/build_log.md`** — append entry under `## Build prompts`, AFTER the prompt 10c-ii entry. Template:

```markdown
### Prompt 10c-iii — proactive pipelines (reminder_dispatch + crm_surface + custody_brief)
- **Refactored**: by Partner in Claude Chat, <YYYY-MM-DD>. Prompt file: prompts/10c-iii-reminder-crm-custody.md (~<NNN> lines, quality bar = 10c-ii + reuse + 3× substitution).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge-date>.
- **Outcome**: IN FLIGHT (PR open).
- **Evidence**:
  - adminme/events/schemas/proactive.py — ReminderDispatchedV1 + CrmSurfaceComposedV1 + CustodyBriefComposedV1 at v1, registered in EVENT_TYPE_REGISTRY per [D7].
  - bootstrap/openclaw/programs/{reminder_dispatch,crm_surface,custody_brief}.md — three full standing-order program files replacing 10c-i stub prose.
  - bootstrap/openclaw/cron.yaml — three new rows; full proactive-pipeline standing-order roster now at six.
  - packs/skills/compose_reminder/ — full 09b-shape skill pack at v1.0.0.
  - packs/skills/compose_crm_surface/ — full 09b-shape skill pack at v1.0.0.
  - packs/skills/compose_custody_brief/ — full 09b-shape skill pack at v1.0.0.
  - packs/pipelines/reminder_dispatch/{pipeline.yaml,config.example.yaml,config.schema.json,handler.py,tests/test_pack_load.py} — ReminderDispatchPipeline(ProactivePipeline); scans commitments for upcoming due_at within lookahead window; event-log dedupe via recent reminder.dispatched lookup; observation-mode gated via outbound.dispatcher.
  - packs/pipelines/crm_surface/... — CrmSurfacePipeline(ProactivePipeline); weekly per opt-in member; scans parties with stale contact.
  - packs/pipelines/custody_brief/... — CustodyBriefPipeline(ProactivePipeline); nightly per parent member with coparent relationship.
  - tests/unit/test_pipeline_reminder_dispatch.py — 6 handler-direct unit tests.
  - tests/unit/test_pipeline_crm_surface.py — 6 handler-direct unit tests.
  - tests/unit/test_pipeline_custody_brief.py — 6 handler-direct unit tests.
  - tests/integration/test_pipeline_10c_iii_integration.py — 6 round-trip tests (3 observation-on + 3 observation-off).
  - tests/unit/test_event_schemas_proactive.py — 3 new schema-validation tests.
  - Total new tests: <count>; suite tally <before> → <after> passed.
  - [§6.13] observation mode verified for all 3 pipelines; emitted *.composed event lands even when delivery is suppressed.
  - [§7.3] / [§7.4] / [§7.7] / [§8.9] / [§12.4] / [§15] / [D6] / [D7] / [D17] / [D22] all clean — same pattern as morning_digest.
  - [D17] specifically verified: none of the three new handlers write to projection tables directly; verify_invariants.sh exit 0.
  - Causation-id wiring: every emitted *.composed event uses causation_id=tick_event.event_id (proactive-pipeline causation pattern from 10c-i).
  - F-8 carry-forward applied: all three compose_* skill prompts reference member_profile.tone for tone-matching.
  - Six standing orders now registered: morning_digest, paralysis_detection, reward_dispatch (from 10c-i/10c-ii) + reminder_dispatch, crm_surface, custody_brief (this prompt).
- **Carry-forward for prompt 10d** (checkpoint pipeline ↔ skill consistency):
  - Six proactive pipelines now exist. 10d's pipeline/skill consistency walk should cover all six.
  - Three new event types registered at v1; 10d's event-schema review should confirm round-trippability through the existing projections (members, parties, commitments, calendar).
  - The event-log dedupe pattern (reminder_dispatch's recent-event lookup) is a new pattern. 10d may flag it as worthy of a shared helper extraction; deferred until a third pipeline needs it.
- **Carry-forward for prompt 14b/14c** (console surfaces):
  - The reminder.dispatched / crm_surface.composed / custody_brief.composed events should each have a visible audit trail in the relevant console view. 14b/14c may want to add per-event filters or a dedicated "proactive activity" surface.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - Bootstrap §3 collects per-member overrides for the three new pipelines (reminders_enabled default-true, crm_proactive_enabled default-false opt-in, custody_brief_enabled default-true when coparent relationship exists).
  - Bootstrap §6 should mention that custody_brief only fires when a coparent_relationship_member_id is set.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - With observation_mode flipped off, confirm one of each: a reminder fires for a due commitment, a CRM surface lands on Sunday evening for the opted-in member, a custody brief lands at 20:00 for the parent member with coparent.
- **Soft-watch for future:**
  - The event-log dedupe pattern in reminder_dispatch is the only one of its kind right now. If two more pipelines adopt it, extract `adminme/pipelines/_dedupe.py` with `is_duplicate_recent(event_log, event_type, key_field, key_value, within_minutes)`.
  - per_member_overrides config shape is now duplicated across morning_digest, paralysis_detection, reminder_dispatch, crm_surface, custody_brief. The duplication is accepted; future refactor extracts to `adminme/pipelines/_config.py::load_per_member_config()` once a sixth pipeline needs it.
```

**Commit 4 verification:**

```
poetry run ruff check adminme/ packs/ tests/ 2>&1 | tail -10
poetry run mypy adminme/ 2>&1 | tail -10
poetry run pytest -v 2>&1 | tail -25
bash scripts/verify_invariants.sh
git add docs/build_log.md tests/integration/test_pipeline_10c_iii_integration.py
git commit -m "phase 10c-iii commit 4: integration tests + BUILD_LOG"
git push origin HEAD
```

Expected: ruff clean on adminme/packs/tests scope; mypy clean; full suite green with ≥27 new tests across 10c-iii (3 schema + ~12 skill + 18 pipeline-unit + 6 integration); `verify_invariants.sh` exit 0.

PR title: `phase 10c-iii: reminder_dispatch + crm_surface + custody_brief proactive pipelines`

PR body: copy the BUILD_LOG `Evidence` section verbatim. Add a top line: "Closes the third wave of the 10c cohort per `prompts/PROMPT_SEQUENCE.md` line 113. Three proactive pipelines literally cloning the morning_digest shape with substitutions; three compose_* skill packs in 09b shape; three event schemas at v1; replaces 10c-i's stub prose for the three program files. No new infrastructure; no projection schema changes; observation-mode-default-on per [D22]."

After PR opens: one round of `mcp__github__pull_request_read` (status, reviews, comments), report, stop.

---

## Stop

Three proactive pipeline packs registered (`reminder_dispatch`, `crm_surface`, `custody_brief`). Three `compose_*` skill packs at v1.0.0 live. Three new event types (`reminder.dispatched`, `crm_surface.composed`, `custody_brief.composed`) registered at v1. Six standing orders now in `bootstrap/openclaw/cron.yaml`. Observation-mode gating verified end-to-end. Ready for prompt 10d (pipeline/skill consistency checkpoint).
