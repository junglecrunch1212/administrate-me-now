# Prompt 11b-iv: Google Tasks central (Cohort D step 2-δ)

**Phase:** L1 / Cohort D step 2-δ — fourth and FINAL sub-prompt of the four-way 11b split. Second fresh Cat-B adapter (mirrors 11b-iii Google Calendar). Parent draft: `prompts/11b-iv-google-tasks-central.md` (merged PR #171).
**Branch:** `claude/11b-iv-c<N>` per c-band.
**Stop condition:** NEW pack `packs/adapters/tasks/google-tasks/` with `pack.yaml` + `adapter.py` declaring `class GoogleTasksAdapter(ExternalStateMirrorAdapter)`. `family="tasks"` (per the §5.3 taxonomy authorized by PR #170 sidecar Cat-B Literal extension). `capabilities=[Cat-B central, event_families=[list_item.added]]`. `write_capabilities=["create_task"]`. Inbound poll emits existing `list_item.added@v1` (no new schema). Outbound `_apply_write_impl` for `create_task` calls `tasks.insert` wrapped via `adminme.lib.observation.outbound(...)`. `adminme adapters validate packs/adapters/tasks/google-tasks` exits 0. **Closes Cohort D step 2.**
**Depends-on:** canonical 11a/b-i/b-iii closed; sidecar #170 closed (Cat-B `family` Literal extended); parent draft merged (PR #171, main `b7e1c13f`).
**Quality-bar reference:** `prompts/_active/11b-iii-google-calendar-central.md` (most-recent fresh-Cat-B precedent; 11b-iv transplants structurally).
**Estimated duration:** c1 ~10-15 min · c2 ~90-120 min · c3 ~60 min · c4 ~60 min · total ~4-4.5 hrs wall / ~$80-110 spend (matching 11b-iii's profile).

---

## §1 Behavioral grounding

11b-iv closes the 4-way 11b split entirely (11b-i Apple Reminders + 11b-ii Apple Calendar TBD + 11b-iii Google Calendar + 11b-iv Google Tasks). Second fresh Cat-B pack; first to declare `family="tasks"`. Three load-bearing items:

1. **Fresh Cat-B pack** at `packs/adapters/tasks/google-tasks/` with `family="tasks"`. Manifest: Cat-B + `write_capabilities=["create_task"]` per [UT-27] (asymmetric — only what's implemented) + `sharing_model_discriminator: owner_member_id` + `dedup_strategy: {kind: external_id}` (Google's stable `task.id`) + `sensitivity_default: normal` + `observation_mode_required: true`. Pack id `adapter:tasks-google-tasks`. Structurally mirrors 11b-iii.

2. **Google Tasks v1 API surface bound to Cat-B ABC** — inbound: `service.tasks().list(tasklist=..., maxResults=100, showCompleted=False, showDeleted=False).execute()`. Outbound: `service.tasks().insert(tasklist=..., body={"title": ..., "notes": ..., "due": ...}).execute()` wrapped through `observation_outbound(...)`. Error classification mirrors `_classify_calendar_error` (401/403 → fatal auth_failed; 429 → transient rate_limited; 5xx → transient network).

3. **Map onto existing `list_item.added@v1`** — schema field set from `adminme/events/schemas/lists.py`: `{member_id, list_external_id, item_external_id, list_name, title, notes, due_at, priority, completed, last_modified_at, sync_cycle_id}`. Google Tasks inbound emit: `member_id=config["member_id"]`, `list_external_id=config["tasklist_id"]`, `item_external_id=task["id"]`, `list_name=config.get("list_name") or config["tasklist_id"]`, `title=task.get("title", "")`, `notes=task.get("notes")`, `due_at=task.get("due")` (RFC 3339 from Google), `priority=5` (Google Tasks has no priority — map to medium per `Literal[0,1,5,9]`), `completed=task.get("status") == "completed"`, `last_modified_at=task.get("updated")`, `sync_cycle_id=ctx-minted`. NO new schema per [D7]. The "google_tasks" emitter context is encoded by the adapter's emission site, NOT by a schema field (per 11b-i §1 decision to drop the legacy `direction` field).

OUT OF SCOPE per parent draft §"Out of scope":
- `list_item.updated/completed/deleted@v1` inbound emissions (cache + negative-set; defer to 11b-iv-β).
- `list.*@v1` tasklist CRUD.
- Push notifications (Google Tasks API has no webhook support — poll-only is the only path).
- Additional write_capabilities (`update_task`, `delete_task`, `complete_task`).
- Multi-tasklist polling.
- Real OAuth credentials.
- Cross-adapter changes.

Citations: [§6 invariant 10] (sensitivity floor `normal`), [§6.20] (observation wrap), [§7.3, D17] (no projection writes), [§8.2, D6] (no LLM SDK), [D7] (schemas v1 immutable; uses 11b-i's `list_item.added@v1`), [D8] (causation/correlation propagation), [D18] (canonical `list_item.*` taxonomy for tasks), [D19] (Cat-B taxonomy), [PM-27], [PM-34], [PM-35], [UT-27]. Memo 05 §1.2, §2.3, §2.5, §3.1, §3.2, §3.4, §5.3. Memo 08 §3.4. PR #170 (Cat-B Literal extension).

## §2 Read first (required)

Per c-band:

**c1 (verify-only no-op) — 3 files**

- `prompts/11b-iv-google-tasks-central.md` (merged parent draft).
- `adminme/events/schemas/lists.py` (on main) — **READ CONTENTS** per Gate 0. `ListItemAddedV1` field set is c1's verify target + c2's emit mapping.
- `adminme/events/registry.py` (on main) — `registry.get(name, version)` API.

**c2 (fresh adapter pack) — 6 files**

- `adminme/lib/adapters/external_state_mirror_base.py` (on main, post-PR-#170) — confirm `family: Literal["reminders", "calendaring", "contacts", "tasks"]` includes `"tasks"`. PR #170 sidecar landed this; verify at orientation.
- `adminme/lib/adapters/__init__.py` (on main).
- `adminme/lib/observation.py` (on main).
- `packs/adapters/calendaring/google-calendar/adapter.py` (on main) — fresh-Cat-B precedent. Transplant the lazy-import pattern, observation seam slots, `_classify_*_error`, `_emit_authenticated`, `_emit_error`, `_append` helpers.
- `packs/adapters/calendaring/google-calendar/pack.yaml` (on main) — pack.yaml shape precedent.
- `adminme/adapters/base.py` (on main) — `AdapterContext` shape.

**c3 (framework tests) — 5 files**

- `tests/unit/adapters/calendaring/test_google_calendar.py` (on main) — most-recent fresh-Cat-B test precedent (~15 tests pattern transplants).
- `adminme/events/schemas/lists.py::ListItemAddedV1` — assertion target.
- `packs/adapters/tasks/google-tasks/adapter.py` (on the c2 branch).
- `prompts/_active/11b-iii-google-calendar-central.md` — c3 deliverable shape.
- `tests/unit/adapters/reminders/test_apple_eventkit_dual_emit.py` (on main) — `_FakeEventLog` + `_make_ctx` + `_FakeService` shape precedent for the inbound-emit assertion tests.

**c4 (pack-internal + integration + BUILD_LOG) — 4 files**

- `packs/adapters/calendaring/google-calendar/tests/test_google_calendar_pack_internal.py` (on main) — pack-internal precedent (4 tests).
- `tests/integration/test_adapter_google_calendar_supervisor.py` (on main) — integration sibling precedent.
- `docs/build_log.md` (append).
- `prompts/_active/11b-iii-google-calendar-central.md` (c4 BUILD_LOG entry shape).

## §3 Reconciliation notes (carry-forward)

- **F-1 (HARD/from cycle #17): READ FILE CONTENTS for any schema file cited in c1.** `adminme/events/schemas/lists.py` is the c1 target; the `list_item.added@v1` schema must be present and unchanged. Grep `registry.register("list_item.added"` to confirm.
- **F-2 (HARD/from 11a/11b-i/11b-iii): MAP existing field semantics; do not invent new schemas.** Google Tasks inbound emits `ListItemAddedV1` field set BYTE-FOR-BYTE.
- **F-3 (HARD/from 11b-i/11b-iii): observation-suppressed path skips the emit too.** `_apply_write_impl` returns `WriteResult(applied=False, suppressed=True)` on `outbound()` suppress.
- **F-4 (HARD/standing): pack-internal test basename uniqueness.** `test_google_tasks_pack_internal.py` is unique against all prior pack-internal basenames (verified).
- **F-5 (SOFT): adapter.py diff size — anti-vision halt at ≤500 lines added.** Fresh-pack ceiling established by 11b-iii (which shipped 446 lines).
- **F-6 (HARD/from 11a c4 + 11b-iii c4): NO empty `__init__.py` in pack-internal tests dir.** Required: `tests/unit/adapters/tasks/__init__.py`. Forbidden: `packs/adapters/tasks/google-tasks/tests/__init__.py`.
- **F-7 (SOFT/new for 11b-iv): Google Tasks priority defaulting.** The Google Tasks API does NOT carry a priority field. The `ListItemAddedV1` schema requires `priority: Literal[0, 1, 5, 9]`. Map all Google Tasks emissions to `priority=5` (medium) as the default. Document the mapping in the adapter docstring + a c3 test asserts the default.

## §4 Assumptions to verify (Executor: confirm at orientation)

- **A-1: Cat-B `family` Literal includes `"tasks"` post-PR-#170.** Verify via `from adminme.lib.adapters.external_state_mirror_base import ExternalStateMirrorAdapter` + reflection.
- **A-2: `ListItemAddedV1` field set on main matches §1 mapping.** Verify `priority: Literal[0, 1, 5, 9]` (NOT `[1, 5, 9]` — F-6 carry-forward from 11b-i).
- **A-3: `googleapiclient.discovery.build("tasks", "v1", ...)` is the correct discovery service shape.** Mirrors `build("calendar", "v3", ...)` from Gmail/Google Calendar.
- **A-4: Google Tasks API `task["status"]` values.** `"needsAction"` (incomplete) or `"completed"`. The emit's `completed: bool` derives from `task["status"] == "completed"`.
- **A-5: `task.get("due")` is RFC 3339.** Same as Google Calendar's `event["start"]["dateTime"]`. Pass through; downstream consumers parse.
- **A-6: `observation_outbound` import path.** `from adminme.lib.observation import outbound as observation_outbound` (matches 11b-iii pattern).

## §5 Objective

Build `packs/adapters/tasks/google-tasks/` (fresh Cat-B pack, `family="tasks"`) implementing inbound poll + outbound `create_task` for Google Tasks v1 API. Maps onto existing `list_item.added@v1` (11b-i registration) for inbound; observation-wrapped real `_apply_write_impl` for outbound. No new schemas.

## §6 Out of scope

(Same as parent draft.)

## §7 Deliverables

**c1: VERIFY-ONLY NO-OP** — assert `registry.get("list_item.added", 1) is ListItemAddedV1`. Empty commit body documents the no-op. 0 production-code lines.

**c2: NEW pack files** (≤500 lines for adapter.py; F-5 anti-vision halt):

1. `packs/adapters/tasks/google-tasks/pack.yaml` (~40 lines) — Cat-B manifest mirroring 11b-iii but `family: tasks`, `event_families: [list_item.added]`, `write_capabilities: [create_task]`.

2. `packs/adapters/tasks/google-tasks/config.schema.json` (~30 lines) — `service_account_key_ref`, `tasklist_id`, `user_email`, `member_id`, `list_name` (optional display), `poll_interval_s`.

3. `packs/adapters/tasks/google-tasks/config.example.yaml` (~10 lines).

4. `packs/adapters/tasks/google-tasks/adapter.py` (≤500 lines):
   - Imports + module docstring + `from adminme.events.schemas.lists import ListItemAddedV1` (for type-reference / pack-internal-test guard; the emit happens via `_append(event_type="list_item.added")` with the dict payload).
   - `class GoogleTasksAdapter(ExternalStateMirrorAdapter):` with `name="tasks-google-tasks"`, `family: Literal["reminders", "calendaring", "contacts", "tasks"] = "tasks"`, `version="1.0.0"`, `sensitivity_floor="normal"`, `capabilities`, `write_capabilities=["create_task"]`, `sharing_model_discriminator="owner_member_id"`.
   - `__init__`: config, three optional `observation_*` seam slots, `_service`, `_ctx`, `_seen_inbound: dict[str, str] = {}`, `_status: AdapterStatus`.
   - `start(ctx)`: build service via `build("tasks", "v1", credentials=...)`, emit `adapter.authenticated@v1`, spawn `_poll_loop()`.
   - `stop()`, `status()`: lifecycle.
   - `_poll_loop()` / `_poll_once()` / `_fetch_tasks()` (pagination via `pageToken`): inbound poll cadence; emit `list_item.added@v1` for new task ids; populate `_seen_inbound[task["id"]] = task.get("updated", "")`.
   - `_emit_list_item_added(ctx, tasklist_id, task)`: map Google task → `ListItemAddedV1` field set per §1 item 3.
   - `_apply_write_impl(operation)`: defense-in-depth `if operation.kind != "create_task"` → `WriteCapabilityError`. Build task body `{"title": payload["title"], "notes": payload.get("notes"), "due": payload.get("due")}`. Call `outbound(...)` wrap, return `WriteResult`.
   - `_on_external_change`: Phase A no-op.
   - `_classify_tasks_error(exc)`: mirrors 11b-iii's `_classify_calendar_error`.
   - `_emit_authenticated`, `_emit_error`, `_append`: standard helpers.

**c3: `tests/unit/adapters/tasks/test_google_tasks.py` (NEW, F-4 unique basename, ≥15 tests)** + `tests/unit/adapters/tasks/__init__.py`:

1. `test_google_tasks_adapter_inherits_external_state_mirror_adapter`.
2. `test_family_class_attribute_is_tasks_literal` — `family == "tasks"` (validates the PR #170 Literal extension).
3. `test_write_capabilities_declared_asymmetrically` — `["create_task"]`; `len == 1`.
4. `test_apply_write_gate_raises_on_undeclared_kind` — `pytest.raises(WriteCapabilityError)` for `kind="update_task"`.
5. `test_apply_write_dispatches_to_impl_on_declared_kind`.
6. `test_apply_write_routes_through_observation_outbound`.
7. `test_apply_write_observation_suppressed_returns_unapplied`.
8. `test_apply_write_direct_call_when_seams_unset_returns_applied`.
9. `test_apply_write_4xx_auth_failure_classifies_fatal`.
10. `test_apply_write_5xx_classifies_transient`.
11. `test_apply_write_429_classifies_rate_limited_transient`.
12. `test_poll_once_emits_list_item_added_on_first_observation`.
13. `test_poll_once_skips_already_seen_task_id`.
14. `test_poll_emit_payload_validates_against_list_item_added_v1` — `ListItemAddedV1.model_validate(payload)` succeeds.
15. `test_poll_emit_priority_defaults_to_medium_per_F7` — Google Tasks has no priority; emit's `priority == 5`.
16. `test_poll_emit_completed_derives_from_task_status` — `task["status"] == "completed"` → `completed=True`; `"needsAction"` → `completed=False`.

**c4: pack-internal + integration + BUILD_LOG**:

1. `packs/adapters/tasks/google-tasks/tests/test_google_tasks_pack_internal.py` (NEW, F-4 unique basename, ≥3 tests):
   - `test_capabilities_class_attribute_matches_manifest`.
   - `test_pack_yaml_round_trips_through_parse_adapter_manifest` (asserts family=tasks + Cat-B + write_capabilities + sharing_model + dedup_strategy).
   - `test_inbound_emit_payload_validates_against_list_item_added_v1_schema`.
   - `test_six_validator_checks_return_zero_errors_and_no_advisory`.

2. `tests/integration/test_adapter_google_tasks_supervisor.py` (NEW, ≥3 tests):
   - `test_supervisor_loaded_adapter_inherits_external_state_mirror_adapter`.
   - `test_supervisor_write_capabilities_gate_through_loader`.
   - `test_supervisor_inbound_poll_emits_list_item_added`.

3. `docs/build_log.md` — append 11b-iv entry. **Mark `Closes Cohort D step 2 (4-way 11b split)` prominently.** Carry-forward: 11b-iv-β (update/complete/delete inbound emissions) + 11b-ii Apple Calendar dual + 11b-i-β Apple Reminders dual-deployment + 11c-i bridge daemon are the post-Cohort-D-step-2 queue.

## §8 Commit structure

Four commits. Format `phase 11b-iv commit <N>: <imperative summary>`.

- **c1: `phase 11b-iv commit 1: verify list_item.added@v1 already registered (no-op)`** — 0 production-code lines.
- **c2: `phase 11b-iv commit 2: GoogleTasksAdapter fresh Cat-B tasks-family pack`** — pack files ≤500 lines.
- **c3: `phase 11b-iv commit 3: framework-level Google Tasks tests`** — ≥15 tests.
- **c4: `phase 11b-iv commit 4: pack-internal + integration tests + BUILD_LOG`** — closes the 4-way 11b split.

## §8.5 Branch strategy — STACKED

c-band file scopes non-overlapping: c1 verify-only; c2 new pack dir; c3 new test dir; c4 new pack-internal dir + new integration + BUILD_LOG append. Zero ghost conflicts expected per 11b-iii precedent.

Test floor: ≥15 framework + ≥3 pack-internal + ≥3 integration = **≥21 total**.

## §9 Verification

**c1**:
```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.lists import ListItemAddedV1; assert registry.get('list_item.added', 1) is ListItemAddedV1; print('list_item.added@v1 verified ✓')"
```

**c2**:
```bash
poetry run python -c "from packs.adapters.tasks.google_tasks.adapter import GoogleTasksAdapter; from adminme.lib.adapters import ExternalStateMirrorAdapter; assert issubclass(GoogleTasksAdapter, ExternalStateMirrorAdapter); assert GoogleTasksAdapter.family == 'tasks'; assert GoogleTasksAdapter.write_capabilities == ['create_task']; print('c2 inheritance + family + write_capabilities ✓')"
poetry run mypy adminme/ packs/adapters/tasks/google-tasks/ 2>&1 | tail -5
```

**c3**:
```bash
poetry run pytest tests/unit/adapters/tasks/test_google_tasks.py -v
# Expected: ≥15 passed
```

**c4**:
```bash
poetry run pytest packs/adapters/tasks/google-tasks/tests/test_google_tasks_pack_internal.py tests/integration/test_adapter_google_tasks_supervisor.py -v
poetry run adminme adapters validate packs/adapters/tasks/google-tasks
# Expected: exit 0; NO advisory_pending_retrofit; NO errors
bash scripts/verify_invariants.sh
# Expected: exit 0
```

## §10 Stop

When `adminme adapters validate packs/adapters/tasks/google-tasks` exits 0 + inbound poll emits `list_item.added@v1` on first observation + outbound `create_task` routes through `outbound()` + `list_item.added@v1` schema unchanged on main, c4 is done. **Closes Cohort D step 2 (4-way 11b split).** Do NOT proceed to 11b-iv-β, 11b-ii, or 11b-i-β without operator review.

After merge: Partner runs three-job QC, then opens the batched partner-state-snapshot PR (post-11a + post-11b-i + post-11b-iii + post-11b-iv) via Chrome MCP `file_upload`.
