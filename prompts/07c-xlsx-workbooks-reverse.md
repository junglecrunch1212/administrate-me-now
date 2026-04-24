# Prompt 07c: xlsx_workbooks reverse daemon + forward sidecar extension

**Phase:** BUILD.md PHASE 2. Closes the round-trip opened by 07b.
**Depends on:** 01–07b merged. 11 projections live; `scripts/verify_invariants.sh` on main with `ALLOWED_EMITS='xlsx\.regenerated'`.
**Stop condition:** New daemon at `adminme/daemons/xlsx_sync/reverse.py` watches both workbooks via `watchdog`, diffs against sidecar JSON, emits domain events for principal edits. Forward daemon (07b's `XlsxWorkbooksProjection._regenerate`) extended to write sidecar inside its lock. Three dead stubs in `adminme/projections/xlsx_workbooks/` deleted (PM-10). Two new system events at v1. ≥30 unit tests + 1 integration round-trip pass.

---

## Read first

The slim universal preamble (`prompts/PROMPT_SEQUENCE.md`) governs cross-cutting discipline. Do not re-derive it here.

**Spec for this prompt — read in order, twice on §3.11:**

- `ADMINISTRATEME_BUILD.md` lines 993–1080 — §3.11 reverse projection algorithm, conflict resolution, rebuild semantics, testing.
- `ADMINISTRATEME_DIAGRAMS.md` §6 — xlsx round-trip (two daemons, one shared lock, sidecar state).
- `docs/SYSTEM_INVARIANTS.md` §1 (event log), §2 (projections derived; **the reverse daemon is NOT a projection** — see Architectural placement below), §6 (sensitivity), §10 (xlsx bidirectional + lock contention), §13 item 5, §15.
- `docs/DECISIONS.md` D5, D7, D14.

**Codebase context — load only what you reference:**

- `adminme/projections/xlsx_workbooks/__init__.py`, `builders.py`, `lockfile.py`, `query_context.py` — the forward daemon you extend.
- `adminme/projections/xlsx_workbooks/sheets/{tasks,commitments,recurrences,raw_data}.py` — `HEADERS` and `DERIVED_COLUMNS` are the source of truth for each sheet's reverse descriptor (see Sheet descriptors below). Read once each; do not re-summarize them in the diff module.
- `adminme/projections/xlsx_workbooks/sheets/{people,accounts,metadata_ops,metadata_finance}.py` — read-only sheets; the reverse daemon drops all edits with a WARN log.
- `adminme/projections/xlsx_workbooks/{forward,reverse,schemas}.py` — confirm these are the 22-line stubs from prompt 02. **Delete all three in Commit 1** (PM-10).
- `adminme/events/schemas/system.py` — pattern for the two new system events.
- `adminme/events/log.py` — `EventLog.append()` signature.
- `adminme/lib/instance_config.py` — confirm `xlsx_workbooks_dir`.
- `scripts/verify_invariants.sh` — `ALLOWED_EMITS` regex, line ~107.

**Do NOT load** during this session: BUILD.md outside §3.11, L4/pipelines/console specs, other constitutional docs beyond the targeted ranges.

---

## Architectural placement (binding)

The reverse daemon lives at `adminme/daemons/xlsx_sync/reverse.py` (BUILD.md §3.11 line 995). It is an **L1-adjacent adapter, not a projection**:

- Does NOT register with `ProjectionRunner`.
- Emits domain events on principal authority (xlsx file edits).
- Emits two new system events for observability — but from `adminme/daemons/`, outside `adminme/projections/`.

`scripts/verify_invariants.sh`'s `ALLOWED_EMIT_FILES` audits `adminme/projections/` only, so it does NOT need a new entry. `ALLOWED_EMITS` regex DOES extend — a future projection might legitimately need to emit these system events, and hygiene says the allowlist names what's allowed. See Commit 1.

**PM-14 is now a HARD prompt-meta rule:** daemons live in `adminme/daemons/`; projections live in `adminme/projections/`. The forward xlsx daemon is the exception (it IS a projection). Future adapter prompts (11, 12) populate `adminme/adapters/`.

---

## Sidecar JSON pathway (resolves UT-6)

BUILD.md §3.11 line 1009 (read sidecar) + line 1015 (write sidecar) + line 1054 (rebuild deletes both files plus sidecar) imply both daemons touch it:

- **Forward writes the sidecar after each regeneration, inside the same lock as the xlsx write.** Gives reverse a stable read baseline; means a forward-vs-human race resolves to "forward wins, reverse comes back to find sidecar matches workbook, no diff."
- **Reverse rewrites the sidecar at the end of each cycle.** Captures post-emit state so two principal saves in quick succession diff against each other, not against the pre-edit baseline.

**Path:** `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`. Resolve via `InstanceConfig.xlsx_workbooks_dir.parent / ".xlsx-state"` — **sibling** to the workbooks dir, not inside it. Ensures no future watchdog reverse on the sidecar tree could self-trigger.

**Per-sheet contents:** bidirectional sheets store row dicts keyed by id column; read-only sheets store `{"content_hash": "<sha256>"}` so principal edits to read-only sheets are detectable for WARN logging without persisting full row data.

**Forward integration:** the sidecar writer reads the just-written xlsx file (not re-queries projections). This guarantees sidecar matches what reverse will see when loading the workbook back. Eliminates "wrote rows from current projection state but sidecar derived from a slightly different snapshot" failure mode.

---

## Sensitivity preservation (binding per §6)

When the reverse daemon emits an UPDATE or DELETE event, it looks up the row's current sensitivity from the source projection (via `query_context`) and stamps the envelope. ADD events default to `'normal'` — there is no path here for principals to add privileged rows via xlsx (privileged rows enter through privileged-floor adapters per §6.10).

Without this, every reverse cycle silently downgrades privileged data to normal — a §6 violation the test suite must catch.

---

## Sheet descriptors

Build a registry at `adminme/daemons/xlsx_sync/sheet_schemas.py` describing per-sheet diff behavior. Each descriptor declares: `id_column`, `editable_columns` (frozenset OR callable for per-row determination), `always_derived`, `adds_emit_event`, `updates_emit_event`, `deletes_emit_event`, and a `row_to_payload` function.

**Source of truth:** the sheet builder in `adminme/projections/xlsx_workbooks/sheets/<sheet>.py`. Its `HEADERS` enumerates columns; its `DERIVED_COLUMNS` is your `always_derived` set. Match them exactly — drift between the two breaks round-trip silently. (07.5 will audit this equivalence.)

**Per-sheet event mapping:**

| Sheet | ADD | UPDATE | DELETE |
|---|---|---|---|
| Tasks | `task.created` | `task.updated` | `task.deleted` (5s undo window) |
| Commitments | drop + INFO ("commitments are pipeline-proposed only per §4.2") | `commitment.edited` | drop + INFO ("commitments cancel via API; row deletion dropped") |
| Recurrences | `recurrence.added` | `recurrence.updated` | drop + INFO ("recurrences not deletable in v1") |
| Raw Data — manual row (`is_manual==TRUE`) | `money_flow.manually_added` | drop + INFO ("money_flow.recategorized not registered yet; deferred") | `money_flow.manually_deleted` (5s undo window) |
| Raw Data — Plaid row (`is_manual==FALSE`) | drop + WARN ("non-manual row added via xlsx; rejecting per §13.4") | drop + INFO ("Plaid-authoritative; assigned_category/notes/memo edits deferred") | drop + WARN ("Plaid row deletion via xlsx ignored") |
| People / Accounts / Metadata (read-only) | drop + WARN | drop + WARN | drop + WARN |

All drops also rewrite the sidecar to current sheet state — otherwise the diff keeps firing on the same orphan row each cycle.

For Raw Data, `editable_columns` is a callable returning `{"date", "account_last4", "merchant_name", "merchant_category", "amount", "memo", "assigned_category", "notes"}` for manual rows and `{"assigned_category", "notes", "memo"}` for Plaid rows. `is_manual` itself is in `always_derived` — principals cannot flip a row's manual/Plaid status from xlsx.

---

## Diff core

Pure-functional, sync, no I/O, no openpyxl imports. Lives at `adminme/daemons/xlsx_sync/diff.py`. Signature:

```
diff_sheet(current_rows, sidecar_rows, descriptor) -> DiffResult
```

`DiffResult` has `added`, `updated` (list of (row, changed-fields-dict) tuples), `deleted`, and `dropped_edits` (rows where only non-editable columns changed; for INFO log).

**Type normalization:** openpyxl returns native Python types (int, float, str, datetime, None); JSON sidecar serializes to JSON-native types. Compare with: floats at abs-tolerance 1e-9, datetimes normalized to ISO 8601 string, `None` and `""` treated as equal.

**ID column edits** (principal blanks then re-types `task_id`): diff sees the original id missing and a new id present → reports as delete-plus-add. The daemon emits the delete (with undo window) and the add immediately.

**Blank id on a new row:** diff sees an added row with no id. The daemon (not the diff core) generates `tsk_<ulid>` etc. and stamps it on the emitted event payload. Next forward regenerate writes the row with that id; subsequent edits look like updates, not re-adds.

---

## Daemon

Lives at `adminme/daemons/xlsx_sync/reverse.py`. Class `XlsxReverseDaemon(config, query_context, *, event_log, flush_wait_s=2.0, forward_lock_timeout_s=10.0, delete_undo_window_s=5.0)`.

**Public API:**

- `async start()` — begin watchdog observers; idempotent.
- `async stop()` — cancel pending cycles + stop observer; idempotent.
- `async run_cycle_now(workbook)` — bypass watchdog + flush wait; drives a full cycle. Used by tests.

**Per-cycle algorithm (BUILD.md §3.11):**

1. Wait `flush_wait_s` for the writer to flush.
2. Acquire the same `.lock` the forward daemon uses (`acquire_workbook_lock` from `lockfile.py`). If forward holds it >`forward_lock_timeout_s`, emit `xlsx.reverse_skipped_during_forward` and abandon — do NOT also emit `xlsx.reverse_projected`; skip is the cycle terminus.
3. Open workbook with `openpyxl.load_workbook(..., data_only=True)`.
4. For each bidirectional sheet: load current rows; read sidecar; `diff_sheet()`; emit add/update events immediately; schedule deletes with `delete_undo_window_s` cancellation tracking keyed by `(sheet, row_id)`. If a subsequent cycle sees the row reappear before the window elapses, cancel the queued delete.
5. For each read-only sheet: hash content; compare to sidecar hash; if different, log WARN.
6. Rewrite all sidecar JSON to current sheet state.
7. Release lock.
8. Emit `xlsx.reverse_projected` with `events_emitted` (envelope IDs in emit order), `sheets_affected`, `duration_ms`.

**Watchdog → asyncio bridge:** watchdog `Observer.schedule()` callbacks fire on watchdog's thread. The handler hops to the asyncio loop via `loop.call_soon_threadsafe(schedule_fn, workbook_name)`. The on-loop `schedule_fn` debounces (`flush_wait_s`) and runs the cycle. Concurrent cycles for the same workbook serialize via an internal `asyncio.Lock`.

**Actor identity on emitted envelopes:** use `config.principal_member_id` if present; fall back to literal `"xlsx_reverse"`. We do not know which principal made the edit (would require filesystem owner inspection or 1Password integration; out of scope).

**Cold start (no sidecar present):** treat the workbook as authoritative — write a sidecar from current workbook state, emit no domain events. Per-sheet: if Tasks sidecar missing but Recurrences sidecar present, Tasks gets cold-start treatment while Recurrences diffs normally.

---

## Out of scope

- `adminme projection rebuild xlsx_workbooks` CLI → prompt 17.
- Registering `money_flow.recategorized` / similar field-edit event types → future prompt; current behavior drops with INFO log.
- Adding `observation_mode_active` to forward emit payload per D5 → prompt 16.
- Daemon lifecycle (start-on-boot, stop-on-shutdown) → prompt 16.
- Routing reverse-emitted events through Session/guardedWrite → prompt 08 (UT-7).
- Apple Reminders / BlueBubbles / Plaid / Gmail adapters → 11/12.
- Tests requiring live Numbers or Excel — synthetic openpyxl writes only.
- Touching read-only sheet builders.

---

## Commit discipline (four commits per slim preamble §commit-discipline)

### Commit 1 — Schema + verify_invariants + stub deletion + forward sidecar

- Append two system event classes to `adminme/events/schemas/system.py`: `XlsxReverseProjectedV1` (`workbook_name`, `detected_at`, `sheets_affected`, `events_emitted`, `duration_ms`) and `XlsxReverseSkippedDuringForwardV1` (`workbook_name`, `detected_at`, `skip_reason: Literal["forward_lock_held"]`). Both `model_config = {"extra": "forbid"}`. Register at v1.
- `scripts/verify_invariants.sh` — extend `ALLOWED_EMITS` regex to `'xlsx\.regenerated|xlsx\.reverse_projected|xlsx\.reverse_skipped_during_forward'`. Add a maintenance comment explaining why `ALLOWED_EMIT_FILES` does NOT extend (daemon is outside the projections audit scope).
- `git rm` the three dead stubs (`forward.py`, `reverse.py`, `schemas.py` in `adminme/projections/xlsx_workbooks/`). Grep for any imports first; if the grep returns hits, those imports were also dead — prune.
- New module `adminme/projections/xlsx_workbooks/sidecar.py` — pure-functional sidecar I/O: `sidecar_dir(xlsx_workbooks_dir)`, `sidecar_path(...)`, `write_sheet_state`, `write_readonly_state` (uses content hash), `read_sheet_state`, `read_readonly_state`, `hash_readonly_sheet`. Atomic writes via `.tmp` + `os.replace`. JSON serialization with `sort_keys=True` for stability.
- Extend `XlsxWorkbooksProjection._regenerate()` (in `adminme/projections/xlsx_workbooks/__init__.py`) to call a new `_write_sidecar_for(workbook)` helper as the LAST step inside the lock, after the xlsx write and before lock release. The helper opens the just-written xlsx with `openpyxl(data_only=True)` and writes per-sheet sidecar JSON. Two new module-level constants `_BIDIRECTIONAL_SHEETS` and `_READONLY_SHEETS` describe the per-workbook sheet inventory.

**Tests (Commit 1):**

- Extend `tests/unit/test_schema_registry.py` with one assertion that both new event types are in `registry.known_types()`.
- New `tests/unit/test_xlsx_sidecar.py` (≥6 tests) — sibling-of-workbooks invariant; write/read roundtrip; atomic write (no `.tmp` left after mid-write failure); read-missing returns None; readonly state stores hash not rows; readonly hash determinism.
- New `tests/unit/test_xlsx_forward_writes_sidecar.py` (≥4 tests) — bidirectional sheets get JSON sidecar after regenerate; readonly sheets get hash sidecar; finance Raw Data sidecar matches workbook rows; sidecar exists before lock context exits.

### Commit 2 — Sheet descriptors + diff core

- New empty packages: `adminme/daemons/__init__.py`, `adminme/daemons/xlsx_sync/__init__.py`.
- `adminme/daemons/xlsx_sync/sheet_schemas.py` — descriptor dataclass + four bidirectional descriptors per the table above.
- `adminme/daemons/xlsx_sync/diff.py` — `diff_sheet()` per spec above. Pure sync; no openpyxl/watchdog imports.

**Tests (Commit 2) — `tests/unit/test_xlsx_diff.py` (≥10 tests):** no-changes-empty; added-only; deleted-only; editable-change-updated; derived-change-dropped-not-updated; id-column-change-as-delete-plus-add; manual-row-full-editable-set; Plaid-row-amount-change-dropped; Plaid-row-assigned-category-dropped-in-v1; float-tolerance; None-vs-empty-string-equivalent; callable-editable-columns-per-row.

### Commit 3 — Reverse daemon

- `adminme/daemons/xlsx_sync/reverse.py` per Daemon section above.

**Tests (Commit 3) — four files:**

- `tests/unit/test_xlsx_reverse_basic.py` (≥10): no-edits-no-events; new-task-emits-task.created; edit-title-emits-task.updated; delete-emits-after-window; delete-undo-within-window-cancels; derived-column-edit-dropped; id-column-change-as-delete-plus-add; readonly-sheet-edit-WARN-no-event; sidecar-rewritten-after-cycle; sensitivity-preserved-on-update; xlsx.reverse_projected-emitted-at-cycle-end.
- `tests/unit/test_xlsx_reverse_lock_contention.py` (≥4): forward-holds-emits-skipped; forward-releases-within-timeout-proceeds; concurrent-cycles-serialized; skipped-cycle-does-not-also-emit-reverse_projected.
- `tests/unit/test_xlsx_reverse_finance.py` (≥6): manual-row-add-emits-money_flow.manually_added; non-manual-row-add-WARN-no-event; manual-row-delete-after-window-emits-money_flow.manually_deleted; Plaid-row-delete-WARN-no-event; assigned-category-edit-INFO-deferred; Plaid-amount-edit-INFO-Plaid-authoritative.
- `tests/unit/test_xlsx_reverse_cold_start.py` (≥2): no-sidecar-writes-sidecar-emits-nothing; partial-sidecar-only-diffs-what-can-diff.

Run tests with daemon time windows reduced to ~0.05s so the suite completes in seconds.

### Commit 4 — Integration + smoke + BUILD_LOG + push

- `tests/integration/test_xlsx_roundtrip.py` — end-to-end: 10 sqlite projections + forward daemon + reverse daemon (no watchdog, drive via `run_cycle_now`); append ~30 events; forward regenerate (sidecars written); programmatic xlsx edits (modify/add/delete on Tasks; Recurrence delete dropped); reverse cycle; assert correct event sequence and `xlsx.reverse_projected` at cycle end; forward regenerate again; assert workbook reflects new state; repeat for finance with manual + Plaid rows.
- `scripts/demo_xlsx_roundtrip.py` — standalone smoke: temp instance → 10 events → forward regen → programmatic edit → reverse cycle → forward regen → exit 0 in <30s.
- `docs/build_log.md` append — entry per slim preamble's BUILD_LOG template. Sections: Refactored / Session merged / Outcome / Evidence (list landed modules, schema additions, PM-10 disposition, UT-6 resolution, ≥30 unit tests + integration) / Carry-forward (07.5: audit DERIVED_COLUMNS ↔ always_derived equivalence; 08: route reverse-emitted events through guardedWrite; 16: daemon lifecycle; future prompts: register `money_flow.recategorized` then update Raw Data descriptor's `updates_emit_event`).

**Verification block** (one block, end of Commit 4 — slim preamble's `verify_block` covers the ruff/mypy/pytest/verify_invariants/smoke pattern; this prompt's specifics):

- Full suite expected: ~245+ passed, 1 skipped (identity canary).
- 07c-specific: ≥30 unit tests across the four new test files + 1 integration test.
- `bash scripts/verify_invariants.sh` clean (extended `ALLOWED_EMITS`).
- Both smoke scripts exit 0.

**Push + open PR** per slim preamble (gh fallback to MCP). PR title: `Phase 07c: xlsx_workbooks reverse daemon + forward sidecar`. Single-purpose; no sidecar fixes.

---

## Stop

Per slim preamble's stop discipline: post-PR, ONE round of `mcp__github__pull_request_read` (`get_status`, `get_reviews`, `get_comments`); report; STOP. Do not poll. Do not merge.

Stop report names: branch, PR URL, four commit SHAs, full-suite count, ≥30 unit tests confirmed, integration round-trip green, smoke scripts green, `bash scripts/verify_invariants.sh` clean. Note PM-10 disposition (3 stubs deleted) and UT-6 resolution (sidecar pathway implemented). Flag UT-7 as the new open tension for prompt 08.
