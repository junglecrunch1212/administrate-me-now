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
- **Session merged**: PR #20, commits aa395dd / 7305acd / fcdb592 / 1d770ec, merged 2026-04-24.
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
  - 28 new unit tests across four sites: 1 schema-registry extension, 8 sidecar I/O, 4 forward-writes-sidecar, 15 diff core (the diff site exceeds the ≥10 requirement; sidecar site exceeds ≥6). [QC correction: PR description and earlier draft of this entry said "23 new" — actual tally is 1+8+4+15 = 28.]
  - Ruff clean, mypy clean, `bash scripts/verify_invariants.sh` clean. Full test suite passes.
  - **QC note (descriptor visibility):** descriptor symbols in `sheet_schemas.py` are private (`_TASKS`, `_COMMITMENTS`, `_RECURRENCES`, `_RAW_DATA`); accessible only via `descriptor_for(workbook, sheet)` and `editable_columns_for(descriptor, row)` and the `BIDIRECTIONAL_DESCRIPTORS` tuple. Earlier 07c-β draft cited them by `TASKS_DESCRIPTOR`-style names that don't exist; 07c-β was patched in refactor to use the public accessor everywhere. See PM-16.
  - **QC note (read_only flag):** `_write_sidecar_for` opens the just-written xlsx with `read_only=False`. `read_only=True` would be cheaper for read-back. Not a violation; not blocking; flagging for awareness only.
  - **QC note (test fixture amendment):** during Commit 3 development, the `money_flow.manually_added` test fixture initially omitted `added_by_party_id` and used a `linked_account` field since removed from schema. Claude Code amended the fixture to include `added_by_party_id: "p1"` and re-ran; tests passed. Healthy debug loop. Surfaces UT-7 (actor attribution) as relevant for 07c-β's daemon emit path — daemon will use `actor_identity="xlsx_reverse"` per 07c-β draft until prompt 08 closes UT-7.
- **Carry-forward for prompt 07c-β (reverse daemon + integration round-trip)**:
  - Descriptors at `adminme/daemons/xlsx_sync/sheet_schemas.py` — bidirectional set is exactly Tasks / Commitments / Recurrences / Raw Data. People / Accounts / Metadata are read-only; 07c-β handles them via a separate "WARN if hash drifted" code path, NOT via descriptors here. **Reach descriptors via `descriptor_for(workbook, sheet)`; iterate via `BIDIRECTIONAL_DESCRIPTORS`. Private symbols (`_TASKS` etc.) are not exported.**
  - Diff core at `adminme/daemons/xlsx_sync/diff.py` — sync, pure-functional, returns `DiffResult(added, updated, deleted, dropped_edits)`. Daemon wraps it with watchdog → asyncio bridge, lock acquisition, undo window, sensitivity preservation, cold-start handling.
  - Sidecar I/O at `adminme/projections/xlsx_workbooks/sidecar.py` — forward already writes inside its lock. Reverse must rewrite at the end of each cycle (per BUILD.md §3.11 line 1015) so two principal saves diff against each other. Path: `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`, sibling of workbooks dir.
  - The reverse daemon emits domain events on principal authority. The full UT-7 actor-attribution path (which member id authored the edit) does NOT resolve here; it resolves in prompt 08 when the event router knows about Session/principal_member_id. 07c-β stubs actor with `actor_identity="xlsx_reverse"` per the draft's envelope template.
  - The `xlsx.regenerated` system event provides a signal 07c-β's reverse can use to skip diffing a workbook the forward daemon just wrote.
  - **Recurrences sheet shape:** the 07b-built Recurrences sheet has columns `recurrence_id, title, cadence, next_due, assigned_member, notes, active, last_completed_at`. There is no `rrule` column on the sheet — the projection's underlying `rrule` is rendered into the human-readable `cadence` column at forward time. 07c-β's Recurrences ADD must map sheet `cadence` → `RecurrenceAddedV1.rrule` directly. The schema accepts free-text (`str`, `min_length=1`) — no RFC 5545 validation. Whatever the principal typed in `cadence` passes through unchanged. (Earlier 07c-β draft assumed both columns existed; patched in refactor.)
- **Carry-forward for prompt 08 (Session + scope + governance)**:
  - UT-7 opens here, resolves there: route reverse-emitted events through Session/guardedWrite so the actor (`added_by_party_id`, `principal_member_id`, etc.) is principal-attributed.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Daemon lifecycle (start-on-boot, stop-on-shutdown) for the reverse daemon — shipped in 07c-β, wired into bootstrap in 16.
  - Adding `observation_mode_active` to forward emit payload per D5 — deferred to 16 alongside observation-mode wiring.
- **Carry-forward for prompt 17 (CLI)**:
  - `adminme projection rebuild xlsx_workbooks` CLI per BUILD.md §3.11 line 1054 (rebuild deletes both workbook files plus the sidecar tree, then regenerates).

### Prompt 07c-β — xlsx reverse daemon + integration round-trip
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/07c-beta-reverse-daemon.md.
- **Session merged**: PR #21, commits ffa6d9c / bf649ed / 00bff7d / 2788761, merged 2026-04-25.
- **Outcome**: MERGED
- **Note**: Part 2 of a two-prompt split. 07c-α (PR #20, merged 2026-04-24) landed schema/descriptors/diff/sidecar; together with this prompt it closes the xlsx round-trip and resolves UT-6.
- **Evidence**:
  - `XlsxReverseDaemon` lands at `adminme/daemons/xlsx_sync/reverse.py`. Public API: `start()` (begins watchdog observer; idempotent), `stop()` (cancels pending cycles + undo-window tasks + observer; idempotent), `run_cycle_now(workbook)` (bypasses the watchdog and flush wait; used by tests). Constructor signature: `(config, query_context, *, event_log, flush_wait_s=2.0, forward_lock_timeout_s=10.0, delete_undo_window_s=5.0)` per spec.
  - Per-cycle algorithm follows BUILD.md §3.11 lines 993–1080 verbatim: flush wait → forward lock acquisition (timeout → emit `xlsx.reverse_skipped_during_forward` and return — NO `xlsx.reverse_projected`) → load workbook with `openpyxl.load_workbook(data_only=True)` → for each bidirectional sheet, diff live rows vs sidecar (cold-start sheets emit nothing) → for each read-only sheet, hash live rows + WARN on drift → rewrite ALL sidecar JSON to current state (so subsequent cycles diff cleanly) → release lock → emit `xlsx.reverse_projected` with `events_emitted` (envelope ids) + `sheets_affected` + `duration_ms`.
  - Watchdog→asyncio bridge: `watchdog.observers.Observer` schedules a `FileSystemEventHandler` whose callbacks run on watchdog's thread; the daemon hops to its asyncio loop via `loop.call_soon_threadsafe(self._schedule_cycle, workbook_name)`. The on-loop `_schedule_cycle` cancels any pending debounce for the workbook and schedules a fresh one after `flush_wait_s`. Per-workbook serialization is enforced by an internal `asyncio.Lock` keyed per workbook; tests assert `_max_concurrent_observed <= 1`.
  - Undo window: deletes on sheets where `descriptor.deletes_use_undo_window=True` queue an `asyncio.Task` via `_schedule_undo_delete` that sleeps `delete_undo_window_s` then emits the delete event. Subsequent cycles observing the row return cancel the task via `cancel_pending_delete`. Note: emits scheduled inside the undo window do NOT count toward the cycle's `events_emitted` list (they fire after the cycle's terminal `xlsx.reverse_projected`). Acceptable observability gap, documented in the daemon's module docstring.
  - All four bidirectional sheet pathways wired:
    - **Tasks** — ADD: mints `tsk_<8 hex>` on blank `task_id`, builds `TaskCreatedV1` from sheet (title, optional notes→description, assigned_member→owner_member_id, due_date→due, energy). UPDATE: emits `task.updated` with `field_updates`. DELETE: undo-window → `task.deleted` with `deleted_by_party_id="xlsx_reverse"`.
    - **Commitments** — ADD: drops INFO ("commitments are pipeline-proposed only per [§4.2]"). UPDATE: emits `commitment.edited` with `edited_by_party_id="xlsx_reverse"`. DELETE: drops INFO.
    - **Recurrences** — ADD: mints `rec_<8 hex>` on blank id, maps sheet `cadence` directly into `RecurrenceAddedV1.rrule` (free-text passthrough; no RFC 5545 validation), `linked_kind="household"` / `linked_id="household"`, sheet `title`→event `kind`, sheet `next_due`→event `next_occurrence` (today's date if blank). UPDATE: emits `recurrence.updated`. DELETE: drops INFO.
    - **Raw Data** — ADD branches on `is_manual`: TRUE mints `flow_<8 hex>` on blank id and emits `money_flow.manually_added` (amount→`int(round(amount * 100))`, currency `USD`, kind `paid`, assigned_category→category, `added_by_party_id="xlsx_reverse"`); FALSE drops WARN. UPDATE: drops INFO regardless of which row (descriptor.updates_emit_event=None at v1 — `money_flow.recategorized` not registered yet). DELETE branches on the sidecar row's `is_manual`: TRUE → undo-window → `money_flow.manually_deleted`; FALSE → drops WARN.
  - Sensitivity preservation on UPDATE / DELETE: helper `_lookup_sensitivity` selects from `tasks` / `commitments` / `recurrences` / `money_flows` (tenant-scoped) and falls back to `'normal'` if the row isn't in the projection. ADD events default to `'normal'`. Coerced to the envelope's `Literal['normal','sensitive','privileged']` via `_sensitivity_literal`.
  - Actor attribution: every emitted envelope carries `actor_identity="xlsx_reverse"` and every `*_by_party_id` payload field uses the literal `"xlsx_reverse"`. UT-7 (principal_member_id resolution) remains OPEN here per the draft; it resolves in prompt 08 when the event router knows about Session/guardedWrite.
  - Test pyramid: 23 new unit tests + 1 integration round-trip test, all green.
    - `tests/unit/test_xlsx_reverse_basic.py` (11): no-op, ADD-with-blank-id minting, title edit→`task.updated`, delete-after-undo-window, delete-cancelled-within-window, derived-column dropped (created_at edit silently absorbed), id-column edit surfaces as delete+add, read-only sheet WARN with no domain emit, terminal `xlsx.reverse_projected` always fires with non-negative `duration_ms`, sensitivity preserved on `task.updated`.
    - `tests/unit/test_xlsx_reverse_lock_contention.py` (4): forward-lock-held emits skipped (no `reverse_projected`), released-in-time proceeds, concurrent cycles serialize per-workbook, skip cycle never emits `reverse_projected` (explicit canary).
    - `tests/unit/test_xlsx_reverse_finance.py` (6): manual ADD emits `money_flow.manually_added` with `flow_<8hex>` shape canary, Plaid ADD drops WARN, manual DELETE after window emits `money_flow.manually_deleted`, Plaid DELETE drops WARN, assigned_category edit on Plaid row drops INFO, amount edit on manual row drops INFO (descriptor.updates_emit_event=None at v1).
    - `tests/unit/test_xlsx_reverse_cold_start.py` (2): no sidecar tree → cycle writes sidecars + emits no domain events + `reverse_projected` with empty `events_emitted`; partial sidecar (Tasks deleted) → Tasks treated as cold while Recurrences diff fires normally.
    - `tests/integration/test_xlsx_roundtrip.py` (1): 7 sqlite projections + forward + reverse end-to-end. Seeds ~10 events, forward regenerates both workbooks, performs principal-style edits (rename Task, append blank-id Task, delete-Recurrence-attempt, append manual + Plaid Raw Data rows, delete a manual row), runs reverse cycles, asserts the correct event sequence (recurrence delete drops, task ADD mints `tsk_<8hex>`, money flow ADD mints `flow_<8hex>`, Plaid ADD WARNs and drops, manual DELETE after window emits), and finally regenerates forward to prove the principal-authored deltas land in the workbook.
  - `scripts/demo_xlsx_roundtrip.py` smoke: temp instance → 9 seed events → forward regen → programmatic xlsx edit → reverse cycle → forward regen, prints event counts at each stage. Runs in ~1.4s on the dev box, well under the 30s ceiling.
  - Full suite: **261 passed, 1 skipped** (prior 07c-α baseline 237 passed + 23 new unit + 1 new integration). Ruff clean. Mypy clean (111 source files). `bash scripts/verify_invariants.sh` clean — `ALLOWED_EMITS` already covers `xlsx.reverse_projected` and `xlsx.reverse_skipped_during_forward` from 07c-α.
- **Carry-forward for prompt 07.5 (checkpoint)**:
  - The integration round-trip test (`tests/integration/test_xlsx_roundtrip.py`) is the canary for `DERIVED_COLUMNS` ↔ `descriptor.always_derived` equivalence. If a forward sheet builder adds a new derived column without updating the descriptor, the test will start emitting spurious `*.updated` events and fail. 07.5's audit should formalize this canary check.
- **Carry-forward for prompt 08 (Session + scope + governance)**:
  - **UT-7 OPEN**: route reverse-emitted events through Session / guardedWrite so the actor (`updated_by_party_id`, `deleted_by_party_id`, `added_by_party_id`, `edited_by_party_id`) is principal-attributed instead of the literal `"xlsx_reverse"` stub. The reverse daemon currently writes that literal on every emit; prompt 08's authority gate replaces it with the authenticated principal_member_id.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Daemon lifecycle (start-on-boot, stop-on-shutdown) wires into the bootstrap. The daemon's `start()` / `stop()` are idempotent and ready to be called from the supervisor.
  - `observation_mode_active` field on the forward emit payload still deferred per 07c-α carry-forward; the reverse daemon does not need observation-mode awareness in this prompt.
- **Carry-forward for future prompt (event-type expansion)**:
  - Once `money_flow.recategorized` is registered, flip Raw Data descriptor's `updates_emit_event` from `None` to that type and replace the INFO-drop in `_emit_raw_data`. The descriptor change is a one-liner in `sheet_schemas.py`; the test `test_amount_edit_on_manual_row_drops_info` and `test_assigned_category_edit_drops_info` will need to be updated to assert emission instead of drop.

### Prompt 08a — Session model + scope enforcement (read side)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/08a-session-and-scope.md (165 lines, quality bar = 07c-α/β slim form).
- **Session merged**: PR #<PR-08a>, commits <sha1-08a> / <sha2-08a> / <sha3-08a> / <sha4-08a>, merged 2026-04-25.
- **Outcome**: MERGED.
- **Evidence**:
  - `adminme/lib/session.py` (re)populated. Frozen `Session` dataclass per BUILD.md L3-continued: `tenant_id`, `auth_member_id`, `auth_role` (`principal`/`child`/`ambient`/`coach_session`/`device`), `view_member_id`, `view_role`, `dm_scope`, `source` (7-value enum incl. `xlsx_workbooks` / `xlsx_reverse_daemon`), `correlation_id`. `is_view_as` and `allowed_scopes` properties; the latter is the visibility-scope axis only (sensitivity / privileged-owner checks live in `scope.allowed_read`). Three constructors: `build_session_from_node` (Tailscale identity bridge per CONSOLE_PATTERNS §1), `build_session_from_openclaw` (slash-command + standing-order), `build_internal_session` (CLI / daemons / xlsx forward / skill runner). All three enforce DIAGRAMS §4 view-as matrix; only principals can view-as, children + coach + ambient cannot, ambient is never a view target.
  - `adminme/lib/scope.py` (re)populated. `ScopeViolation` exception lives here; `allowed_read(session, sensitivity, owner_scope)` mirrors the SQL WHERE clause for the DIAGRAMS §5 (auth_role × sensitivity × owner_scope) matrix. `privacy_filter` collapses non-owner privileged calendar events to a busy-block allowlist shape per CONSOLE_PATTERNS §6 (allowlist not blocklist). `coach_column_strip` drops `financial_*` / `health_*` columns per [§13]. `child_hidden_tag_filter` drops rows tagged with `CHILD_FORBIDDEN_TAGS = {finance, health, legal, adult_only}` per CONSOLE_PATTERNS §6/§7 — same constant 14a's middleware will consume. `filter_rows` / `filter_one` is the single bundle helper every projection query calls.
  - All 10 SQLite projections wrapped: parties (5 funcs), interactions (3), artifacts (3), commitments (5), tasks (6), recurrences (4), calendars (5), places_assets_accounts (7), money (6), vector_search (4). Total 48 # TODO(prompt-08) markers cleared (`grep -rn 'TODO(prompt-08)' adminme/projections/ | wc -l == 0`). Every public query function takes `session: Session` as the leading positional parameter and derives `tenant_id` from `session.tenant_id`.
  - Special case [§13.9] / UT-8 carve-out on `vector_search.nearest`: privileged exclusion is hardcoded at the SQL level regardless of session role (handler refuses writes; SQL excludes reads; `filter_rows` is the third-line canary). Returns empty for ambient sessions. The scope filter does NOT raise `ScopeViolation` on a privileged-owner query — that path returns empty so coach context builders downstream cannot inadvertently leak existence through error semantics.
  - `xlsx_workbooks` builders (`build_ops_workbook` / `build_finance_workbook`) construct an internal Session via `build_internal_session("xlsx_workbooks", "device", tenant_id)` once at workbook entry; threaded into every sheet builder's `session=` kwarg. Sheet builders accept the parameter for forward-compatibility — current sheet code reads raw SQL via `ctx.{projection}_conn`, so the session is unused inside sheets; future sheet refactors that route through the `queries.py` modules will use it directly. Anchoring the session-construction site at one place per workbook is the carry-forward 08b's guardedWrite consumers rely on.
  - 11 existing projection test files updated for the new signature. Each gains a top-level `_S(tenant_id="tenant-a")` helper that mints an internal-actor principal session via `build_internal_session("test_actor", "principal", tenant_id)`. Demo script (`scripts/demo_projections.py`) similarly updated to pass `_demo_session()` into every query call. Six pre-existing `test_scope_canary_stub_privileged_lands` tests (in tasks / commitments / recurrences / calendars / places_assets_accounts / money) were rewritten to `test_scope_canary_privileged_drops_for_non_owner`: the privileged-sensitivity row is still in the projection table per [§6.4] but is dropped from a non-owner read; the rewrite asserts both halves.
  - 69 new unit tests: 30 in `tests/unit/test_session.py` (every row of DIAGRAMS §4 matrix + three constructors + AuthError paths + correlation-id propagation), 39 in `tests/unit/test_scope.py` (auth_role × sensitivity × owner_scope cells + privacy_filter content-field-drop canary + view-as auth-member privileged check + coach_column_strip + child_hidden_tag_filter for finance/health/legal/adult_only tags + JSON / comma-string / list tag shapes + ScopeViolation canary). Overshoot vs the prompt's "≥42" floor: +27 (12+30 → 30+39).
  - Full suite: **330 passed, 1 skipped** (prior baseline 261 + 30 session + 39 scope = 330; canary rewrites kept the test count constant). Ruff clean. Mypy clean (60 source files). `bash scripts/verify_invariants.sh` clean.
- **Carry-forward for prompt 08b**:
  - Session API surface frozen; 08b imports `Session`, `allowed_read`, `privacy_filter`, `ScopeViolation`, `CHILD_FORBIDDEN_TAGS`. Plus `filter_rows` / `filter_one` if guardedWrite consumers want to reuse the bundle.
  - The 10 SQLite-projection `queries.py` signatures now accept `session: Session`; 08b's guardedWrite consumers can rely on Session being available at every read call site.
  - **UT-7 OPEN**: `adminme/daemons/xlsx_sync/reverse.py` still uses `actor_identity="xlsx_reverse"` literal at line ~821 (the `_append` helper's `_ACTOR` constant). 08b's surgical edit replaces with the Session-resolved principal_member_id once the authority gate lands.
  - `Source` enum already includes `xlsx_reverse_daemon` so 08b's UT-7 fix can use `build_internal_session("xlsx_reverse", ...)` and have the source label come out correctly.
  - Allowed_scopes computed at Session construction returns `frozenset()` for ambient — the defense-in-depth check 08b's outbound filter can rely on for the "no surface at all" rule.
- **Carry-forward for prompt 09a**:
  - Skill runner constructs sessions via `build_internal_session("skill_runner", "device", tenant_id)` for skill-call provenance. Source resolves to `product_api_internal`.
- **Carry-forward for prompt 13a/b** (product API):
  - Product API endpoints construct sessions via `build_session_from_node(req, config)` for every authenticated request. `req.identity` and `req.view_as` are the contract; `req.correlation_id` propagates.
- **Carry-forward for prompt 14a** (server-side child block):
  - `CHILD_FORBIDDEN_TAGS = {"finance", "health", "legal", "adult_only"}` is the same constant the server-side nav middleware consumes. Imported from `adminme.lib.scope`.
- **QC note (canary rewrites):** six projection test files had pre-existing `test_scope_canary_stub_privileged_lands` tests with the docstring "prompt 06 stub — prompt 08 extends to ScopeViolation on query." 08a's read-time filter does NOT raise `ScopeViolation` for non-owner privileged reads — it drops the row silently (existence-leak avoidance). The rewrites match this design and assert "row absent from filtered query, present in raw SQL count" — a stronger guarantee than the original "extends to ScopeViolation" plan. If 08b decides ScopeViolation should fire instead, the rewrites become trivial flips.

### Prompt 08b — Governance + observation mode + reverse-daemon attribution (UT-7)
- **Refactored**: by Partner in Claude Chat, pre-session. Prompt file: prompts/08b-governance-and-observation.md (197 lines, quality bar = 07c-β slim form).
- **Session merged**: PR #<PR-08b>, commits <sha1-08b> / <sha2-08b> / <sha3-08b> / <sha4-08b>, merged 2026-04-25.
- **Outcome**: MERGED.
- **Evidence**:
  - `adminme/lib/governance.py` populated. `ActionGateConfig` (action_gates dict + rate_limits dict + forbidden_outbound_parties list); `RateLimit(window_s, max_n)` and `RateLimiter` with sliding-log buckets per CONSOLE_PATTERNS §4 (in-place shift prune; per-key isolation; injectable `time_fn` for deterministic tests; `decide()` returns `RateLimitDecision(allowed, retry_after_s)` with `retry_after_s` rounded up); `AgentAllowlist` with three-form glob match (`*`, `prefix.*`, `prefix:*`) per CONSOLE_PATTERNS §3 (regex deliberately unsupported). `GuardedWriteResult(pass_, layer_failed, reason, review_id, retry_after_s, correlation_id)` carries the four-layer outcome — `pass_` is named with trailing underscore because `pass` is reserved. `derive_agent_id(session)` maps Session.source × auth_member_id to the allowlist key (`daemon:xlsx_reverse`, `daemon:xlsx_workbooks`, `openclaw:<member>`, `system:internal`, `system:bootstrap`, `user:<member>`). `GuardedWrite.check(session, action, payload)` runs the three layers in strict order per [§6.5-6.8]; first refusal short-circuits, emits `write.denied` with `layer_failed` attribution. `'review'` gate emits `review_request` with payload + review_id + actor_identity instead of `write.denied`. `'hard_refuse'` is non-overridable even with admin-equivalent (`['*']` allowlist) sessions [§6.7]. Config loaders accept BUILD.md vocabulary aliases (`confirm` → `review`; `window_sec`/`max_calls` ↔ `window_s`/`max_n`).
  - `adminme/lib/observation.py` populated. `ObservationState(active, enabled_at, enabled_by)` frozen dataclass. `ObservationManager(event_log, runtime_config_path)` persists state to `<config_dir>/runtime.yaml` per BUILD.md line 2149; reads use `asyncio.to_thread`; toggle operations are guarded by an `asyncio.Lock` for read-modify-write safety. **Default-on for new instances** [§6.16] — absent or corrupt `runtime.yaml` returns `active=True` (failing closed for the security invariant). `enable()` / `disable()` emit `observation.enabled` / `observation.disabled` with `prior_state`, `actor`, `reason`, and timestamp per [§13]. `outbound(session, action, payload, action_fn, *, manager, event_log, target_channel, target_identifier)` is the single enforcement point per [§6.14]: active → emits `observation.suppressed` with `would_have_sent_payload` + `observation_mode_active=True`, does NOT call `action_fn`; inactive → calls `action_fn`, emits `external.sent` on success; failure path raises through `action_fn`'s exception without emitting `external.sent`.
  - Five new event schemas registered at v1 per [D7] in `adminme/events/schemas/governance.py`:
    - **`write.denied`** — `layer_failed` ∈ {`allowlist`, `governance`, `rate_limit`}, `reason`, `agent_id`, `action`, `payload_echo`, optional `review_id` / `retry_after_s` / `actor_identity`.
    - **`review_request`** — `review_id`, `agent_id`, `action`, `payload`, `requested_at`, optional `actor_identity`.
    - **`observation.suppressed`** — extended with `observation_mode_active: bool = True` (optional with default to preserve the existing v1 envelope test). Existing fields (attempted_action, attempted_at, target_channel, target_identifier, would_have_sent_payload, reason, session_correlation_id) unchanged.
    - **`observation.enabled`** / **`observation.disabled`** — actor, reason, prior_state, enabled_at/disabled_at.
    - **`external.sent`** — action, sent_at, target_channel, target_identifier, payload, optional session_correlation_id. Companion to `observation.suppressed` for the success path.
  - Fixtures: `tests/fixtures/governance/sample_governance.yaml` (action_gates with `allow`/`review`/`deny`/`hard_refuse` enums; rate_limits with `__default__` fallback + per-action `outbound.send` 60s/20 cap + `task.create` 60s/30 cap + `burst.action` 1s/2 cap; `forbidden_outbound_parties` mirroring BUILD.md `never:` block) and `tests/fixtures/authority/sample_authority.yaml` (agent_allowlist for system/daemon/user/openclaw agents). Both marked `# fixture:tenant_data:ok` per [§12.4]. Use `<persona.handle>` placeholder pattern; no specific tenant identity bleed.
  - **UT-7 closure (08b)**: `adminme/daemons/xlsx_sync/reverse.py` — removed `_ACTOR = "xlsx_reverse"` literal at line 91; `_append` helper now takes `session: Session` and derives `actor_identity` from `session.auth_member_id`. Returns `str | None` to support guarded-write refusals. Optional `guarded_write: GuardedWrite | None` parameter on `XlsxReverseDaemon.__init__`; when wired, every `_append` routes through the three-layer check before append. Optional `principal_member_id_resolver: Callable[[str], str | None]` parameter resolves a workbook name to a detected principal; `_session_for(workbook)` uses it to construct the per-cycle Session via the new `build_session_from_xlsx_reverse_daemon(detected_member_id, config)` helper in `session.py`. Each of the eight `_emit_*` methods now threads the cycle's session through and replaces literal `"xlsx_reverse"` references in `*_by_party_id` payload fields with `session.auth_member_id`. The terminal `xlsx.reverse_projected` and skip-cycle `xlsx.reverse_skipped_during_forward` events stay system-attributed (`actor_identity="xlsx_reverse"`) per [§13] — they are system observability signals, not domain events.
  - 47 new tests (overshoot vs prompt's ≥30 floor):
    - `tests/unit/test_governance.py` (30): glob pattern matching (exact, segment-aware `.*`, namespace `:*`); agent-id derivation (xlsx_reverse / node_console_user / system_internal); rate-limiter mechanics (admit / deny with retry_after / per-key isolation / window-reopen via injected `time_fn`); config loaders (action_gates / rate_limit defaults / per-action limits / forbidden parties); per-layer denials (allowlist / governance deny / hard_refuse non-overridable / review emits review_request not write.denied / rate_limit exhaustion); short-circuit ordering (layer-1 refusal does not consume rate budget; layer-2 hard_refuse does not consume rate budget); happy path (all three pass); event structure correctness (write.denied carries full attribution + actor_identity; review_request carries full payload).
    - `tests/unit/test_observation.py` (13): default-on when runtime.yaml absent and when corrupt; toggle persistence (disable / enable / round-trip via fresh manager — console-restart contract); enable + disable emit toggle audit events with correct prior_state; outbound suppress path (event payload structure + action_fn NOT called); outbound passes-through when inactive (action_fn called + external.sent emitted with same shape); outbound propagates action_fn exceptions without emitting external.sent; ObservationState + OutboundResult immutability.
    - `tests/integration/test_security_end_to_end.py` (4): view-as records writer.actor distinct from owner per [§6.3]; privileged calendar event read by non-owner via `privacy_filter` is busy-block redacted per [§6.9, CONSOLE_PATTERNS.md §6]; `filter_rows` drops privileged rows for non-owner queries entirely (existence-leak avoidance per [§6.9 / §13.9]); guardedWrite + outbound() composition suppresses external sends during observation while the audit trail (observation.suppressed) lands.
    - `tests/integration/test_xlsx_roundtrip.py` (1): UT-7 closure case — daemon constructed with `principal_member_id_resolver=lambda _wb: "principal_a"`; programmatic Tasks edits trigger task.created + task.updated; both events carry `actor_identity == "principal_a"` (NOT `"xlsx_reverse"`); the `updated_by_party_id` payload field also reflects the principal. Cycle-terminus `xlsx.reverse_projected` stays system-attributed.
  - Full suite: **378 passed, 1 skipped** (prior 08a baseline 330 + 30 governance + 13 observation + 4 security + 1 UT-7 = 378). Ruff clean. Mypy clean (20 source files in lib/+daemons/). `bash scripts/verify_invariants.sh` clean — no new ALLOWED_EMITS update needed (the new event types emit from product code / outbound wrappers, not from projections; the [§2.2] projection-emit canary is unaffected).
- **Carry-forward for prompt 09a**:
  - Skill runner outbound calls go through `outbound(session, action, payload, action_fn, manager=..., event_log=...)` from `adminme.lib.observation`.
  - Skill calls go through `guarded_write.check(session, "skill.invoke", ...)` before the openclaw HTTP dispatch.
- **Carry-forward for prompt 11+** (adapter prompts, e.g. Gmail / Plaid / BlueBubbles):
  - All adapters that emit domain events on external authority follow the reverse-daemon pattern: build a Session attributing the detected principal (or device-role placeholder if none), pass to `_append`, route through `guarded_write.check`. `build_session_from_xlsx_reverse_daemon` is the template; per-adapter helpers can mirror its shape.
- **Carry-forward for prompt 13a/b** (product API):
  - Every product API write endpoint constructs a `GuardedWrite` (load_governance_config + load_agent_allowlist + RateLimiter + EventLog) at startup; each route handler calls `await gw.check(session, action, payload)` before performing the write. On `result.pass_ == False`, map `layer_failed` → HTTP status (allowlist/governance → 403; rate_limit → 429 with `retry_after_s`).
- **Carry-forward for prompt 14a** (Node console parallel):
  - The Node-side `console/lib/guarded_write.js` mirrors the Python `GuardedWrite.check` semantics. Both must agree on the gate enum (`allow`/`review`/`deny`/`hard_refuse`) and the layer-failed attribution string set; the schema for `write.denied` is the contract.
- **Carry-forward for prompt 15** (OpenClaw exec-approval composition):
  - Two independent gates per [§8.7]: AdministrateMe's `GuardedWrite` runs at the HTTP API boundary BEFORE the openclaw skill_runner is invoked; OpenClaw's exec-approval runs at the tool-execution boundary AFTER policy. Neither substitutes for the other; both must pass. Prompt 15 wires up the composed flow.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - The bootstrap wizard authors the production `governance.yaml` and `authority.yaml` per the household's profile (`adhd_executive_profile` etc. from BUILD.md §AUTHORITY rate_limits). Default observation state at bootstrap end is ACTIVE (matches the manager's default-on behavior).
- **UT-7 status**: **RESOLVED 2026-04-25**. Reverse-daemon rewrite stayed in this prompt (Commit 3); the sidecar hedge to 08.5 was not activated. `_ACTOR` literal grep canary returns 0 hits.

### Prompt 09a — skill runner wrapper (around OpenClaw)
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: prompts/09a-skill-runner.md (~250 lines, quality bar = 08b).
- **Session merged**: PR #29, commits ff0b319 / 2ba917e / 9c92b33 / a55277e, merged 2026-04-26.
- **Outcome**: MERGED.
- **Evidence**:
  - `adminme/lib/skill_runner/wrapper.py` — `run_skill()` 9-step flow per [BUILD.md L4-continued] + [ADR-0002]. Provider-preference fallback iterates inside the wrapper; one POST per provider per attempt; deterministic 4xx and malformed 200 short-circuit the loop. Module-level event-log DI via `set_default_event_log()` mirrors 08b's `outbound()` pattern.
  - `adminme/lib/skill_runner/pack_loader.py` — parses `pack.yaml` + `SKILL.md` frontmatter + `schemas/input.schema.json` + `schemas/output.schema.json` + optional `handler.py`. Schemas validated with `Draft202012Validator.check_schema()`; cache by `(pack_id, version)`; `invalidate_cache()` test hook.
  - `packs/skills/classify_test/` — full pack scaffold for tests; trivial `(text) -> (is_thing, confidence)` classifier.
  - `adminme/events/schemas/domain.py` — `SkillCallRecordedV2.input_tokens`, `output_tokens`, `cost_usd`, and `openclaw_invocation_id` relaxed to Optional per `[ADR-0002]` graceful-degradation clause.
  - `adminme/events/schemas/system.py` — `SkillCallFailedV1` (closed-enum `failure_class`) and `SkillCallSuppressedV1` (closed-enum `reason`) registered at v1.
  - `scripts/verify_invariants.sh` — extended with a single-seam check that `skill.call.recorded`, `skill.call.failed`, `skill.call.suppressed` are emitted only from `adminme/lib/skill_runner/wrapper.py`. Same single-seam pattern as the xlsx forward projector.
  - `pyproject.toml` — `jsonschema >=4.21` (runtime, used by wrapper + pack_loader) and `respx` (dev) added; mypy `ignore_missing_imports` overrides for both. `markers = ["requires_live_services: ..."]` declared so the integration stub doesn't warn.
  - 14 unit tests (`tests/unit/test_skill_wrapper.py`) covering the full failure-mode pyramid: happy path with body-shape assertion, input invalid, sensitivity refused, scope insufficient, provider fallback (5xx → next provider), all providers 5xx, malformed 200 envelope, timeout, handler raises (defensive default returned + raw response saved), output validation fails, observation-mode short-circuit, dry-run short-circuit, large-input spillover, token/cost graceful degradation. All HTTP routed through `httpx.MockTransport` (the AdministrateMe sandbox has no live OpenClaw gateway).
  - 10 pack-loader tests (`tests/unit/test_pack_loader.py`) — manifest fields, schema validation samples, no-handler / handler-loaded / handler-without-`post_process`, cache hit, cache invalidation, malformed yaml, invalid JSON Schema.
  - 6 schema tests (`tests/unit/test_event_schemas.py`) — `SkillCallRecordedV2` accepts `None` tokens and cost; `SkillCallFailedV1` round-trip + rejects bad `failure_class`; `SkillCallSuppressedV1` round-trip + rejects bad `reason`.
  - `tests/integration/test_skill_wrapper_live.py` — live integration stub marked `requires_live_services`, skipped in Phase A.
  - `[§7]`/`[D7]`: `skill.call.failed` and `skill.call.suppressed` registered at v1.
  - `[§8]`/`[D6]`: zero new SDK imports; `verify_invariants.sh` clean.
  - `[ADR-0002]`: wrapper POSTs `/tools/invoke` with `tool: "llm-task"`; provider iteration in wrapper, not in OpenClaw.
  - **QC overshoot (Partner, 2026-04-26):** Commit 1 also relaxed `SkillCallRecordedV2.openclaw_invocation_id` to Optional (in addition to the three token/cost fields the prompt named). Defensible per [ADR-0002] provenance table — `openclaw_invocation_id` is documented as "from `/tools/invoke` response if present in response envelope." Logged as quality signal, not drift.
- **Carry-forward for prompt 09b**:
  - `pack_loader` accepts the canonical pack shape; `classify_thank_you_candidate` will be the second pack to load through it.
  - `run_skill` is stable; 09b just supplies a pack and asserts the round-trip.
- **Carry-forward for prompt 10a**:
  - Pipelines call `await run_skill(skill_id, inputs, SkillContext(session=..., correlation_id=...))`. Causation wiring (set causation_id on `skill.call.recorded` to the triggering domain event) lands in pipeline runner.
  - The wrapper's `_resolve_pack_root` accepts absolute paths, repo-relative slugs, and `namespace:name` forms; production pipelines should pass the absolute path resolved via `InstanceConfig.packs_dir`.
- **Carry-forward for prompt 16 (bootstrap)**:
  - Bootstrap §6 wires `OPENCLAW_GATEWAY_TOKEN` from 1Password; 09a falls back to env for now.
  - Bootstrap §7 calls `set_default_event_log(...)` at service start so production callers don't need to construct `_Runtime` themselves.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - `tests/integration/test_skill_wrapper_live.py` is the test that activates against the live OpenClaw gateway. The skip in 09a is its Phase-A placeholder.

### Prompt 09b — first canonical skill pack (classify_thank_you_candidate)
- **Refactored**: by Partner in Claude Chat, 2026-04-27. Prompt file: prompts/09b-first-skill-pack.md (~290 lines, quality bar = 09a).
- **Session merged**: PR #<PR-09b>, commits bae1681 / 585d9bf / 36fd872 / <commit4-09b>, merged <merge-date-09b>.
- **Outcome**: MERGED.
- **Evidence**:
  - `packs/skills/classify_thank_you_candidate/` — full pack at version 1.3.0 per [REFERENCE_EXAMPLES.md §3]; pack.yaml + SKILL.md (frontmatter + §3 body) + schemas/{input,output}.schema.json + prompt.jinja2 + handler.py.
  - `handler.py` — top-level `post_process(raw, inputs, ctx)`; only logic is the urgency-coercion safety net per [REFERENCE_EXAMPLES.md §3 lines 1389-1395]. Zero `adminme_platform`-style imports.
  - `tests/test_skill.py` — pack-loads-cleanly canary + handler-direct unit cases; 4 tests (loads + well-formed pass-through + missing-urgency coercion + non-dict defensive).
  - **QC overshoot (Partner, 2026-04-27 post-merge):** prompt floor for `tests/test_skill.py` was 1 test (loader canary only); shipped 4 (loader canary + 3 handler-direct unit cases). Adds the non-dict defensive case that mirrors the handler's first guard. Logged as quality signal, not drift. Prompt-floor for `tests/integration/test_classify_thank_you_pack.py` was 4 (3 fixtures + 1 handler-direct safety net) — shipped exactly 4. Total 09b new tests: **8** (4 unit + 4 integration), vs. floor 5.
  - `tests/integration/test_classify_thank_you_pack.py` — three fixture tests (kleins_hosted_us, reciprocal_coffee, coparent_pickup) + handler-direct safety-net test; 4 tests; all HTTP via `httpx.MockTransport`.
  - `bootstrap/pack_install_order.yaml` — NEW; single-entry list queued for prompt 15 / 16 install path.
  - `[§8]`/`[D6]`: zero LLM/embedding SDK imports; `verify_invariants.sh` clean.
  - `[§12.4]`: tenant-identity strings (stice-james, Klein, Mike) confined to `packs/skills/classify_thank_you_candidate/tests/fixtures/` and integration test fixture-construction sites with `# fixture:tenant_data:ok`.
  - `[ADR-0002]`: pack consumes `run_skill()` which POSTs to `/tools/invoke` with `tool: "llm-task"`. No new HTTP seams.
- **Carry-forward for prompt 10a (pipeline runner)**:
  - `bootstrap/pack_install_order.yaml` exists with one entry. Pipeline runner does not consume this file directly — that's prompt 15/16. 10a should reference packs by absolute path resolved via `InstanceConfig.packs_dir` (per UT-9 SOFT note).
- **Carry-forward for prompt 10b (thank_you pipeline)**:
  - The pipeline calls `await run_skill("classify_thank_you_candidate", inputs, ctx)` against this pack. Inputs match `schemas/input.schema.json`; outputs decoded per `schemas/output.schema.json`. The pipeline emits `commitment.proposed` (or equivalent) when `is_candidate=true`; that emit happens in pipeline code, not in skill code (skills never emit events directly per [REFERENCE_EXAMPLES.md §3 line 1527]).
- **Carry-forward for prompt 15 (OpenClaw integration)**:
  - `bootstrap/pack_install_order.yaml` is the source-of-truth list of built-in packs to register with OpenClaw. Prompt 15's persona-compiler / pack-registration path reads this file.
- **Carry-forward for prompt 16 (bootstrap wizard)**:
  - Bootstrap §6 / §7 walks `bootstrap/pack_install_order.yaml` and calls `openclaw skill install` per entry. Phase B only.

### Prompt 10a — pipeline runner
- **Refactored**: by Partner in Claude Chat, 2026-04-26. Prompt file: prompts/10a-pipeline-runner.md (~340 lines, quality bar = 09a).
- **Session merged**: PR #33, commits edb3920 / 1fa335a / f30a6f2 / c96cf55, merged 2026-04-26.
- **Outcome**: MERGED.
- **Evidence**:
  - `adminme/pipelines/base.py` — `Pipeline` Protocol + `PipelineContext` frozen dataclass + `Triggers` TypedDict + `PipelinePackLoadError`.
  - `adminme/pipelines/pack_loader.py` — `load_pipeline_pack()` parses `pipeline.yaml` + imports `handler.py` + instantiates `runtime.class`; cache by `(pack_id, version)`.
  - `adminme/pipelines/runner.py` — `PipelineRunner` with `register()` / `discover(builtin_root, installed_root)` / `start()` / `stop()` / `status()`. Discovery walks two roots: in-tree `adminme/pipelines/` and `instance_config.packs_dir / "pipelines"`. Per UT-9, callers pass absolute paths.
  - `tests/fixtures/pipelines/echo_logger/` — trivial fixture pack (`pipeline.yaml` + `handler.py` with `EchoLoggerPipeline`); used by runner tests. NOT a production pipeline.
  - `tests/fixtures/pipelines/echo_emitter/` — sibling fixture pack used by the integration causation test; emits `messaging.sent` with `causation_id=ctx.triggering_event_id` (no new schema registration, reuses `messaging.sent` v1).
  - 8 unit tests (`tests/unit/test_pipeline_pack_loader.py`) + 5 unit tests (`tests/unit/test_pipeline_runner.py`) + 4 integration tests (`tests/integration/test_pipeline_runner_integration.py`) — total 17 new tests (vs. floor 14).
  - **QC overshoot (Partner, 2026-04-26 post-merge):** prompt floor 14 tests; shipped **17** (overshoot from 3 extra `pack_loader` cases — wrong-kind manifest, missing-entrypoint-file, class-without-handle protocol-check). Logged as quality signal, not drift. Full repo: 429 passed / 1 skipped per Claude Code's transcript.
  - **QC silent-architecture decision (Partner, 2026-04-26 post-merge):** the prompt's Commit 1 plan named test #6 as "runtime.class not present in `handler.py`" combined with "instance does not implement handle"; Claude Code factored these into TWO separate tests (`test_missing_runtime_class_raises` and `test_class_missing_handle_raises`), which is why the unit-test count overshot 5→8. Cleaner separation. Accept and note.
  - `[§7.3]` (no projection direct writes): pipeline → projection canary in `verify_invariants.sh` is armed and clean.
  - `[§7.4]`/`[§8]`/`[D6]`: zero new SDK imports; `verify_invariants.sh` clean.
  - `[§7.7]` (one failure does not halt the bus): bus checkpoint advancement on success, non-advancement on raise — both asserted in tests.
  - `[ADR-0002]`: `PipelineContext.run_skill_fn` is the one seam pipelines use to call skills; no direct OpenClaw HTTP calls from pipeline code.
- **Carry-forward for prompt 10b** (reactive pipelines):
  - `PipelineRunner.discover()` will pick up packs added under `adminme/pipelines/<namespace>/<n>/`. 10b's four pipelines (`identity_resolution`, `noise_filtering`, `commitment_extraction`, `thank_you_detection`) each get their own subdirectory and `pipeline.yaml` + `handler.py`.
  - Each 10b pipeline's `handle()` receives a `PipelineContext` with pre-built Session + run_skill_fn + outbound_fn + guarded_write. They do NOT construct these themselves.
  - Causation wiring: pipelines emitting derived events set `causation_id=ctx.triggering_event_id` on the emitted envelope. The integration test for `echo_emitter` is the canary.
- **Carry-forward for prompt 10c** (proactive pipelines):
  - 10c registers proactive pipelines with OpenClaw via workspace-prose AGENTS.md + `openclaw cron add` per cheatsheet Q3. The runner's `discover()` reads `triggers.schedule` / `triggers.proactive` but does NOT subscribe them; 10c's bootstrap step picks up the registered packs and writes the AGENTS.md sections.
- **Carry-forward for prompt 16** (bootstrap wizard):
  - `pipeline_supervisor.py` remains a docstring-only stub. Bootstrap §7 wires `PipelineRunner` lifecycle (start-on-boot via the supervisor, stop-on-shutdown).
  - Bootstrap §6 calls `set_default_event_log(...)` (already from 09a). Pipeline runner uses the same shared event log.
- **Carry-forward for prompt 19** (Phase B smoke test):
  - Confirm at least one reactive pipeline fires end-to-end against a live bus + live skill-runner against live OpenClaw.

---

## Sidecar PRs (out-of-band, no four-commit discipline)

Sidecar PRs are single-purpose fixes against already-merged code, run
outside the build-prompt sequence. They have no carry-forwards and do
not appear in the `## Build prompts` ledger above. This section is a
chronological trace; the per-PR detail lives in the PR description on
GitHub. Tracked here so future Partner sessions reading the build_log
see the full PR landscape.

### sidecar-raw-data-is-manual-derived (PR #35, merged 2026-04-26)
- **Surfaced by**: 07.5 audit finding C-1 (`docs/checkpoints/07.5-projection-consistency.md`).
- **Diff scope**: 2 files. `adminme/projections/xlsx_workbooks/sheets/raw_data.py` (1-line set-literal expansion + 4-line docstring comment); `tests/unit/test_xlsx_finance_workbook.py` (3 imports + 1 sync test).
- **Why**: Raw Data builder's `ALWAYS_DERIVED` was missing `is_manual` while the bidirectional descriptor's `always_derived` included it. Cosmetic protection drift, not a correctness bug — `is_manual` is set by the projection from event type and has no live workbook-side edit path, so the missing cell-level lock had nothing to gate. But the two declarations are meant to be equivalent in each direction (builder owns sheet-side cell protection; descriptor owns reverse-diff drop behavior); re-drift in either direction would silently change round-trip semantics.
- **Guard**: `test_raw_data_always_derived_matches_descriptor` asserts `ALWAYS_DERIVED == set(descriptor_for(FINANCE_WORKBOOK_NAME, "Raw Data").always_derived)`. Both-direction drift fails CI on next push.
- **Verification (per Claude Code transcript)**: ruff clean, mypy clean on `raw_data.py`, full unit suite 412 passed / 1 skipped (was 411/1; +1 from the new sync test), `scripts/verify_invariants.sh` exits 0 (no `ALLOWED_EMITS` change).
- **Closes**: 07.5 finding C-1. UT-1 was already CLOSED 2026-04-25 when the audit landed; this PR is the formal closure of the audit's queued sidecar.
- **Outcome**: MERGED.
