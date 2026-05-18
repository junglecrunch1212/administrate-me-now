# Prompt 11b-iv: Google Tasks central (Cohort D step 2-δ)

**Phase:** L1 / Cohort D step 2-δ — fourth and FINAL sub-prompt of the four-way 11b split per `docs/08-architecture-amendment-adapter-rebase.md` §3.4. Second fresh Cat-B adapter under the canonical-build pattern (mirrors 11b-iii Google Calendar's structure; first adapter declaring `family="tasks"` per the §5.3 canonical taxonomy that PR #170 sidecar wired into the Cat-B ABC).

**Depends on:** canonical 11a closed (PRs #154-#157); canonical 11b-i closed (PRs #160-#163) — Tasks emits the `list_item.*@v1` family 11b-i registered; canonical 11b-iii closed (PRs #166-#169) — Google Tasks mirrors the fresh-pack template; sidecar #170 closed (Cat-B `family` Literal includes `"tasks"`).

**Estimated duration:** ~3-4 hours. **Narrow scope** mirroring 11b-iii: inbound poll (first-observation emission only) + outbound `create_task` via `tasks.insert`. Updated/completed/deleted inbound emissions deferred (cache + negative-set logic; analogous to 11b-iii's deferred `event_updated/deleted` work).

**Stop condition:** NEW pack `packs/adapters/tasks/google-tasks/` with `pack.yaml` + `adapter.py` declaring `class GoogleTasksAdapter(ExternalStateMirrorAdapter)` with `family="tasks"` + `capabilities=[Cat-B central, event_families=[list_item.added]]` + `write_capabilities=["create_task"]`. Inbound poll emits the EXISTING `list_item.added@v1` schema (registered by 11b-i at `adminme/events/schemas/lists.py`); outbound `_apply_write_impl(WriteOperation(kind="create_task", ...))` calls `service.tasks().insert(...)` wrapped via `adminme.lib.observation.outbound(...)`. `adminme adapters validate packs/adapters/tasks/google-tasks` exits 0.

---

## Read first

1. `docs/08-architecture-amendment-adapter-rebase.md` §3.4 — 4-way 11b split (this closes it).
2. `docs/05-architecture-amendment-personal-data-layer.md` §1.2 (Cat-B contract), §5.3 (canonical `list_item.*` taxonomy for tasks/reminders/notes-checklists).
3. `adminme/events/schemas/lists.py` (on main) — **READ CONTENTS** per Gate 0 strengthening. `ListItemAddedV1` field set: `{member_id, list_external_id, item_external_id, list_name, title, notes, due_at, priority (Literal[0,1,5,9]), completed, last_modified_at, sync_cycle_id}`. Google Tasks inbound MUST map to these field names.
4. `adminme/lib/adapters/external_state_mirror_base.py` (on main, post-PR-#170) — Cat-B ABC with `family: Literal["reminders", "calendaring", "contacts", "tasks"]` (the `"tasks"` member is what unblocks this prompt).
5. `adminme/lib/observation.py` (on main) — `outbound(...)` signature.
6. `packs/adapters/calendaring/google-calendar/pack.yaml` + `adapter.py` (on main) — most-recent fresh-Cat-B precedent. 11b-iv structurally near-identical except: family="tasks", schemas, API surface.
7. `packs/adapters/reminders/apple-eventkit/adapter.py::_emit_inbound_synced` (on main) — `list_item.*@v1` emit-payload mapping precedent from 11b-i.
8. `prompts/_active/11b-iii-google-calendar-central.md` (on main) — quality-bar reference; same structure transplants.

## Operating context

11b-iv closes Cohort D step 2 entirely. Five canonical Cat-B emissions now exist on main (1 from 11r-c retrofit + 1 from 11b-i dual-emit + 1 from 11b-iii inbound poll + 2 to ship in 11b-iv: `list_item.added@v1` inbound + `list_item.added@v1` outbound on `create_task` success). Three load-bearing items:

1. **Second fresh Cat-B pack** at `packs/adapters/tasks/google-tasks/`. Pattern transplants from 11b-iii: pack.yaml with Cat-B + asymmetric write_capabilities + sharing_model_discriminator + dedup_strategy; lazy SDK import inside `_default_service_factory`; observation seam slots; `_classify_*_error` 401/403/429/5xx classification.

2. **Google Tasks v1 API surface bound to Cat-B ABC** — inbound poll uses `service.tasks().list(tasklist=..., maxResults=100, showCompleted=False, showDeleted=False).execute()`. Outbound write uses `service.tasks().insert(tasklist=..., body={"title": ..., "notes": ..., "due": ...}).execute()` wrapped through `observation_outbound(...)`.

3. **Map onto existing `list_item.added@v1`** — field set is `{member_id, list_external_id, item_external_id, list_name, title, notes, due_at, priority, completed, last_modified_at, sync_cycle_id}` per `adminme/events/schemas/lists.py`. Google Tasks inbound emits: `source-equivalent = "google_tasks"` (NOT a schema field — encoded by emitter context per 11b-i §1); `member_id=config["member_id"]`; `list_external_id=config["tasklist_id"]`; `item_external_id=task["id"]`; `list_name=config.get("list_name") or tasklist_id`; `title=task.get("title", "")`; `notes=task.get("notes")`; `due_at=task.get("due")` (Google Tasks RFC 3339); `priority=5` (Google Tasks has no priority — map to "medium" default); `completed=task.get("status") == "completed"`; `last_modified_at=task.get("updated")`; `sync_cycle_id=ctx-minted`. NO new schema per [D7] + canonical 11a's "MAP DON'T REGISTER".

OUT OF SCOPE:

- **`list_item.updated@v1` / `list_item.completed@v1` / `list_item.deleted@v1` emissions** — cache-based diff + negative-set; defer to 11b-iv-β.
- **`list.*@v1` family** (tasklist CRUD) — defer; `write_capabilities` doesn't include `create_list/update_list/delete_list` here.
- **Push notifications** — Google Tasks API does not support webhooks; poll-only is the only path (no Phase B alternative for Tasks specifically).
- **`update_task` / `delete_task` / `complete_task` write capabilities** — declaring without impl fails `write_capabilities_check`. Only `create_task` ships.
- **Multi-tasklist polling** — single `tasklist_id` per pack instance.
- **Real OAuth credentials** — Phase A uses mocks + `requires_live_services` gate.
- **Cross-adapter changes** — Gmail + Plaid + Apple Reminders + Google Calendar preserved byte-for-byte.

Citations: [§6 invariant 10] (sensitivity floor `normal`), [§6.20] (observation wrap), [§7.3, D17] (no projection writes), [§8.2, D6] (no LLM SDK), [D7] (schemas v1 immutable; uses 11b-i's `list_item.added@v1`), [D8] (causation/correlation propagation), [D18] (canonical `list_item.*` taxonomy for tasks), [D19] (Cat-B taxonomy), [PM-27] (constructor injection no-op defaults), [PM-34] (5 mutually exclusive ABCs), [PM-35] (capabilities-as-list), [UT-27] (asymmetric write_capabilities). Memo 05 §1.2, §2.3, §2.5, §3.1, §3.2, §3.4, §5.3. Memo 08 §3.4.

[VISION §"Real family"]: Google Tasks is a niche surface for James's family (Apple-first stack); 11b-iv closes the 4-way 11b split for completeness, demonstrating the canonical Cat-B pattern across all four sub-families (reminders + calendaring + contacts + tasks). Future Cohort F/G prompts that need Tasks will inherit from this template.

## Objective

Build a fresh Cat-B `GoogleTasksAdapter` at `packs/adapters/tasks/google-tasks/` (NEW pack). Inbound poll `tasks.list` → emit existing `list_item.added@v1` on first observation. Outbound real `_apply_write_impl` for `create_task` via `tasks.insert` wrapped through `outbound()`. No new schemas. Mirrors 11b-iii structurally.

## Out of scope

(See above.)

## Approach (architectural sketch)

Pattern mirrors 11b-iii Google Calendar:

1. **`pack.yaml`** — Cat-B manifest, `family: tasks`, `capabilities=[Cat-B central, event_families=[list_item.added]]`, `write_capabilities=[create_task]`, `sharing_model_discriminator=owner_member_id`, `dedup_strategy={kind: external_id}`, `sensitivity_default: normal`, `observation_mode_required: true`. Pack id `adapter:tasks-google-tasks`.

2. **`config.schema.json` + `config.example.yaml`** — minimal config carrying `service_account_key_ref`, `tasklist_id`, `user_email`, `member_id`, `list_name` (display), `poll_interval_s`.

3. **`adapter.py`** — `class GoogleTasksAdapter(ExternalStateMirrorAdapter)`:
   - ClassVars: `name="tasks-google-tasks"`, `family: Literal[..."tasks"] = "tasks"`, `version="1.0.0"`, `sensitivity_floor="normal"`, `capabilities`, `write_capabilities=["create_task"]`, `sharing_model_discriminator="owner_member_id"`.
   - `__init__`: same shape as 11b-iii (config, credentials_resolver, service_factory, max_poll_cycles, three observation seam slots default None, `_service`, `_ctx`, `_seen_inbound: dict[str, str]`).
   - `start(ctx)` / `stop()` / `status()`: lifecycle mirror.
   - `_poll_loop()` / `_poll_once()`: inbound poll cadence; emit `list_item.added@v1` for new task ids; populate `_seen_inbound[task["id"]] = task.get("updated", "")`.
   - `_emit_list_item_added`: map Google task → `ListItemAddedV1` field set (per §1 item 3).
   - `_apply_write_impl(operation)`: `if kind == "create_task"` build task body from operation.payload, call `outbound(...)` wrap, return `WriteResult`.
   - `_on_external_change`: Phase A no-op.
   - `_classify_tasks_error`: 401/403 → fatal auth_failed; 429 → transient rate_limited; 5xx → transient network. Mirrors `_classify_gmail_error` + `_classify_calendar_error`.

## Deliverables

### NEW pack files
- `packs/adapters/tasks/google-tasks/pack.yaml` (~40 lines)
- `packs/adapters/tasks/google-tasks/config.schema.json` (~30 lines)
- `packs/adapters/tasks/google-tasks/config.example.yaml` (~10 lines)
- `packs/adapters/tasks/google-tasks/adapter.py` (target: ≤500 lines per the F-5 fresh-pack ceiling established by 11b-iii)

### Tests
- `tests/unit/adapters/tasks/__init__.py` (NEW dir; standard package marker)
- `tests/unit/adapters/tasks/test_google_tasks.py` (NEW, F-4 unique basename, ~15 tests)
- `packs/adapters/tasks/google-tasks/tests/test_google_tasks_pack_internal.py` (NEW, F-4 unique basename, ~3-4 tests)
- `tests/integration/test_adapter_google_tasks_supervisor.py` (NEW, ~3 tests; default-CI sibling)
- F-6: NO empty `__init__.py` in pack-internal tests dir.

### Documentation
- `docs/build_log.md` — append the 11b-iv entry per the canonical 11a / 11b-iii c4 template. Mark **Closes 4-way 11b split** prominently.

## Verification

```bash
poetry run python -c "from adminme.events.registry import registry; from adminme.events.schemas.lists import ListItemAddedV1; assert registry.get('list_item.added', 1) is ListItemAddedV1; print('list_item.added@v1 verified ✓')"

poetry run pytest tests/unit/adapters/tasks/test_google_tasks.py packs/adapters/tasks/google-tasks/tests/test_google_tasks_pack_internal.py tests/integration/test_adapter_google_tasks_supervisor.py -v

poetry run mypy adminme/ packs/adapters/tasks/google-tasks/ 2>&1 | tail -5
poetry run ruff check packs/adapters/tasks/google-tasks/ 2>&1 | tail -3

poetry run adminme adapters validate packs/adapters/tasks/google-tasks
# Expected: exit 0; NO advisory_pending_retrofit; NO errors

bash scripts/verify_invariants.sh
# Expected: exit 0
```

## Stop

When the Google Tasks pack passes validator + tests + the inbound poll emits `list_item.added@v1` on first observation + the outbound `create_task` calls `tasks.insert` wrapped through `outbound()`, c4 is done. **Closes Cohort D step 2 (the 4-way 11b split).** Do NOT proceed to 11b-iv-β (updated/completed/deleted emissions), 11b-ii Apple Calendar dual, or 11b-i-β Apple Reminders dual-deployment without operator review.

## What this PR is NOT

Parent draft only. The v4 refactor follows in `partner/refactor-11b-iv` after this merges + pre-authorship gate runs against current main HEAD.

No constitutional doc edits. No PROMPT_SEQUENCE rows touched. No `_active/` file yet.
