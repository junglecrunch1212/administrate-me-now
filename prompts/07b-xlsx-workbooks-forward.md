# Prompt 07b: xlsx_workbooks forward daemon (events → xlsx)

**Phase:** BUILD.md PHASE 2 — xlsx forward projection. First of two xlsx prompts; 07c is the reverse daemon.
**Depends on:** Prompts 01–07a merged. 10 projections live, 40 event types registered at v1.
**Estimated duration:** 4–6 hours across four batch commits.
**Stop condition:** `xlsx_workbooks` projection exists as a forward-only daemon. Both workbooks (`adminme-ops.xlsx`, `adminme-finance.xlsx`) are generated from live projection state on every trigger event with 5s debounce. File locking, atomic writes, derived-cell protection, and sheet-level conditional formatting all work. Golden-fixture smoke test passes. **Reverse daemon is NOT built in this prompt — it's prompt 07c.**

---

## Phase + repository + documentation + sandbox discipline

You are in **Phase A** — generating code in Anthropic's sandbox against https://github.com/junglecrunch1212/administrate-me-now. No live OpenClaw, BlueBubbles, Plaid, or other external services.

Sandbox egress is allowlisted. github.com and raw.githubusercontent.com work. Most other hosts return HTTP 403 host_not_allowed — expected, move on.

**Session start:**

```bash
git checkout main
git pull origin main
git checkout -b phase-07b-xlsx-workbooks-forward
# Harness may auto-reassign you to claude/<random> — work on whatever
# branch you actually get. Do NOT fight it. (CF-2.)
```

**Verify prerequisites on main:**

```bash
ls -la docs/SYSTEM_INVARIANTS.md docs/DECISIONS.md docs/architecture-summary.md

# 10 projections live (from 07a)
ls -d adminme/projections/parties adminme/projections/interactions \
      adminme/projections/artifacts adminme/projections/commitments \
      adminme/projections/tasks adminme/projections/recurrences \
      adminme/projections/calendars adminme/projections/places_assets_accounts \
      adminme/projections/money adminme/projections/vector_search

# Event-type count
poetry run python -c "from adminme.events.registry import registry, ensure_autoloaded; \
  ensure_autoloaded(); \
  types = sorted(registry.known_types()); \
  print(f'total: {len(types)}')"
# Expected: 40

# Full suite green on main
poetry run pytest -q 2>&1 | tail -3
# Expected: ~180 passed, 1 skipped
```

If event-type count != 40 or any projection directory is missing: **STOP** — a prior phase regressed.

**Env-requirement reality check:** If pytest fails with `ModuleNotFoundError: No module named 'sqlcipher3'`, run `poetry install 2>&1 | tail -5` and retry. Sandbox quirk; do not fix in code.

---

## Read first (required, in this order)

**Context budget is load-bearing. Targeted reads only.**

1. **docs/DECISIONS.md** — full re-read. **D4** (CRM/ops are shared L3 concerns — forward xlsx reads across projections), **D7** (new event types at v1), **D14** (async API, sync driver via asyncio.to_thread — openpyxl is sync; wrap it), **D15** (instance-path discipline — `InstanceConfig.xlsx_workbooks_dir` already exists).

2. **docs/SYSTEM_INVARIANTS.md** — targeted:
   - `sed -n '20,34p'` — **§1** (event log sacred).
   - `sed -n '35,46p'` — **§2**. **READ §2.2 TWICE.** Projections consume, never emit. The xlsx forward daemon emits one event type: `xlsx.regenerated`. This is resolved by categorizing `xlsx.regenerated` as a **system event** (observability), not a domain event. See "System event classification" below.
   - `sed -n '82,100p'` — **§6** (sensitivity; privileged content must not land in cross-party sheets).
   - `sed -n '192,206p'` — **§13** — the explicit non-connections list.
   - `sed -n '216,225p'` — **§15** — instance-path discipline.

3. **ADMINISTRATEME_BUILD.md** — four targeted ranges only:
   - `sed -n '811,869p' ADMINISTRATEME_BUILD.md` — **§3.11 opening + adminme-ops.xlsx sheet specs (Tasks, Recurrences, Commitments, People, Lists)**.
   - `sed -n '870,960p' ADMINISTRATEME_BUILD.md` — **Members, Metadata, adminme-finance.xlsx sheet specs (Raw Data, Accounts, Assumptions, Dashboard, Balance Sheet, Pro Forma, Budget vs Actual)**.
   - `sed -n '961,997p' ADMINISTRATEME_BUILD.md` — **Forward projection algorithm**. This is your spec.
   - `sed -n '1040,1055p' ADMINISTRATEME_BUILD.md` — "Why computed values, not Excel formulas" — determines how derived sheets are built.

4. **adminme/projections/runner.py** — full read. Understand how a projection is subscribed, how callbacks work, how `apply()` is invoked per event. 07b's forward daemon uses the same subscription model but the "apply" isn't writing to SQLite — it's marking the workbook dirty and scheduling a debounced regeneration.

5. **adminme/projections/base.py** — full read (small). You're adding xlsx_workbooks as another `Projection` subclass, same contract.

6. **adminme/lib/instance_config.py** — full read. `xlsx_workbooks_dir` exists per prompt 05's scaffold; you'll use it.

7. **adminme/projections/places_assets_accounts/queries.py**, **adminme/projections/money/queries.py**, **adminme/projections/tasks/queries.py**, **adminme/projections/commitments/queries.py**, **adminme/projections/recurrences/queries.py**, **adminme/projections/parties/queries.py** — full reads. These are what the forward daemon reads from to build sheets.

8. **pyproject.toml** — `grep -E "openpyxl|anyio|watchdog" pyproject.toml`. `openpyxl = "*"` is already declared. `watchdog` lands in 07c, not here.

**Do NOT read** during this session:
- BUILD.md §3.11 "Reverse projection" and onward — **prompt 07c**.
- BUILD.md §L4 onward.
- Any console or API prompts.

---

## Operating context

You are building a **forward-only** xlsx daemon that watches the event bus, debounces bursts of trigger events, then regenerates two xlsx workbooks from live projection state. Structurally a projection per [§2.2] — consumes events, does not emit domain events. Emits one **system event** (`xlsx.regenerated`) that's purely observability.

**The daemon is not running in this phase.** Phase A is code + tests only. The production daemon starts under bootstrap in prompt 16. For this prompt you build the `XlsxWorkbooksProjection` class, register it with `ProjectionRunner`, and write tests that drive regeneration synchronously.

### Scope for adminme-ops.xlsx

Sheets you BUILD in this prompt:
- **Tasks** [bidirectional-shape, forward-only in this prompt] — from `tasks` projection.
- **Recurrences** [bidirectional-shape, forward-only] — from `recurrences` projection.
- **Commitments** [bidirectional-shape, forward-only] — from `commitments` projection.
- **People** [read-only] — from `parties` projection (persons + orgs, not households, not memberships-only).
- **Metadata** [read-only] — provenance sheet.

Sheets you SKIP in this prompt (requires unregistered event types; add TODO markers):
- Lists sheet — needs `list_item.*` event types, registered in a future prompt.
- Members sheet — needs `member.created/profile_changed/role_changed` event types, registered in a future prompt (bootstrap/principal setup).

### Scope for adminme-finance.xlsx

Sheets you BUILD:
- **Raw Data** [bidirectional-shape, forward-only] — from `money` projection (joins `accounts` for `account_last4`).
- **Accounts** [read-only] — from `places_assets_accounts.accounts`.
- **Metadata** [read-only].

Sheets you SKIP (need unregistered events or derived math not built yet):
- Assumptions — needs `assumption.*` event types.
- Dashboard, Balance Sheet, 5-Year Pro Forma, Budget vs Actual — needs derived-math pipelines (prompt 10c+). Add TODO.

### Trigger event subscription

Only subscribe to event types **currently registered** (40 total as of 07a merge). Pruned list:

**adminme-ops.xlsx triggers:**
```
task.created, task.updated, task.completed, task.deleted,
commitment.proposed, commitment.confirmed, commitment.completed,
commitment.dismissed, commitment.edited, commitment.snoozed,
commitment.cancelled, commitment.delegated, commitment.expired,
recurrence.added, recurrence.updated, recurrence.completed,
party.created, party.merged, identifier.added,
membership.added, relationship.added,
calendar.event_added, calendar.event_updated, calendar.event_deleted
```

**adminme-finance.xlsx triggers:**
```
money_flow.recorded, money_flow.manually_added, money_flow.manually_deleted,
account.added, account.updated, place.added, place.updated,
asset.added, asset.updated
```

Do NOT subscribe to `list_item.*`, `member.*`, `party.tag.*`, `party.tier.computed`, `assumption.*`, `plaid.*`, `institution.*` — they don't exist yet. Add a module-level comment listing these as "TODO(future-prompt): extend subscription when these event types register."

### System event classification

`xlsx.regenerated` is a new event type but it's NOT a domain event. It's observability — telling downstream observers (reverse daemon; console status bar) that a regeneration cycle completed. Register it in a new file `adminme/events/schemas/system.py` that carries a module-level comment explaining the system-event category. This keeps `ops.py`, `domain.py`, `ingest.py`, `crm.py`, `governance.py` exclusively for domain events.

Per [§2.2]: the invariant "projections never emit" is about **domain events**. The xlsx forward daemon emitting a system event is explicitly allowed by this categorization. Document this reasoning in a comment at the top of `xlsx_workbooks/__init__.py`:

```python
# xlsx_workbooks is structurally a projection per [§2.2]: it consumes
# events and regenerates derived state. It emits exactly one event,
# `xlsx.regenerated`, which is categorized as a SYSTEM event (not a
# domain event). System events are observability-only and do not
# represent facts about the world. This resolves the apparent tension
# between BUILD.md §3.11 step 9 ("emit xlsx.regenerated") and [§2.2]
# ("projections never emit"). Prompt 07.5's audit relies on this
# classification.
```

### Lock + atomic write

- `.lock` sidecar per workbook: `<xlsx_workbooks_dir>/adminme-ops.xlsx.lock` (and similarly for finance).
- Acquire with `fcntl.flock(fd, fcntl.LOCK_EX)` under a timeout (10s).
- Write through temp file: `<target>.tmp.<pid>`, then `os.replace(tmp, target)` for atomicity.
- Release the lock after the replace.

### Debounce

- 5-second window: an asyncio.Task per workbook that sleeps 5s and then runs the regenerate.
- When a new trigger event arrives and a task is already pending, cancel + reschedule.
- Tests bypass the debounce by calling `await projection.regenerate_now(workbook="adminme-ops")` directly — do NOT sleep 5s in tests.

### Derived-cell protection

For each bidirectional sheet, mark columns that the reverse daemon must NOT interpret as edits:
- Tasks: `task_id`, `created_at`, `completed_at` — derived/assigned by backend.
- Recurrences: `next_due`, `last_completed_at` — derived.
- Commitments: `confidence`, `strength`, `source_summary` — from extraction pipeline.
- Raw Data: `txn_id`, `plaid_category`, and `date/account_last4/merchant_name/amount` on rows where `is_manual=FALSE`.

Implementation: set `cell.protection = Protection(locked=True)` on all derived cells. At sheet level, apply `ws.protection.sheet = True` with a known password (configurable via instance secrets — use a placeholder `"adminme-placeholder"` in this prompt; real secret flows in prompt 16).

### Sheet protection scope

Sheets tagged `[read-only]` (People, Metadata, Accounts): every cell gets `cell.protection = Protection(locked=True)`, sheet protection enabled.

Sheets tagged `[bidirectional-shape]`: only `[derived]` columns get `Protection(locked=True)`; the rest are `Protection(locked=False)` so Excel permits edits.

### Test-time workbook comparison

**Do NOT assert byte-equivalence of whole xlsx files.** openpyxl doesn't guarantee stable byte output across runs. Instead, the regenerate-twice idempotence test asserts:
- Both produced files exist and have non-zero size.
- Loading both via `openpyxl.load_workbook` and comparing via a helper `_sheet_rows_equal(ws1, ws2)` — cell-by-cell on visible data for each sheet.
- The `generated_at` field on the Metadata sheet is allowed to differ between the two (spec says human-useful timestamp wins).

---

## Out of scope

- Do NOT build xlsx_workbooks **reverse** daemon — **prompt 07c**.
- Do NOT register `list_item.*`, `member.*`, `assumption.*`, `plaid.*`, or any other currently-unregistered event type — future prompts.
- Do NOT build the Dashboard / Balance Sheet / 5-Year Pro Forma / Budget vs Actual derived sheets — prompt 10c+ ships the math pipelines.
- Do NOT implement Apple Reminders sync — prompt 11.
- Do NOT implement Session / scope enforcement — prompt 08.
- Do NOT import any LLM/embedding SDK into `adminme/` — [§8], [D6].
- Do NOT reference tenant identity in `adminme/` — no "James", "Laura", "Charlie", "Stice", "Morningside" ([§12.4]). Tests may use illustrative names.
- Do NOT launch the daemon for real — Phase A is code + tests only.

---

## Carry-forwards folded in

**CF-1 — gh CLI fallback:** Use `gh pr create` first; on failure, call `mcp__github__create_pull_request`.

**CF-2 — harness branch override:** Work on whatever branch the harness assigns.

**CF-3 — stop means stop:** Post-PR, one round of status check. Then STOP.

**CF-4 — mypy preflight:** `openpyxl` is already in pyproject.toml. If mypy complains about missing stubs, add to `[[tool.mypy.overrides]]` with `ignore_missing_imports = true`. Preflight before Commit 1.

**CF-5 — async-subscriber test discipline:** Forward daemon has a debounce — tests must call `await projection.regenerate_now(...)` synchronously, never wait for the debounce timer. When testing "event ignored because unregistered type" (can't happen in this prompt; type registration is the gate), use CF-5's follow-up-event pattern.

**CF-6 — CHECK-constraint style:** No CHECK constraints added here — there's no SQLite schema for this projection.

**BUILD_LOG append as a commit:** Commit 4 includes the BUILD_LOG append; inline template at the bottom of this prompt.

**NEW CF-7 — direct-handler pattern for failure tests:** From 07a, the `test_account_added_with_raw_credential_fails_check` test had to bypass the bus to test an IntegrityError without putting the subscriber in degraded state. For 07b, any test that wants to assert "a malformed row doesn't land" should call the sheet-builder function directly, not route through the bus + debounce machinery.

---

## Incremental commit discipline — MANDATORY

Four batch commits. If a turn times out mid-section: STOP. The operator re-launches.

### Commit 1 — `xlsx.regenerated` system event + schemas layout

Create `adminme/events/schemas/system.py`:

```python
"""
System-event schemas.

System events are observability-only. They do not represent facts about the
world and do not participate in domain reasoning. They exist so downstream
observers (reverse daemons, status surfaces, diagnostics) can react to
platform-internal state transitions.

Per SYSTEM_INVARIANTS.md §2.2: projections never emit DOMAIN events. System
events are explicitly outside that scope — they emit a signal that a
regeneration cycle completed, nothing more.

Currently contains:
- xlsx.regenerated — emitted after xlsx_workbooks forward daemon writes.
"""

class XlsxRegeneratedV1(BaseModel):
    model_config = {"extra": "forbid"}
    workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]
    generated_at: str
    last_event_id_consumed: str
    sheets_regenerated: list[str]
    duration_ms: int = Field(ge=0)

registry.register("xlsx.regenerated", 1, XlsxRegeneratedV1)
```

**Verify commit 1:**

```bash
poetry run pytest tests/unit/test_schema_registry.py -v 2>&1 | tail -5

poetry run python -c "
from adminme.events.registry import registry, ensure_autoloaded
ensure_autoloaded()
assert 'xlsx.regenerated' in registry.known_types()
print(f'total types: {len(registry.known_types())}')
"
# Expected: 41

poetry run ruff check adminme/events/schemas/system.py 2>&1 | tail -3
poetry run mypy adminme/events/schemas/system.py 2>&1 | tail -3

git add adminme/events/schemas/system.py
git commit -m "phase 07b-1: register xlsx.regenerated system event"
```

### Commit 2 — XlsxWorkbooksProjection core + sheet builders (ops workbook)

#### `adminme/projections/xlsx_workbooks/__init__.py`

Exports `XlsxWorkbooksProjection(Projection)`:
- `name = "xlsx_workbooks"`
- `version = 1`
- `subscribes_to = [...]` (the merged trigger list from both workbooks).
- Override `apply(envelope, conn)` to:
  - Ignore `conn` — no SQLite backing store for this projection.
  - Determine which workbook this event affects (ops vs finance — via a type-to-workbook map).
  - Schedule a debounced regeneration via `_schedule_regeneration(workbook)`.
- Expose `regenerate_now(workbook: str) -> None` for test-time synchronous regeneration.
- Expose `_schedule_regeneration(workbook: str) -> None` (internal; cancels + reschedules the asyncio.Task).

The projection holds references to an `InstanceConfig` so it can resolve `xlsx_workbooks_dir`, and to a set of `QueryContext` (a simple dataclass) that exposes handles to the other projections' DB connections. It gets these via a small accessor protocol — see "Query context plumbing" below.

Header comment explains the §2.2 resolution (as in Operating context).

#### Query context plumbing

xlsx forward needs to read 7 other projections. Two patterns acceptable:

1. **Runner-passed**: `ProjectionRunner.start()` already holds connections for every registered projection. Add a method `ProjectionRunner.connection(name)` that returns the sqlcipher3 connection. (This already exists — confirmed in 07a's read of runner.py.)

2. **Projection-passed**: `XlsxWorkbooksProjection` receives a `QueryContext` at construction time that holds these references.

Use pattern (2). Reason: it keeps `Projection.apply()` stateless and keeps the xlsx projection's dependencies explicit at construction. Define:

```python
# adminme/projections/xlsx_workbooks/query_context.py
@dataclass
class XlsxQueryContext:
    parties_conn: sqlcipher3.Connection
    tasks_conn: sqlcipher3.Connection
    commitments_conn: sqlcipher3.Connection
    recurrences_conn: sqlcipher3.Connection
    calendars_conn: sqlcipher3.Connection
    places_assets_accounts_conn: sqlcipher3.Connection
    money_conn: sqlcipher3.Connection
    # NOTE: vector_search and artifacts not needed by any current sheet.
    # Add when sheets that reference them are built.
```

`XlsxWorkbooksProjection.__init__(config, query_context)` takes both. The runner's `start()` doesn't know how to construct this — a test fixture or bootstrap sets it up. Runner's existing registration API doesn't need modification; just construct the projection with `(config, context)` before calling `runner.register(xlsx_projection)`.

Add a documentation comment in the runner (not a code change) noting that `xlsx_workbooks` is constructed differently from other projections because it reads cross-projection.

#### Sheet builders

One file per sheet: `adminme/projections/xlsx_workbooks/sheets/tasks.py`, `recurrences.py`, `commitments.py`, `people.py`, `metadata.py`, `raw_data.py`, `accounts.py`, `metadata_finance.py`.

Each exposes `def build(ws: openpyxl.Worksheet, ctx: XlsxQueryContext, *, tenant_id: str) -> None` that writes rows and applies protection.

Sheet builder conventions (enforced across all builders):
1. Write header row from a module-level `HEADERS: list[str]` constant.
2. Freeze header row: `ws.freeze_panes = "A2"`.
3. Style header row bold.
4. For each data row: iterate the query result, write one cell per column.
5. Apply derived-cell protection per-column if the column is in the module-level `DERIVED_COLUMNS: set[str]` constant.
6. At end: call `_apply_sheet_protection(ws, readonly=<bool>)` helper.

This commit ships the five adminme-ops.xlsx sheet builders (Tasks, Recurrences, Commitments, People, Metadata) and the workbook-assembly code that calls them. Finance workbook sheets land in Commit 3.

#### Workbook assembly

`adminme/projections/xlsx_workbooks/builders.py`:

```python
def build_ops_workbook(path: Path, ctx: XlsxQueryContext, *, tenant_id: str,
                       last_event_id: str) -> None:
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)
    for sheet_name, builder in [
        ("Tasks", tasks.build),
        ("Recurrences", recurrences.build),
        ("Commitments", commitments.build),
        ("People", people.build),
        ("Metadata", metadata_ops.build),
    ]:
        ws = wb.create_sheet(sheet_name)
        if sheet_name == "Metadata":
            # Metadata needs last_event_id in addition to ctx
            metadata_ops.build(ws, ctx, tenant_id=tenant_id,
                               last_event_id=last_event_id)
        else:
            builder(ws, ctx, tenant_id=tenant_id)
    # Atomic write
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    wb.save(str(tmp))
    os.replace(tmp, path)
```

Lock acquisition wraps this. `build_finance_workbook` has the same shape; lands in Commit 3.

#### Lock helper

`adminme/projections/xlsx_workbooks/lockfile.py`:

```python
@contextmanager
def acquire_workbook_lock(lock_path: Path, *, timeout_s: float = 10.0):
    """Non-blocking flock with timeout. Creates the lock file if missing.
    Raises TimeoutError if the lock can't be acquired."""
    # Implementation with fcntl.flock + retry loop sleeping 0.1s.
```

#### Tests — `tests/unit/test_xlsx_ops_workbook.py` (≥10 tests)

Fixtures: a helper `_build_populated_instance(tmp_path)` that sets up the 7 projections, inserts a deterministic fixture set (5 parties, 3 tasks, 2 commitments, 2 recurrences, 0 calendars), and returns the `XlsxQueryContext` + `InstanceConfig`.

Tests:
- `test_workbook_creates_file` — call `build_ops_workbook`; file exists, openable.
- `test_tasks_sheet_rows` — 3 tasks in projection → 3 data rows in Tasks sheet with correct task_id/title/status.
- `test_tasks_sheet_headers_frozen` — `ws.freeze_panes == "A2"`.
- `test_tasks_derived_cells_locked` — `task_id` column cells have `protection.locked == True`.
- `test_recurrences_sheet_rows` — 2 recurrences present, correct cadence/next_due.
- `test_commitments_sheet_status_filter` — only active commitments shown (or full set, depending on spec — read §3.4 again; default full set).
- `test_people_sheet_readonly` — every cell locked, sheet.protection.sheet=True.
- `test_metadata_sheet_populated` — `generated_at`, `tenant_id`, `last_event_id_consumed` cells present and match inputs.
- `test_regenerate_idempotent_semantically` — call `build_ops_workbook` twice, compare cell-by-cell (excluding Metadata.generated_at).
- `test_atomic_write_tmp_file_gone_on_success` — after build, no `.tmp.*` file remains.
- `test_lock_held_by_another_process_times_out` — acquire lock in a thread, call build in main thread with `timeout_s=0.5`, expect `TimeoutError`.
- Multi-tenant isolation: two tenants with different task sets → separate workbook files with separate content (if path is tenant-scoped; if not, the sheet's tenant filter ensures content separation and the test asserts that filter).

**Verify commit 2:**

```bash
poetry run pytest tests/unit/test_xlsx_ops_workbook.py -v 2>&1 | tail -5
# Expected: ≥10 tests passing.

poetry run pytest tests/unit/test_projection_*.py -q 2>&1 | tail -3
# Regression: all prior projection tests still pass.

poetry run ruff check adminme/ tests/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

git add adminme/projections/xlsx_workbooks/ tests/unit/test_xlsx_ops_workbook.py
git commit -m "phase 07b-2: xlsx_workbooks forward — ops workbook builders + locking"
```

### Commit 3 — Finance workbook + debounce machinery + xlsx.regenerated emit

Add:
- `adminme/projections/xlsx_workbooks/sheets/raw_data.py`, `accounts.py`, `metadata_finance.py`.
- `build_finance_workbook()` in builders.py.
- Debounce logic in `XlsxWorkbooksProjection`:
  - `_pending_tasks: dict[str, asyncio.Task]` keyed by workbook name.
  - `_schedule_regeneration(workbook)` cancels existing pending task and schedules a new one with 5s sleep.
  - `regenerate_now(workbook)` bypasses the debounce for tests.
- After a successful regeneration, emit `xlsx.regenerated` through the event log. **This is the ONLY emit in this projection.** Use `EventLog.append()` directly — the projection was constructed with a reference to the log for this purpose. Document the §2.2 resolution on the emit call site.

#### xlsx.regenerated emit site

```python
async def _regenerate(self, workbook: str) -> None:
    start_ms = int(time.time() * 1000)
    path = self._config.xlsx_workbooks_dir / workbook
    lock_path = path.with_suffix(path.suffix + ".lock")
    with acquire_workbook_lock(lock_path, timeout_s=10.0):
        last_event_id = await self._log.latest_event_id() or ""
        if workbook == "adminme-ops.xlsx":
            sheets = ["Tasks", "Recurrences", "Commitments", "People", "Metadata"]
            await asyncio.to_thread(
                build_ops_workbook, path, self._ctx,
                tenant_id=self._config.tenant_id,
                last_event_id=last_event_id,
            )
        else:
            sheets = ["Raw Data", "Accounts", "Metadata"]
            await asyncio.to_thread(
                build_finance_workbook, path, self._ctx,
                tenant_id=self._config.tenant_id,
                last_event_id=last_event_id,
            )
    duration_ms = int(time.time() * 1000) - start_ms
    # Emit xlsx.regenerated as a SYSTEM event per [§2.2] resolution.
    await self._log.append(EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=self._config.tenant_id,
        type="xlsx.regenerated",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="xlsx_workbooks",
        source_account_id="projection",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload={
            "workbook_name": workbook,
            "generated_at": EventEnvelope.now_utc_iso(),
            "last_event_id_consumed": last_event_id,
            "sheets_regenerated": sheets,
            "duration_ms": duration_ms,
        },
    ))
```

#### Tests — `tests/unit/test_xlsx_finance_workbook.py` (≥8 tests)

- `test_raw_data_sheet_shows_plaid_and_manual` — 2 recorded + 1 manually_added → 3 rows, is_manual flags correct.
- `test_raw_data_excludes_soft_deleted` — manually_added + manually_deleted → row present but not rendered (deleted_at flag excludes from sheet).
- `test_raw_data_plaid_cells_locked` — on `is_manual=FALSE` rows, `date/account_last4/merchant_name/amount` cells are locked.
- `test_raw_data_manual_cells_unlocked` — on `is_manual=TRUE` rows, all cells unlocked (except `txn_id` which is derived everywhere).
- `test_accounts_sheet_readonly` — all cells locked.
- `test_accounts_sheet_rows` — 3 accounts in projection → 3 rows, correct institution/last4/status.
- `test_finance_metadata_populated` — same as ops metadata test.
- `test_finance_idempotent_semantically` — build twice, semantic equal.

#### Tests — `tests/unit/test_xlsx_regenerated_emit.py` (≥4 tests)

- `test_regenerate_emits_xlsx_regenerated` — populate fixture, call `regenerate_now("adminme-ops.xlsx")`, assert `xlsx.regenerated` appears in event log with correct `workbook_name` and `sheets_regenerated`.
- `test_regenerate_finance_emits_finance_name` — same but for finance workbook.
- `test_regenerate_duration_ms_sensible` — duration_ms ≥ 0 and < 60000 (sanity).
- `test_regenerate_last_event_id_matches_latest` — emitted `last_event_id_consumed` equals log's latest at call time.

#### Tests — `tests/unit/test_xlsx_debounce.py` (≥3 tests)

- `test_multiple_events_coalesce` — bus delivers 10 events, only one regenerate runs (patch `_regenerate` to count calls; drive via `_schedule_regeneration` directly, use a short fake debounce duration for the test via a constructor parameter).
- `test_debounce_cancels_on_new_event` — schedule at t=0, new event at t=1s resets timer.
- `test_regenerate_now_bypasses_debounce` — even with a pending task, `regenerate_now` runs immediately.

**Verify commit 3:**

```bash
poetry run pytest tests/unit/test_xlsx_finance_workbook.py \
                 tests/unit/test_xlsx_regenerated_emit.py \
                 tests/unit/test_xlsx_debounce.py -v 2>&1 | tail -5
# Expected: ≥15 tests passing.

poetry run pytest tests/unit/test_projection_*.py tests/unit/test_xlsx_*.py -q 2>&1 | tail -3

poetry run ruff check adminme/ tests/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

git add adminme/projections/xlsx_workbooks/ \
        tests/unit/test_xlsx_finance_workbook.py \
        tests/unit/test_xlsx_regenerated_emit.py \
        tests/unit/test_xlsx_debounce.py
git commit -m "phase 07b-3: finance workbook + debounce + xlsx.regenerated emit"
```

### Commit 4 — Integration + smoke + verification + BUILD_LOG + push

#### Integration test — `tests/integration/test_xlsx_forward_end_to_end.py`

One test: full scenario.

1. Spin up 10 projections + XlsxWorkbooksProjection via `ProjectionRunner`.
2. Append ~50 events across trigger types (tasks, commitments, money flows, accounts, parties, places).
3. Wait for all subscribers to catch up (including xlsx).
4. Call `regenerate_now("adminme-ops.xlsx")` and `regenerate_now("adminme-finance.xlsx")` (bypass debounce).
5. Verify both files exist, openable, and contain expected rows.
6. Verify two `xlsx.regenerated` events in the log.
7. Call `regenerate_now` again with no new events — semantically-equal output (verify via cell-by-cell compare).
8. Verify NO events other than `xlsx.regenerated` were emitted by the xlsx projection — scan the log since the first regenerate for events sourced from `source_adapter="xlsx_workbooks"`; only `xlsx.regenerated` types.

This last check enforces the §2.2 resolution: even the xlsx projection only emits the one categorized system event.

#### Smoke script — `scripts/demo_xlsx_forward.py`

Small script: builds the same fixture as the integration test, regenerates both workbooks, prints file paths and row counts per sheet. Exits 0 in < 30 seconds. Does NOT start a live daemon — everything is synchronous.

#### Full verification block

```bash
poetry run ruff check adminme/ tests/ scripts/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

# Regression
poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py \
                 tests/unit/test_schema_registry.py tests/unit/test_event_validation.py \
                 tests/unit/test_projection_*.py -q 2>&1 | tail -3

# 07b tests
poetry run pytest tests/unit/test_xlsx_*.py -q 2>&1 | tail -3

# Integration
poetry run pytest tests/integration/ -v 2>&1 | tail -10

# Canaries
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v 2>&1 | tail -3
poetry run pytest tests/unit/test_no_hardcoded_identity.py -v 2>&1 | tail -3

# Full suite
poetry run pytest -q 2>&1 | tail -3

# Inviolable-invariant greps
grep -iE "^anthropic|^openai|^sentence_transformers|anthropic =|openai =|sentence-transformers =" pyproject.toml \
    && echo "VIOLATION of [§8]" || echo "OK"

# Strict import grep (not docstrings)
grep -rnE "^\s*import (anthropic|openai|sentence_transformers)|^\s*from (anthropic|openai|sentence_transformers)" adminme/ \
    | grep -v "#\|\"\"\"" && echo "VIOLATION of [§8]" || echo "OK: no LLM SDK imports"

grep -rn "~/.adminme\|'/.adminme\|\"/.adminme" adminme/ bootstrap/ packs/ --include='*.py' --include='*.sh' 2>/dev/null \
    | grep -v "^docs/" || echo "OK: no hardcoded instance paths"

grep -rn "INSERT INTO.*projection\|projection_db.*write\|from adminme.projections.*import.*handlers" adminme/pipelines/ 2>/dev/null \
    || echo "OK: no pipeline→projection writes"

grep -rniE "james|laura|charlie|stice|morningside" adminme/ --include='*.py' \
    | grep -v "tests/\|# example\|# illustration" || echo "OK: no tenant identity in platform code"

# §2.2 audit — xlsx_workbooks must only emit 'xlsx.regenerated', not any
# domain event type. This grep inspects the xlsx projection module for
# event-log append calls and verifies the type argument.
grep -rn "log.append\|append(.*EventEnvelope" adminme/projections/xlsx_workbooks/ | \
  grep -v 'type="xlsx.regenerated"' || echo "OK: xlsx projection only emits xlsx.regenerated"
# (If this grep finds any log.append without type=xlsx.regenerated, that's a §2.2 violation.)

# Smoke
poetry run python scripts/demo_xlsx_forward.py 2>&1 | tail -20
```

Expected:
- Ruff clean, mypy clean
- Prompt 03–07a tests: ~180 passing
- 07b unit tests: ~30 passing
- Integration: new test + 07a/06 rebuild tests all passing
- Canaries: PASSING + SKIPPED
- Full suite: ~210+ passed, 1 skipped
- All greps: OK
- Smoke: clean, row counts reported

#### BUILD_LOG append

```markdown
### Prompt 07b — xlsx_workbooks forward daemon
- **Refactored**: by Partner in Claude Chat, <refactor date>. Prompt file: prompts/07b-xlsx-workbooks-forward.md.
- **Session merged**: PR #<N>, commits <sha1>/<sha2>/<sha3>/<sha4>, merged <merge date>.
- **Outcome**: <MERGED or otherwise>.
- **Evidence**:
  - xlsx_workbooks projection built as forward-only daemon consuming events from 10 projections; structurally a projection per [§2.2], emits only the categorized system event `xlsx.regenerated`.
  - 1 new event type at v1: `xlsx.regenerated` — in new `schemas/system.py` module to keep domain event files clean.
  - 2 workbook builders: adminme-ops.xlsx (Tasks, Recurrences, Commitments, People, Metadata) and adminme-finance.xlsx (Raw Data, Accounts, Metadata). Sheets requiring unregistered event types (Lists, Members, Assumptions, Dashboard, Balance Sheet, Pro Forma, Budget vs Actual) skipped with TODO markers.
  - File locking via fcntl.flock with 10s timeout; atomic write via temp+rename.
  - 5s debounce with cancel-on-new-event; `regenerate_now` bypasses for tests.
  - Derived-cell protection at cell level + sheet protection on read-only sheets.
  - ~30 new unit tests + 1 integration test + demo script.
  - §2.2 grep audit added to verification block — xlsx only emits xlsx.regenerated.
- **Carry-forward for prompt 07c (xlsx reverse daemon)**:
  - Sidecar state JSON (last-known sheet state) is NOT written in this prompt. 07c writes it after each forward regeneration OR reads from the just-written workbook at adapter startup. 07c decides the implementation path.
  - `xlsx.regenerated` emit provides a signal 07c's reverse adapter can subscribe to, to avoid re-diffing a workbook that forward just wrote.
  - Sheet protection passwords hardcoded to `"adminme-placeholder"` — 07c accepts this; real secrets flow lands in prompt 16.
- **Carry-forward for prompt 08**:
  - No query functions in xlsx projection; nothing for Session to wrap. Sheet builders read other projections' queries (which are the ones Session wraps).
- **Carry-forward for prompt 10+**:
  - Dashboard/Balance Sheet/Pro Forma/Budget vs Actual sheets skipped — derived-math pipelines build them later.
- **Carry-forward for future bootstrap (prompt 16)**:
  - Real xlsx daemon lifecycle (start-on-boot, stop-on-shutdown) lands in bootstrap. Phase A is code + tests only.
```

#### Push + open PR

Same gh/MCP fallback pattern as 07a.

```bash
git push origin HEAD
gh pr create --base main --head $(git branch --show-current) \
  --title "Phase 07b: xlsx_workbooks forward daemon" \
  --body "$(cat <<'EOF'
xlsx_workbooks forward daemon — events → xlsx. Structurally a projection per [§2.2]; emits exactly one event type, `xlsx.regenerated`, categorized as a SYSTEM event (observability, not domain). Documented on the emit call site + in BUILD_LOG.

**Landed:**
- `xlsx.regenerated` event registered at v1 in new `schemas/system.py` module
- `XlsxWorkbooksProjection` subscribes to currently-registered trigger events across both workbooks
- adminme-ops.xlsx: Tasks, Recurrences, Commitments, People, Metadata sheets
- adminme-finance.xlsx: Raw Data, Accounts, Metadata sheets
- Unregistered-event sheets (Lists, Members, Assumptions, Dashboard, Balance Sheet, Pro Forma, Budget vs Actual) skipped with TODO markers
- 5s debounce with cancel-on-new-event; `regenerate_now` for tests
- fcntl.flock file locking (10s timeout), atomic write via temp+rename
- Derived-cell protection (cell.protection.locked=True) + sheet-level protection on read-only sheets
- ~30 new unit tests + 1 integration test

**§2.2 resolution:**
BUILD.md §3.11 step 9 says "emit xlsx.regenerated" while §2.2 says projections never emit. Resolved by categorizing `xlsx.regenerated` as a **system event** (observability only), distinct from domain events. This is the ONLY emit in the xlsx projection; a grep in the verification block enforces it.

**Carry-forward for 07c:** Sidecar state JSON pathway deferred to 07c — reverse daemon decides whether to read from sidecar or snapshot from just-written workbook.

Single-purpose PR per phase-07b prompt. No sidecar fixes.
EOF
)"
```

MCP fallback: `mcp__github__create_pull_request` with equivalent args.

#### Stop

Post-PR, one round of `get_status` / `get_reviews` / `get_comments`. Report. Then STOP. Do NOT poll. Do NOT respond to webhook events arriving after the stop message.

**Stop template:**

```
xlsx_workbooks forward daemon in. Forward path: 10 projections → 2 xlsx files, debounced, atomic, protected.
Branch: <harness-assigned>
PR: <URL>
Commits: phase 07b-1 through phase 07b-4 on top of main.

Verification summary:
- ruff / mypy: clean
- prompt 03-07a tests: <N> passed, 0 failed
- prompt 07b unit tests: ops workbook <N>, finance workbook <N>, emit <N>, debounce <N> (total ~30)
- integration: xlsx forward end-to-end test passing; 07a rebuild + 06 rebuild still passing
- canaries: instance-path PASSING, identity SKIPPED
- full suite: <N> passed, 1 skipped
- §2.2 grep audit: PASSING (xlsx only emits xlsx.regenerated)
- smoke: clean

1 new event type at v1: xlsx.regenerated (system event category, new schemas/system.py module).

Sheets built:
- adminme-ops.xlsx: Tasks, Recurrences, Commitments, People, Metadata
- adminme-finance.xlsx: Raw Data, Accounts, Metadata

Sheets skipped with TODO markers (await future event-type registration):
- Lists, Members, Assumptions, Dashboard, Balance Sheet, 5-Year Pro Forma, Budget vs Actual

BUILD_LOG appended in Commit 4.

Post-PR status check: <CI result>, <reviews result>, <comments result>

Ready for prompt 07c (xlsx reverse adapter) once this branch is reviewed and merged.
```

Then STOP.
