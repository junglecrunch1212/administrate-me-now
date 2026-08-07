"""
XlsxReverseDaemon — the reverse xlsx daemon (07c-β, UT-7 closed in 08b).

Per ADMINISTRATEME_BUILD.md §3.11 (lines 993–1080) and SYSTEM_INVARIANTS.md
§§2.2 / 6 / 10 / 13. The daemon is L1-adjacent (NOT a projection): it
watches the workbook on disk via watchdog, debounces a flush window,
acquires the same per-workbook advisory lock the forward daemon uses,
diffs the live workbook against the per-sheet sidecar baseline, and emits
domain events on principal authority.

UT-7 closure (08b): the daemon now constructs a Session via
``build_session_from_xlsx_reverse_daemon`` per cycle and passes it to
``_append``. ``actor_identity`` is derived from the session's
``auth_member_id`` — a detected principal becomes their own attribution; a
no-detection cycle uses the device-role placeholder ``"xlsx_reverse"``.
Each emit additionally routes through ``guarded_write.check`` (when a
``GuardedWrite`` is wired in) so the three-layer write check applies to
xlsx-originated events identically to console-originated ones [§6.5-6.8].

Per-cycle algorithm:
1. Wait ``flush_wait_s`` for writers to flush.
2. Acquire forward lock with ``forward_lock_timeout_s``. On TimeoutError,
   emit ``xlsx.reverse_skipped_during_forward`` and return — skip is the
   cycle terminus, no ``xlsx.reverse_projected`` follows.
3. Open the workbook (data_only=True).
4. For each bidirectional sheet: load rows, read sidecar, diff if sidecar
   exists, dispatch added/updated/deleted to per-sheet emit helpers.
   Cold-start sheets (no sidecar) emit nothing this cycle.
5. For each read-only sheet: hash live rows; WARN if hash drifted.
6. Rewrite sidecar JSON to current state. A Finance workbook that fails its
   save gate retains the last accepted planning sidecars so a corrected save
   is diffed against accepted truth rather than the rejected file.
7. Release the lock.
8. Emit ``xlsx.reverse_projected`` with ``events_emitted`` (envelope ids),
   ``sheets_affected`` (sheets that produced ≥1 event or had a non-empty
   diff), and ``duration_ms``.

Undo window: deletes on sheets with ``deletes_use_undo_window=True`` queue
an asyncio.Task that sleeps ``delete_undo_window_s`` then emits the delete
event. A subsequent cycle observing the row return cancels the task. Note:
delete events fired off the undo-window task are NOT included in the
``events_emitted`` of any ``xlsx.reverse_projected`` payload — they emit
after the originating cycle's terminal emit. Acceptable observability gap
documented in BUILD.md §3.11.

Watchdog→asyncio bridge: ``watchdog.observers.Observer`` callbacks run on
watchdog's thread. The daemon hops to its asyncio loop via
``loop.call_soon_threadsafe(self._schedule_cycle, workbook_name)``; the
on-loop ``_schedule_cycle`` debounces (``flush_wait_s`` sleep, cancellable
on subsequent file events) and runs the cycle. Per-workbook serialization
is enforced by an internal ``asyncio.Lock``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import time
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal

import sqlcipher3
from openpyxl import load_workbook

from adminme.daemons.xlsx_sync.diff import DiffResult, diff_sheet
from adminme.daemons.xlsx_sync.sheet_schemas import (
    SheetDescriptor,
    descriptor_for,
)
from adminme.events.envelope import EventEnvelope
from adminme.events.writer import EventSink
from adminme.lib.governance import GuardedWrite
from adminme.lib.instance_config import InstanceConfig
from adminme.lib.session import (
    Session,
    build_session_from_xlsx_reverse_daemon,
)
from adminme.projections.xlsx_workbooks import (
    FINANCE_WORKBOOK_NAME,
    OPS_WORKBOOK_NAME,
)
from adminme.projections.xlsx_workbooks.lockfile import acquire_workbook_lock
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sidecar import (
    hash_readonly_sheet,
    read_readonly_state,
    read_sheet_state,
    write_readonly_state,
    write_sheet_state,
)

_log = logging.getLogger(__name__)

MAX_FINANCE_RAW_DATA_ROWS = 5_000

_BIDIRECTIONAL_SHEETS: dict[str, tuple[str, ...]] = {
    OPS_WORKBOOK_NAME: ("Tasks", "Recurrences", "Commitments"),
    FINANCE_WORKBOOK_NAME: (
        "Assumptions",
        "Budget vs Actual",
        "Scenarios",
        "Goals",
        "Raw Data",
    ),
}
_READONLY_SHEETS: dict[str, tuple[str, ...]] = {
    OPS_WORKBOOK_NAME: ("People", "Metadata"),
    FINANCE_WORKBOOK_NAME: (
        "Dashboard",
        "Cash Flow Plan",
        "Balance Sheet",
        "Accounts",
        "Checks",
        "Metadata",
        "Instructions",
    ),
}

_SensitivityLiteral = Literal["normal", "sensitive", "privileged"]


def _mint_id(prefix: str) -> str:
    return f"{prefix}{secrets.token_hex(4)}"


def _sensitivity_literal(value: str) -> _SensitivityLiteral:
    """Coerce a stored projection sensitivity to the envelope literal type.

    Defends against legacy or corrupt rows: anything outside the allowed
    triple falls back to ``'normal'``.
    """
    if value in ("normal", "sensitive", "privileged"):
        return value  # type: ignore[return-value]
    return "normal"


class XlsxReverseDaemon:
    """Reverse xlsx daemon. See module docstring for the per-cycle algorithm."""

    def __init__(
        self,
        config: InstanceConfig,
        query_context: XlsxQueryContext,
        *,
        event_sink: EventSink,
        flush_wait_s: float = 2.0,
        forward_lock_timeout_s: float = 10.0,
        delete_undo_window_s: float = 5.0,
        guarded_write: GuardedWrite | None = None,
        principal_member_id_resolver: Any = None,
    ) -> None:
        self._config = config
        self._ctx = query_context
        self._events = event_sink
        self._flush_wait_s = flush_wait_s
        self._forward_lock_timeout_s = forward_lock_timeout_s
        self._delete_undo_window_s = delete_undo_window_s
        # UT-7 closure (08b): the daemon may be wired with a GuardedWrite
        # so xlsx-originated emits route through the same three-layer
        # check as console writes. Older callers (and the basic 07c-β
        # test rig) construct without it; the daemon falls back to direct
        # appends in that case.
        self._guarded_write = guarded_write
        # Optional resolver maps a workbook name to a detected principal
        # member_id. None ⇒ system-internal device-role attribution.
        self._principal_member_id_resolver = principal_member_id_resolver

        self._observer: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._cycle_locks: dict[str, asyncio.Lock] = {}
        self._debounce_tasks: dict[str, asyncio.Task[None]] = {}

        # (sheet, row_id) -> Task scheduled to emit the delete after the
        # undo window elapses.
        self._pending_deletes: dict[tuple[str, str], asyncio.Task[None]] = {}

        # Test instrumentation: counter of currently-running cycles per
        # workbook (used by tests to assert serialization).
        self._inflight_cycles: dict[str, int] = {
            OPS_WORKBOOK_NAME: 0,
            FINANCE_WORKBOOK_NAME: 0,
        }
        self._max_concurrent_observed = 0

        self._started = False
        self._stopping = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Begin watchdog observation. Idempotent."""
        if self._started:
            return
        self._loop = asyncio.get_running_loop()

        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        workbooks_dir = self._config.xlsx_workbooks_dir
        workbooks_dir.mkdir(parents=True, exist_ok=True)

        daemon = self

        class _Handler(FileSystemEventHandler):
            def on_modified(self, event: Any) -> None:
                self._dispatch(event)

            def on_created(self, event: Any) -> None:
                self._dispatch(event)

            def _dispatch(self, event: Any) -> None:
                if event.is_directory:
                    return
                src = Path(str(event.src_path))
                name = src.name
                if name.endswith(".lock"):
                    return
                if name not in (OPS_WORKBOOK_NAME, FINANCE_WORKBOOK_NAME):
                    return
                loop = daemon._loop
                if loop is None:
                    return
                loop.call_soon_threadsafe(daemon._schedule_cycle, name)

        observer = Observer()
        observer.schedule(_Handler(), str(workbooks_dir), recursive=False)
        observer.start()
        self._observer = observer
        self._started = True

    async def stop(self) -> None:
        """Cancel pending cycles and stop the watchdog observer. Idempotent."""
        if self._stopping:
            return
        self._stopping = True
        for task in list(self._debounce_tasks.values()):
            if not task.done():
                task.cancel()
        for task in list(self._pending_deletes.values()):
            if not task.done():
                task.cancel()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        self._started = False
        self._stopping = False

    # ------------------------------------------------------------------
    # Cycle scheduling
    # ------------------------------------------------------------------
    def _schedule_cycle(self, workbook: str) -> None:
        """On the asyncio loop: cancel any pending debounce for ``workbook``
        and schedule a fresh cycle after the flush wait."""
        existing = self._debounce_tasks.get(workbook)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _wait_then_run() -> None:
            try:
                await asyncio.sleep(self._flush_wait_s)
            except asyncio.CancelledError:
                return
            try:
                await self._run_cycle_internal(workbook, waited=True)
            except Exception:
                _log.exception("xlsx_reverse: cycle failed for %s", workbook)

        loop = self._loop or asyncio.get_event_loop()
        self._debounce_tasks[workbook] = loop.create_task(_wait_then_run())

    async def run_cycle_now(self, workbook: str) -> None:
        """Bypass watchdog + flush wait; drive a full cycle. Used by tests."""
        if workbook not in _BIDIRECTIONAL_SHEETS:
            raise ValueError(f"unknown workbook: {workbook!r}")
        await self._run_cycle_internal(workbook, waited=False)

    # ------------------------------------------------------------------
    # The cycle itself
    # ------------------------------------------------------------------
    async def _run_cycle_internal(self, workbook: str, *, waited: bool) -> None:
        lock = self._cycle_locks.setdefault(workbook, asyncio.Lock())
        async with lock:
            self._inflight_cycles[workbook] += 1
            self._max_concurrent_observed = max(
                self._max_concurrent_observed,
                sum(self._inflight_cycles.values()),
            )
            try:
                await self._cycle_under_serialization(workbook, waited=waited)
            finally:
                self._inflight_cycles[workbook] -= 1

    def _session_for(self, workbook: str) -> Session:
        """Construct the per-cycle Session per UT-7 closure (08b).

        If a ``principal_member_id_resolver`` is wired and resolves to a
        non-None member id, the resulting Session attributes that
        principal as actor; otherwise the Session is the device-role
        ``"xlsx_reverse"`` placeholder. ``actor_identity`` on every
        emitted envelope downstream comes from this Session via
        ``_append``."""
        detected: str | None = None
        if self._principal_member_id_resolver is not None:
            try:
                detected = self._principal_member_id_resolver(workbook)
            except Exception:
                detected = None
        return build_session_from_xlsx_reverse_daemon(detected, self._config)

    async def _cycle_under_serialization(self, workbook: str, *, waited: bool) -> None:
        del waited  # debounce wait handled by ``_schedule_cycle``
        start_ms = int(time.time() * 1000)
        path = self._config.xlsx_workbooks_dir / workbook
        lock_path = path.with_suffix(path.suffix + ".lock")

        # UT-7 closure (08b): one Session per cycle; passed into every
        # emit so actor_identity reflects the detected principal (or the
        # device placeholder when no principal could be detected).
        session = self._session_for(workbook)

        events_emitted: list[str] = []
        sheets_affected: list[str] = []

        # Sentinel value returned by ``_under_lock`` when the lock could
        # not be acquired within the configured timeout.
        SKIP = object()

        def _under_lock() -> Any:
            diffs_for_post: list[tuple[str, DiffResult]] = []
            validation_failures: list[str] = []
            workbook_sha256: str | None = None
            try:
                cm = acquire_workbook_lock(
                    lock_path, timeout_s=self._forward_lock_timeout_s
                )
            except TimeoutError:
                return SKIP
            try:
                with cm:
                    if not path.exists():
                        return diffs_for_post, validation_failures, workbook_sha256
                    if workbook == FINANCE_WORKBOOK_NAME:
                        validation_failures = self._validate_finance_workbook(path)
                        workbook_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
                    wb = load_workbook(str(path), data_only=True)
                    try:
                        for sheet_name in _BIDIRECTIONAL_SHEETS.get(workbook, ()):
                            if sheet_name not in wb.sheetnames:
                                continue
                            ws = wb[sheet_name]
                            rows = list(ws.iter_rows(values_only=True))
                            descriptor = descriptor_for(workbook, sheet_name)
                            if descriptor is None:
                                continue
                            header_row = descriptor.header_row
                            if len(rows) < header_row:
                                current_rows: list[dict[str, Any]] = []
                            else:
                                headers = [
                                    str(h) if h is not None else ""
                                    for h in rows[header_row - 1]
                                ]
                                current_rows = [
                                    {headers[i]: raw[i] for i in range(len(headers))}
                                    for raw in rows[header_row:]
                                    if any(value is not None for value in raw)
                                ]
                            sidecar_rows = read_sheet_state(
                                self._config.xlsx_workbooks_dir,
                                workbook,
                                sheet_name,
                            )
                            if sidecar_rows is None:
                                diffs_for_post.append((sheet_name, DiffResult()))
                            else:
                                result = diff_sheet(
                                    current_rows, sidecar_rows, descriptor
                                )
                                diffs_for_post.append((sheet_name, result))
                            if not validation_failures:
                                write_sheet_state(
                                    self._config.xlsx_workbooks_dir,
                                    workbook,
                                    sheet_name,
                                    current_rows,
                                )
                        for sheet_name in _READONLY_SHEETS.get(workbook, ()):
                            if sheet_name not in wb.sheetnames:
                                continue
                            ws = wb[sheet_name]
                            raw_rows = [list(r) for r in ws.iter_rows(values_only=True)]
                            live_hash = hash_readonly_sheet(raw_rows)
                            prior_hash = read_readonly_state(
                                self._config.xlsx_workbooks_dir,
                                workbook,
                                sheet_name,
                            )
                            if prior_hash is not None and prior_hash != live_hash:
                                _log.warning(
                                    "xlsx_reverse: read-only sheet %s/%s "
                                    "hash drifted (principal edited a "
                                    "read-only sheet); no domain event emitted",
                                    workbook,
                                    sheet_name,
                                )
                            write_readonly_state(
                                self._config.xlsx_workbooks_dir,
                                workbook,
                                sheet_name,
                                raw_rows,
                            )
                    finally:
                        wb.close()
            except TimeoutError:
                return SKIP
            return diffs_for_post, validation_failures, workbook_sha256

        result_obj = await asyncio.to_thread(_under_lock)
        if result_obj is SKIP:
            await self._emit_skip(workbook, session)
            return
        diffs, finance_failures, finance_sha256 = result_obj
        has_finance_changes = workbook == FINANCE_WORKBOOK_NAME and any(
            diff_result.added or diff_result.updated or diff_result.deleted
            for _sheet_name, diff_result in diffs
        )

        # Cancel pending undo-window deletes whose row reappeared this cycle.
        for sheet_name, diff_result in diffs:
            descriptor = descriptor_for(workbook, sheet_name)
            if descriptor is None or not descriptor.deletes_use_undo_window:
                continue
            current_ids = {
                str(r.get(descriptor.id_column))
                for r in diff_result.added
                if r.get(descriptor.id_column)
            } | {
                str(r.get(descriptor.id_column))
                for r, _changes in diff_result.updated
                if r.get(descriptor.id_column)
            }
            for rid in current_ids:
                self.cancel_pending_delete(sheet_name, rid)

        # Emit domain events from observed diffs (lock released).
        for sheet_name, diff_result in diffs:
            descriptor = descriptor_for(workbook, sheet_name)
            if descriptor is None:
                continue
            if not (diff_result.added or diff_result.updated or diff_result.deleted):
                continue
            if finance_failures:
                sheets_affected.append(sheet_name)
                continue
            sheet_event_ids = await self._emit_diff(
                workbook, descriptor, diff_result, session
            )
            events_emitted.extend(sheet_event_ids)
            if (
                sheet_event_ids
                or diff_result.added
                or diff_result.updated
                or diff_result.deleted
            ):
                sheets_affected.append(sheet_name)

        if has_finance_changes and finance_sha256 is not None:
            published = await self._append(
                session=session,
                event_type="finance.workbook_published",
                payload={
                    "workbook_name": FINANCE_WORKBOOK_NAME,
                    "workbook_sha256": finance_sha256,
                    "workbook_schema_version": 2,
                    "published_at": EventEnvelope.now_utc_iso(),
                    "published_by_party_id": session.auth_member_id,
                    "validation_state": "rejected" if finance_failures else "valid",
                    "validation_failures": finance_failures,
                },
                sensitivity="sensitive",
            )
            if published is not None:
                events_emitted.append(published)

        # Step 8 (continued): emit xlsx.reverse_projected.
        # The terminal cycle event is system observability and stays
        # under the device-role placeholder identity per [§13]: detected
        # principal authorship attaches to the domain events emitted
        # within the cycle, not to the cycle's terminal signal.
        duration_ms = max(0, int(time.time() * 1000) - start_ms)
        await self._events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=self._config.tenant_id,
                type="xlsx.reverse_projected",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="xlsx_reverse",
                source_account_id="daemon",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="normal",
                actor_identity="xlsx_reverse",
                payload={
                    "workbook_name": workbook,
                    "detected_at": EventEnvelope.now_utc_iso(),
                    "sheets_affected": sheets_affected,
                    "events_emitted": events_emitted,
                    "duration_ms": duration_ms,
                },
            )
        )

    async def _emit_skip(self, workbook: str, session: Session) -> None:
        # session is accepted for symmetry with other emit pathways but
        # the skip signal is system observability — actor_identity stays
        # the device-role placeholder per the same rationale as
        # xlsx.reverse_projected above.
        del session
        await self._events.write(
            EventEnvelope(
                event_at_ms=int(time.time() * 1000),
                tenant_id=self._config.tenant_id,
                type="xlsx.reverse_skipped_during_forward",
                schema_version=1,
                occurred_at=EventEnvelope.now_utc_iso(),
                source_adapter="xlsx_reverse",
                source_account_id="daemon",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                sensitivity="normal",
                actor_identity="xlsx_reverse",
                payload={
                    "workbook_name": workbook,
                    "detected_at": EventEnvelope.now_utc_iso(),
                    "skip_reason": "forward_lock_held",
                },
            )
        )

    # ------------------------------------------------------------------
    # Per-sheet emit dispatch
    # ------------------------------------------------------------------
    async def _emit_diff(
        self,
        workbook: str,
        descriptor: SheetDescriptor,
        result: DiffResult,
        session: Session,
    ) -> list[str]:
        emitted: list[str] = []
        sheet = descriptor.sheet
        if sheet == "Tasks":
            emitted.extend(await self._emit_tasks(result, session))
        elif sheet == "Commitments":
            emitted.extend(await self._emit_commitments(result, session))
        elif sheet == "Recurrences":
            emitted.extend(await self._emit_recurrences(result, session))
        elif sheet == "Raw Data":
            emitted.extend(await self._emit_raw_data(result, session))
        elif sheet == "Assumptions":
            emitted.extend(await self._emit_assumptions(result, session))
        elif sheet == "Budget vs Actual":
            emitted.extend(await self._emit_budgets(result, session))
        elif sheet == "Scenarios":
            emitted.extend(await self._emit_scenarios(result, session))
        elif sheet == "Goals":
            emitted.extend(await self._emit_goals(result, session))
        else:
            _log.info(
                "[reverse] no emit pathway for sheet %s/%s; "
                "%d adds, %d updates, %d deletes ignored",
                workbook,
                sheet,
                len(result.added),
                len(result.updated),
                len(result.deleted),
            )
        return emitted

    # ------------------------------------------------------------------
    # Tasks pathway
    # ------------------------------------------------------------------
    async def _emit_tasks(self, result: DiffResult, session: Session) -> list[str]:
        emitted: list[str] = []
        for row in result.added:
            task_id = row.get("task_id") or ""
            if task_id == "" or task_id is None:
                task_id = _mint_id("tsk_")
            payload: dict[str, Any] = {
                "task_id": task_id,
                "title": row.get("title") or "",
            }
            notes = row.get("notes")
            if notes:
                payload["description"] = notes
            assigned = row.get("assigned_member")
            if assigned:
                payload["owner_member_id"] = assigned
            due = row.get("due_date")
            if due:
                payload["due"] = self._stringify_date(due)
            energy = row.get("energy")
            if energy in ("low", "medium", "high"):
                payload["energy"] = energy
            if not payload["title"]:
                _log.info("[reverse] tasks ADD with blank title; skipping")
                continue
            event_id = await self._append(
                session=session,
                event_type="task.created",
                payload=payload,
                sensitivity="normal",
            )
            if event_id is not None:
                emitted.append(event_id)

        for row, changes in result.updated:
            task_id = row.get("task_id")
            if not task_id:
                continue
            field_updates = {col: cv for col, (_sv, cv) in changes.items()}
            payload = {
                "task_id": task_id,
                "updated_at": EventEnvelope.now_utc_iso(),
                "updated_by_party_id": session.auth_member_id,
                "field_updates": field_updates,
            }
            sensitivity = self._lookup_sensitivity(
                self._ctx.tasks_conn, "tasks", "task_id", task_id
            )
            event_id = await self._append(
                session=session,
                event_type="task.updated",
                payload=payload,
                sensitivity=sensitivity,
            )
            if event_id is not None:
                emitted.append(event_id)

        for row in result.deleted:
            task_id = row.get("task_id")
            if not task_id:
                continue
            self._schedule_undo_delete(
                sheet="Tasks",
                row_id=str(task_id),
                emit_factory=lambda tid=str(task_id): self._emit_task_deleted(
                    tid, session
                ),
            )
        return emitted

    async def _emit_task_deleted(self, task_id: str, session: Session) -> None:
        sensitivity = self._lookup_sensitivity(
            self._ctx.tasks_conn, "tasks", "task_id", task_id
        )
        await self._append(
            session=session,
            event_type="task.deleted",
            payload={
                "task_id": task_id,
                "deleted_at": EventEnvelope.now_utc_iso(),
                "deleted_by_party_id": session.auth_member_id,
            },
            sensitivity=sensitivity,
        )

    # ------------------------------------------------------------------
    # Commitments pathway
    # ------------------------------------------------------------------
    async def _emit_commitments(
        self, result: DiffResult, session: Session
    ) -> list[str]:
        emitted: list[str] = []
        if result.added:
            _log.info(
                "[reverse] commitments are pipeline-proposed only per [§4.2]; "
                "dropping %d added rows",
                len(result.added),
            )
        for row, changes in result.updated:
            commitment_id = row.get("commitment_id")
            if not commitment_id:
                continue
            field_updates = {col: cv for col, (_sv, cv) in changes.items()}
            sensitivity = self._lookup_sensitivity(
                self._ctx.commitments_conn,
                "commitments",
                "commitment_id",
                commitment_id,
            )
            event_id = await self._append(
                session=session,
                event_type="commitment.edited",
                payload={
                    "commitment_id": commitment_id,
                    "edited_at": EventEnvelope.now_utc_iso(),
                    "edited_by_party_id": session.auth_member_id,
                    "field_updates": field_updates,
                },
                sensitivity=sensitivity,
            )
            if event_id is not None:
                emitted.append(event_id)
        if result.deleted:
            _log.info(
                "[reverse] commitments cancel via API; dropping %d row deletions",
                len(result.deleted),
            )
        return emitted

    # ------------------------------------------------------------------
    # Recurrences pathway
    # ------------------------------------------------------------------
    async def _emit_recurrences(
        self, result: DiffResult, session: Session
    ) -> list[str]:
        emitted: list[str] = []
        for row in result.added:
            recurrence_id = row.get("recurrence_id") or ""
            if recurrence_id == "" or recurrence_id is None:
                recurrence_id = _mint_id("rec_")
            cadence = row.get("cadence") or "weekly"
            kind = row.get("title") or "recurrence"
            next_due = row.get("next_due")
            if next_due:
                next_occurrence = self._stringify_date(next_due)
            else:
                next_occurrence = EventEnvelope.now_utc_iso().split("T")[0]
            payload: dict[str, Any] = {
                "recurrence_id": recurrence_id,
                "linked_kind": "household",
                "linked_id": "household",
                "kind": kind,
                "rrule": cadence,
                "next_occurrence": next_occurrence,
            }
            notes = row.get("notes")
            if notes:
                payload["notes"] = notes
            event_id = await self._append(
                session=session,
                event_type="recurrence.added",
                payload=payload,
                sensitivity="normal",
            )
            if event_id is not None:
                emitted.append(event_id)

        for row, changes in result.updated:
            recurrence_id = row.get("recurrence_id")
            if not recurrence_id:
                continue
            field_updates = {col: cv for col, (_sv, cv) in changes.items()}
            sensitivity = self._lookup_sensitivity(
                self._ctx.recurrences_conn,
                "recurrences",
                "recurrence_id",
                recurrence_id,
            )
            event_id = await self._append(
                session=session,
                event_type="recurrence.updated",
                payload={
                    "recurrence_id": recurrence_id,
                    "updated_at": EventEnvelope.now_utc_iso(),
                    "field_updates": field_updates,
                },
                sensitivity=sensitivity,
            )
            if event_id is not None:
                emitted.append(event_id)

        if result.deleted:
            _log.info(
                "[reverse] recurrences not deletable in v1 per descriptor; "
                "dropping %d row deletions",
                len(result.deleted),
            )
        return emitted

    # ------------------------------------------------------------------
    # Raw Data pathway
    # ------------------------------------------------------------------
    async def _emit_raw_data(self, result: DiffResult, session: Session) -> list[str]:
        emitted: list[str] = []
        for row in result.added:
            raw_manual = row.get("is_manual")
            if raw_manual in (None, "") and not row.get("txn_id"):
                # A new protected workbook row cannot author is_manual or its
                # backend ID. That exact shape is the manual-add affordance.
                is_manual = True
            else:
                try:
                    is_manual = self._bool_cell(raw_manual)
                except ValueError:
                    _log.warning("[reverse] invalid is_manual value; rejecting row")
                    continue
            if not is_manual:
                _log.warning(
                    "[reverse] non-manual row added via xlsx; rejecting per [§13.4]"
                )
                continue
            flow_id = row.get("txn_id") or ""
            if flow_id == "" or flow_id is None:
                flow_id = _mint_id("flow_")
            try:
                amount_minor = self._money_minor(row.get("amount"))
            except (TypeError, ValueError):
                _log.warning("[reverse] manual row amount unparseable; skipping")
                continue
            occurred = row.get("date")
            occurred_at = (
                self._stringify_date(occurred)
                if occurred
                else EventEnvelope.now_utc_iso()
            )
            payload: dict[str, Any] = {
                "flow_id": flow_id,
                "amount_minor": amount_minor,
                "currency": str(row.get("currency") or "USD").upper(),
                "occurred_at": occurred_at,
                "kind": "paid" if amount_minor >= 0 else "received",
                "added_by_party_id": session.auth_member_id,
            }
            payload["amount_minor"] = abs(amount_minor)
            assigned = row.get("assigned_category")
            if assigned:
                payload["category"] = assigned
            notes = row.get("notes")
            if notes:
                payload["notes"] = notes
            event_id = await self._append(
                session=session,
                event_type="money_flow.manually_added",
                payload=payload,
                sensitivity="sensitive",
            )
            if event_id is not None:
                emitted.append(event_id)
                enrichment = {
                    key: value
                    for key, value in {
                        "linked_account": row.get("account_id"),
                        "merchant_name": row.get("merchant_name"),
                        "merchant_category": row.get("merchant_category"),
                        "memo": row.get("memo"),
                        "included": row.get("included"),
                    }.items()
                    if value not in (None, "")
                }
                if enrichment:
                    enrichment.update(
                        {
                            "flow_id": str(flow_id),
                            "updated_at": EventEnvelope.now_utc_iso(),
                            "updated_by_party_id": session.auth_member_id,
                        }
                    )
                    enrichment_id = await self._append(
                        session=session,
                        event_type="money_flow.manually_updated",
                        payload=enrichment,
                        sensitivity="sensitive",
                    )
                    if enrichment_id is not None:
                        emitted.append(enrichment_id)

        for row, changes in result.updated:
            flow_id = row.get("txn_id")
            if not flow_id:
                continue
            try:
                is_manual = self._bool_cell(row.get("is_manual"))
            except ValueError:
                _log.warning("[reverse] invalid is_manual value; rejecting update")
                continue
            if is_manual:
                update_payload: dict[str, Any] = {
                    "flow_id": str(flow_id),
                    "updated_at": EventEnvelope.now_utc_iso(),
                    "updated_by_party_id": session.auth_member_id,
                }
                changed = set(changes)
                if "amount" in changed:
                    try:
                        amount_minor = self._money_minor(row.get("amount"))
                    except (TypeError, ValueError):
                        continue
                    update_payload["amount_minor"] = abs(amount_minor)
                    update_payload["kind"] = "paid" if amount_minor >= 0 else "received"
                mappings = {
                    "date": "occurred_at",
                    "currency": "currency",
                    "account_id": "linked_account",
                    "merchant_name": "merchant_name",
                    "merchant_category": "merchant_category",
                    "assigned_category": "category",
                    "memo": "memo",
                    "notes": "notes",
                    "included": "included",
                }
                for column, target in mappings.items():
                    if column in changed and row.get(column) is not None:
                        value = row[column]
                        update_payload[target] = (
                            self._stringify_date(value) if column == "date" else value
                        )
                event_type = "money_flow.manually_updated"
            else:
                update_payload = {
                    "flow_id": str(flow_id),
                    "updated_at": EventEnvelope.now_utc_iso(),
                    "updated_by_party_id": session.auth_member_id,
                }
                for column in ("assigned_category", "memo", "notes", "included"):
                    if column in changes and row.get(column) is not None:
                        target = "category" if column == "assigned_category" else column
                        update_payload[target] = row[column]
                if len(update_payload) == 3:
                    continue
                event_type = "money_flow.classification_set"
            event_id = await self._append(
                session=session,
                event_type=event_type,
                payload=update_payload,
                sensitivity="sensitive",
            )
            if event_id is not None:
                emitted.append(event_id)

        for row in result.deleted:
            try:
                is_manual = self._bool_cell(row.get("is_manual"))
            except ValueError:
                _log.warning("[reverse] invalid is_manual value; rejecting deletion")
                continue
            if not is_manual:
                _log.warning("[reverse] Plaid row deletion via xlsx ignored")
                continue
            flow_id = row.get("txn_id")
            if not flow_id:
                continue
            self._schedule_undo_delete(
                sheet="Raw Data",
                row_id=str(flow_id),
                emit_factory=lambda fid=str(flow_id): self._emit_money_flow_deleted(
                    fid, session
                ),
            )
        return emitted

    async def _emit_assumptions(
        self, result: DiffResult, session: Session
    ) -> list[str]:
        emitted: list[str] = []
        for row in [*result.added, *(value for value, _changes in result.updated)]:
            assumption_id = row.get("assumption_id") or _mint_id("assumption_")
            if not row.get("category") or not row.get("label"):
                _log.warning("[reverse] incomplete Assumptions row; skipping")
                continue
            value_cell = row.get("value")
            try:
                if value_cell is None:
                    raise ValueError("missing value")
                value = str(float(value_cell))
            except (TypeError, ValueError):
                _log.warning("[reverse] invalid Assumptions value; skipping")
                continue
            event_id = await self._append(
                session=session,
                event_type="finance.assumption_set",
                payload={
                    "assumption_id": str(assumption_id),
                    "category": str(row["category"]),
                    "label": str(row["label"]),
                    "value_decimal": value,
                    "unit": str(row.get("unit") or "USD/month"),
                    "notes": str(row["notes"]) if row.get("notes") else None,
                    "as_of": self._stringify_date(
                        row.get("as_of") or EventEnvelope.now_utc_iso()
                    ),
                },
                sensitivity="sensitive",
            )
            if event_id is not None:
                emitted.append(event_id)
        return emitted

    async def _emit_budgets(self, result: DiffResult, session: Session) -> list[str]:
        emitted: list[str] = []
        for row in [*result.added, *(value for value, _changes in result.updated)]:
            budget_id = row.get("budget_id") or _mint_id("budget_")
            if not row.get("category"):
                _log.warning("[reverse] Budget row missing category; skipping")
                continue
            try:
                amount_minor = abs(self._money_minor(row.get("monthly_budget")))
            except (TypeError, ValueError):
                _log.warning("[reverse] invalid Budget amount; skipping")
                continue
            event_id = await self._append(
                session=session,
                event_type="finance.budget_set",
                payload={
                    "budget_id": str(budget_id),
                    "category": str(row["category"]),
                    "amount_minor": amount_minor,
                    "currency": "USD",
                    "cadence": "monthly",
                    "active": self._bool_cell(row.get("active"), default=True),
                    "notes": str(row["notes"]) if row.get("notes") else None,
                    "as_of": EventEnvelope.now_utc_iso(),
                },
                sensitivity="sensitive",
            )
            if event_id is not None:
                emitted.append(event_id)
        return emitted

    async def _emit_scenarios(self, result: DiffResult, session: Session) -> list[str]:
        emitted: list[str] = []
        for row in [*result.added, *(value for value, _changes in result.updated)]:
            scenario_id = row.get("scenario_id") or _mint_id("scenario_")
            if not row.get("name"):
                _log.warning("[reverse] Scenario row missing name; skipping")
                continue
            try:
                payload = {
                    "scenario_id": str(scenario_id),
                    "name": str(row["name"]),
                    "is_baseline": self._bool_cell(row.get("is_baseline")),
                    "horizon_months": int(row.get("horizon_months") or 60),
                    "opening_cash_delta_minor": self._money_minor(
                        row.get("opening_cash_delta")
                    ),
                    "monthly_income_delta_minor": self._money_minor(
                        row.get("monthly_income_delta")
                    ),
                    "monthly_expense_delta_minor": self._money_minor(
                        row.get("monthly_expense_delta")
                    ),
                    "one_time_month": int(row["one_time_month"])
                    if row.get("one_time_month") is not None
                    else None,
                    "one_time_amount_minor": self._money_minor(
                        row.get("one_time_amount")
                    ),
                    "notes": str(row["notes"]) if row.get("notes") else None,
                    "active": self._bool_cell(row.get("active"), default=True),
                }
            except (TypeError, ValueError):
                _log.warning("[reverse] Scenario numeric value invalid; skipping")
                continue
            event_id = await self._append(
                session=session,
                event_type="finance.scenario_set",
                payload=payload,
                sensitivity="sensitive",
            )
            if event_id is not None:
                emitted.append(event_id)
        return emitted

    async def _emit_goals(self, result: DiffResult, session: Session) -> list[str]:
        emitted: list[str] = []
        for row in [*result.added, *(value for value, _changes in result.updated)]:
            goal_id = row.get("goal_id") or _mint_id("goal_")
            if not row.get("name"):
                _log.warning("[reverse] Goal row missing name; skipping")
                continue
            try:
                target_minor = abs(self._money_minor(row.get("target_amount")))
                current_minor = abs(self._money_minor(row.get("current_amount")))
                priority = int(row.get("priority") or 100)
            except (TypeError, ValueError):
                _log.warning("[reverse] Goal numeric value invalid; skipping")
                continue
            event_id = await self._append(
                session=session,
                event_type="finance.goal_set",
                payload={
                    "goal_id": str(goal_id),
                    "name": str(row["name"]),
                    "target_minor": target_minor,
                    "current_minor": current_minor,
                    "target_date": self._stringify_date(row["target_date"])
                    if row.get("target_date")
                    else None,
                    "priority": priority,
                    "status": str(row.get("status") or "active"),
                    "notes": str(row["notes"]) if row.get("notes") else None,
                },
                sensitivity="sensitive",
            )
            if event_id is not None:
                emitted.append(event_id)
        return emitted

    async def _emit_money_flow_deleted(self, flow_id: str, session: Session) -> None:
        sensitivity = self._lookup_sensitivity(
            self._ctx.money_conn, "money_flows", "flow_id", flow_id
        )
        await self._append(
            session=session,
            event_type="money_flow.manually_deleted",
            payload={
                "flow_id": flow_id,
                "deleted_at": EventEnvelope.now_utc_iso(),
                "deleted_by_party_id": session.auth_member_id,
            },
            sensitivity=sensitivity,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _schedule_undo_delete(
        self,
        *,
        sheet: str,
        row_id: str,
        emit_factory: Any,
    ) -> None:
        """Queue the delete to fire after ``delete_undo_window_s``. If the
        same (sheet, row_id) is observed back in a subsequent cycle, the
        scheduled task is cancelled."""
        key = (sheet, row_id)
        existing = self._pending_deletes.get(key)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _wait_then_emit() -> None:
            try:
                await asyncio.sleep(self._delete_undo_window_s)
            except asyncio.CancelledError:
                return
            try:
                await emit_factory()
            except Exception:
                _log.exception(
                    "xlsx_reverse: undo-window emit failed for %s/%s",
                    sheet,
                    row_id,
                )
            finally:
                self._pending_deletes.pop(key, None)

        loop = self._loop or asyncio.get_event_loop()
        self._pending_deletes[key] = loop.create_task(_wait_then_emit())

    def cancel_pending_delete(self, sheet: str, row_id: str) -> bool:
        """Cancel a pending undo-window delete if present. Returns True if
        a task was cancelled. Used implicitly by subsequent cycles when a
        diffed row reappears within the undo window."""
        task = self._pending_deletes.pop((sheet, row_id), None)
        if task is None:
            return False
        if not task.done():
            task.cancel()
            return True
        return False

    @staticmethod
    def _stringify_date(value: Any) -> str:
        from datetime import date, datetime as dt

        if isinstance(value, dt):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _money_minor(value: Any) -> int:
        if value is None or value == "":
            return 0
        return int(
            (XlsxReverseDaemon._decimal_cell(value) * Decimal(100)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )

    @staticmethod
    def _decimal_cell(value: Any) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("boolean is not a number")
        if not isinstance(value, (int, float, Decimal, str)):
            raise ValueError(f"not a numeric cell: {value!r}")
        text = str(value).strip()
        if not text:
            raise ValueError(f"not a numeric cell: {value!r}")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"not a numeric cell: {value!r}") from exc
        if not number.is_finite():
            raise ValueError(f"not a finite numeric cell: {value!r}")
        return number

    @staticmethod
    def _number_cell(value: Any) -> float:
        """Return a workbook scalar as a number, rejecting formula objects."""

        return float(XlsxReverseDaemon._decimal_cell(value))

    @staticmethod
    def _integer_cell(value: Any) -> int:
        """Return an exact workbook integer without silently truncating."""

        number = XlsxReverseDaemon._number_cell(value)
        if not number.is_integer():
            raise ValueError(f"not an integer cell: {value!r}")
        return int(number)

    @staticmethod
    def _bool_cell(value: Any, *, default: bool = False) -> bool:
        if value is None or value == "":
            return default
        if value is True or value == 1:
            return True
        if value is False or value == 0:
            return False
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "yes", "y", "1"}:
                return True
            if lowered in {"false", "no", "n", "0"}:
                return False
        raise ValueError(f"not a boolean cell: {value!r}")

    @staticmethod
    def _validate_finance_workbook(path: Path) -> list[str]:
        """Fail closed before reverse-projecting malformed planning rows."""

        wb = load_workbook(str(path), data_only=True, read_only=True)
        failures: list[str] = []
        try:
            if "Raw Data" in wb.sheetnames:
                populated_raw_rows = sum(
                    1
                    for row in wb["Raw Data"].iter_rows(min_row=2, values_only=True)
                    if any(value is not None for value in row)
                )
                if populated_raw_rows > MAX_FINANCE_RAW_DATA_ROWS:
                    failures.append(
                        "Raw Data: row count exceeds the reviewed household "
                        f"limit of {MAX_FINANCE_RAW_DATA_ROWS}"
                    )
            required: dict[str, tuple[str, ...]] = {
                "Assumptions": ("category", "label", "value", "unit", "as_of"),
                "Budget vs Actual": ("category", "monthly_budget", "active"),
                "Scenarios": ("name", "horizon_months", "active"),
                "Goals": ("name", "target_amount", "current_amount", "status"),
            }
            for sheet_name, fields in required.items():
                if sheet_name not in wb.sheetnames:
                    failures.append(f"missing required sheet: {sheet_name}")
                    continue
                ws = wb[sheet_name]
                headers = [cell.value for cell in ws[4]]
                index = {str(value): position for position, value in enumerate(headers)}
                for field in (str(headers[0]) if headers else "", *fields):
                    if field and field not in index:
                        failures.append(f"{sheet_name}: missing column {field}")
                id_field = str(headers[0]) if headers else ""
                seen: set[str] = set()
                for row_number, raw in enumerate(
                    ws.iter_rows(min_row=5, values_only=True), start=5
                ):
                    if not any(value is not None for value in raw):
                        continue
                    row_id = raw[index[id_field]] if id_field in index else None
                    if row_id:
                        if str(row_id) in seen:
                            failures.append(
                                f"{sheet_name} row {row_number}: duplicate {id_field}"
                            )
                        seen.add(str(row_id))
                    for field in fields:
                        position = index.get(field)
                        if position is None or raw[position] in (None, ""):
                            failures.append(
                                f"{sheet_name} row {row_number}: {field} is required"
                            )
                    try:
                        if sheet_name == "Assumptions":
                            XlsxReverseDaemon._number_cell(raw[index["value"]])
                            if str(raw[index["unit"]]) not in {
                                "USD",
                                "USD/month",
                                "USD/year",
                                "ratio",
                                "months",
                                "count",
                            }:
                                raise ValueError("unsupported unit")
                        elif sheet_name == "Budget vs Actual":
                            if (
                                XlsxReverseDaemon._number_cell(
                                    raw[index["monthly_budget"]]
                                )
                                < 0
                            ):
                                raise ValueError("budget must be non-negative")
                            XlsxReverseDaemon._bool_cell(raw[index["active"]])
                        elif sheet_name == "Scenarios":
                            horizon = XlsxReverseDaemon._integer_cell(
                                raw[index["horizon_months"]]
                            )
                            if not 1 <= horizon <= 600:
                                raise ValueError("horizon must be 1–600 months")
                            for field in (
                                "opening_cash_delta",
                                "monthly_income_delta",
                                "monthly_expense_delta",
                                "one_time_amount",
                            ):
                                position = index.get(field)
                                if position is not None:
                                    XlsxReverseDaemon._number_cell(raw[position] or 0)
                            month_position = index.get("one_time_month")
                            if month_position is not None and raw[
                                month_position
                            ] not in (
                                None,
                                "",
                            ):
                                one_time_month = XlsxReverseDaemon._integer_cell(
                                    raw[month_position]
                                )
                                if not 1 <= one_time_month <= 600:
                                    raise ValueError("one-time month must be 1–600")
                            XlsxReverseDaemon._bool_cell(raw[index["is_baseline"]])
                            XlsxReverseDaemon._bool_cell(raw[index["active"]])
                        elif sheet_name == "Goals":
                            if (
                                XlsxReverseDaemon._number_cell(
                                    raw[index["target_amount"]]
                                )
                                < 0
                                or XlsxReverseDaemon._number_cell(
                                    raw[index["current_amount"]]
                                )
                                < 0
                            ):
                                raise ValueError("goal amounts must be non-negative")
                            priority_position = index.get("priority")
                            if priority_position is not None:
                                priority = XlsxReverseDaemon._integer_cell(
                                    raw[priority_position] or 100
                                )
                                if not 0 <= priority <= 1000:
                                    raise ValueError("priority must be 0–1000")
                            if str(raw[index["status"]]) not in {
                                "active",
                                "paused",
                                "complete",
                            }:
                                raise ValueError("unsupported status")
                    except (KeyError, TypeError, ValueError) as exc:
                        failures.append(
                            f"{sheet_name} row {row_number}: invalid value ({exc})"
                        )
                if sheet_name == "Scenarios":
                    baseline_index = index.get("is_baseline")
                    active_index = index.get("active")
                    if baseline_index is not None and active_index is not None:
                        try:
                            active_rows = [
                                row
                                for row in ws.iter_rows(min_row=5, values_only=True)
                                if any(value is not None for value in row)
                                and XlsxReverseDaemon._bool_cell(
                                    row[active_index], default=True
                                )
                            ]
                            baseline_count = sum(
                                1
                                for row in active_rows
                                if XlsxReverseDaemon._bool_cell(row[baseline_index])
                            )
                            if active_rows and baseline_count != 1:
                                failures.append(
                                    "Scenarios: exactly one active baseline is required"
                                )
                        except ValueError:
                            # The row-specific validation above already records
                            # the malformed boolean; do not crash the save gate.
                            pass
        finally:
            wb.close()
        return failures[:100]

    def _lookup_sensitivity(
        self,
        conn: sqlcipher3.Connection,
        table: str,
        id_column: str,
        row_id: str,
    ) -> str:
        try:
            cur = conn.execute(
                f"SELECT sensitivity FROM {table} "
                f"WHERE tenant_id = ? AND {id_column} = ?",
                (self._config.tenant_id, row_id),
            )
            row = cur.fetchone()
        except Exception:
            return "normal"
        if row is None:
            return "normal"
        try:
            value = row["sensitivity"]
        except (KeyError, IndexError, TypeError):
            value = row[0] if isinstance(row, (tuple, list)) else None
        return str(value) if value else "normal"

    async def _append(
        self,
        *,
        session: Session,
        event_type: str,
        payload: dict[str, Any],
        sensitivity: str,
    ) -> str | None:
        """Append an envelope to the event log on the cycle's Session
        authority. Returns the event_id on success, None when the
        guarded-write check refused.

        UT-7 closure (08b): ``actor_identity`` is the cycle session's
        ``auth_member_id`` — a detected principal becomes their own
        attribution; a no-detection cycle uses the device-role placeholder.
        ``source_adapter`` stays ``"xlsx_reverse"`` per [arch §3] (the row
        schema's source_adapter is the adapter identity, not the actor).

        When a ``GuardedWrite`` is wired in, the three-layer check runs
        before the append. A refused write emits ``write.denied`` (inside
        ``GuardedWrite.check``) and the domain event is NOT appended."""
        if self._guarded_write is not None:
            result = await self._guarded_write.check(session, event_type, payload)
            if not result.pass_:
                _log.info(
                    "[reverse] guarded_write refused %s at layer %s "
                    "(reason=%s); skipping append",
                    event_type,
                    result.layer_failed,
                    result.reason,
                )
                return None
        envelope = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=self._config.tenant_id,
            type=event_type,
            schema_version=1,
            occurred_at=EventEnvelope.now_utc_iso(),
            source_adapter="xlsx_reverse",
            source_account_id="daemon",
            owner_scope="shared:household",
            visibility_scope="shared:household",
            sensitivity=_sensitivity_literal(sensitivity),
            actor_identity=session.auth_member_id,
            payload=payload,
        )
        return await self._events.write(envelope)


__all__ = ["MAX_FINANCE_RAW_DATA_ROWS", "XlsxReverseDaemon"]
