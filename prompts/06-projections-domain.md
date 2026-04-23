# Prompt 06: Projections domain (commitments, tasks, recurrences, calendars)

**Phase:** BUILD.md PHASE 2 continued — the domain spine at L3.
**Depends on:** Prompts 01a/01b/01c/02/03/03.5/04/05 merged to main. Projection runner from prompt 05 is live. `parties`, `interactions`, `artifacts` projections consume events and answer queries. `InstanceConfig` is fully implemented per [§15/D15]. The `§15` canary test passes (no longer skipped). `commitment.proposed@v1`, `commitment.confirmed@v1`, `task.created@v1`, `task.completed@v1`, `calendar.event_added@v1` schemas are registered.
**Estimated duration:** 3.5–5 hours across four batch commits.
**Stop condition:** Four new projections (`commitments`, `tasks`, `recurrences`, `calendars`) consume events, expose query functions, survive rebuild-from-log. 14 new event types registered at v1. Runner discovers and dispatches to all seven projections (05's three + 06's four). All prompt-03/04/05 tests still pass. ~35 new tests pass. Smoke script extended to exercise domain events.

---

## Phase + repository + documentation + sandbox discipline

You are in **Phase A**: generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. The Mac Mini is not involved. You do not contact live OpenClaw, live BlueBubbles, live Plaid, or any other external service. Tests that would require those are marked `@pytest.mark.requires_live_services` and skipped.

Sandbox egress is allowlisted. `github.com` and `raw.githubusercontent.com` work. Most other hosts return HTTP 403 `host_not_allowed` — expected, move on.

**Session start (required sequence):**

```bash
git checkout main
git pull origin main
git checkout -b phase-06-projections-domain
# (harness may override with claude/<random>; work on whatever branch gets assigned)
```

Do NOT `git pull` again during the session. Do NOT push to `main`. You WILL open a PR at the end via `gh pr create`; James reviews and merges it.

**Verify prerequisites on main:**

```bash
# Tier-2 docs present
ls -la docs/SYSTEM_INVARIANTS.md docs/DECISIONS.md docs/architecture-summary.md \
       docs/openclaw-cheatsheet.md docs/reference/_manifest.yaml

# Prompt 05's artifacts present
ls -la adminme/projections/base.py adminme/projections/runner.py \
       adminme/projections/parties/schema.sql \
       adminme/projections/parties/handlers.py \
       adminme/projections/parties/queries.py \
       adminme/projections/interactions/schema.sql \
       adminme/projections/artifacts/schema.sql \
       adminme/lib/instance_config.py \
       scripts/demo_projections.py

# Prompt-05 tests green
poetry run pytest tests/unit/test_projection_parties.py \
                 tests/unit/test_projection_interactions.py \
                 tests/unit/test_projection_artifacts.py \
                 tests/unit/test_schema_registry.py -q 2>&1 | tail -3

# Canary: §15/D15 instance-path discipline (should PASS, not skip)
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v 2>&1 | tail -5

# Event-type registry tail
poetry run python -c "from adminme.events import registry; \
  print('\n'.join(sorted(f'{t}@v{v}' for t,v in registry.all_registered())))" \
  | grep -E "commitment|task\.|recurrence|calendar\.event" || true
```

Expected registry tail: `commitment.proposed@v1`, `commitment.confirmed@v1`, `task.created@v1`, `task.completed@v1`, `calendar.event_added@v1`. If any of those are missing, STOP — prompt 04 did not fully land. If the §15 canary is skipped rather than passing, STOP — prompt 05 did not fully land.

---

## Read first (required, in this order)

**Read in the order listed. Every read uses a specific line range or a complete small file — no full reads of BUILD.md or REFERENCE_EXAMPLES.md, no full reads of docs/SYSTEM_INVARIANTS.md without a range. Context budget is load-bearing; wide reads cost you the session.**

1. **`docs/DECISIONS.md`** — full. Pay particular attention to **D4** (CRM is a shared L3 projection concern, not a product concern; Core/Comms/Automation are peers of Capture for CRM reads — shapes who reads these projections), **D7** (schema_version is monotonically increasing per event_type; upcasters form a chain), **D13** (sqlcipher3-binary), **D14** (async API / sync driver via `asyncio.to_thread`), **D15** (instance-path discipline — every new projection DB path goes through `InstanceConfig`).

2. **`docs/SYSTEM_INVARIANTS.md`** — targeted sections only. Use these ranges:
   - `sed -n '20,34p' docs/SYSTEM_INVARIANTS.md` — **§1** (event log sacred, 10 invariants — confirms projections never emit).
   - `sed -n '35,46p' docs/SYSTEM_INVARIANTS.md` — **§2** (projections derived, deterministic apply, 11 total — all 7 invariants binding here).
   - `sed -n '59,70p' docs/SYSTEM_INVARIANTS.md` — **§4** (commitments + tasks + recurrences domain spine — 7 invariants). This is the section that most shapes prompt 06.
   - `sed -n '71,81p' docs/SYSTEM_INVARIANTS.md` — **§5** (calendars read-only from AdministrateMe's perspective; no writeback; `tasks.due_date` does not create calendar events).
   - `sed -n '181,191p' docs/SYSTEM_INVARIANTS.md` — **§12** (multi-tenant isolation; `tenant_id` required on every query; no hardcoded tenant identity in platform code).
   - `sed -n '192,206p' docs/SYSTEM_INVARIANTS.md` — **§13** (explicit non-connections — items 1, 2, 3 in particular for this prompt).
   - `sed -n '216,225p' docs/SYSTEM_INVARIANTS.md` — **§15** (instance-path discipline; canary test from prompt 05 must stay green).

3. **`docs/architecture-summary.md`** — targeted. `sed -n '1,80p' docs/architecture-summary.md` covers the five-layer overview + projection table row 3.4–3.7 context. No further reads needed from this file.

4. **`ADMINISTRATEME_BUILD.md`** — four targeted ranges, all small:
   - `sed -n '601,624p' ADMINISTRATEME_BUILD.md` — **§3.4 `commitments`** canonical DDL.
   - `sed -n '625,663p' ADMINISTRATEME_BUILD.md` — **§3.5 `tasks`** canonical DDL + prose on tasks-vs-commitments.
   - `sed -n '664,681p' ADMINISTRATEME_BUILD.md` — **§3.6 `recurrences`** canonical DDL.
   - `sed -n '682,714p' ADMINISTRATEME_BUILD.md` — **§3.7 `calendars`** canonical DDL (two tables: `calendar_events` + `availability_blocks`).

5. **`ADMINISTRATEME_REFERENCE_EXAMPLES.md`** — one range: `sed -n '2037,2245p' ADMINISTRATEME_REFERENCE_EXAMPLES.md` — **§5** (`commitment.proposed` canonical event shape, consumer list, downstream events, failure modes, testing requirements). The example JSON uses tenant-specific names ("Stice", "Kate", "James") — those are legitimate in example documentation but MUST NOT appear in your `adminme/` source. Structural patterns only.

6. **`adminme/events/schemas/domain.py`** — full read. This is where you extend. Understand the existing `CommitmentProposedV1`, `CommitmentConfirmedV1`, `TaskCreatedV1`, `TaskCompletedV1` models; copy their structural conventions (imports, Pydantic config, registry calls at bottom).

7. **`adminme/events/schemas/ingest.py`** — full read. `CalendarEventAddedV1` lives here. You will add `CalendarEventUpdatedV1` and `CalendarEventDeletedV1` here.

8. **`adminme/projections/parties/schema.sql`**, **`adminme/projections/parties/handlers.py`**, **`adminme/projections/parties/queries.py`**, **`adminme/projections/parties/__init__.py`** — full reads of all four. This is your quality-bar template for the commit-2/3 projections. Match structural conventions: one handler function per subscribed event type; `INSERT ... ON CONFLICT DO UPDATE` idempotency; `tenant_id` required kwarg on every query; `# TODO(prompt-08): wrap with Session scope check` above every query def; no ambient session state.

9. **`adminme/projections/runner.py`** — targeted. `sed -n '1,60p' adminme/projections/runner.py` — understand how projections register and how `rebuild()` drops+replays. You will register four more projections in `scripts/demo_projections.py`.

10. **`tests/unit/test_projection_parties.py`** — targeted. `sed -n '1,80p' tests/unit/test_projection_parties.py` — copy the fixture conventions and scope-canary stub pattern.

**Do NOT read** during this session:
- Sections of BUILD.md for later projections (§3.8–§3.11 — prompt 07) or later layers (§L4 onward).
- `ADMINISTRATEME_CONSOLE_PATTERNS.md` or `ADMINISTRATEME_CONSOLE_REFERENCE.html` — console is prompt 14.
- `ADMINISTRATEME_DIAGRAMS.md` sections other than §1/§2 which you already absorbed at 01b.
- Any file under `adminme/products/` — products are prompts 13a/b.

---

## Operating context

You are writing four more L3 projections on top of the runner/base/InstanceConfig scaffold prompt 05 established. Same pattern, different tables:

- **`commitments`** — obligation tracker. Every row is an obligation one party owes another. Pipelines propose via `commitment.proposed`; principals confirm via `commitment.confirmed`; fulfillment via `commitment.completed`; dismissal via `commitment.dismissed`; transitions via `commitment.snoozed` / `commitment.cancelled` / `commitment.delegated` / `commitment.edited`; expiry (14-day stale-proposal timeout) via `commitment.expired` (emitted by a prompt-10c timer pipeline — you only register the schema and handler here).

- **`tasks`** — household work. NOT the same thing as commitments (`[§4.3]`, `[§13.1]`). Tasks are things the household does (mow lawn, renew license). Commitments are obligations to other parties. The inbox surface merges them at read time (later), but the projections are separate.

- **`recurrences`** — RFC 5545 RRULE templates. A recurrence firing does NOT auto-create a task (`[§4.5]`); the prompt-10c `reminder_dispatch` pipeline materializes occurrences as tasks or surfaces them directly. Your job here is to store the template and advance `next_occurrence` when a completion event lands.

- **`calendars`** — external calendar events. External adapters (prompt 11) ingest Google Calendar / iCloud CalDAV / Microsoft Graph events and emit `calendar.event_added` / `_updated` / `_deleted`. You store them. You do NOT write back to external providers (`[§5.1]`, `[§5.2]`).

Each projection follows the prompt-05 pattern exactly:

1. **Subscribes to a subset of event types** via the `EventBus`. One subscriber per projection.
2. **Writes rows into its own SQLite database.** `<instance_dir>/projections/<name>.db`, resolved via `InstanceConfig.projection_db_path(name)`. SQLCipher-encrypted with the instance master key.
3. **Exposes query functions** taking `(conn, *, tenant_id, ...)` — plain functions; `# TODO(prompt-08): wrap with Session scope check` above each `def`.
4. **Is deterministic.** `INSERT ... ON CONFLICT DO UPDATE` idempotency. `rebuild()` drops the DB, re-applies schema.sql, replays the event log from event 0, produces byte-identical state.
5. **Never emits events.** Projections consume only (`[§1.1]`, `[§2.2]`).

**Critical cross-DB FK note.** BUILD.md's canonical DDL declares FK relationships — e.g. `commitments.owed_by_party REFERENCES parties(party_id)`, `tasks.assignee_party REFERENCES parties(party_id)`, `tasks.recurring_id REFERENCES recurrences(recurrence_id)`. Because each projection lives in its own SQLite DB per prompt 05's pattern, **SQLite cannot enforce these FKs across DBs**. Keep the `REFERENCES` clauses in `schema.sql` as documentation of semantic intent; handlers perform no cross-DB validation (projections trust events — if an event carries a bad `party_id`, the row lands anyway; integrity is preserved upstream by the pipeline that emitted the event). Integration test `test_projection_rebuild.py` asserts referential integrity via a cross-DB JOIN in Python after a 800-event replay.

---

## Out of scope

- Do NOT build `money`, `places_assets_accounts`, `vector_search`, `xlsx_workbooks` projections — prompt 07.
- Do NOT build L4 pipelines that emit these events (`commitment_extraction`, `reminder_dispatch`, `recurrence_extraction`) — prompts 10a–c.
- Do NOT build the background timer pipeline that emits `commitment.expired` — prompt 10c. You register the event type; that is all.
- Do NOT auto-materialize occurrences from recurrences (`[§4.5]`). Handler for `recurrence.completed` advances `next_occurrence` using the RRULE. It does NOT create a `task.created` event. It does NOT emit anything.
- Do NOT auto-complete commitments from task completion (`[§4.6]`, `[§13.1]`). `task.completed` updates `tasks`; it does not touch `commitments`. A later pipeline may bridge them — not your prompt.
- Do NOT create calendar events from task `due_date` fields (`[§5.3]`). `tasks` and `calendar_events` are separate projections merged only at read time in later surfaces.
- Do NOT write back to external calendar providers (`[§5.1]`, `[§5.2]`). `calendars` is append-from-adapter, read-only-outbound.
- Do NOT implement `Session` / scope enforcement — prompt 08. Query functions ship plain; `# TODO(prompt-08)` marker on each.
- Do NOT wire projections into any FastAPI router — products are prompts 13a/b.
- Do NOT ship plugin-provided projection support. Core-only.
- Do NOT generate xlsx sheets for these projections — prompt 07's xlsx daemon consumes your query functions.
- Do NOT add LLM SDKs to `pyproject.toml` or import `anthropic` / `openai` anywhere (`[§8]`, `[D6]`).
- Do NOT reference tenant identity in platform code — no "James", "Laura", "Charlie", "Stice", "Morningside", etc. in `adminme/`. `tests/fixtures/` may use illustrative names; `adminme/` never does (`[§12.4]`).

---

## Incremental commit discipline — MANDATORY

Four batch commits. Same anti-timeout pattern as prompts 01b, 01c, 02, 04, 05. If a turn times out mid-section: STOP. Do not attempt recovery. The operator re-launches.

### Commit 1 — Event-type registry expansion

Add 14 new Pydantic models and their registry calls. All register at **v1** per [D7] — new event types start at 1 regardless of what REFERENCE_EXAMPLES.md narrative says about `commitment.proposed@v3` (that narrative describes hypothetical upcasting history; registry state is authoritative and today's starting version is 1).

**In `adminme/events/schemas/domain.py`** — append to existing models:

- `CommitmentCompletedV1` — `commitment_id: str`, `completed_at: str` (ISO 8601), `completed_by_party_id: str`, `completion_note: str | None = None`.
- `CommitmentDismissedV1` — `commitment_id: str`, `dismissed_at: str`, `dismissed_by_party_id: str`, `reason: str | None = None`.
- `CommitmentEditedV1` — `commitment_id: str`, `edited_at: str`, `edited_by_party_id: str`, `field_updates: dict[str, Any]`. (Named `edited` per REFERENCE_EXAMPLES.md §5 canonical vocab, NOT `updated` — previous prompt draft had this wrong.)
- `CommitmentSnoozedV1` — `commitment_id: str`, `snoozed_at: str`, `snoozed_until: str`, `snoozed_by_party_id: str`.
- `CommitmentCancelledV1` — `commitment_id: str`, `cancelled_at: str`, `cancelled_by_party_id: str`, `reason: str | None = None`.
- `CommitmentDelegatedV1` — `commitment_id: str`, `delegated_at: str`, `delegated_by_party_id: str`, `delegated_to_party_id: str`.
- `CommitmentExpiredV1` — `commitment_id: str`, `expired_at: str`. (Emitted by prompt-10c timer; prompt 06 only registers the type.)
- `TaskUpdatedV1` — `task_id: str`, `updated_at: str`, `updated_by_party_id: str | None = None`, `previous_status: str | None = None`, `new_status: str | None = None`, `field_updates: dict[str, Any]`. One event covers all intermediate status transitions (inbox→next→in_progress→waiting_on→deferred) plus edits. Rationale: task transitions are structurally symmetric; commitments' are not (each commitment transition has distinct semantic payload — `delegated` carries `delegated_to_party_id`, `snoozed` carries `snoozed_until`, etc. — so those get distinct event types).
- `TaskDeletedV1` — `task_id: str`, `deleted_at: str`, `deleted_by_party_id: str`. Soft-delete: handler sets `status='dismissed'`; row is not removed. Rebuild correctness requires the row persist.
- `RecurrenceAddedV1` — `recurrence_id: str`, `linked_kind: str` (one of `party` | `asset` | `account` | `household`), `linked_id: str`, `kind: str`, `rrule: str`, `next_occurrence: str`, `lead_time_days: int = 0`, `trackable: bool = False`, `notes: str | None = None`.
- `RecurrenceCompletedV1` — `recurrence_id: str`, `completed_at: str`, `completed_by_party_id: str | None = None`, `occurrence_date: str | None = None`. Handler advances `next_occurrence` via RRULE.
- `RecurrenceUpdatedV1` — `recurrence_id: str`, `updated_at: str`, `field_updates: dict[str, Any]`. Covers RRULE changes, lead-time changes, trackable flips, notes edits.

**In `adminme/events/schemas/ingest.py`** — append alongside existing `CalendarEventAddedV1`:

- `CalendarEventUpdatedV1` — `calendar_event_id: str`, `calendar_source: str`, `external_uid: str`, `updated_at: str`, `field_updates: dict[str, Any]`. Emitted by calendar adapters on external-side update detection.
- `CalendarEventDeletedV1` — `calendar_event_id: str`, `calendar_source: str`, `external_uid: str`, `deleted_at: str`.

Register each with `registry.register("<type>", 1, <ModelClass>)`.

All 14 models use `model_config = {"extra": "forbid"}` per existing convention.

**Verify commit 1 before proceeding:**

```bash
poetry run pytest tests/unit/test_schema_registry.py -v 2>&1 | tail -10
# Expected: all existing schema tests pass, registry now shows 14 additional types.

poetry run python -c "from adminme.events import registry; \
  types = sorted(f'{t}@v{v}' for t,v in registry.all_registered()); \
  new_types = [t for t in types if any(p in t for p in \
    ['commitment.completed','commitment.dismissed','commitment.edited', \
     'commitment.snoozed','commitment.cancelled','commitment.delegated', \
     'commitment.expired','task.updated','task.deleted', \
     'recurrence.added','recurrence.completed','recurrence.updated', \
     'calendar.event_updated','calendar.event_deleted'])]; \
  print('\n'.join(new_types)); \
  assert len(new_types) == 14, f'expected 14, got {len(new_types)}'"
# Expected: 14 types listed, assertion passes.

git add adminme/events/schemas/domain.py adminme/events/schemas/ingest.py
git commit -m "phase 06-1: register 14 domain event types (commitments/tasks/recurrences/calendars)"
```

If the registry assertion fails or any schema test fails, STOP and fix before commit 2.

### Commit 2 — `commitments` + `tasks` projections

Two projection modules, each with four files: `schema.sql`, `handlers.py`, `queries.py`, `__init__.py`. Match the `adminme/projections/parties/` pattern structurally.

**`adminme/projections/commitments/schema.sql`** — one table per BUILD.md §3.4 verbatim column list. Keep FK clauses (`REFERENCES parties(party_id)`, `REFERENCES interactions(interaction_id)`) as documentation — they are not enforced cross-DB. Add a file-header docstring comment explaining: "Cross-DB FK references are documentation only. Integrity is preserved by upstream pipelines (`[§2.3]`), verified by integration test rebuild."

**`adminme/projections/commitments/handlers.py`** — nine handler functions, one per subscribed event type:
- `apply_commitment_proposed(envelope, conn)` — INSERT with `status='pending'`, populate `proposed_at`, `source_*`, `owed_by_party` (from `owed_by_member_id`), `owed_to_party`, `kind`, `description` (from `text_summary`), `confidence`, `due_at` (from `suggested_due`). ON CONFLICT DO UPDATE keyed on `commitment_id`.
- `apply_commitment_confirmed(envelope, conn)` — UPDATE `status='pending'`→nothing-changed-structurally; set `confirmed_at`, `confirmed_by`.
- `apply_commitment_completed(envelope, conn)` — UPDATE `status='done'`, set `completed_at`.
- `apply_commitment_dismissed(envelope, conn)` — UPDATE `status='cancelled'`, set `completed_at` (semantic: the dismissal ends the commitment's lifecycle).
- `apply_commitment_edited(envelope, conn)` — UPDATE only the fields listed in `field_updates`. Do NOT touch `status`.
- `apply_commitment_snoozed(envelope, conn)` — UPDATE `status='snoozed'`, `due_at=snoozed_until`.
- `apply_commitment_cancelled(envelope, conn)` — UPDATE `status='cancelled'`, `completed_at=cancelled_at`.
- `apply_commitment_delegated(envelope, conn)` — UPDATE `status='delegated'`, `owed_by_party=delegated_to_party_id`.
- `apply_commitment_expired(envelope, conn)` — UPDATE `status='cancelled'` with no completed_by. Distinct from `dismissed` semantically (expiry = nobody acted; dismissed = someone dismissed).

Every handler sets `last_event_id` to the envelope's event_id. Every handler is idempotent: re-applying the same event produces the same row state.

**`adminme/projections/commitments/queries.py`** — five functions. Every function has `# TODO(prompt-08): wrap with Session scope check` on the line above `def`. Every function takes `conn` as first arg and `tenant_id` as required kwarg.

- `get_commitment(conn, *, tenant_id, commitment_id) -> dict | None`
- `open_for_member(conn, *, tenant_id, member_party_id) -> list[dict]` — `status IN ('pending', 'snoozed')` AND `owed_by_party = ?`.
- `pending_approval(conn, *, tenant_id, limit=50) -> list[dict]` — `status='pending'` ordered by `proposed_at DESC`.
- `by_party(conn, *, tenant_id, party_id) -> list[dict]` — rows where party is `owed_by` OR `owed_to`, ordered by most recent.
- `by_source_interaction(conn, *, tenant_id, interaction_id) -> list[dict]` — for loop detection (prompt 10b's `noise_filtering`).

**`adminme/projections/commitments/__init__.py`** — exports `CommitmentsProjection(Projection)` with `name='commitments'`, `version=1`, `subscribes_to=['commitment.proposed', 'commitment.confirmed', 'commitment.completed', 'commitment.dismissed', 'commitment.edited', 'commitment.snoozed', 'commitment.cancelled', 'commitment.delegated', 'commitment.expired']`, `schema_path=Path(__file__).parent / 'schema.sql'`, and an `apply()` method dispatching on `envelope['type']` to the right handler.

**`adminme/projections/tasks/schema.sql`** — one table per BUILD.md §3.5 verbatim. Include self-reference `goal_ref` (sub-tasks under a goal) — stored as plain TEXT, parent task_id. No cascading. `# TODO(prompt-10c): whatnow pipeline traverses goal_ref DAG for ranking` comment in the schema.sql header.

**`adminme/projections/tasks/handlers.py`** — four handler functions:
- `apply_task_created(envelope, conn)` — INSERT with full field set from payload. ON CONFLICT DO UPDATE keyed on `task_id`.
- `apply_task_completed(envelope, conn)` — UPDATE `status='done'`, set `completed_at`, `completed_by`.
- `apply_task_updated(envelope, conn)` — UPDATE only the fields listed in `field_updates`. If `new_status` is set, write it to `status`.
- `apply_task_deleted(envelope, conn)` — UPDATE `status='dismissed'`; do NOT remove the row (soft delete, required for rebuild correctness).

**`adminme/projections/tasks/queries.py`** — six functions, same discipline:
- `get_task(conn, *, tenant_id, task_id) -> dict | None`
- `today_for_member(conn, *, tenant_id, member_party_id, today_iso) -> list[dict]` — `assignee_party=?` OR `assignee_party IS NULL`; `due_date <= today_iso OR status='in_progress'`; `status NOT IN ('done','dismissed','deferred')`.
- `open_for_member(conn, *, tenant_id, member_party_id) -> list[dict]` — broader than today; includes `next`, `waiting_on`.
- `by_context(conn, *, tenant_id, domain: str) -> list[dict]` — filtered by `domain` column.
- `in_status(conn, *, tenant_id, status: str) -> list[dict]`
- `sub_tasks_of(conn, *, tenant_id, goal_ref_task_id) -> list[dict]` — all tasks with `goal_ref=?`. `# TODO(prompt-10c): whatnow pipeline uses this for goal-DAG ranking`.

**`adminme/projections/tasks/__init__.py`** — exports `TasksProjection(Projection)` with `name='tasks'`, `version=1`, `subscribes_to=['task.created', 'task.completed', 'task.updated', 'task.deleted']`.

**Tests — `tests/unit/test_projection_commitments.py`** (≥10 tests):
- Apply `commitment.proposed`; row exists with `status='pending'`, correct `owed_by_party`, `proposed_at` populated.
- Apply `commitment.confirmed` after `proposed`; `confirmed_at` and `confirmed_by` populated.
- Apply `commitment.completed`; `status='done'`, `completed_at` populated.
- Apply `commitment.dismissed`; `status='cancelled'`.
- Apply `commitment.edited`; only listed fields updated.
- Apply `commitment.snoozed`; `status='snoozed'`, `due_at` updated.
- Apply `commitment.cancelled`; `status='cancelled'`.
- Apply `commitment.delegated`; `owed_by_party` updated to `delegated_to_party_id`.
- Apply `commitment.expired`; `status='cancelled'` with no `completed_by`.
- Idempotency: apply same proposal event twice; one row, same state.
- Rebuild correctness: 30-event fixture, snapshot, rebuild, snapshot, byte-equal.
- Multi-tenant isolation: events for `tenant-a` do not appear in `tenant-b` queries.
- Scope canary stub: append a `sensitivity='privileged'` envelope, assert row lands with correct sensitivity column. (Prompt 08 extends to `ScopeViolation`.)

**Tests — `tests/unit/test_projection_tasks.py`** (≥10 tests):
- Apply `task.created`; row exists with all fields from payload.
- Apply `task.completed`; `status='done'`, `completed_at`/`completed_by` populated.
- Apply `task.updated` with `new_status='in_progress'`; `status` changed, other fields untouched.
- Apply `task.updated` with `field_updates={'energy': 'high', 'micro_script': 'open doc'}`; those fields changed, nothing else.
- Apply `task.deleted`; row still present, `status='dismissed'`.
- Sub-task hierarchy: create parent with `task.created`, create child with `goal_ref=<parent_id>`; `sub_tasks_of(parent_id)` returns the child.
- `today_for_member` filters correctly by member and date window.
- Idempotency: apply same event twice; one row, same state.
- Rebuild correctness: 30-event fixture, snapshot, rebuild, snapshot, byte-equal.
- Multi-tenant isolation.
- Scope canary stub.

Register `CommitmentsProjection` and `TasksProjection` in `scripts/demo_projections.py` (extending, not replacing — prompt 05 left the script with three projections; you add two more).

**Verify commit 2 before proceeding:**

```bash
poetry run pytest tests/unit/test_projection_commitments.py \
                 tests/unit/test_projection_tasks.py -v 2>&1 | tail -5
# Expected: ≥20 tests passing, 0 failing.

poetry run pytest tests/unit/test_projection_parties.py \
                 tests/unit/test_projection_interactions.py \
                 tests/unit/test_projection_artifacts.py -q 2>&1 | tail -3
# Expected: all prompt-05 tests still green.

git add adminme/projections/commitments/ adminme/projections/tasks/ \
        tests/unit/test_projection_commitments.py \
        tests/unit/test_projection_tasks.py \
        scripts/demo_projections.py
git commit -m "phase 06-2: commitments + tasks projections"
```

If any test fails, STOP and fix before commit 3.

### Commit 3 — `recurrences` + `calendars` projections

Same module shape. Key details below.

**`adminme/projections/recurrences/schema.sql`** — BUILD.md §3.6 verbatim. Add a file-header comment: "`# [§4.5]` — recurrence firing does not auto-create tasks. Occurrence materialization is a pipeline concern (prompt 10c `reminder_dispatch`)."

**`adminme/projections/recurrences/handlers.py`** — three handlers:
- `apply_recurrence_added(envelope, conn)` — INSERT all fields from payload. ON CONFLICT DO UPDATE keyed on `recurrence_id`.
- `apply_recurrence_completed(envelope, conn)` — ADVANCE `next_occurrence` using RRULE. Use `dateutil.rrule.rrulestr(row.rrule, dtstart=datetime.fromisoformat(row.next_occurrence)).after(datetime.fromisoformat(envelope.payload.completed_at))` to compute the next firing. UPDATE `next_occurrence=<new ISO>` and store `last_event_id`. Do NOT emit anything. Do NOT create tasks.
- `apply_recurrence_updated(envelope, conn)` — UPDATE only fields listed in `field_updates`. If `rrule` is in the updates, recompute `next_occurrence` from the new RRULE using `datetime.utcnow()` as the starting point.

If `python-dateutil` is not already in `pyproject.toml`, add it (`python-dateutil = "^2.8"`). Most likely it is already a transitive dep; check with `poetry show python-dateutil` first. If present, do not re-add.

**`adminme/projections/recurrences/queries.py`** — four functions:
- `get_recurrence(conn, *, tenant_id, recurrence_id) -> dict | None`
- `due_within(conn, *, tenant_id, days: int, as_of_iso: str) -> list[dict]` — `next_occurrence <= as_of + days`, ordered by `next_occurrence ASC`.
- `for_member(conn, *, tenant_id, member_party_id) -> list[dict]` — joins via `linked_kind='party'` AND `linked_id=?` OR `linked_kind='household'`.
- `all_active(conn, *, tenant_id) -> list[dict]` — no status field on recurrences; all rows are considered active. Returns all ordered by `next_occurrence ASC`.

**`adminme/projections/recurrences/__init__.py`** — exports `RecurrencesProjection(Projection)` with `name='recurrences'`, `version=1`, `subscribes_to=['recurrence.added', 'recurrence.completed', 'recurrence.updated']`.

**`adminme/projections/calendars/schema.sql`** — BUILD.md §3.7 verbatim. **Both tables**: `calendar_events` AND `availability_blocks`. Note the `UNIQUE (calendar_source, external_uid)` constraint on `calendar_events` — this is load-bearing for adapter idempotency in prompt 11 (Gmail/Calendar/CalDAV polling may replay events; the UNIQUE lets the upsert handle it). Add a file-header comment: "`# [§5.1]` — calendars projection is populated by external adapters; AdministrateMe does not write back to external providers. `# [§5.3]` — `tasks.due_date` does not create calendar events. These projections are merged only at read time in surface layers."

**`adminme/projections/calendars/handlers.py`** — three handlers:
- `apply_calendar_event_added(envelope, conn)` — INSERT with full field set. ON CONFLICT on `(calendar_source, external_uid)` DO UPDATE (this is what makes adapter idempotency work).
- `apply_calendar_event_updated(envelope, conn)` — UPDATE only fields in `field_updates`. Match on `(calendar_source, external_uid)` via the UNIQUE index.
- `apply_calendar_event_deleted(envelope, conn)` — DELETE the row. (Hard delete, unlike tasks — calendar events have a corresponding external source of truth, and an event that's gone externally should be gone internally. `[§5.2]`.)

**`adminme/projections/calendars/queries.py`** — five functions:
- `get_calendar_event(conn, *, tenant_id, calendar_event_id) -> dict | None`
- `today(conn, *, tenant_id, member_party_id, today_iso, tz_name) -> list[dict]` — events where `owner_party=?` OR party is in `attendees_json`; `start_at` within today's window. Privacy filter deferred to prompt 08.
- `week(conn, *, tenant_id, member_party_id, start_date_iso) -> list[dict]` — 7-day window from `start_date_iso`.
- `busy_slots(conn, *, tenant_id, member_party_id, range_start_iso, range_end_iso) -> list[dict]` — from BOTH `calendar_events` (where party is owner or attendee) AND `availability_blocks`. Returns `(start_at, end_at)` pairs only; no event content.
- `by_source(conn, *, tenant_id, calendar_source: str, external_uid: str) -> dict | None` — for adapter deduplication.

**`adminme/projections/calendars/__init__.py`** — exports `CalendarsProjection(Projection)` with `name='calendars'`, `version=1`, `subscribes_to=['calendar.event_added', 'calendar.event_updated', 'calendar.event_deleted']`.

**Tests — `tests/unit/test_projection_recurrences.py`** (≥8 tests):
- Apply `recurrence.added` with daily RRULE; row exists, `next_occurrence` matches payload.
- Apply `recurrence.completed`; `next_occurrence` advances via RRULE (assert the new date is consistent with RRULE semantics — daily → +1 day, weekly → +7 days).
- Apply `recurrence.updated` with new `rrule`; `next_occurrence` recomputes.
- `due_within(30, as_of_iso)` returns only recurrences firing in next 30 days.
- `for_member` filters correctly.
- Idempotent re-application of `recurrence.added`; one row.
- Rebuild correctness: 30-event fixture, snapshot, rebuild, byte-equal.
- Multi-tenant isolation.
- Scope canary stub.

**Tests — `tests/unit/test_projection_calendars.py`** (≥8 tests):
- Apply `calendar.event_added`; row exists with all fields.
- Apply `calendar.event_added` twice with same `(calendar_source, external_uid)`; one row (UNIQUE upsert verified).
- Apply `calendar.event_updated`; fields updated.
- Apply `calendar.event_deleted`; row removed. **Note**: this is hard-delete, unlike tasks — verify row count decreases.
- `today(member, today_iso, tz)` filters correctly.
- `busy_slots` returns overlapping windows from both tables.
- Rebuild correctness: 30 events including an add→update→delete sequence. After rebuild, row state matches live state (including hard-deletes — replay must result in the same final row-count).
- Multi-tenant isolation.
- Scope canary stub.

Register `RecurrencesProjection` and `CalendarsProjection` in `scripts/demo_projections.py`.

**Verify commit 3 before proceeding:**

```bash
poetry run pytest tests/unit/test_projection_recurrences.py \
                 tests/unit/test_projection_calendars.py -v 2>&1 | tail -5
# Expected: ≥16 tests passing.

poetry run pytest tests/unit/test_projection_*.py -q 2>&1 | tail -3
# Expected: all 05+06 projection tests green (~50 total).

git add adminme/projections/recurrences/ adminme/projections/calendars/ \
        tests/unit/test_projection_recurrences.py \
        tests/unit/test_projection_calendars.py \
        scripts/demo_projections.py \
        pyproject.toml poetry.lock    # if dateutil was added
git commit -m "phase 06-3: recurrences + calendars projections"
```

If any test fails, STOP and fix before commit 4.

### Commit 4 — integration + smoke + verification + push

**Extend `tests/integration/test_projection_rebuild.py`** (do NOT rewrite — prompt 05 established it; you add fixtures for 06's four projections):

- Existing fixture has ~500 mixed events across parties/interactions/artifacts. Grow it to ~800 events by appending: 50 commitments (mix of proposed, confirmed, completed, dismissed, snoozed, edited, cancelled, delegated, expired); 80 tasks (mix of created, completed, updated with status transitions, deleted); 30 recurrences (add, complete, update); 40 calendar events (add, update, delete sequences).
- Assert per-projection rebuild equivalence for all 7 projections (existing 3 + new 4): snapshot via `row_count_per_table + sha256_of_sorted_pk_rows`; `rebuild(<name>)` for each; re-snapshot; assert equal.
- New assertion: cross-DB referential integrity. For every `commitments.owed_by_party`, verify a row exists in `parties` (open both DBs, JOIN in Python). Same for `tasks.assignee_party`, `tasks.recurring_id → recurrences.recurrence_id`, `calendar_events.owner_party`. Missing references are ACCEPTABLE (projections trust events — an event with a bad `party_id` still lands a row), but the test should log them as informational for the operator's BUILD_LOG review. **Test passes either way** — this is a visibility check, not an enforcement check. Per [§2.3]: projections do not raise on data conflicts; they surface them via state.

**Extend `scripts/demo_projections.py`**:
- Seed a household with 3 members + 2 external parties (inherited from prompt 05).
- Add: 3 commitments (one pending, one confirmed, one completed).
- Add: 5 tasks (mix of inbox/next/in_progress).
- Add: 2 recurrences (one birthday, one oil_change).
- Add: 4 calendar events (two today, two this week).
- After all registrations + runner start + 0.5s catch-up, print per-projection row counts via `status()`.
- Exit 0 in under 10 seconds on lab hardware.

**Run full verification block:**

```bash
# Lint + types
poetry run ruff check adminme/ tests/ scripts/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

# Prompt 03/04/05 tests still pass
poetry run pytest tests/unit/test_event_log.py \
                 tests/unit/test_event_bus.py \
                 tests/unit/test_schema_registry.py \
                 tests/unit/test_event_validation.py \
                 tests/unit/test_projection_parties.py \
                 tests/unit/test_projection_interactions.py \
                 tests/unit/test_projection_artifacts.py -v 2>&1 | tail -5

# Prompt 06 unit tests
poetry run pytest tests/unit/test_projection_commitments.py \
                 tests/unit/test_projection_tasks.py \
                 tests/unit/test_projection_recurrences.py \
                 tests/unit/test_projection_calendars.py -v 2>&1 | tail -5

# Integration tests (extended)
poetry run pytest tests/integration/ -v 2>&1 | tail -5

# Canaries
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v 2>&1 | tail -3
poetry run pytest tests/unit/test_no_hardcoded_identity.py -v 2>&1 | tail -3
# Expected: no_hardcoded_instance_path PASSES (enforced since prompt 05).
# Expected: no_hardcoded_identity still SKIPS (prompt 08 or 17 implements).

# Full suite
poetry run pytest -q 2>&1 | tail -3

# Inviolable-invariant greps (belt and braces — the canaries are primary defense)
grep -iE "^anthropic|^openai|anthropic =|openai =" pyproject.toml && echo "VIOLATION of [§8]" || echo "OK: no LLM SDKs in pyproject"
grep -rn "~/.adminme\|'/.adminme\|\"/.adminme" adminme/ bootstrap/ packs/ --include='*.py' --include='*.sh' 2>/dev/null | grep -v "^docs/" || echo "OK: no hardcoded instance paths"
grep -rn "INSERT INTO.*projection\|projection_db.*write" adminme/pipelines/ 2>/dev/null || echo "OK: no pipeline→projection direct writes (no pipelines yet anyway)"
grep -rniE "james|laura|charlie|stice|morningside" adminme/ --include='*.py' | grep -v "tests/\|# example\|# illustration" || echo "OK: no tenant identity in platform code"

# Smoke
poetry run python scripts/demo_projections.py 2>&1 | tail -20
```

**Expected at the end:**
- Ruff: `All checks passed!`
- Mypy: `Success: no issues found`
- Prompt 03/04/05 tests: ~55 passed (approx).
- Prompt 06 unit tests: ~37 passed (10 commitments + 10 tasks + 8 recurrences + 8 calendars = 36+; actual may be higher).
- Integration: extended rebuild test + existing scope canary, all passing.
- Canaries: instance-path PASSING, identity SKIPPED.
- All four inviolable greps: "OK".
- Full suite: ~110 passed, 1 skipped.
- Smoke script: runs clean, prints 7 non-zero projection row counts.

If any failure appears, fix BEFORE commit 4. Do not commit a broken state.

```bash
git add tests/integration/test_projection_rebuild.py scripts/demo_projections.py
git commit -m "phase 06-4: integration + smoke + verification"
```

### Push + open PR

```bash
git log --oneline | head -6
# Expect 4 phase 06-N commits on top of main

git status
# Expect clean working tree

git push origin HEAD

gh pr create \
  --base main \
  --head $(git branch --show-current) \
  --title "Phase 06: domain projections (commitments, tasks, recurrences, calendars)" \
  --body "Four L3 projections on top of prompt 05's scaffold, following the same pattern as parties/interactions/artifacts.

**Landed:**
- 14 new event types registered at v1 (commitment transitions, task updated/deleted, recurrence lifecycle, calendar event updated/deleted)
- \`commitments\` projection — 9 event subscriptions, 5 query functions
- \`tasks\` projection — 4 event subscriptions, 6 query functions (includes goal_ref sub-task traversal)
- \`recurrences\` projection — 3 event subscriptions, RRULE advancement via python-dateutil
- \`calendars\` projection — 3 event subscriptions, UNIQUE(calendar_source, external_uid) upsert for adapter idempotency
- Extended integration rebuild test to ~800 events across 7 projections
- Extended demo script to exercise domain events

**Invariants respected:**
- [§1.1], [§2.2]: projections consume only, never emit
- [§4.3], [§4.5], [§4.6], [§13.1]: tasks ≠ commitments; recurrence firing doesn't auto-create tasks; task completion doesn't auto-complete commitments
- [§5.1]–[§5.3]: calendars read-only from AdministrateMe's perspective; no writeback; tasks don't create calendar events
- [§12.1], [§12.4]: tenant_id required on every query; no hardcoded tenant identity in platform code
- [§15]/[D15]: every projection DB path routes through InstanceConfig
- [D7]: all new event types register at v1

**Carry-forward for prompt 07:** xlsx forward daemon consumes these query functions directly per [D4]. Query signatures are stable.
**Carry-forward for prompt 08:** every query has \`# TODO(prompt-08): wrap with Session scope check\` marker.
**Carry-forward for prompt 10b:** \`commitment_extraction\` pipeline emits \`commitment.proposed\`; consumer wired.
**Carry-forward for prompt 10c:** \`commitment.expired\` schema registered; timer pipeline can now emit.
**Carry-forward for prompt 11:** calendar adapters use UNIQUE(calendar_source, external_uid) upsert; idempotent polling verified.

Single-purpose PR per phase-06 prompt. No sidecar fixes bundled."
```

If `gh pr create` fails for any reason, report the exact error and stop — do not retry with modified flags. James will review the branch state and decide.

---

## Stop

**Explicit stop message to the operator:**

```
Four domain projections in. Event log → projections path covers 7 of 11.

Branch: <harness-assigned branch name>
PR: <URL returned by gh pr create>
Commits: phase 06-1 through phase 06-4 on top of main.

Verification summary:
- ruff / mypy: clean
- prompt 03/04/05 tests: <N passed, 0 failed>
- prompt 06 unit tests: commitments <N>, tasks <N>, recurrences <N>, calendars <N> (total ≥36)
- integration rebuild: 7 projections' rebuild equivalence verified; cross-DB FK check logged
- scope canary stubs: 4 new (prompt 08 extends to ScopeViolation)
- instance-path canary: PASSING
- identity canary: still skipped (prompt 08 or 17)
- full suite: <N> passed, 1 skipped
- inviolable-invariant greps: all OK (no LLM SDKs / no hardcoded paths / no pipeline-projection writes / no tenant identity)
- smoke script: clean, 7 projection row counts reported

14 new event types registered at v1:
- commitment.{completed,dismissed,edited,snoozed,cancelled,delegated,expired}
- task.{updated,deleted}
- recurrence.{added,completed,updated}
- calendar.event_{updated,deleted}

Cross-DB FK note: commitments/tasks/recurrences/calendars reference parties and
each other via FK clauses that SQLite cannot enforce across separate DBs. Integrity
relies on upstream pipelines per [§2.3]. Integration test logs orphans as informational.

Ready for prompt 07 (ops projections: money, places_assets_accounts, vector_search, xlsx_workbooks)
once this branch is reviewed and merged.
```

Then STOP. Do not merge the PR yourself. Do not push to main. Do not proceed to prompt 07.
