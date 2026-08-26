"""
xlsx_workbooks — forward-only xlsx projection daemon (prompt 07b).

xlsx_workbooks is structurally a projection per [§2.2]: it consumes
events and regenerates derived state. It emits exactly one event,
``xlsx.regenerated``, which is categorized as a SYSTEM event (not a
domain event). System events are observability-only and do not
represent facts about the world. This resolves the apparent tension
between BUILD.md §3.11 step 9 ("emit xlsx.regenerated") and [§2.2]
("projections never emit"). Prompt 07.5's audit relies on this
classification.

Unlike other L3 projections, this one does NOT own a SQLite backing
store: the derived state is two files on disk (``adminme-ops.xlsx`` and
``adminme-finance.xlsx``) under ``InstanceConfig.xlsx_workbooks_dir``.
``apply()`` does not write DB rows; instead it schedules a debounced
regeneration per workbook.

Sheets built in this prompt:
- adminme-ops.xlsx: Tasks, Recurrences, Commitments, People, Metadata.
- adminme-finance.xlsx: the CO-006 planning/model contract plus protected
  provider observations and provenance.

CO-006 supersedes the thin three-sheet Finance artifact with an auditable
planning workbook while preserving the two-file topology and event round trip.

Construction is different from other projections because xlsx reads
across seven projection databases. The runner's register() still works
unchanged; the bootstrap (or test fixture) constructs
XlsxWorkbooksProjection(config, query_context, event_log=log) with
connection handles gathered after the other projections have started.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventSink
from adminme.lib.instance_config import InstanceConfig
from adminme.projections.base import Projection
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext
from adminme.projections.xlsx_workbooks.sidecar import (
    write_readonly_state,
    write_sheet_state,
)

_log = logging.getLogger(__name__)

OPS_WORKBOOK_NAME = "adminme-ops.xlsx"
FINANCE_WORKBOOK_NAME = "adminme-finance.xlsx"

# Single source of truth for which sheets each workbook contains and
# whether reverse-projection treats them as bidirectional or read-only.
# Drives both the ``xlsx.regenerated`` payload's ``sheets_regenerated``
# list and the per-cycle sidecar writer.
_BIDIRECTIONAL_SHEETS: dict[str, list[str]] = {
    OPS_WORKBOOK_NAME: ["Tasks", "Recurrences", "Commitments"],
    FINANCE_WORKBOOK_NAME: [
        "Assumptions",
        "Budget vs Actual",
        "Scenarios",
        "Goals",
        "Raw Data",
    ],
}
_READONLY_SHEETS: dict[str, list[str]] = {
    OPS_WORKBOOK_NAME: ["People", "Metadata"],
    FINANCE_WORKBOOK_NAME: [
        "Dashboard",
        "Cash Flow Plan",
        "Balance Sheet",
        "Accounts",
        "Checks",
        "Metadata",
        "Instructions",
    ],
}

# adminme-ops.xlsx trigger event types (currently registered only).
_OPS_TRIGGERS: tuple[str, ...] = (
    "task.created",
    "task.updated",
    "task.completed",
    "task.deleted",
    "commitment.proposed",
    "commitment.confirmed",
    "commitment.completed",
    "commitment.dismissed",
    "commitment.edited",
    "commitment.snoozed",
    "commitment.deferred",
    "commitment.blocked",
    "commitment.unblocked",
    "commitment.cancelled",
    "commitment.delegated",
    "commitment.expired",
    "recurrence.added",
    "recurrence.updated",
    "recurrence.completed",
    "recurrence.paused",
    "recurrence.resumed",
    "recurrence.ended",
    "routine.defined",
    "routine.step_outcome_recorded",
    "routine.paused",
    "routine.resumed",
    "routine.ended",
    "party.created",
    "party.merged",
    "identifier.added",
    "membership.added",
    "relationship.added",
    "calendar.event_added",
    "calendar.event_updated",
    "calendar.event_deleted",
)

# adminme-finance.xlsx trigger event types (currently registered only).
_FINANCE_TRIGGERS: tuple[str, ...] = (
    "money_flow.recorded",
    "money_flow.manually_added",
    "money_flow.manually_deleted",
    "money_flow.manually_updated",
    "money_flow.classification_set",
    "money_flow.provider_observed",
    "money_flow.provider_removed",
    "financial.account_observed",
    "finance.assumption_set",
    "finance.budget_set",
    "finance.scenario_set",
    "finance.goal_set",
    "finance.goal_paused",
    "finance.goal_completed",
    "finance.goal_reopened",
    "finance.workbook_published",
    "money_flow.plaid_synced",
    "account.added",
    "account.updated",
    "account.archived",
    "account.restored",
    "place.added",
    "place.updated",
    "place.archived",
    "place.restored",
    "asset.added",
    "asset.updated",
    "asset.archived",
    "asset.restored",
)

_TYPE_TO_WORKBOOK: dict[str, str] = {}
for _t in _OPS_TRIGGERS:
    _TYPE_TO_WORKBOOK[_t] = OPS_WORKBOOK_NAME
for _t in _FINANCE_TRIGGERS:
    _TYPE_TO_WORKBOOK[_t] = FINANCE_WORKBOOK_NAME


class XlsxWorkbooksProjection(Projection):
    """Forward-only xlsx projection daemon.

    Structurally a projection per [§2.2]. Emits only the system event
    ``xlsx.regenerated`` after each successful regenerate.
    """

    name = "xlsx_workbooks"
    version = 2
    subscribes_to: list[str] | str = [
        str(event_type) for event_type in (*_OPS_TRIGGERS, *_FINANCE_TRIGGERS)
    ]
    schema_path: Path = Path(__file__).parent / "schema.sql"

    def __init__(
        self,
        config: InstanceConfig,
        query_context: XlsxQueryContext | None,
        *,
        event_log: EventLog,
        event_sink: EventSink,
        query_context_factory: Callable[[], XlsxQueryContext] | None = None,
        query_context_runner: Callable[
            [Callable[[XlsxQueryContext, str], None], dict[str, str]], Awaitable[str]
        ]
        | None = None,
        debounce_s: float = 5.0,
    ) -> None:
        self._config = config
        if query_context is None and query_context_factory is None:
            raise ValueError("query_context or query_context_factory is required")
        self._ctx = query_context
        self._query_context_factory = query_context_factory
        self._query_context_runner = query_context_runner
        self._log = event_log
        self._events = event_sink
        self._debounce_s = debounce_s
        self._pending_tasks: dict[str, asyncio.Task[None]] = {}
        self._regen_lock = asyncio.Lock()
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._replaying = False
        self._replay_dirty_workbooks: set[str] = set()
        self._pending_trigger_events: dict[str, dict[str, str]] = {
            OPS_WORKBOOK_NAME: {},
            FINANCE_WORKBOOK_NAME: {},
        }
        self._config.xlsx_workbooks_dir.mkdir(parents=True, exist_ok=True)

    def _query_context(self) -> XlsxQueryContext:
        if self._ctx is not None:
            return self._ctx
        assert self._query_context_factory is not None
        return self._query_context_factory()

    async def stop(self) -> None:
        """Cancel and join pending debounce tasks before DB connections close."""

        tasks = [task for task in self._pending_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._pending_tasks.clear()

    def bind_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the runtime loop before worker-side projection callbacks begin."""

        self._event_loop = loop

    # ------------------------------------------------------------------
    # Projection contract
    # ------------------------------------------------------------------
    def apply(self, envelope: dict[str, Any], conn: Any) -> None:
        """Schedule a debounced regeneration for the affected workbook.

        Ignores ``conn`` — this projection has no SQLite backing store.
        xlsx_workbooks is the only projection allowed to write non-DB
        derived state (two files on disk) per [§13.5].
        """
        event_type = envelope.get("type")
        if event_type is None:
            return
        workbook = _TYPE_TO_WORKBOOK.get(event_type)
        if workbook is None:
            return
        event_id = envelope.get("event_id")
        if isinstance(event_id, str) and event_id:
            current = self._pending_trigger_events[workbook].get(str(event_type))
            if current is None or event_id > current:
                self._pending_trigger_events[workbook][str(event_type)] = event_id
        if self._replaying:
            self._replay_dirty_workbooks.add(workbook)
            return
        self._schedule_regeneration(workbook)

    def before_replay(self, conn: Any) -> None:
        del conn
        self._replaying = True
        self._replay_dirty_workbooks.clear()

    def after_replay(self, conn: Any, *, succeeded: bool) -> None:
        del conn
        dirty = tuple(sorted(self._replay_dirty_workbooks)) if succeeded else ()
        self._replay_dirty_workbooks.clear()
        self._replaying = False
        for workbook in dirty:
            self._schedule_regeneration(workbook)

    # ------------------------------------------------------------------
    # Debounce + regeneration
    # ------------------------------------------------------------------
    def _schedule_regeneration(self, workbook: str) -> None:
        """Cancel any pending regenerate for this workbook and schedule a
        fresh one that fires after ``debounce_s`` seconds of silence."""
        loop = self._event_loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is None:
            if running is None:
                raise RuntimeError("xlsx regeneration has no bound event loop")
            loop = running
            self._event_loop = loop
        if running is not loop:
            loop.call_soon_threadsafe(self._schedule_regeneration_on_loop, workbook)
            return
        self._schedule_regeneration_on_loop(workbook)

    def _schedule_regeneration_on_loop(self, workbook: str) -> None:
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return

        existing = self._pending_tasks.get(workbook)
        if existing is not None and not existing.done():
            existing.cancel()

        async def _wait_then_run() -> None:
            try:
                await asyncio.sleep(self._debounce_s)
            except asyncio.CancelledError:
                return
            try:
                await self.regenerate_now(workbook)
            except Exception:
                _log.exception("xlsx_workbooks: regeneration failed for %s", workbook)

        self._pending_tasks[workbook] = loop.create_task(_wait_then_run())

    async def regenerate_now(self, workbook: str, *, emit_event: bool = True) -> None:
        """Bypass the debounce: regenerate ``workbook`` immediately and
        emit ``xlsx.regenerated``. Used by tests and by ``adminme
        projection rebuild xlsx_workbooks``."""
        if workbook not in (OPS_WORKBOOK_NAME, FINANCE_WORKBOOK_NAME):
            raise ValueError(f"unknown workbook: {workbook!r}")

        async with self._regen_lock:
            await self._regenerate(workbook, emit_event=emit_event)

    async def regenerate_all(self, *, emit_event: bool = True) -> None:
        """Regenerate both canonical workbooks from one projection snapshot each."""

        for workbook in (OPS_WORKBOOK_NAME, FINANCE_WORKBOOK_NAME):
            await self.regenerate_now(workbook, emit_event=emit_event)

    async def _regenerate(self, workbook: str, *, emit_event: bool) -> None:
        from adminme.projections.xlsx_workbooks.builders import (
            build_finance_workbook,
            build_ops_workbook,
        )
        from adminme.projections.xlsx_workbooks.lockfile import (
            acquire_workbook_lock,
        )

        start_ms = int(time.time() * 1000)
        path = self._config.xlsx_workbooks_dir / workbook
        lock_path = path.with_suffix(path.suffix + ".lock")
        trigger_events = dict(self._pending_trigger_events[workbook])

        def _under_lock(
            query_context: XlsxQueryContext,
            represented_event_id: str,
        ) -> None:
            with acquire_workbook_lock(lock_path, timeout_s=10.0):
                if workbook == OPS_WORKBOOK_NAME:
                    build_ops_workbook(
                        path,
                        query_context,
                        tenant_id=self._config.tenant_id,
                        last_event_id=represented_event_id,
                    )
                else:
                    build_finance_workbook(
                        path,
                        query_context,
                        tenant_id=self._config.tenant_id,
                        last_event_id=represented_event_id,
                    )
                # Sidecar must be written INSIDE the same lock as the xlsx
                # write so reverse (07c-β) cannot observe a workbook whose
                # sidecar baseline is from the prior cycle. Reads back from
                # the just-written xlsx (not re-queries projections) so the
                # sidecar is byte-aligned with the workbook on disk.
                self._write_sidecar_for(workbook, path)

        if self._query_context_runner is not None:
            last_event_id = await self._query_context_runner(
                _under_lock,
                trigger_events,
            )
        else:
            query_context = self._query_context()
            last_event_id = await self._log.latest_event_id() or ""
            await asyncio.to_thread(_under_lock, query_context, last_event_id)

        pending = self._pending_trigger_events[workbook]
        for event_type, event_id in trigger_events.items():
            if pending.get(event_type) == event_id:
                pending.pop(event_type, None)

        sheets = _BIDIRECTIONAL_SHEETS[workbook] + _READONLY_SHEETS[workbook]
        duration_ms = int(time.time() * 1000) - start_ms

        if emit_event:
            # Emit xlsx.regenerated as a SYSTEM event per [§2.2] resolution.
            # This is the ONLY emit in this projection.
            await self._events.write(
                EventEnvelope(
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
                )
            )

    # ------------------------------------------------------------------
    # Sidecar
    # ------------------------------------------------------------------
    def _write_sidecar_for(self, workbook: str, xlsx_path: Path) -> None:
        """Write per-sheet sidecar JSON next to ``xlsx_path``.

        Reads the just-written xlsx (rather than re-querying projection
        databases) so the sidecar is guaranteed to match the workbook
        byte-for-byte. Bidirectional sheets get a row-list payload;
        read-only sheets get a content-hash payload (07c-β WARN signal,
        not a state baseline).
        """
        from openpyxl import load_workbook
        from adminme.daemons.xlsx_sync.sheet_schemas import descriptor_for

        workbooks_dir = self._config.xlsx_workbooks_dir
        wb = load_workbook(str(xlsx_path), data_only=True, read_only=False)
        try:
            for sheet_name in _BIDIRECTIONAL_SHEETS.get(workbook, []):
                ws = wb[sheet_name]
                rows = list(ws.iter_rows(values_only=True))
                descriptor = descriptor_for(workbook, sheet_name)
                header_row = descriptor.header_row if descriptor is not None else 1
                if len(rows) < header_row:
                    write_sheet_state(workbooks_dir, workbook, sheet_name, [])
                    continue
                headers = [
                    str(h) if h is not None else "" for h in rows[header_row - 1]
                ]
                row_dicts: list[dict[str, Any]] = []
                for raw in rows[header_row:]:
                    if not any(value is not None for value in raw):
                        continue
                    row_dicts.append({headers[i]: raw[i] for i in range(len(headers))})
                write_sheet_state(workbooks_dir, workbook, sheet_name, row_dicts)
            for sheet_name in _READONLY_SHEETS.get(workbook, []):
                ws = wb[sheet_name]
                raw_rows = [list(r) for r in ws.iter_rows(values_only=True)]
                write_readonly_state(workbooks_dir, workbook, sheet_name, raw_rows)
        finally:
            wb.close()


__all__ = [
    "XlsxWorkbooksProjection",
    "XlsxQueryContext",
    "OPS_WORKBOOK_NAME",
    "FINANCE_WORKBOOK_NAME",
]
