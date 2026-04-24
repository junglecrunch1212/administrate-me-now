# Prompt 07c: xlsx_workbooks reverse daemon (xlsx → events) + forward sidecar extension

**Phase:** BUILD.md PHASE 2 — xlsx reverse daemon. Closes the round-trip opened by 07b.
**Depends on:** Prompts 01–07b merged. 11 projections live (10 sqlite + xlsx_workbooks forward); 41 event types registered at v1; `scripts/verify_invariants.sh` on main with `ALLOWED_EMITS='xlsx\.regenerated'` and `ALLOWED_EMIT_FILES=("adminme/projections/xlsx_workbooks/__init__.py")`.
**Estimated duration:** 4–6 hours across four batch commits.
**Stop condition:** A new daemon at `adminme/daemons/xlsx_sync/reverse.py` watches both workbooks via `watchdog`, debounces on file modifications, acquires the same `.lock` the forward daemon uses, diffs against sidecar JSON, emits domain events for legitimate edits, drops derived/Plaid-authoritative/id-column edits silently, holds deletions in a 5s undo window, and emits `xlsx.reverse_projected` (or `xlsx.reverse_skipped_during_forward` when the forward lock is held) at cycle end. Forward daemon (07b's `XlsxWorkbooksProjection._regenerate`) extended to write sidecar JSON in the same lock as each xlsx write. Three dead stub files in `adminme/projections/xlsx_workbooks/` (`forward.py`, `reverse.py`, `schemas.py`) deleted per PM-10. `scripts/verify_invariants.sh` extended for the two new system events. ≥30 new unit tests + extended integration test pass. **Reverse daemon is an L1-adjacent adapter living under `adminme/daemons/`; it is NOT a projection.**

---

## Read first (required, in this order)

Targeted reads only. Context budget is load-bearing. Slim universal preamble (in `prompts/PROMPT_SEQUENCE.md`) covers cross-cutting discipline; do not re-derive it here.

1. **docs/DECISIONS.md** — full re-read with focus on **D5** (xlsx forward runs unconditionally; emits include `observation_mode_active` flag — note this is on the `xlsx.regenerated` payload from 07b but is not yet wired through; you do NOT add it here, leave for prompt 16's observation-mode pass), **D7** (new event types at v1), **D14** (async API, sync I/O via `asyncio.to_thread`; watchdog callbacks fire on watchdog's own thread — bridge into the asyncio loop carefully).

2. **docs/SYSTEM_INVARIANTS.md** — targeted:
   - `sed -n '20,34p'` — **§1** (event log sacred; every reverse-emitted domain event flows through `EventLog.append()`).
   - `sed -n '35,46p'` — **§2** (projections derived). **§2.2 reasoning re-confirmed:** the reverse daemon is NOT a projection — it lives in `adminme/daemons/`, emits domain events on human authority, and is architecturally an adapter. The §2.2 ALLOWED_EMIT_FILES allowlist therefore does not need a new entry; it stays scoped to `adminme/projections/`. The two new system events the daemon emits (`xlsx.reverse_projected`, `xlsx.reverse_skipped_during_forward`) are categorized as system events but emitted from outside the projections directory — verify_invariants.sh's existing logic handles this correctly (it only checks files inside `adminme/projections/`).
   - `sed -n '82,100p'` — **§6** (sensitivity; an xlsx-edited row carrying a privileged-source provenance must NOT be re-emitted with normal sensitivity — see "Sensitivity preservation" below).
   - `sed -n '141,180p'` — **§10** (xlsx is bidirectional; protection rules; lock contention behavior).
   - `sed -n '192,206p'` — **§13** (explicit non-connections — item 5: xlsx is the only projection writing disk files; this prompt's daemon emits domain events but does NOT write any projection table; soft-delete on money_flows is via the `money_flow.manually_deleted` event, NOT a direct projection update).
   - `sed -n '216,225p'` — **§15** (instance-path discipline — every path resolves through `InstanceConfig`).

3. **ADMINISTRATEME_BUILD.md** — three targeted ranges:
   - `sed -n '993,1027p' ADMINISTRATEME_BUILD.md` — **§3.11 Reverse projection algorithm**. This is your spec. READ TWICE.
   - `sed -n '1038,1054p' ADMINISTRATEME_BUILD.md` — **Conflict resolution** (lock contention skip; `xlsx.reverse_skipped_during_forward` emit) and **Rebuild from scratch** semantics.
   - `sed -n '1056,1080p' ADMINISTRATEME_BUILD.md` — **Testing** subsection — names the canonical test files for round-trip, new-task, protected-cell-ignored, lock-contention, plaid-protection, replay-equivalence.

4. **ADMINISTRATEME_DIAGRAMS.md** — full read of **§6 xlsx round-trip** (small; ~80 lines). Internalize the two-daemon, one-shared-lock, sidecar-state model.

5. **prompts/07-projections-ops.md** — full read (small, ~50 lines). The pre-split source's "xlsx specifically" subsection names the canonical test files (`test_forward_tasks_roundtrip.py`, `test_reverse_new_task.py`, `test_reverse_protected_cell_ignored.py`, `test_lock_contention.py`, `test_plaid_row_protection.py`, `test_replay_equivalence.py`, `test_assumption_pro_forma_math.py`). Adopt those names where applicable; `test_assumption_pro_forma_math.py` is OUT OF SCOPE (Assumptions sheet not built yet).

6. **prompts/07b-xlsx-workbooks-forward.md** — quality-bar reference. Read its Operating Context, Out of Scope, and Verification block. Match structural quality.

7. **adminme/projections/xlsx_workbooks/__init__.py** (full read), **builders.py** (full read), **lockfile.py** (full read), **query_context.py** (full read). The forward daemon is what your reverse daemon races against; you must understand its emit shape, lock acquisition pattern, and `regenerate_now()` test bypass.

8. **adminme/projections/xlsx_workbooks/sheets/tasks.py**, **commitments.py**, **recurrences.py**, **raw_data.py** — full reads (each ~70-130 lines). These tell you:
   - The exact column ordering per sheet (`HEADERS`).
   - Which columns are derived (`DERIVED_COLUMNS` set per sheet).
   - For raw_data: which columns are `ALWAYS_DERIVED` vs `PLAID_AUTHORITATIVE`, and the `is_manual` distinction.
   - How values are written (so you can reverse them — e.g. raw_data writes `amount_minor / 100.0` as a float; reverse must multiply by 100 and round to int).

9. **adminme/projections/xlsx_workbooks/sheets/people.py**, **accounts.py**, **metadata_ops.py**, **metadata_finance.py** — quick scan only. These are read-only sheets; reverse must drop ALL edits to them with a warning log.

10. **adminme/projections/xlsx_workbooks/forward.py** + **reverse.py** + **schemas.py** — full reads (each is a 22-line stub from prompt 02). **Confirm they are dead code.** This prompt deletes all three (PM-10 disposition).

11. **adminme/events/schemas/system.py** — full read. Pattern for the two new system events you register.

12. **adminme/events/schemas/domain.py** + **adminme/events/schemas/ops.py** — `grep -n "task.updated\|task.deleted\|task.created\|commitment.edited\|recurrence.updated\|money_flow.manually_added\|money_flow.manually_deleted" adminme/events/schemas/*.py`. Confirm payload shapes for every domain event the reverse daemon emits.

13. **adminme/events/log.py** — `sed -n '1,80p'` to confirm the `EventLog.append(envelope)` signature. The reverse daemon emits through this same path.

14. **adminme/lib/instance_config.py** — full read (small). Confirm `xlsx_workbooks_dir` exists and is the right location for the sidecar JSON tree (sibling to the .xlsx files: `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`).

15. **scripts/verify_invariants.sh** — full read. You will extend `ALLOWED_EMITS` (lines 107–110) in Commit 1 to include the two new system events. `ALLOWED_EMIT_FILES` does NOT change — the reverse daemon lives in `adminme/daemons/`, outside the projections directory the verify script audits.

16. **pyproject.toml** — `grep -E "watchdog|pandas" pyproject.toml`. Both should be present (watchdog declared in 02's scaffold; pandas declared as L3 projection dep). Run mypy preflight per slim preamble; if `watchdog` complains about missing stubs, add to `[[tool.mypy.overrides]]` block in Commit 1.

**Do NOT read** during this session:
- BUILD.md outside the §3.11 ranges above.
- L4 / pipelines / console specs.
- Any other constitutional doc beyond the targeted ranges.

---

## Operating context

You are building the second half of the xlsx round-trip: a daemon that watches the two xlsx files, detects principal edits, and emits the domain events those edits represent. The forward daemon (built in 07b) regenerates the xlsx from event-driven projection state. Together they form a closed loop; alone, they don't.

### Architectural placement

The reverse daemon is **NOT a projection**. Per BUILD.md §3.11 line 995, it lives at `adminme/daemons/xlsx_sync/reverse.py`. Architecturally it is an **adapter** (L1-adjacent): it ingests external state (a human's edits to a file on disk) and emits typed events into the event log. The fact that it shares a directory tree with the forward projection is incidental to the round-trip — they happen to coordinate via a `.lock` and a sidecar JSON.

This placement means:
- The reverse daemon does NOT register with `ProjectionRunner`.
- The reverse daemon DOES emit domain events (unlike projections, which emit only system events per [§2.2]).
- The reverse daemon emits two new SYSTEM events (`xlsx.reverse_projected`, `xlsx.reverse_skipped_during_forward`) for observability — these are emitted from `adminme/daemons/`, outside `adminme/projections/`, so `verify_invariants.sh`'s `ALLOWED_EMIT_FILES` allowlist (which audits only the projections directory) does not need extension.
- `scripts/verify_invariants.sh`'s `ALLOWED_EMITS` regex MUST be extended to include the two new types — verify_invariants.sh checks `ALLOWED_EMITS` against any emit found in `adminme/projections/`, and although the new system events are emitted from a daemon, future projections might legitimately want to emit them too (e.g. a future cleanup projection). Extending the regex is the correct hygiene.

### Sidecar JSON: forward writes, reverse rewrites

**Resolves UT-6.** BUILD.md §3.11 step 5 (line 1015) says the reverse daemon writes the sidecar at the end of each cycle; line 1009 says the reverse daemon reads it at the start of each cycle. The forward side's role is implied at line 1054 (`adminme projection rebuild xlsx_workbooks` deletes both xlsx files plus their sidecar state and regenerates). Two corollaries:

1. **Forward daemon writes the sidecar after each regeneration**, in the same lock as the xlsx write. This gives the reverse daemon a stable read baseline at adapter startup, after a forward regeneration races a human save (forward wins; reverse comes back to find the workbook restored to projection truth, sidecar matches, no diff to emit), and after `adminme projection rebuild xlsx_workbooks` re-establishes both files + sidecar from event-log replay.
2. **Reverse daemon rewrites the sidecar at the end of its cycle** to capture the new state (post-emitted-events; reflects what the workbook now represents). This handles the case where the principal makes a sequence of edits and saves twice in quick succession — the second cycle's diff is against the first cycle's post-edit state, not against the pre-edit baseline.

The sidecar lives at `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json` (resolve via `InstanceConfig.xlsx_workbooks_dir.parent / ".xlsx-state"` — note: SIBLING to the workbooks dir, not inside it; this avoids any chance the xlsx daemon mistakes the JSON for a workbook and the watchdog reverse daemon doesn't trip on its own writes).

Each sheet's JSON file contains a list of row dicts keyed by the sheet's id column (`task_id` for Tasks, `commitment_id` for Commitments, `recurrence_id` for Recurrences, `flow_id` for Raw Data — note: the sheet column is `txn_id`, but the canonical id is `flow_id` from the `money_flows` projection; map at write time). Read-only sheets (People, Accounts, Metadata) get a sidecar file too — its presence makes "did the principal edit a read-only sheet?" detectable; its content is a hash of the regenerated rows so we can compare without storing the full row set.

### Sensitivity preservation

A row in the projection may carry `sensitivity='sensitive'` or `'privileged'`. When the principal edits that row in xlsx and the reverse daemon emits a domain event, the new event MUST inherit the same sensitivity. Otherwise we silently downgrade privileged data to normal on every reverse cycle — a §6 violation.

Implementation: when the reverse daemon emits an update event, it looks up the current row's sensitivity from the source projection (via the same query context the forward daemon uses) and stamps the envelope with that sensitivity. For ADD events (new manual rows in Raw Data), the principal-supplied row defaults to `'normal'` — they're principal-authored, not derived from a sensitive upstream source. There is no path in this prompt for the principal to add a privileged row via xlsx; that's correct (privileged rows enter the system through privileged-floor adapters per [§6.10]).

### Domain events the reverse daemon emits (per sheet)

| Sheet | ADD event | UPDATE event | DELETE event |
|---|---|---|---|
| Tasks | `task.created` | `task.updated` | `task.deleted` |
| Commitments | _N/A_ — commitments are pipeline-proposed only per [§4.2]; reverse drops principal-added rows with a warning log | `commitment.edited` | _N/A_ — commitments cancel via `commitment.cancelled`, not delete; reverse drops row deletions with a warning log |
| Recurrences | `recurrence.added` | `recurrence.updated` | _N/A_ — recurrences are not deletable in v1; reverse drops row deletions with a warning log |
| Raw Data | `money_flow.manually_added` (only if `is_manual` cell == TRUE on the new row) | UPDATE for manual rows: `money_flow.manually_added` re-emitted with the same `flow_id`? — NO, this corrupts provenance. Instead: there is no edit-event for money_flows in v1 because the principal-editable fields (`assigned_category`, `notes`, `memo`) are not yet wired as event types. A future prompt registers `money_flow.recategorized` etc. **For 07c, edits to `assigned_category` / `notes` / `memo` on existing rows are silently dropped with an INFO log noting the deferred event type.** | `money_flow.manually_deleted` (only if the row had `is_manual == TRUE`; Plaid rows can't be deleted from xlsx — drop with warning log) |
| People (read-only) | drop | drop | drop |
| Accounts (read-only) | drop | drop | drop |
| Metadata (read-only) | drop | drop | drop |

The Commitments-add and Recurrences-delete and Raw Data-edit drops are all "future event type registers later" cases. They emit no event but DO log at INFO level so the principal can debug if their edit "doesn't take." All four cases also rewrite the sidecar to current sheet state so the diff doesn't keep firing on the same orphan row.

### Watchdog → asyncio bridge

`watchdog`'s `Observer.schedule()` invokes callbacks on watchdog's own thread. The reverse daemon's actual work (lock acquisition, sidecar I/O, event emission) needs the asyncio loop. Bridge pattern:

```python
class _XlsxFileEventHandler(FileSystemEventHandler):
    def __init__(self, loop: asyncio.AbstractEventLoop,
                 schedule_fn: Callable[[str], None]) -> None:
        self._loop = loop
        self._schedule = schedule_fn

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix != ".xlsx":
            return
        # Hop from watchdog thread back to the asyncio loop.
        self._loop.call_soon_threadsafe(self._schedule, path.name)
```

The `schedule_fn` runs on the loop. It debounces (2s wait per BUILD.md §3.11 line 1000) and then runs the cycle — analogous to 07b's forward debounce.

### 5-second undo window for deletions

BUILD.md §3.11 line 1012 specifies: "deleted rows ... emit deletion event, guarded by a 5-second 'undo window' where if the principal CTRL-Zs within 5s the deletion is cancelled." Implementation: when a row is detected as deleted, queue the deletion-emit task with a 5s sleep; if a subsequent cycle on the same sheet sees the row reappear before the 5s elapses, cancel the queued task. This needs per-`(sheet, row_id)` cancellation tracking.

The undo window does NOT apply to ADD events — we emit those immediately, because there's no harmful side effect of re-emitting if the principal CTRL-Zs and the row reappears in the next cycle (they'll get a no-op idempotent insert in the projection; no second event because the diff against the rewritten sidecar shows no add).

The undo window does NOT apply to UPDATE events for the same reason.

### Replay equivalence

`adminme projection rebuild xlsx_workbooks` is mentioned in BUILD.md §3.11 line 1054 as a CLI command. **You do NOT implement that CLI command in this prompt** — it lands in prompt 17 (CLI). You DO need to make sure the daemon, on cold-start with no sidecar present, treats the workbook as authoritative (writes a sidecar from the workbook's current state, emits no events). This is the "boot from existing workbook" path.

---

## Out of scope

- Do NOT implement `adminme projection rebuild xlsx_workbooks` CLI — prompt 17.
- Do NOT register `money_flow.recategorized`, `money_flow.notes_updated`, or any other event type for xlsx Raw Data field edits — defer; principal edits to `assigned_category`/`notes`/`memo` are dropped with INFO log in this prompt.
- Do NOT extend the forward daemon's emit payload with `observation_mode_active` per [D5] — observation mode is wired in prompt 16 (bootstrap §5/§9).
- Do NOT wire the daemon into a running supervisor / systemd / launchd unit — daemon lifecycle (start-on-boot, stop-on-shutdown) lands in prompt 16.
- Do NOT add Apple Reminders, BlueBubbles, Plaid, Gmail, or any other adapter — prompts 11/12.
- Do NOT add Session / scope enforcement around the emitted events — prompt 08. Reverse-emitted events carry `actor_identity` from `InstanceConfig.principal_member_id` (the file owner), but no scope check on the emit path.
- Do NOT add tests that require a live Numbers or Excel install — synthetic openpyxl writes only.
- Do NOT touch the People, Accounts, Metadata sheet builders — they remain read-only; the reverse daemon drops edits to them.

---

## Incremental commit discipline — MANDATORY

Four batch commits. Slim universal preamble (PROMPT_SEQUENCE.md) governs cross-cutting discipline.

### Commit 1 — Two new system events + verify_invariants extension + PM-10 stub deletion + forward sidecar write

This commit lays the schema and infrastructure plumbing before any daemon code lands.

#### 1a. Register two new system events in `adminme/events/schemas/system.py`

Append (do not replace existing content):

```python
class XlsxReverseProjectedV1(BaseModel):
    model_config = {"extra": "forbid"}
    workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]
    detected_at: str = Field(min_length=1)
    sheets_affected: list[str]
    events_emitted: list[str]   # event_id list, ordered as emitted
    duration_ms: int = Field(ge=0)


class XlsxReverseSkippedDuringForwardV1(BaseModel):
    model_config = {"extra": "forbid"}
    workbook_name: Literal["adminme-ops.xlsx", "adminme-finance.xlsx"]
    detected_at: str = Field(min_length=1)
    skip_reason: Literal["forward_lock_held"]


registry.register("xlsx.reverse_projected", 1, XlsxReverseProjectedV1)
registry.register("xlsx.reverse_skipped_during_forward", 1, XlsxReverseSkippedDuringForwardV1)
```

Update the module docstring to add both event names to the "Currently contains" list.

#### 1b. Extend `scripts/verify_invariants.sh`

Replace line 107 (`ALLOWED_EMITS='xlsx\.regenerated'`) with:

```bash
ALLOWED_EMITS='xlsx\.regenerated|xlsx\.reverse_projected|xlsx\.reverse_skipped_during_forward'
```

`ALLOWED_EMIT_FILES` stays unchanged — the reverse daemon emits from `adminme/daemons/xlsx_sync/reverse.py`, outside the `adminme/projections/` audit scope.

Add a maintenance comment near the allowlist:

```bash
# 07c (reverse daemon) emits xlsx.reverse_projected and
# xlsx.reverse_skipped_during_forward from adminme/daemons/xlsx_sync/reverse.py.
# The daemon directory is outside the projections audit scope, so
# ALLOWED_EMIT_FILES does not change. ALLOWED_EMITS extends so a future
# projection that legitimately needs to emit these system events is allowed.
```

#### 1c. Delete three stub files (PM-10 disposition)

```bash
git rm adminme/projections/xlsx_workbooks/forward.py
git rm adminme/projections/xlsx_workbooks/reverse.py
git rm adminme/projections/xlsx_workbooks/schemas.py
```

These stubs were created in prompt 02 and never populated. 07b built alongside them rather than in them. The forward daemon code lives in `adminme/projections/xlsx_workbooks/__init__.py` + `builders.py` + `lockfile.py`; the reverse daemon will live at `adminme/daemons/xlsx_sync/reverse.py` per BUILD.md §3.11. The three stubs are dead code. Delete.

If anything in the codebase imports them: it's an old test or a stale demo script. Grep first:

```bash
grep -rn "from adminme.projections.xlsx_workbooks import.*forward\|from adminme.projections.xlsx_workbooks.forward\|from adminme.projections.xlsx_workbooks.reverse\|from adminme.projections.xlsx_workbooks.schemas" adminme/ tests/ scripts/
```

If grep returns hits, those imports were also dead code — fix or remove them. Document in the commit message which (if any) imports were also pruned.

#### 1d. Extend the forward daemon to write sidecar JSON

This is the UT-6 resolution. In `adminme/projections/xlsx_workbooks/__init__.py`, the `_regenerate()` coroutine writes the xlsx atomically; immediately after that write (still inside the lock, BEFORE releasing), it must also write the sidecar JSON files for every bidirectional sheet in the workbook just regenerated.

Two new module-level constants in `__init__.py`:

```python
XLSX_STATE_DIRNAME = ".xlsx-state"

# Map sheet name → id-column name. Read-only sheets store a content hash
# instead of row data; bidirectional sheets store the rows keyed by id.
_BIDIRECTIONAL_SHEETS: dict[str, dict[str, str]] = {
    OPS_WORKBOOK_NAME: {
        "Tasks": "task_id",
        "Recurrences": "recurrence_id",
        "Commitments": "commitment_id",
    },
    FINANCE_WORKBOOK_NAME: {
        "Raw Data": "txn_id",   # column name on the sheet; canonical = flow_id
    },
}
_READONLY_SHEETS: dict[str, list[str]] = {
    OPS_WORKBOOK_NAME: ["People", "Metadata"],
    FINANCE_WORKBOOK_NAME: ["Accounts", "Metadata"],
}
```

A new helper module `adminme/projections/xlsx_workbooks/sidecar.py`:

```python
"""
Sidecar JSON state for the xlsx round-trip.

The forward daemon writes sidecar JSON (one file per sheet) immediately
after each xlsx regeneration, in the same file lock. The reverse daemon
reads these files to compute its diff against principal edits and rewrites
them at the end of each cycle.

Path: <instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json
(SIBLING to the workbooks dir; the reverse daemon does NOT watch this tree
— only the .xlsx files themselves trigger watchdog events.)

Per BUILD.md §3.11 line 1009 + line 1015. Resolves Partner UT-6.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet


def sidecar_dir(xlsx_workbooks_dir: Path) -> Path:
    """Return the directory holding sidecar JSON. Sibling to the xlsx files
    so neither directory contains the other; no risk of self-loop in
    watchdog observers that might one day watch sidecar."""
    return xlsx_workbooks_dir.parent / ".xlsx-state"


def sidecar_path(xlsx_workbooks_dir: Path, workbook: str, sheet: str) -> Path:
    return sidecar_dir(xlsx_workbooks_dir) / workbook / f"{sheet}.json"


def write_sheet_state(
    xlsx_workbooks_dir: Path,
    workbook: str,
    sheet: str,
    rows: list[dict[str, Any]],
) -> None:
    """Write a bidirectional sheet's row state. ``rows`` is a list of dicts
    with stable string-typed values (no datetime, no Decimal) — JSON-safe."""
    p = sidecar_path(xlsx_workbooks_dir, workbook, sheet)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, sort_keys=True, indent=2)
    tmp.replace(p)


def write_readonly_state(
    xlsx_workbooks_dir: Path,
    workbook: str,
    sheet: str,
    content_hash: str,
) -> None:
    """For read-only sheets we don't store rows — just a hash so a principal
    edit on the sheet (which we intend to drop) is detectable for logging."""
    p = sidecar_path(xlsx_workbooks_dir, workbook, sheet)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"content_hash": content_hash}, f)
    tmp.replace(p)


def read_sheet_state(
    xlsx_workbooks_dir: Path, workbook: str, sheet: str
) -> list[dict[str, Any]] | None:
    """Return rows; None if the sidecar file is missing (cold-start)."""
    p = sidecar_path(xlsx_workbooks_dir, workbook, sheet)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return None


def read_readonly_state(
    xlsx_workbooks_dir: Path, workbook: str, sheet: str
) -> str | None:
    p = sidecar_path(xlsx_workbooks_dir, workbook, sheet)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "content_hash" in data:
        return str(data["content_hash"])
    return None


def hash_readonly_sheet(ws: Worksheet) -> str:
    """Stable hash of a read-only sheet's visible content."""
    h = hashlib.sha256()
    for row in ws.iter_rows(values_only=True):
        h.update(repr(row).encode("utf-8"))
    return h.hexdigest()
```

Extend the forward daemon's `_regenerate()` to call the sidecar writers as the LAST step inside the lock (after `wb.save() + os.replace()`, before the lock context exits). The simplest way: extract the sidecar-writing into a `_write_sidecar_for(workbook)` helper on `XlsxWorkbooksProjection` that opens the just-written xlsx with `openpyxl.load_workbook(data_only=True)`, walks each known sheet, and writes the appropriate sidecar JSON. Call it inside the lock context in `_under_lock()`.

The helper reads the xlsx file we just wrote rather than re-querying projections — this guarantees the sidecar matches what reverse will see when it loads the workbook back, eliminating any chance of "we wrote rows derived from current projection state, but the sidecar was derived from a slightly different snapshot."

#### 1e. Tests for Commit 1

`tests/unit/test_schema_registry.py` already exists; add assertions for the two new event types:

```python
def test_xlsx_reverse_events_registered():
    from adminme.events.registry import registry, ensure_autoloaded
    ensure_autoloaded()
    assert "xlsx.reverse_projected" in registry.known_types()
    assert "xlsx.reverse_skipped_during_forward" in registry.known_types()
```

`tests/unit/test_xlsx_sidecar.py` (new file, ≥6 tests):
- `test_sidecar_dir_is_sibling_of_workbooks` — `sidecar_dir(xlsx_dir).parent == xlsx_dir.parent`.
- `test_write_then_read_roundtrip` — write rows, read rows, assert equality.
- `test_atomic_write_no_partial_on_disk` — patch `tmp.replace` to raise mid-write; assert no `.tmp` left behind.
- `test_read_missing_returns_none` — fresh dir, read returns None.
- `test_readonly_state_uses_hash_not_rows` — write_readonly with hash 'abc'; read_readonly returns 'abc'; no row data persisted.
- `test_hash_readonly_sheet_deterministic` — same content → same hash; different content → different hash.

`tests/unit/test_xlsx_forward_writes_sidecar.py` (new, ≥4 tests):
- `test_regenerate_writes_sidecar_for_each_bidirectional_sheet` — populate fixture, call `regenerate_now("adminme-ops.xlsx")`, assert sidecar files exist for Tasks, Recurrences, Commitments and contain the same row data the workbook now contains.
- `test_regenerate_writes_sidecar_for_each_readonly_sheet` — same but for People + Metadata; sidecar files contain `{"content_hash": "..."}`.
- `test_finance_regenerate_writes_raw_data_sidecar` — Raw Data sidecar matches workbook rows.
- `test_sidecar_written_inside_lock` — patch `acquire_workbook_lock` to spy on the lock-released state; assert sidecar files exist BEFORE the lock context exits.

Update `scripts/demo_xlsx_forward.py` to print sidecar file paths.

**Verify commit 1:**

```bash
poetry run pytest tests/unit/test_schema_registry.py \
                 tests/unit/test_xlsx_sidecar.py \
                 tests/unit/test_xlsx_forward_writes_sidecar.py -v 2>&1 | tail -10

# Stub deletion: confirm no imports broken
grep -rn "xlsx_workbooks.forward\b\|xlsx_workbooks.reverse\b\|xlsx_workbooks.schemas\b" adminme/ tests/ scripts/ \
    || echo "OK: no dead imports of deleted stubs"

# verify_invariants regression on main: extended ALLOWED_EMITS still passes
bash scripts/verify_invariants.sh && echo "OK: verify_invariants clean"

# Forward daemon regression
poetry run pytest tests/unit/test_xlsx_*.py -q 2>&1 | tail -5

poetry run ruff check adminme/ tests/ scripts/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

git add adminme/events/schemas/system.py \
        adminme/projections/xlsx_workbooks/__init__.py \
        adminme/projections/xlsx_workbooks/sidecar.py \
        scripts/verify_invariants.sh \
        tests/unit/test_schema_registry.py \
        tests/unit/test_xlsx_sidecar.py \
        tests/unit/test_xlsx_forward_writes_sidecar.py \
        scripts/demo_xlsx_forward.py
git rm adminme/projections/xlsx_workbooks/forward.py \
       adminme/projections/xlsx_workbooks/reverse.py \
       adminme/projections/xlsx_workbooks/schemas.py
git commit -m "phase 07c-1: register reverse system events; forward writes sidecar; delete 02 stubs"
```

If any test fails, STOP and fix before commit 2.

### Commit 2 — Reverse-projection diff core (no daemon yet)

Build the pure-functional core of the reverse projection in `adminme/daemons/xlsx_sync/diff.py`. Design: the diff core is sync, takes (current sheet rows, sidecar rows, sheet schema descriptor) and returns a `DiffResult` describing adds/updates/deletes. The daemon (Commit 3) wires this into the watchdog + lock + emit pathway.

Separating diff from I/O makes both testable in isolation — diff against handcrafted DataFrames; daemon against a temp filesystem with a fake xlsx.

#### File: `adminme/daemons/__init__.py` (new, empty package marker)

#### File: `adminme/daemons/xlsx_sync/__init__.py` (new, empty package marker)

#### File: `adminme/daemons/xlsx_sync/sheet_schemas.py`

A registry of per-sheet diff descriptors:

```python
"""
Per-sheet diff descriptors for the xlsx reverse daemon.

Each descriptor declares:
- id_column: which column uniquely identifies a row.
- editable_columns: columns the principal may edit (drives UPDATE event detection).
- always_derived: columns whose edits are silently dropped (matches the
  forward daemon's DERIVED_COLUMNS set).
- adds_emit_event: event type emitted for added rows (None = drop with log).
- updates_emit_event: event type emitted for updated rows (None = drop with log).
- deletes_emit_event: event type emitted for deleted rows (None = drop with log).
- row_to_payload: function producing the event payload from a row dict.

For Raw Data: `is_manual_predicate(row) -> bool` and special handling
because the editable column set differs per row.

This module is sync, has no I/O, and imports nothing from openpyxl or
watchdog — the diff algorithm consumes plain dict rows.
"""
```

Bidirectional sheets: Tasks, Recurrences, Commitments, Raw Data. For each, fill in the descriptor based on the matching sheet builder's HEADERS / DERIVED_COLUMNS sets.

For Tasks:
- id_column = "task_id"
- editable_columns = {"title", "status", "assigned_member", "owed_to_party", "due_date", "urgency", "effort_min", "energy", "context", "notes"}
- always_derived = {"task_id", "created_at", "completed_at"}
- adds_emit_event = "task.created"
- updates_emit_event = "task.updated"
- deletes_emit_event = "task.deleted"
- row_to_payload: stamps the editable fields into the registered schema's shape.

For Commitments:
- id_column = "commitment_id"
- editable_columns = {"owed_by_member", "owed_to_party", "kind", "text_summary", "suggested_due", "status"}
- always_derived = {"commitment_id", "confidence", "strength", "source_summary"}
- adds_emit_event = None  → log INFO ("commitments are pipeline-proposed only per [§4.2]; principal-added row dropped: {row}")
- updates_emit_event = "commitment.edited"
- deletes_emit_event = None  → log INFO ("commitments cancel via /api/commitments/<id>/cancel; row deletion dropped: {row}")

For Recurrences:
- id_column = "recurrence_id"
- editable_columns = {"title", "cadence", "assigned_member", "notes", "active"}
- always_derived = {"recurrence_id", "next_due", "last_completed_at"}
- adds_emit_event = "recurrence.added"
- updates_emit_event = "recurrence.updated"
- deletes_emit_event = None  → log INFO ("recurrences are not deletable in v1; row deletion dropped: {row}")

For Raw Data:
- id_column = "txn_id"  (sheet column; canonical id is `flow_id`)
- editable_columns is conditional on `is_manual`:
   - For manual rows (is_manual == TRUE): {"date", "account_last4", "merchant_name", "merchant_category", "amount", "memo", "assigned_category", "notes"}
   - For Plaid rows (is_manual == FALSE): {"assigned_category", "notes", "memo"}
- always_derived = {"txn_id", "plaid_category", "is_manual"}
   - is_manual is itself derived: principal cannot flip a Plaid row to manual or vice versa. Edit ignored.
- adds_emit_event = "money_flow.manually_added" (only if `is_manual == TRUE` on the new row; else drop with WARN log: "non-manual row added via xlsx; rejecting per [§13.4]")
- updates_emit_event = None for v1 (the editable columns assigned_category/notes/memo do not yet have a registered event type; defer to future prompt; INFO log)
- deletes_emit_event = "money_flow.manually_deleted" (only if the row had `is_manual == TRUE` in the sidecar; else WARN log: "Plaid row deletion via xlsx ignored")

#### File: `adminme/daemons/xlsx_sync/diff.py`

```python
"""
Pure-functional diff core for the xlsx reverse daemon.

Given (current sheet rows from openpyxl, sidecar rows from JSON,
sheet descriptor from sheet_schemas), compute three lists:
- added: rows in current not in sidecar
- updated: rows in both with editable-column differences
- deleted: rows in sidecar not in current

Edits to non-editable columns are silently dropped from `updated`. A row
that has only non-editable changes is considered semantically unchanged
and produces no UPDATE.
"""

@dataclass(frozen=True)
class DiffResult:
    added: list[dict[str, Any]]
    updated: list[tuple[dict[str, Any], dict[str, str]]]   # (current row, changed-fields dict)
    deleted: list[dict[str, Any]]
    dropped_edits: list[tuple[dict[str, Any], list[str]]]  # rows where only non-editable cols changed; for INFO log


def diff_sheet(
    current_rows: list[dict[str, Any]],
    sidecar_rows: list[dict[str, Any]],
    descriptor: SheetDescriptor,
) -> DiffResult:
    ...
```

The diff core handles per-row `editable_columns` (callable taking the row dict, returning the set of editable columns for that specific row — used by Raw Data's is_manual conditional) by accepting `editable_columns` as either a frozenset or a callable.

Type coercion: openpyxl reads values back as their native types (int, float, str, datetime, None); sidecar JSON serializes everything to JSON-native types (str, int, float, bool, None). Compare with a normalize step: floats compared with abs-tolerance 1e-9 (for amount fields); datetime values normalized to ISO 8601 string; None and "" treated as equal.

#### Tests — `tests/unit/test_xlsx_diff.py` (≥10 tests)

- `test_no_changes_returns_empty_diff`
- `test_added_row_appears_in_added_only`
- `test_deleted_row_appears_in_deleted_only`
- `test_editable_field_change_appears_in_updated`
- `test_derived_field_change_does_not_appear_in_updated_or_anywhere` — assert it lands in `dropped_edits` for INFO logging
- `test_id_column_change_treated_as_delete_plus_add` — principal blanked task_id and re-typed it; diff sees the original id missing and the new id added.
- `test_raw_data_manual_row_full_editable_set` — manual row with date/amount changed → updated.
- `test_raw_data_plaid_row_amount_change_dropped` — Plaid row with amount changed → dropped_edits, not updated.
- `test_raw_data_plaid_row_assigned_category_dropped_in_v1` — assigned_category change → dropped_edits with future-event-type note (because money_flow.recategorized doesn't exist in v1).
- `test_float_comparison_tolerance` — amount 12.34 in sidecar vs 12.340000001 in current → NOT updated.
- `test_none_and_empty_string_equivalent` — sidecar None vs current "" → not updated.
- `test_descriptor_with_callable_editable_columns_uses_per_row` — Raw Data descriptor's `editable_columns(row)` invoked per row.

**Verify commit 2:**

```bash
poetry run pytest tests/unit/test_xlsx_diff.py -v 2>&1 | tail -5
poetry run pytest tests/unit/test_xlsx_*.py -q 2>&1 | tail -5
poetry run ruff check adminme/ tests/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

git add adminme/daemons/__init__.py \
        adminme/daemons/xlsx_sync/__init__.py \
        adminme/daemons/xlsx_sync/diff.py \
        adminme/daemons/xlsx_sync/sheet_schemas.py \
        tests/unit/test_xlsx_diff.py
git commit -m "phase 07c-2: pure-functional reverse diff core + sheet descriptors"
```

### Commit 3 — Reverse daemon (watchdog + lock + emit + undo window)

The full daemon. Wires diff core to filesystem and event log.

#### File: `adminme/daemons/xlsx_sync/reverse.py`

```python
"""
xlsx reverse daemon — file edits → domain events.

Watches the two xlsx workbooks via watchdog. On any modified event:
1. Wait 2s for the writer to flush (BUILD.md §3.11 line 1000).
2. Acquire the same .lock the forward daemon uses. If forward holds it
   for >10s, emit xlsx.reverse_skipped_during_forward and abandon this
   cycle (BUILD.md §3.11 line 1001 + line 1047).
3. Open the workbook with openpyxl(data_only=True).
4. For each bidirectional sheet:
   - Load current rows from the worksheet.
   - Read sidecar JSON for the sheet's last-known state.
   - diff_sheet() → DiffResult.
   - Emit add/update events immediately.
   - Schedule delete events with a 5s undo window (BUILD.md §3.11 line 1012).
5. For each read-only sheet:
   - Hash current content; compare to sidecar hash.
   - If different, log WARN ("read-only sheet edited; ignoring").
6. Rewrite all sidecar JSON files to current state.
7. Release the lock.
8. Emit xlsx.reverse_projected.

Per BUILD.md §3.11 — the canonical spec.

The daemon is NOT a projection ([§2.2] applies to the projections
directory; this lives in adminme/daemons/). It is L1-adjacent:
ingest external state (file edits), emit typed events.
"""
```

Class outline:

```python
class XlsxReverseDaemon:
    def __init__(
        self,
        config: InstanceConfig,
        query_context: XlsxQueryContext,
        *,
        event_log: EventLog,
        flush_wait_s: float = 2.0,
        forward_lock_timeout_s: float = 10.0,
        delete_undo_window_s: float = 5.0,
    ) -> None: ...

    async def start(self) -> None:
        """Begin watching both workbooks. Idempotent."""

    async def stop(self) -> None:
        """Cancel any pending cycles and stop the observer. Idempotent."""

    async def run_cycle_now(self, workbook: str) -> "CycleResult":
        """Bypass watchdog + flush wait; run a full reverse cycle on
        ``workbook``. Used by tests."""

    # ----- internal -----
    def _on_workbook_modified_threadsafe(self, workbook_name: str) -> None: ...
    async def _scheduled_cycle(self, workbook_name: str) -> None: ...
    async def _do_cycle(self, workbook: str) -> "CycleResult": ...
    async def _emit_domain_event(self, ...) -> str: ...
    async def _emit_reverse_projected(self, ...) -> None: ...
    async def _emit_skipped(self, workbook: str) -> None: ...
    def _schedule_delete_with_undo(self, ...) -> None: ...
```

`CycleResult` records the outcome (events emitted, sheets affected, duration_ms) for the system event payload.

The daemon constructs domain-event payloads by mapping diff rows to the registered Pydantic models. For Tasks: a `task.created` payload requires `task_id` (use a fresh `task_<n>` id from the daemon's id-generator if blank — but then the principal didn't supply an id, so the diff comparison failed; reject with WARN: "task add requires task_id column to be filled by the principal — convention: leave blank, projection will assign on first regenerate"). Actually — re-think: when the principal adds a row to Tasks, they should NOT be expected to invent a `task_id`. The daemon assigns one. The diff already saw the row as "added" because the (blank) id wasn't in sidecar; we generate `tsk_<ulid>` here, stamp it on the emitted event payload, and the next forward regeneration will write the row with that id (then sidecar reflects it; subsequent edits look like updates, not re-adds).

This is why id-column changes are tricky. Spec: if the principal types into a blank task_id cell, treat the typed value as their suggested id. If they leave it blank, we generate. If they edit an existing non-blank id to a different non-blank id, the diff sees that as delete-plus-add (already handled).

Sensitivity preservation: when emitting an UPDATE event, the daemon looks up the row's current sensitivity from the source projection (via `query_context`) and passes it to `EventLog.append()`. For ADD events, sensitivity defaults to `'normal'`. For DELETE events, look up from the projection (the row still exists there until the delete event is consumed).

The `actor_identity` field on emitted envelopes: use `config.principal_member_id` (the file owner). If that field doesn't exist on `InstanceConfig`, fall back to a hardcoded `"xlsx_reverse"` literal — there's no real "actor" since we don't know which principal made the edit; we'd need filesystem owner inspection or 1Password integration to do better, both out of scope here.

#### Tests — `tests/unit/test_xlsx_reverse_basic.py` (≥10 tests)

The fixture sets up a temp instance dir, runs forward daemon to populate ops + finance workbooks + sidecars, then constructs the reverse daemon (without starting watchdog) and uses `run_cycle_now()` to drive cycles.

- `test_no_edits_no_events` — call `run_cycle_now("adminme-ops.xlsx")` on unmodified workbook → no events emitted, sidecar unchanged.
- `test_new_task_emits_task_created` — programmatically open ops xlsx, append a row to Tasks, save. Run cycle. Assert one `task.created` event with the supplied title/status fields; assert `task_id` was generated.
- `test_edit_task_title_emits_task_updated` — modify Tasks.title cell. Run cycle. Assert `task.updated` with payload containing only the changed field.
- `test_delete_task_emits_after_undo_window` — delete Tasks row. Run cycle (with `delete_undo_window_s=0.05`). Wait 0.1s. Assert `task.deleted` emitted.
- `test_delete_undo_within_window_cancels_emit` — delete Tasks row. Run cycle (window=0.5s). Within 0.2s, restore the row and run cycle again. Wait 0.6s. Assert NO `task.deleted` event was emitted.
- `test_edit_derived_column_silently_dropped` — modify `created_at` cell on a Tasks row. Run cycle. Assert no events emitted; INFO log captured.
- `test_edit_id_column_treated_as_delete_plus_add` — change task_id. Run cycle. Assert one `task.deleted` (after undo window) AND one `task.created` (immediate).
- `test_edit_readonly_sheet_logged_no_event` — edit a People sheet cell. Run cycle. Assert no events; WARN log present.
- `test_sidecar_rewritten_after_cycle` — sidecar JSON differs after cycle if events were emitted; sidecar matches sheet state.
- `test_sensitivity_preserved_on_update` — fixture sets one task with `sensitivity='sensitive'` in events that built the projection; principal edits it; emitted `task.updated` carries `sensitivity='sensitive'`.
- `test_xlsx_reverse_projected_emitted_at_cycle_end` — run cycle with edits; `xlsx.reverse_projected` event lands with non-empty `events_emitted` list.

#### Tests — `tests/unit/test_xlsx_reverse_lock_contention.py` (≥4 tests)

- `test_forward_holds_lock_emits_skipped` — patch the forward's `acquire_workbook_lock` to hold the lock on a separate thread; run reverse cycle with `forward_lock_timeout_s=0.2`. Assert `xlsx.reverse_skipped_during_forward` emitted; no domain events.
- `test_forward_releases_lock_within_timeout_proceeds` — forward holds for 0.1s then releases; reverse with timeout 0.5s acquires and proceeds normally.
- `test_back_to_back_cycles_serialized_via_internal_lock` — fire two `run_cycle_now` concurrently; the second waits for the first.
- `test_skipped_emit_not_counted_in_reverse_projected` — when skipped, the `xlsx.reverse_projected` event does NOT also fire (skip is the cycle terminus).

#### Tests — `tests/unit/test_xlsx_reverse_finance.py` (≥6 tests)

- `test_new_manual_row_emits_money_flow_manually_added` — append row to Raw Data with is_manual=TRUE.
- `test_new_non_manual_row_dropped_with_warn` — append row to Raw Data with is_manual=FALSE; no event; WARN log.
- `test_delete_manual_row_emits_manually_deleted` — delete a manual row; after undo window, `money_flow.manually_deleted` emitted.
- `test_delete_plaid_row_dropped_with_warn` — delete Plaid row; no event; WARN log.
- `test_edit_assigned_category_dropped_with_info_for_now` — edit assigned_category on Plaid row; no event; INFO log mentions "money_flow.recategorized not registered yet; deferred."
- `test_edit_amount_on_plaid_row_dropped` — Plaid row amount change; no event; INFO log (it's a Plaid-authoritative drop, not a future-event-type drop — the log message distinguishes).

#### Tests — `tests/unit/test_xlsx_reverse_cold_start.py` (≥2 tests)

- `test_no_sidecar_present_writes_sidecar_emits_nothing` — delete sidecar JSON files. Run cycle. Assert sidecar files exist after cycle, NO domain events emitted (the workbook becomes the new baseline).
- `test_partial_sidecar_present_only_diffs_what_can_diff` — delete sidecar for Tasks but keep others. Run cycle. Tasks treated as cold-start (no events); other sheets diffed normally.

**Verify commit 3:**

```bash
poetry run pytest tests/unit/test_xlsx_reverse_*.py -v 2>&1 | tail -10
poetry run pytest tests/unit/test_xlsx_*.py -q 2>&1 | tail -5
poetry run pytest tests/unit/test_projection_*.py -q 2>&1 | tail -3
poetry run ruff check adminme/ tests/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3
bash scripts/verify_invariants.sh && echo "OK: invariants clean"

git add adminme/daemons/xlsx_sync/reverse.py \
        tests/unit/test_xlsx_reverse_basic.py \
        tests/unit/test_xlsx_reverse_lock_contention.py \
        tests/unit/test_xlsx_reverse_finance.py \
        tests/unit/test_xlsx_reverse_cold_start.py
git commit -m "phase 07c-3: xlsx reverse daemon (watchdog + lock + emit + undo window)"
```

### Commit 4 — Integration test (full round-trip) + smoke + verification + BUILD_LOG + push

#### Integration test — `tests/integration/test_xlsx_roundtrip.py`

The closing-the-loop test. End-to-end:

1. Spin up 10 sqlite projections + `XlsxWorkbooksProjection` (forward) via `ProjectionRunner`.
2. Construct `XlsxReverseDaemon` (do NOT start watchdog; drive cycles synchronously via `run_cycle_now`).
3. Append ~30 events: 5 tasks created via events, 3 commitments proposed, 2 recurrences added, 5 money_flows recorded (3 plaid + 2 manual).
4. Drive forward `regenerate_now("adminme-ops.xlsx")` and `regenerate_now("adminme-finance.xlsx")`. Confirm sidecars written.
5. Programmatically open ops xlsx; modify one Task title; add a new Task row with blank task_id; delete a Recurrence (should drop with INFO log per descriptor — Recurrence delete is None).
6. Close the file. Run reverse cycle on ops.
7. Assert: `task.updated` event for the modified row; `task.created` event for the new row with auto-generated task_id; NO recurrence delete event (dropped per descriptor); `xlsx.reverse_projected` event at end.
8. Drive forward regenerate again (in real life this would be triggered by the new domain events on the bus; for the test we call `regenerate_now` directly to verify round-trip semantics).
9. Open ops xlsx again. Assert: the new Task row now has the auto-generated task_id; the modified Task row has the updated title; the deleted-attempt Recurrence is still present (drop succeeded — no event, projection unchanged, forward regenerated unchanged).
10. Repeat for finance: add a manual money_flow row, delete a Plaid row (dropped), delete a manual row (after undo window). Assert correct event sequence.

Run the test with the daemon's debounce/wait/undo windows set to small values (0.05s) so the test completes in seconds, not minutes.

#### Smoke script — `scripts/demo_xlsx_roundtrip.py`

A standalone script that:
- Sets up a temp instance.
- Builds initial state via 10 events.
- Forward regenerates; prints "ops.xlsx generated, X tasks visible".
- Programmatically edits the xlsx.
- Reverse cycle; prints "events emitted: [...]".
- Forward regenerates again; prints final row counts.
- Exits 0 in <30 seconds.

Add to README if a demo-scripts catalog exists; if not, leave the script discoverable in `scripts/`.

#### Full verification block

```bash
# Lint + types
poetry run ruff check adminme/ tests/ scripts/ 2>&1 | tail -3
poetry run mypy adminme/ 2>&1 | tail -3

# Regression: prompts 03-07b tests still green
poetry run pytest tests/unit/test_event_log.py tests/unit/test_event_bus.py \
                 tests/unit/test_schema_registry.py tests/unit/test_event_validation.py \
                 tests/unit/test_projection_*.py \
                 tests/unit/test_xlsx_ops_workbook.py \
                 tests/unit/test_xlsx_finance_workbook.py \
                 tests/unit/test_xlsx_regenerated_emit.py \
                 tests/unit/test_xlsx_debounce.py -q 2>&1 | tail -3

# 07c unit tests
poetry run pytest tests/unit/test_xlsx_sidecar.py \
                 tests/unit/test_xlsx_forward_writes_sidecar.py \
                 tests/unit/test_xlsx_diff.py \
                 tests/unit/test_xlsx_reverse_*.py -q 2>&1 | tail -3

# Integration tests
poetry run pytest tests/integration/ -v 2>&1 | tail -10

# Canaries
poetry run pytest tests/unit/test_no_hardcoded_instance_path.py -v 2>&1 | tail -3
poetry run pytest tests/unit/test_no_hardcoded_identity.py -v 2>&1 | tail -3

# Full suite
poetry run pytest -q 2>&1 | tail -3

# Cross-cutting invariants — ONE LINE per slim preamble
bash scripts/verify_invariants.sh

# Smokes
poetry run python scripts/demo_xlsx_forward.py 2>&1 | tail -10
poetry run python scripts/demo_xlsx_roundtrip.py 2>&1 | tail -15
```

Expected:
- Ruff clean, mypy clean.
- Prompt 03–07b tests: ~210 passed, 0 failed.
- 07c unit tests: ≥30 passed.
- Integration: round-trip + 07b + 07a + 06 rebuild all passing.
- Canaries: PASSING + SKIPPED (identity canary).
- Full suite: ~245+ passed, 1 skipped.
- `verify_invariants.sh`: clean (extended `ALLOWED_EMITS`; no projection emits non-allowed events; reverse daemon's emits live outside the audited directory).
- Smokes: clean.

#### BUILD_LOG append

Per slim preamble, this is a Commit 4 changeset addition; no separate PR.

```markdown
### Prompt 07c — xlsx_workbooks reverse daemon + forward sidecar
- **Refactored**: by Partner in Claude Chat, <refactor date>. Prompt file: prompts/07c-xlsx-workbooks-reverse.md (~<NNN> lines, quality bar = 07b).
- **Session merged**: PR #<N>, commits <sha1> / <sha2> / <sha3> / <sha4>, merged <merge date>.
- **Outcome**: MERGED.  <!-- IN FLIGHT (PR open) until housekeeping completes -->
- **Evidence**:
  - 2 new system events at v1: `xlsx.reverse_projected`, `xlsx.reverse_skipped_during_forward` in `adminme/events/schemas/system.py`.
  - `scripts/verify_invariants.sh` extended: `ALLOWED_EMITS` now matches all three xlsx system events. `ALLOWED_EMIT_FILES` unchanged — reverse daemon lives in `adminme/daemons/xlsx_sync/reverse.py`, outside the projections audit scope.
  - PM-10 disposition resolved: deleted `adminme/projections/xlsx_workbooks/{forward,reverse,schemas}.py` (dead stubs from prompt 02). No imports broken.
  - UT-6 resolution: forward daemon writes sidecar JSON in same lock as each xlsx write; reverse daemon reads at cycle start, rewrites at cycle end. Sidecar lives at `<instance_dir>/projections/.xlsx-state/<workbook>/<sheet>.json`, sibling to xlsx files.
  - New module `adminme/projections/xlsx_workbooks/sidecar.py` — pure-functional sidecar I/O.
  - New module `adminme/daemons/xlsx_sync/diff.py` — pure-functional sheet-diff core.
  - New module `adminme/daemons/xlsx_sync/sheet_schemas.py` — per-sheet diff descriptors (Tasks, Recurrences, Commitments, Raw Data).
  - New module `adminme/daemons/xlsx_sync/reverse.py` — watchdog + lock + emit pipeline.
  - 5s undo window for deletions per BUILD.md §3.11 line 1012; cancellation on row reappearance verified.
  - Sensitivity preserved on emitted updates per [§6.10] — daemon looks up source-projection sensitivity at emit time.
  - Lock contention skip per BUILD.md §3.11 line 1001 + line 1047 — emits `xlsx.reverse_skipped_during_forward` and abandons cycle when forward lock held >10s.
  - ≥30 new unit tests + 1 integration round-trip test + smoke script.
  - Ruff clean, mypy clean, `bash scripts/verify_invariants.sh` clean.
- **Carry-forward for prompt 07.5** (checkpoint, post-07c merge per UT-1):
  - All 11 projections live; sidecar JSON is the bidirectional contract between forward + reverse.
  - The xlsx round-trip test exercises forward → human edit → reverse → forward stability.
  - 07.5 audit should verify: every bidirectional sheet's `DERIVED_COLUMNS` (in the forward sheet builder) matches `always_derived` (in the reverse descriptor); a divergence between the two would silently break round-trip.
- **Carry-forward for prompt 08**:
  - Reverse daemon emits domain events through `EventLog.append()` directly, NOT via guardedWrite. Prompt 08 will decide whether reverse-emitted events route through guardedWrite (they probably should — a hostile xlsx file editor is a vector). For 07c we leave the seam; 08's session/scope/governance pass closes it.
- **Carry-forward for prompt 16 (bootstrap)**:
  - Daemon lifecycle (start-on-boot via launchd; stop-on-shutdown) is a bootstrap concern. The daemon's `start()` / `stop()` methods are idempotent; bootstrap §8 will wire them.
  - Real sheet-protection password ([UT-4]) still placeholder; resolves in 16.
- **Carry-forward for future prompts (money_flow.recategorized, etc.)**:
  - Edits to `assigned_category` / `notes` / `memo` on existing money_flow rows are currently dropped with INFO log; when those event types register, update `sheet_schemas.py` Raw Data descriptor to set `updates_emit_event` accordingly.
```

#### Push + open PR

Per slim preamble (gh CLI fallback to MCP). PR description includes:
- Two new system events landed.
- verify_invariants.sh extended.
- Three stub files deleted (PM-10).
- Forward daemon extended with sidecar write (UT-6 resolution).
- Reverse daemon shipped; round-trip closed.
- Single-purpose PR per phase-07c prompt.

```bash
git add tests/integration/test_xlsx_roundtrip.py \
        scripts/demo_xlsx_roundtrip.py \
        docs/build_log.md
git commit -m "phase 07c-4: integration round-trip + smoke + BUILD_LOG"

git log --oneline | head -6
git push origin HEAD

# Try gh; MCP fallback per slim preamble.
gh pr create \
  --base main \
  --head $(git branch --show-current) \
  --title "Phase 07c: xlsx_workbooks reverse daemon + forward sidecar" \
  --body "$(cat <<'EOF'
xlsx reverse daemon — closes the round-trip opened by 07b. Daemon watches both workbooks via watchdog, diffs against sidecar JSON, emits domain events for principal edits, drops derived/Plaid-authoritative/id-column edits, holds deletions in a 5s undo window.

**Landed:**
- `adminme/daemons/xlsx_sync/reverse.py` — watchdog + lock + emit pipeline (new package: `adminme/daemons/`)
- `adminme/daemons/xlsx_sync/diff.py` — pure-functional sheet-diff core
- `adminme/daemons/xlsx_sync/sheet_schemas.py` — per-sheet diff descriptors
- `adminme/projections/xlsx_workbooks/sidecar.py` — sidecar JSON I/O
- Forward daemon (`adminme/projections/xlsx_workbooks/__init__.py`) extended to write sidecar JSON inside the same lock as each xlsx write
- 2 new system events at v1: `xlsx.reverse_projected`, `xlsx.reverse_skipped_during_forward`
- `scripts/verify_invariants.sh` extended `ALLOWED_EMITS`
- 3 dead stubs deleted (PM-10): `adminme/projections/xlsx_workbooks/{forward,reverse,schemas}.py`
- ~30 new unit tests + 1 integration round-trip test

**Architectural notes:**
- Reverse daemon is L1-adjacent (an adapter), not a projection per [§2.2]. Lives in `adminme/daemons/`. Emits domain events on principal authority + 2 system events for observability.
- Sidecar JSON resolves UT-6: forward writes after each regeneration; reverse rewrites at cycle end. Sibling directory to workbooks (`<instance_dir>/projections/.xlsx-state/`) so watchdog observers don't self-trigger.
- Sensitivity preserved on emitted updates per [§6.10].

**Out of scope (per prompt):**
- Daemon lifecycle (start-on-boot, stop-on-shutdown) → prompt 16 bootstrap.
- Session/guardedWrite around reverse-emitted events → prompt 08.
- `money_flow.recategorized` and similar field-edit events → future prompt; current behavior drops with INFO log.

**Carry-forward for 07.5 checkpoint:** every bidirectional sheet's `DERIVED_COLUMNS` must match the reverse descriptor's `always_derived`. Audit checkpoint should grep both and assert equality.

Single-purpose PR per phase-07c prompt. No sidecar fixes.
EOF
)"
```

MCP fallback per slim preamble.

---

## Stop

Per slim preamble: post-PR, ONE round of `mcp__github__pull_request_read` (`get_status`, `get_reviews`, `get_comments`); report; STOP.

**Stop template:**

```
xlsx round-trip closed. Forward → human edit → reverse → forward stability verified.

Branch: <harness-assigned>
PR: <URL>
Commits: phase 07c-1 through phase 07c-4 on top of main.

Verification summary:
- ruff / mypy: clean
- prompt 03-07b tests: <N> passed, 0 failed
- prompt 07c unit tests: sidecar <N>, forward-writes-sidecar <N>, diff <N>, reverse-basic <N>, reverse-lock-contention <N>, reverse-finance <N>, reverse-cold-start <N> (total ≥30)
- integration: xlsx round-trip + 07b + 07a + 06 all passing
- canaries: instance-path PASSING, identity SKIPPED
- full suite: <N> passed, 1 skipped
- bash scripts/verify_invariants.sh: clean (ALLOWED_EMITS extended)
- smokes: clean

2 new event types at v1 (system events):
- xlsx.reverse_projected
- xlsx.reverse_skipped_during_forward

3 dead stub files deleted (PM-10 disposition):
- adminme/projections/xlsx_workbooks/forward.py
- adminme/projections/xlsx_workbooks/reverse.py
- adminme/projections/xlsx_workbooks/schemas.py

UT-6 resolution: forward writes sidecar inside lock; reverse rewrites at cycle end.

BUILD_LOG appended in Commit 4.

Post-PR status check: <CI result>, <reviews result>, <comments result>

Ready for prompt 07.5 (projection consistency checkpoint, per UT-1) once this branch merges.
```

Then STOP. Do not poll. Do not respond to webhook events arriving after the stop message. Do not merge yourself.
