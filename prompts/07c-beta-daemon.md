# Prompt 07c-β: xlsx reverse daemon + integration round-trip

**Phase:** BUILD.md PHASE 2. Closes the round-trip 07c-α opened.
**Depends on:** 07c-α merged. On main: two new system events at v1, sidecar I/O at `adminme/projections/xlsx_workbooks/sidecar.py`, forward daemon already writes sidecar inside its lock, descriptor registry at `adminme/daemons/xlsx_sync/sheet_schemas.py`, diff core at `adminme/daemons/xlsx_sync/diff.py`, three dead stubs in `adminme/projections/xlsx_workbooks/` deleted. `scripts/verify_invariants.sh` `ALLOWED_EMITS` already covers `xlsx.reverse_projected` and `xlsx.reverse_skipped_during_forward`.
**Stop condition:** `XlsxReverseDaemon` lives at `adminme/daemons/xlsx_sync/reverse.py` with full per-cycle algorithm, watchdog→asyncio bridge, lock acquisition, undo-window infrastructure, and emit pathways for all four bidirectional sheets. ≥22 unit tests across four new test files + 1 integration round-trip test pass. `bash scripts/verify_invariants.sh` clean.

This is part 2 of a two-prompt split. 07c-α landed the schema and infrastructure; this prompt lands the daemon and proves round-trip end-to-end.

---

## Read first

The slim universal preamble (`prompts/PROMPT_SEQUENCE.md`) governs cross-cutting discipline.

**Spec — read once on §3.11:**

- `ADMINISTRATEME_BUILD.md` lines 993–1080 — §3.11 reverse-projection algorithm. Lines 1009/1015/1054 cover the sidecar pathway (already implemented forward-side; reverse rewrites sidecar at end-of-cycle).
- `ADMINISTRATEME_DIAGRAMS.md` §6 — xlsx round-trip diagram.
- `docs/SYSTEM_INVARIANTS.md` §2 (the reverse daemon is NOT a projection — it emits domain events on principal authority), §6 (sensitivity preservation), §10 (xlsx bidirectional + lock contention), §13 item 5.

**Codebase — already-merged from 07c-α (load only as needed):**

- `adminme/daemons/xlsx_sync/sheet_schemas.py` — descriptors. Already defines `BIDIRECTIONAL_DESCRIPTORS`, `descriptor_for`, `editable_columns_for`, `TASKS_DESCRIPTOR`, `COMMITMENTS_DESCRIPTOR`, `RECURRENCES_DESCRIPTOR`, `RAW_DATA_DESCRIPTOR`.
- `adminme/daemons/xlsx_sync/diff.py` — `diff_sheet`, `DiffResult`. Pure-functional, sync.
- `adminme/projections/xlsx_workbooks/sidecar.py` — `read_sheet_state`, `read_readonly_state`, `write_sheet_state`, `write_readonly_state`, `sidecar_dir`, `sidecar_path`, `hash_readonly_sheet`.
- `adminme/projections/xlsx_workbooks/__init__.py` — forward daemon. Constants `_BIDIRECTIONAL_SHEETS` and `_READONLY_SHEETS` are the per-workbook sheet inventory you mirror.
- `adminme/projections/xlsx_workbooks/lockfile.py` — `acquire_workbook_lock(lock_path, *, timeout_s)` context manager. Raises `TimeoutError`.

**New reads required for emit-payload construction (targeted greps; do NOT full-read):**

- `adminme/events/schemas/domain.py`: `grep -B 2 -A 12 "class TaskCreatedV1\|class TaskUpdatedV1\|class TaskDeletedV1\|class CommitmentEditedV1\|class RecurrenceAddedV1\|class RecurrenceUpdatedV1" adminme/events/schemas/domain.py`
- `adminme/events/schemas/ops.py`: `grep -B 2 -A 12 "class MoneyFlowManuallyAddedV1\|class MoneyFlowManuallyDeletedV1" adminme/events/schemas/ops.py`
- `adminme/lib/instance_config.py`: `sed -n '40,80p'`. **InstanceConfig has no `principal_member_id` field.** Do NOT add one — UT-7 covers actor attribution; resolves in prompt 08. Use the literal `"xlsx_reverse"` for `actor_identity` on every emitted envelope.
- One projection's sensitivity-read pattern: `grep -n "sensitivity" adminme/projections/tasks/handlers.py`. Every projection table has `sensitivity TEXT NOT NULL DEFAULT 'normal' CHECK (sensitivity IN ('normal','sensitive','privileged'))`. Sensitivity preservation: `SELECT sensitivity FROM <table> WHERE tenant_id = ? AND <id_col> = ?`, default `'normal'` for ADD events.

**Do NOT load** during this session: BUILD.md outside §3.11, L4/pipelines/console specs, projection schemas beyond the sensitivity grep.

---

## Out of scope

- Re-doing anything 07c-α landed (schema, sidecar I/O, forward sidecar writer, descriptors, diff core).
- `adminme projection rebuild xlsx_workbooks` CLI — **prompt 17**.
- Registering `money_flow.recategorized` / similar field-edit event types — **future prompt**; current behavior drops with INFO log.
- Adding `observation_mode_active` to forward emit payload — **prompt 16**.
- Daemon lifecycle (start-on-boot, stop-on-shutdown) — **prompt 16**.
- Routing reverse-emitted events through Session/guardedWrite — **prompt 08** (UT-7).
- Touching read-only sheet builders.

---

## Daemon contract (Commit 1 + 2)

`XlsxReverseDaemon(config, query_context, *, event_log, flush_wait_s=2.0, forward_lock_timeout_s=10.0, delete_undo_window_s=5.0)` at `adminme/daemons/xlsx_sync/reverse.py`.

**Public API:**
- `async start()` — begin watchdog observers; idempotent.
- `async stop()` — cancel pending cycles + stop observer; idempotent.
- `async run_cycle_now(workbook)` — bypass watchdog + flush_wait; drives a full cycle. Used by tests.

**Per-cycle algorithm (BUILD.md §3.11):**

1. Wait `flush_wait_s` for the writer to flush.
2. Acquire forward lock (`acquire_workbook_lock` from `lockfile.py`). On `TimeoutError` after `forward_lock_timeout_s`, emit `xlsx.reverse_skipped_during_forward` and return — skip is the cycle terminus, NO `xlsx.reverse_projected` follows.
3. Open workbook via `openpyxl.load_workbook(..., data_only=True)`.
4. For each bidirectional sheet (descriptor present in `BIDIRECTIONAL_DESCRIPTORS`): load current rows into list-of-dicts (header → cell value); read sidecar via `read_sheet_state`. If sidecar is `None`, treat the sheet as cold-start — emit nothing for it this cycle; sidecar gets written at step 6. Otherwise call `diff_sheet` and dispatch each `added` / `updated` / `deleted` to per-sheet emit helpers (below).
5. For each read-only sheet (per `_READONLY_SHEETS`): hash current sheet content via `hash_readonly_sheet`; compare to `read_readonly_state`. If different, log WARN. (Read-only sheets do not appear in `BIDIRECTIONAL_DESCRIPTORS`.)
6. Rewrite all sidecar JSON to current state — bidirectional via `write_sheet_state`, read-only via `write_readonly_state`. Always rewrite, even when nothing emitted, so subsequent cycles diff cleanly.
7. Release lock.
8. Emit `xlsx.reverse_projected` with `events_emitted` (envelope IDs in emit order from this cycle), `sheets_affected` (sheets that produced ≥1 event or had a non-empty diff), `duration_ms`.

**Watchdog→asyncio bridge:** `watchdog.observers.Observer` callbacks run on watchdog's thread. Hop to the asyncio loop via `loop.call_soon_threadsafe(self._schedule_cycle, workbook_name)`. The on-loop `_schedule_cycle` debounces (`flush_wait_s` sleep, cancellable on subsequent file events) and runs the cycle. Concurrent cycles for the same workbook serialize via an internal `asyncio.Lock` keyed per workbook.

**Undo-window infrastructure:** for sheets where `descriptor.deletes_use_undo_window` is True (Tasks, Raw Data manual rows), DELETE diffs queue an `asyncio.Task` that sleeps `delete_undo_window_s` then emits the delete event. Track in `self._pending_deletes[(sheet, row_id)]`. If a subsequent cycle finds `row_id` present again before the sleep elapses, cancel the task and remove from `_pending_deletes`. Note: emits scheduled inside the undo window do NOT count toward the cycle's `events_emitted` list (they fire on a different cycle's task; they DO get emitted though, just attributed to the cycle that observed the row return — actually, attribute to the cycle that SCHEDULED the delete; the delete fires after the lock is released; the `xlsx.reverse_projected` for that cycle has already emitted; the delayed delete is not in any `events_emitted` payload). This is a known and acceptable observability gap.

**Envelope construction template** (every emit):

```python
EventEnvelope(
    event_at_ms=int(time.time() * 1000),
    tenant_id=self._config.tenant_id,
    type=...,
    schema_version=1,
    occurred_at=EventEnvelope.now_utc_iso(),
    source_adapter="xlsx_reverse",
    source_account_id="daemon",
    owner_scope="shared:household",
    visibility_scope="shared:household",
    sensitivity=...,                # see Sensitivity preservation
    actor_identity="xlsx_reverse",  # UT-7 covers principal attribution
    payload=...,
)
```

**Sensitivity preservation:** for UPDATE / DELETE events, `SELECT sensitivity FROM <table> WHERE tenant_id = ? AND <id> = ?` against the appropriate `query_context` connection. ADD events default to `'normal'`. Tables: `tasks` for Tasks, `commitments` for Commitments updates, `recurrences` for Recurrences updates, `money_flows` (column `flow_id`) for Raw Data manual-row deletes. Default `'normal'` if the row isn't found in the projection.

---

## Per-sheet emit helpers (Commit 1 — Tasks; Commit 2 — others)

**Tasks:**
- ADD: if `task_id` blank, generate `tsk_<8 hex chars>` via `secrets.token_hex(4)`. Build `TaskCreatedV1` payload from row dict: `task_id`, `title`, optional `description` from `notes`, optional `owner_member_id` from `assigned_member`, optional `due` from `due_date`, optional `energy`. Emit `task.created`.
- UPDATE: build `TaskUpdatedV1` payload — `task_id`, `updated_at = EventEnvelope.now_utc_iso()`, `updated_by_party_id = None`, `field_updates` = changed-fields dict. Emit `task.updated` with looked-up sensitivity.
- DELETE: queue undo-window task → emit `task.deleted` (`task_id`, `deleted_at`, `deleted_by_party_id = "xlsx_reverse"`).

**Commitments:**
- ADD: drop INFO ("commitments are pipeline-proposed only per [§4.2]").
- UPDATE: emit `commitment.edited` (`commitment_id`, `edited_at = now`, `edited_by_party_id = "xlsx_reverse"`, `field_updates`).
- DELETE: drop INFO ("commitments cancel via API; row deletion dropped").

**Recurrences:**
- ADD: if `recurrence_id` blank, generate `rec_<8hex>`. Emit `recurrence.added` — `linked_kind = "household"` and `linked_id = "household"` (no per-row way to specify in v1; principals editing recurrences in xlsx is a power-user path), `kind` from sheet's `cadence`, `rrule` from sheet (free-text passes through), `next_occurrence` from sheet (if blank, today's ISO date via `EventEnvelope.now_utc_iso().split("T")[0]`).
- UPDATE: emit `recurrence.updated` with `field_updates`.
- DELETE: drop INFO ("recurrences not deletable in v1 per descriptor").

**Raw Data:**
- ADD: branch on row's `is_manual`. TRUE → emit `money_flow.manually_added` (mint `flow_<8hex>` if blank; build payload per `MoneyFlowManuallyAddedV1`: `flow_id`, `amount_minor = int(amount * 100)`, `currency = "USD"`, `occurred_at` from `date` column, `kind = "paid"`, `category` from `assigned_category`, `notes`, `added_by_party_id = "xlsx_reverse"`). FALSE → drop WARN ("non-manual row added via xlsx; rejecting per [§13.4]").
- UPDATE: drop INFO regardless of which row (`money_flow.recategorized` not registered yet — descriptor's `updates_emit_event = None`).
- DELETE: branch on the **sidecar row's** `is_manual`. TRUE → queue undo-window task → emit `money_flow.manually_deleted` (`flow_id`, `deleted_at = now`, `deleted_by_party_id = "xlsx_reverse"`). FALSE → drop WARN ("Plaid row deletion via xlsx ignored").

---

## Commit discipline (four commits per slim preamble)

### Commit 1 — Daemon class + Tasks pathway

Ship the full `XlsxReverseDaemon` class with watchdog→asyncio bridge, lock acquisition, full per-cycle algorithm, undo-window infrastructure, and read-only-sheet WARN-on-hash-mismatch. Wire only Tasks emit helpers; for non-Tasks bidirectional sheets, log INFO "[reverse] deferred to commit 2: <sheet> had N adds, M updates, K deletes" and continue.

**Tests — `tests/unit/test_xlsx_reverse_basic.py` (≥11 tests):**

Fixture builds a temp instance, runs forward daemon to populate workbook + sidecars, then constructs `XlsxReverseDaemon` (no watchdog start; drive via `run_cycle_now`). Use `delete_undo_window_s=0.05`, `flush_wait_s=0.05`, `forward_lock_timeout_s=0.5` so the suite finishes in seconds.

- `test_no_edits_no_events` — call `run_cycle_now`; only `xlsx.reverse_projected` lands; `events_emitted` empty.
- `test_new_task_emits_task_created` — programmatic `openpyxl` edit appending a Tasks row; cycle; assert `task.created` lands with the supplied title.
- `test_blank_id_gets_generated` — append row with blank `task_id`; cycle; assert emitted `task_id` matches `^tsk_[0-9a-f]{8}$`.
- `test_edit_title_emits_task_updated` — modify a Tasks row's `title`; cycle; assert `task.updated.field_updates == {"title": "<new>"}`.
- `test_delete_after_undo_window` — remove a Tasks row; cycle; sleep > undo window; assert `task.deleted` lands.
- `test_delete_cancelled_within_window` — remove row; cycle; before window elapses, restore row and run a second cycle; assert NO `task.deleted` ever emits.
- `test_derived_column_dropped` — modify `created_at`; cycle; no `task.updated`; sidecar rewritten so the next cycle is clean.
- `test_id_column_change_as_delete_plus_add` — change a Tasks row's `task_id` from `t1` to `t99`; cycle; sleep > undo window; assert one `task.deleted` for `t1` AND one `task.created` for `t99`.
- `test_readonly_sheet_warn` — modify a People cell; cycle; assert no domain event; assert WARN log captured; `xlsx.reverse_projected` still emits.
- `test_reverse_projected_at_cycle_end` — every cycle (with or without changes) emits `xlsx.reverse_projected` with non-negative `duration_ms`.
- `test_sensitivity_preserved_on_update` — fixture seeds a task with `sensitivity='sensitive'` (append a `task.created` envelope with that sensitivity directly); the principal edits the title; emitted `task.updated` envelope must carry `sensitivity='sensitive'`.

### Commit 2 — Commitments + Recurrences + Raw Data emit pathways + lock contention + cold start

Extend `reverse.py` with the three remaining sheet pathways per the helper specs above.

**Tests — three new files:**

`tests/unit/test_xlsx_reverse_lock_contention.py` (≥4 tests):
- `test_forward_lock_held_emits_skipped` — patch `acquire_workbook_lock` to raise `TimeoutError`; cycle; assert `xlsx.reverse_skipped_during_forward` emitted, no `xlsx.reverse_projected`, no domain events.
- `test_forward_lock_released_in_time_proceeds` — start a thread that holds the lock briefly then releases; cycle starts after release; assert cycle completes with `xlsx.reverse_projected`.
- `test_concurrent_cycles_serialized` — fire two `run_cycle_now` for the same workbook concurrently via `asyncio.gather`; instrument the daemon (a counter incremented on entry, decremented on exit) to assert only one cycle inside at a time.
- `test_skipped_does_not_emit_reverse_projected` — explicit assertion separate from the first test for clarity.

`tests/unit/test_xlsx_reverse_finance.py` (≥6 tests):
- `test_manual_row_add_emits_money_flow_manually_added` — append manual Raw Data row; cycle; assert event lands; `flow_id` matches `^flow_[0-9a-f]{8}$` (or supplied id if non-blank).
- `test_plaid_row_add_drops_warn` — append non-manual row; cycle; WARN log captured; no event.
- `test_manual_row_delete_after_window` — remove manual Raw Data row; cycle; sleep > window; assert `money_flow.manually_deleted` lands.
- `test_plaid_row_delete_drops_warn` — remove Plaid row; cycle; assert no event.
- `test_assigned_category_edit_drops_info` — edit `assigned_category` on a Plaid row; descriptor says `updates_emit_event=None`; assert INFO log; no event.
- `test_amount_edit_on_manual_row_drops_info` — edit `amount` on a manual row; descriptor says `updates_emit_event=None` at v1; assert INFO log; no event.

`tests/unit/test_xlsx_reverse_cold_start.py` (≥2 tests):
- `test_no_sidecar_writes_sidecar_emits_nothing` — delete the entire `.xlsx-state/` tree before constructing the daemon; cycle; assert sidecar tree exists post-cycle; assert no domain events; `xlsx.reverse_projected` lands with empty `events_emitted`.
- `test_partial_sidecar_only_diffs_what_can_diff` — delete just the Tasks sidecar; cycle; assert Tasks treated as cold (no events even if Tasks has diffs); assert Recurrences diff fired normally.

### Commit 3 — Integration round-trip test + smoke

`tests/integration/test_xlsx_roundtrip.py` — end-to-end: 7 sqlite projections + forward daemon + reverse daemon (no watchdog observer started; drive via `run_cycle_now`). Append ~15 events to seed fixtures across Tasks, Commitments, Recurrences, accounts/manual + Plaid money flows. Forward regenerate both workbooks; assert sidecars on disk. Programmatic edits to `adminme-ops.xlsx`: modify one Task title, append one new Task with blank id, attempt to delete a Recurrence (should drop). Run reverse cycle on ops; assert correct event sequence and `xlsx.reverse_projected` at end. Forward regenerate ops again; assert workbook reflects new state. Repeat for `adminme-finance.xlsx` with one manual-row add, one Plaid-row add (must drop), one manual-row delete after undo window.

`scripts/demo_xlsx_roundtrip.py` — standalone smoke: temp instance → 10 events → forward regen → programmatic xlsx edit → reverse cycle → forward regen → exit 0 in <30s. Print event counts at each stage.

### Commit 4 — BUILD_LOG + push

Append BUILD_LOG entry per slim preamble template. Sections:
- Refactored / Session merged / Outcome.
- Evidence: list landed modules from this prompt (daemon, 4 test files, integration test, smoke), the daemon's watchdog/asyncio/lock/undo-window machinery, all four bidirectional pathways wired, sensitivity preservation, ≥22 unit tests + 1 integration. Note 07c-α landed schema/descriptors/diff/sidecar in PR #<07c-α>; together they close the round-trip.
- Carry-forward to 07.5 (audit `DERIVED_COLUMNS ↔ always_derived` equivalence; the round-trip integration test is the canary), to 08 (route reverse-emitted events through guardedWrite — UT-7 OPEN, this prompt did not resolve it), to 16 (daemon lifecycle wiring), to future prompts (register `money_flow.recategorized` then flip Raw Data descriptor's `updates_emit_event`).

**Verification block** (one block, end of Commit 4):

```
poetry run pytest -q
poetry run ruff check adminme/ tests/ scripts/
poetry run mypy adminme/
bash scripts/verify_invariants.sh
poetry run python scripts/demo_xlsx_forward.py
poetry run python scripts/demo_xlsx_roundtrip.py
```

Full suite expected: prior baseline (with 07c-α included) + ≥22 new tests. All must pass. Both smoke scripts exit 0.

**Push + open PR** per slim preamble. PR title: `Phase 07c-β: xlsx reverse daemon + integration round-trip`. Body notes this is part 2 of 2 — together with 07c-α (already merged), closes the round-trip and resolves UT-6. Single-purpose; no sidecar fixes.

---

## Stop

Per slim preamble's stop discipline: post-PR, ONE round of `mcp__github__pull_request_read` (`get_status`, `get_reviews`, `get_comments`); report; STOP. Do not poll. Do not merge.

Stop report names: branch, PR URL, four commit SHAs, full-suite count, ≥22 new unit tests confirmed (basic ≥11 + lock ≥4 + finance ≥6 + cold ≥2) + 1 integration round-trip green, both smoke scripts green, `bash scripts/verify_invariants.sh` clean. UT-6 fully resolved. Flag UT-7 as the new open tension for prompt 08.
