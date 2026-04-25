build_changelog.md not started until after Prompt 05
Prompt 05 Completed and Merged
Prompt 06 due for review and potential refactor
Permission for Claude Code Opus 4.7 Code Supervision Partner to take over this log now and keep up-to-date with details of each new prompt execution, merge, diff, future prompt refactors, and any other history important to build_changelog.md 

### Prompt 07a — ops spine projections (places_assets_accounts, money, vector_search)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07a-projections-ops-spine.md (~600 lines, quality bar = 06).
- **Session merged**: PR #<N>, commits 81290b0 / edd0c34 / 71731fb / <commit4>, merged <merge date>.
- **Outcome**: MERGED
- **Evidence**:
  - 3 projections: places_assets_accounts (3 entity tables + 2 association tables), money (1 table with is_manual + soft-delete), vector_search (vec0 virtual table + embeddings_meta sidecar).
  - 10 new event types registered at v1 per [D7] (place/asset/account × added/updated, money_flow × 3, embedding.generated).
  - 38 new unit tests (places_assets_accounts 15, money 13, vector_search 10) + integration rebuild extended to 10 projections + ~1200 events.
  - Runner gained `on_connection_opened` hook for projection-specific extension loading (vec0). Default no-op, backward compatible.
  - Privileged-filter at handler time on vector_search per [§13.8]. Envelope- or payload-level `sensitivity='privileged'` drops the write with INFO log.
  - CHECK constraints consistent on enum columns per CF-6 (places.kind, assets.kind, accounts.kind, accounts.status, money_flows.kind, sensitivity).
  - login_vault_ref CHECK accepts only `op://`, `1password://`, `vault://` — belt against broken adapter writes.
  - money soft-delete: `money_flow.manually_deleted` UPDATEs `deleted_at` rather than deleting the row. Rebuild correctness preserved. Queries exclude deleted.
  - BUILD_LOG updated as part of Commit 4 per new rule.
  - Ruff clean, mypy clean (95 source files), all inviolable greps OK.
- **Carry-forward for prompt 07b (xlsx forward daemon)**:
  - Forward daemon reads from all 10 projections' query functions. Query signatures stable.
  - `money.manual_flows` + `money_flow.manually_added` event type already wired for 07c's reverse path.
  - The 07b forward daemon subscribes to event types but MUST NOT emit — it's a projection per [§2.2].
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - `money_flow.manually_added` and `money_flow.manually_deleted` events registered; 07c emits these when principals edit the Raw Data sheet.
  - `task.updated`, `task.deleted`, `commitment.edited` (registered in 06) cover the Tasks/Commitments sheets' reverse path.
  - xlsx reverse is an adapter not a projection — it emits.
- **Carry-forward for prompt 08**:
  - 3 new projections × ~6 queries = ~18 more TODO(prompt-08) markers across queries.py files. Total now ~38 across 10 projections.
- **Carry-forward for future embedding daemon**:
  - `embedding.generated` schema requires pre-computed vector in payload. AdministrateMe does not import embedding SDKs. Daemon will call OpenClaw's embedding endpoint per [§8].

### Prompt 07b — xlsx_workbooks forward daemon
- **Refactored**: by Partner in Claude Chat. Prompt file: prompts/07b-xlsx-workbooks-forward.md.
- **Session merged**: PR #<N>, commits 05e13dd / 3c14625 / 2546061 / <commit4>, merged <merge date>.
- **Outcome**: MERGED
- **Evidence**:
  - xlsx_workbooks projection built as forward-only daemon consuming events from 10 projections; structurally a projection per [§2.2], emits only the categorized system event `xlsx.regenerated`.
  - 1 new event type at v1: `xlsx.regenerated` — in new `schemas/system.py` module to keep domain event files clean.
  - 2 workbook builders: adminme-ops.xlsx (Tasks, Recurrences, Commitments, People, Metadata) and adminme-finance.xlsx (Raw Data, Accounts, Metadata). Sheets requiring unregistered event types (Lists, Members, Assumptions, Dashboard, Balance Sheet, Pro Forma, Budget vs Actual) skipped with TODO markers.
  - File locking via `fcntl.flock` with 10s timeout; atomic write via temp+rename.
  - 5s debounce with cancel-on-new-event; `regenerate_now` bypasses for tests.
  - Derived-cell protection at cell level + sheet protection on read-only sheets (placeholder password `"adminme-placeholder"` — real secrets in prompt 16).
  - XlsxQueryContext holds handles to 7 projection connections (parties, tasks, commitments, recurrences, calendars, places_assets_accounts, money). Runner's register() unchanged; bootstrap constructs the projection with the context after other projections start.
  - 28 new unit tests (ops workbook 13, finance 8, emit 4, debounce 3) + 1 integration test + demo_xlsx_forward.py smoke.
  - §2.2 grep audit: every `log.append` in the xlsx projection is for `xlsx.regenerated` only.
  - Ruff clean, mypy clean (109 source files), all inviolable greps OK (one pre-existing false positive in pipelines/runner.py docstring noted, not introduced by 07b).
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - Sidecar state JSON (last-known sheet state) is NOT written in this prompt. 07c writes it after each forward regeneration OR reads from the just-written workbook at adapter startup. 07c decides the implementation path.
  - `xlsx.regenerated` emit provides a signal 07c's reverse adapter can subscribe to, to avoid re-diffing a workbook that forward just wrote.
  - Sheet protection passwords hardcoded to `"adminme-placeholder"` — 07c accepts this; real secrets flow lands in prompt 16.
  - Stub forward.py / reverse.py / schemas.py scaffolding files (from prompt 05) remain untouched — 07c may repurpose or delete.
- **Carry-forward for prompt 08**:
  - No query functions in xlsx projection; nothing for Session to wrap. Sheet builders read other projections' queries (which are the ones Session wraps).
- **Carry-forward for prompt 10+**:
  - Dashboard / Balance Sheet / Pro Forma / Budget vs Actual sheets skipped — derived-math pipelines build them later.
- **Carry-forward for future bootstrap (prompt 16)**:
  - Real xlsx daemon lifecycle (start-on-boot, stop-on-shutdown) lands in bootstrap. Phase A is code + tests only.

### Prompt 07c-α — xlsx round-trip foundations (schema + sidecar + diff core)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07c-alpha-foundations.md.
- **Session merged**: PR #<N>, commits aa395dd / 7305acd / fcdb592 / <commit4>, merged <merge date>.
- **Outcome**: MERGED
- **Note**: Part 1 of a two-prompt split. 07c-β consumes what this prompt lands and ships the reverse daemon + integration round-trip. Split exists per partner_handoff PM-2 / PM-15: full reverse daemon plus its test pyramid does not fit one Claude Code session window.
- **Evidence**:
  - Two new system events at v1 in `adminme/events/schemas/system.py`: `xlsx.reverse_projected` (workbook_name, detected_at, sheets_affected, events_emitted, duration_ms) and `xlsx.reverse_skipped_during_forward` (workbook_name, detected_at, skip_reason: forward_lock_held). Schema-registry test asserts both register at v1.
  - `scripts/verify_invariants.sh` `ALLOWED_EMITS` regex extended; `ALLOWED_EMIT_FILES` left alone with maintenance comment explaining why (07c-β's reverse daemon lives outside `adminme/projections/` so the projection-emit auditor doesn't apply).
  - PM-10 disposition: deleted `adminme/projections/xlsx_workbooks/forward.py`, `reverse.py`, `schemas.py` — three 22-line scaffolding stubs from prompt 02 with no callers. Pre-delete grep confirmed zero imports.
  - `InstanceConfig.xlsx_workbooks_dir` flipped from `projections/.xlsx-state/` to `projections/xlsx_workbooks/`. Sibling `.xlsx-state/` resolves via `xlsx_workbooks_dir.parent / ".xlsx-state"`. Sibling pathway is required so 07c-β's watchdog scoped to the workbooks dir cannot self-trigger on sidecar writes. Existing 29-test xlsx suite unchanged — all callers use `config.xlsx_workbooks_dir` directly so the path change is transparent.
  - Sidecar I/O module at `adminme/projections/xlsx_workbooks/sidecar.py`: `sidecar_dir`, `sidecar_path`, `write_sheet_state`, `write_readonly_state`, `read_sheet_state`, `read_readonly_state`, `hash_readonly_sheet`. Atomic writes via `.tmp.<pid>` + `os.replace`. Bidirectional sheets persist `{"rows": [...]}`; read-only sheets persist `{"content_hash": "<sha256-hex>"}` only (no row data — hash-only is sufficient for the WARN signal 07c-β needs on read-only edits).
  - Forward daemon extended: `XlsxWorkbooksProjection._regenerate` now calls a new `_write_sidecar_for(workbook, xlsx_path)` helper as the LAST step inside the workbook lock. Helper opens the just-written xlsx with `openpyxl.load_workbook(data_only=True)` and writes per-sheet sidecar JSON. Reading back from the xlsx (rather than re-querying projections) ensures byte-alignment between sidecar and workbook on disk.
  - Two module-level constants `_BIDIRECTIONAL_SHEETS` and `_READONLY_SHEETS` are now the single source of truth for which sheets each workbook contains; both the `xlsx.regenerated` payload's `sheets_regenerated` list and the sidecar writer derive from them.
  - New `adminme/daemons/xlsx_sync/` package with binding placement note: "this daemon is L1-adjacent, NOT a projection per [§2.2] — it emits events." Per-sheet diff descriptors at `sheet_schemas.py` (Tasks/Commitments/Recurrences/Raw Data) declaring id_column, editable_columns (frozenset OR per-row callable for Raw Data), always_derived, add/update/delete event mappings, undo-window flag, new_id_prefix, and drop dispositions for non-emitting cases. Pure-functional diff core at `diff.py` with type normalization (float tol 1e-9, datetime/date → isoformat, None ≡ "", int↔float as floats) and id-column-edit-as-delete-plus-add semantics. Diff core has zero I/O — no openpyxl, no watchdog, no event log.
  - 23 new unit tests across four sites: 1 schema-registry extension, 8 sidecar I/O, 4 forward-writes-sidecar, 15 diff core (the diff site exceeds the ≥10 requirement).
  - Ruff clean, mypy clean, `bash scripts/verify_invariants.sh` clean. Full test suite passes.
- **Carry-forward for prompt 07c-β (reverse daemon + integration round-trip)**:
  - Descriptors at `adminme/daemons/xlsx_sync/sheet_schemas.py` — bidirectional set is exactly Tasks / Commitments / Recurrences / Raw Data. People / Accounts / Metadata are read-only; 07c-β handles them via a separate "WARN if hash drifted" code path, NOT via descriptors here.
  - Diff core at `adminme/daemons/xlsx_sync/diff.py` — sync, pure-functional, returns `DiffResult(added, updated, deleted, dropped_edits)`. Daemon wraps it with watchdog → asyncio bridge, lock acquisition, undo window, sensitivity preservation, cold-start handling.
  - Sidecar I/O at `adminme/projections/xlsx_workbooks/sidecar.py` — forward already writes inside its lock. Reverse must rewrite at the end of each cycle (per BUILD.md §3.11 line 1015) so two principal saves diff against each other. Path: `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`, sibling of workbooks dir.
  - The reverse daemon emits domain events on principal authority. The full UT-7 actor-attribution path (which member id authored the edit) does NOT resolve here; it resolves in prompt 08 when the event router knows about Session/principal_member_id. 07c-β stubs actor with a documented placeholder.
  - The `xlsx.regenerated` system event provides a signal 07c-β's reverse can use to skip diffing a workbook the forward daemon just wrote.
- **Carry-forward for prompt 08 (Session + scope + governance)**:
  - UT-7 opens here, resolves there: route reverse-emitted events through Session/guardedWrite so the actor (`added_by_party_id`, `principal_member_id`, etc.) is principal-attributed.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Daemon lifecycle (start-on-boot, stop-on-shutdown) for the reverse daemon — shipped in 07c-β, wired into bootstrap in 16.
  - Adding `observation_mode_active` to forward emit payload per D5 — deferred to 16 alongside observation-mode wiring.
- **Carry-forward for prompt 17 (CLI)**:
  - `adminme projection rebuild xlsx_workbooks` CLI per BUILD.md §3.11 line 1054 (rebuild deletes both workbook files plus the sidecar tree, then regenerates).
