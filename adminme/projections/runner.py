"""
ProjectionRunner — discovers, dispatches, rebuilds projections.

Per SYSTEM_INVARIANTS.md §2 and ADMINISTRATEME_BUILD.md §L3.

The runner owns one SQLCipher writer per registered projection and one bounded
query-only Parties handle for current authority reads. One serialized native
worker owns writers; one dedicated serialized reader worker prevents fresh
Parties authority reads from queuing behind unrelated projection writes. Each
native handle keeps one owner thread throughout its lifecycle, with one
bus subscription per projection (``subscriber_id = f"projection:{name}"``).
On each delivered event, the bound callback opens a transaction on the
projection's DB, calls ``projection.apply(envelope, conn)``, commits, and
returns on that worker. Checkpoint persistence is owned by the bus — if the
callback raises, the bus does not advance the checkpoint and the event is
redelivered. Projections must be idempotent for this to be safe.

Rebuild captures an event-log watermark, creates and replays a sibling staged
database through exactly that watermark, checkpoints and closes both native
handles on their owner worker, then atomically publishes the stage. The prior
database remains usable until publish; failure or cancellation before publish
removes the stage and preserves the prior connection. Live delivery for that
projection is serialized behind the rebuild and resumes after the captured
checkpoint.

Per §15/D15 all DB paths come from ``InstanceConfig.projection_db_path()``.
No path literals in this module.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import sqlcipher3

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import InstanceConfig
from adminme.projections.base import Projection
from adminme.projections.artifacts import ArtifactsProjection
from adminme.projections.calendars import CalendarsProjection
from adminme.projections.commitments import CommitmentsProjection
from adminme.projections.context_fabric import ContextFabricProjection
from adminme.projections.interactions import InteractionsProjection
from adminme.projections.lists import ListsProjection
from adminme.projections.member_knowledge import MemberKnowledgeProjection
from adminme.projections.money import MoneyProjection
from adminme.projections.parties import PartiesProjection
from adminme.projections.places_assets_accounts import PlacesAssetsAccountsProjection
from adminme.projections.recurrences import RecurrencesProjection
from adminme.projections.tasks import TasksProjection
from adminme.projections.triage import TriageProjection
from adminme.projections.vector_search import VectorSearchProjection
from adminme.projections.xlsx_workbooks import XlsxWorkbooksProjection
from adminme.projections.xlsx_workbooks.query_context import XlsxQueryContext

_log = logging.getLogger(__name__)
_T = TypeVar("_T")

# ``sqlcipher3`` re-exports these native DB-API symbols at runtime, but its
# source-derived type surface does not declare every re-export.  Resolve only
# the exact constants/types needed by the authorizer and native-handle guard;
# omitting defaults preserves a fail-closed import if the native API changes.
_SQLITE_OK: int = getattr(sqlcipher3, "SQLITE_OK")
_SQLITE_DENY: int = getattr(sqlcipher3, "SQLITE_DENY")
_SQLCIPHER_CURSOR_TYPE: type[Any] = getattr(sqlcipher3, "Cursor")

# Default projection classes the runner registers when no caller has supplied an
# explicit set. Pack-provided projections register separately via the pack runner.
# [D18] enumerated the original 13 first-party projections. [D26] added the
# Triage adjudication projection. ADR-0008 adds the rebuildable Context Fabric
# relationship index; this tuple remains the registration anchor consumed by
# integration tests and bootstrap code.
DEFAULT_PROJECTION_CLASSES: tuple[type[Projection], ...] = (
    PartiesProjection,
    InteractionsProjection,
    ArtifactsProjection,
    CommitmentsProjection,
    TasksProjection,
    RecurrencesProjection,
    CalendarsProjection,
    PlacesAssetsAccountsProjection,
    MoneyProjection,
    VectorSearchProjection,
    XlsxWorkbooksProjection,
    MemberKnowledgeProjection,
    ListsProjection,
    TriageProjection,
    ContextFabricProjection,
)

_XLSX_SOURCE_PROJECTIONS = (
    "parties",
    "tasks",
    "commitments",
    "recurrences",
    "calendars",
    "places_assets_accounts",
    "money",
)

_READ_ONLY_DENIED_ACTIONS = frozenset(
    getattr(sqlcipher3, name)
    for name in (
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DELETE",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_INSERT",
        "SQLITE_PRAGMA",
        "SQLITE_UPDATE",
        "SQLITE_ATTACH",
        "SQLITE_DETACH",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
        "SQLITE_CREATE_VTABLE",
        "SQLITE_DROP_VTABLE",
        "SQLITE_TRANSACTION",
        "SQLITE_SAVEPOINT",
    )
    if hasattr(sqlcipher3, name)
)
_READ_ONLY_PRAGMAS = frozenset(
    {
        # SQLite/FTS5 reads this internally while evaluating MATCH/bm25.
        "data_version",
        # Production diagnostics and migration acceptance inspect these
        # catalogs without changing connection or database state.
        "index_info",
        "index_list",
        "index_xinfo",
        "table_info",
        "table_xinfo",
    }
)


def _format_key(encryption_key: bytes) -> str:
    """SQLCipher PRAGMA key expects a quoted hex blob for raw keys."""
    if not isinstance(encryption_key, (bytes, bytearray)):
        raise TypeError("encryption_key must be bytes")
    if len(encryption_key) != 32:
        raise ValueError(f"encryption_key must be 32 bytes, got {len(encryption_key)}")
    return f"x'{bytes(encryption_key).hex()}'"


@dataclass(frozen=True)
class _DetachedRow:
    """A connection-independent row safe to consume outside the DB worker."""

    _columns: tuple[str, ...]
    _values: tuple[Any, ...]

    def keys(self) -> list[str]:
        return list(self._columns)

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, str):
            try:
                key = self._columns.index(key)
            except ValueError as exc:
                raise IndexError(key) from exc
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class _DetachedCursor:
    """Buffered DB-API cursor result with no native cursor ownership."""

    def __init__(
        self,
        rows: Sequence[_DetachedRow] = (),
        *,
        rowcount: int = -1,
        lastrowid: int | None = None,
        description: Any = None,
    ) -> None:
        self._rows = list(rows)
        self._index = 0
        self.rowcount = rowcount
        self.lastrowid = lastrowid
        self.description = description
        self.arraysize = 1

    def fetchone(self) -> _DetachedRow | None:
        if self._index >= len(self._rows):
            return None
        row = self._rows[self._index]
        self._index += 1
        return row

    def fetchmany(self, size: int | None = None) -> list[_DetachedRow]:
        count = self.arraysize if size is None else size
        if count < 0:
            raise ValueError("fetchmany size must be non-negative")
        end = min(len(self._rows), self._index + count)
        rows = self._rows[self._index : end]
        self._index = end
        return rows

    def fetchall(self) -> list[_DetachedRow]:
        rows = self._rows[self._index :]
        self._index = len(self._rows)
        return rows

    def close(self) -> None:
        self._rows.clear()
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self) -> _DetachedRow:
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class _FacadeCursor(_DetachedCursor):
    """Cursor-shaped compatibility object that delegates whole statements."""

    def __init__(self, connection: _ProjectionConnectionFacade) -> None:
        super().__init__()
        self._connection = connection

    def _adopt(self, result: _DetachedCursor) -> _FacadeCursor:
        self._rows = result._rows
        self._index = result._index
        self.rowcount = result.rowcount
        self.lastrowid = result.lastrowid
        self.description = result.description
        return self

    def execute(self, sql: str, parameters: Any = ()) -> _FacadeCursor:
        return self._adopt(self._connection.execute(sql, parameters))

    def executemany(self, sql: str, seq_of_parameters: Iterable[Any]) -> _FacadeCursor:
        return self._adopt(self._connection.executemany(sql, seq_of_parameters))


class _ProjectionConnectionFacade:
    """DB-API-shaped facade that never exposes a native SQLCipher handle.

    Existing synchronous projection query helpers can continue to accept a
    connection-shaped value. Each complete statement runs on the runner's one
    native owner worker and returns detached rows/cursor metadata.
    """

    def __init__(
        self,
        runner: ProjectionRunner,
        name: str,
        *,
        read_only: bool,
    ) -> None:
        self._runner = runner
        self._name = name
        self._read_only = read_only

    def execute(self, sql: str, parameters: Any = ()) -> _DetachedCursor:
        return self._runner._execute_sync(
            self._name,
            sql,
            parameters,
            read_only=self._read_only,
        )

    def executemany(
        self,
        sql: str,
        seq_of_parameters: Iterable[Any],
    ) -> _DetachedCursor:
        return self._runner._execute_sync(
            self._name,
            sql,
            tuple(seq_of_parameters),
            many=True,
            read_only=self._read_only,
        )

    def cursor(self) -> _FacadeCursor:
        return _FacadeCursor(self)

    def commit(self) -> None:
        if not self._read_only:
            self._runner._connection_control_sync(self._name, "commit")

    def rollback(self) -> None:
        if not self._read_only:
            self._runner._connection_control_sync(self._name, "rollback")

    def close(self) -> None:
        # The runner owns the native connection and its exact-once close.
        return None


class ProjectionRunner:
    """Owns the projection DB connections and bus subscriptions."""

    def __init__(
        self,
        bus: EventBus,
        log: EventLog,
        instance_config: InstanceConfig,
        *,
        encryption_key: bytes,
    ) -> None:
        self._bus = bus
        self._log = log
        self._config = instance_config
        self._key_pragma = _format_key(encryption_key)
        self._projections: dict[str, Projection] = {}
        self._conns: dict[str, sqlcipher3.Connection] = {}
        self._parties_read_conn: sqlcipher3.Connection | None = None
        self._staged_conns: dict[str, sqlcipher3.Connection] = {}
        self._staged_paths: dict[str, Path] = {}
        self._facades: dict[str, _ProjectionConnectionFacade] = {}
        self._maintenance_facades: dict[str, _ProjectionConnectionFacade] = {}
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._lifecycle_lock = asyncio.Lock()
        self._submission_lock = threading.Lock()
        self._worker_thread_id: int | None = None
        self._parties_reader_thread_id: int | None = None
        self._parties_submission_lock = threading.Lock()
        self._parties_pending_mutations = 0
        self._closing = False
        self._closed = False
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="adminme-projections",
            initializer=self._capture_worker_thread,
        )
        self._parties_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="adminme-authority-reader",
            initializer=self._capture_parties_reader_thread,
        )
        self._started = False
        self._config.projections_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, projection: Projection) -> None:
        """Register a projection. Must be called before start()."""
        if self._started:
            raise RuntimeError("register() must be called before start()")
        if projection.name in self._projections:
            raise ValueError(f"projection already registered: {projection.name}")
        self._projections[projection.name] = projection

    def projection_names(self) -> list[str]:
        return sorted(self._projections)

    def connection(self, name: str) -> sqlcipher3.Connection:
        """Return a serialized compatibility facade, never a native handle."""

        if name not in self._projections:
            raise KeyError(name)
        facade = self._facades.setdefault(
            name,
            _ProjectionConnectionFacade(self, name, read_only=True),
        )
        return cast(sqlcipher3.Connection, facade)

    def _unsafe_maintenance_connection(self, name: str) -> sqlcipher3.Connection:
        """Return an explicitly writable facade for isolated tests/migrations.

        Production request and workbook code must use :meth:`connection`,
        which is authorizer-enforced query-only.  Keeping this boundary private
        prevents compatibility tests from weakening the runtime read surface.
        """

        if name not in self._projections:
            raise KeyError(name)
        facade = self._maintenance_facades.setdefault(
            name,
            _ProjectionConnectionFacade(self, name, read_only=False),
        )
        return cast(sqlcipher3.Connection, facade)

    async def read(
        self,
        name: str,
        query: Callable[[sqlcipher3.Connection], _T],
    ) -> _T:
        """Run one complete synchronous query on the native owner worker."""

        if name not in self._projections:
            raise KeyError(name)
        result = await self._run_on_worker(self._call_query, name, query)
        self._require_detached_result(result)
        return cast(_T, result)

    async def read_many(
        self,
        names: Iterable[str],
        query: Callable[[dict[str, sqlcipher3.Connection]], _T],
    ) -> _T:
        """Run a cross-projection snapshot callback without handle escape."""

        selected = tuple(dict.fromkeys(names))
        unknown = sorted(set(selected) - set(self._projections))
        if unknown:
            raise KeyError(", ".join(unknown))
        result = await self._run_on_worker(self._call_many_query, selected, query)
        self._require_detached_result(result)
        return cast(_T, result)

    async def _read_native(
        self,
        name: str,
        query: Callable[[sqlcipher3.Connection], _T],
    ) -> _T:
        """Run a trusted internal callback on the owner-worker native handle."""

        if name not in self._projections:
            raise KeyError(name)
        return cast(_T, await self._run_on_worker(self._call_native_query, name, query))

    async def read_snapshot(
        self,
        names: Iterable[str],
        query: Callable[[dict[str, sqlcipher3.Connection]], _T],
    ) -> _T:
        """Run a potentially slow query against encrypted detached snapshots.

        The persistent projection handles are checkpointed and copied on their
        sole owner worker.  The callback then opens independent query-only
        handles and runs on a separate bounded worker, so workbook rendering
        cannot monopolize the native projection owner or the asyncio loop.
        """

        selected = tuple(dict.fromkeys(names))
        unknown = sorted(set(selected) - set(self._projections))
        if unknown:
            raise KeyError(", ".join(unknown))
        snapshot_root = Path(
            tempfile.mkdtemp(
                prefix=".projection-snapshot-",
                dir=self._config.projections_dir,
            )
        )
        snapshot_root.chmod(0o700)
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="adminme-projection-snapshot",
        )
        call: concurrent.futures.Future[_T] | None = None
        try:
            # A staged rebuild deliberately leaves the prior published DB live
            # until its final atomic swap.  Serialize only the quick snapshot
            # copy with that lifecycle so a renderer cannot queue behind replay
            # on the owner worker, copy the old DB, and then publish stale
            # workbooks after the rebuild wins.  Rendering remains detached and
            # does not hold this lock.
            async with self._lifecycle_lock:
                if self._closing or self._closed or not self._started:
                    raise RuntimeError(
                        "projection runner is not available for snapshot"
                    )
                await self._run_on_worker(
                    self._copy_projection_snapshots,
                    selected,
                    snapshot_root,
                )
            call = executor.submit(
                self._call_snapshot_query,
                selected,
                snapshot_root,
                query,
            )
            try:
                result = await asyncio.shield(asyncio.wrap_future(call))
            except asyncio.CancelledError:
                try:
                    call.result()
                except Exception:
                    _log.exception(
                        "projection snapshot query failed after cancellation"
                    )
                raise
            self._require_detached_result(result)
            return result
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
            if call is not None:
                try:
                    call.result()
                except Exception:
                    # The awaited path already propagated the failure.  This
                    # retrieval prevents an abandoned native exception.
                    pass
            shutil.rmtree(snapshot_root, ignore_errors=False)

    async def wait_for_projection_snapshot(
        self,
        names: Iterable[str],
        requirements: dict[str, str],
        *,
        timeout_s: float = 10.0,
    ) -> str:
        """Wait for healthy source checkpoints and return their represented head."""

        selected = tuple(dict.fromkeys(names))
        unknown = sorted(set(selected) - set(self._projections))
        if unknown:
            raise KeyError(", ".join(unknown))
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            checkpoints: list[str] = []
            unsettled: list[str] = []
            for name in selected:
                status = await self._bus.subscriber_status(f"projection:{name}")
                if status.get("degraded"):
                    raise RuntimeError(
                        f"projection {name!r} is degraded and cannot back a workbook"
                    )
                checkpoint = status.get("checkpoint_event_id")
                required = requirements.get(name)
                if status.get("lag_count") not in {0, None} or (
                    required is not None
                    and (checkpoint is None or checkpoint < required)
                ):
                    unsettled.append(name)
                if isinstance(checkpoint, str):
                    checkpoints.append(checkpoint)
            if not unsettled:
                return max(checkpoints, default="")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    "workbook source projections did not settle: "
                    + ", ".join(sorted(unsettled))
                )
            await asyncio.sleep(0.01)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self, *, start_bus: bool = True) -> None:
        """Open each projection's DB, apply schema.sql if new, subscribe to
        the bus with one callback per projection that dispatches to apply()."""
        async with self._lifecycle_lock:
            if self._started:
                return
            if self._closed or self._closing:
                raise RuntimeError("projection runner cannot be restarted after stop")
            self._started = True
            try:
                loop = asyncio.get_running_loop()
                replay_required: list[str] = []
                for name, projection in self._projections.items():
                    bind_loop = getattr(projection, "bind_event_loop", None)
                    if bind_loop is not None:
                        bind_loop(loop)
                    db_path = self._config.projection_db_path(name)
                    db_existed = db_path.exists()
                    stored_version = (
                        await self._run_on_worker(
                            self._stored_projection_version,
                            name,
                        )
                        if db_existed
                        else None
                    )
                    version_changed = db_existed and stored_version != str(
                        projection.version
                    )
                    replay_version_change = version_changed and bool(
                        projection.replay_retained_events_on_version_change
                    )
                    await self._run_on_worker(
                        self._open_and_store_projection,
                        name,
                        projection,
                        not replay_version_change,
                    )
                    self._write_locks[name] = asyncio.Lock()
                    subscriber_id = f"projection:{name}"
                    types: list[str] | Literal["*"]
                    if projection.subscribes_to == "*":
                        types = "*"
                    else:
                        types = list(cast(list[str], projection.subscribes_to))
                    self._bus.subscribe(
                        subscriber_id,
                        types,
                        self._make_callback(name, projection),
                    )
                    if replay_version_change:
                        replay_required.append(name)
                for name in replay_required:
                    _log.info(
                        "replaying projection %s after reviewed version change",
                        name,
                    )
                    projection = self._projections[name]
                    async with self._write_locks[name]:
                        through = await self._log.latest_event_id()
                        await self._replay_through(
                            name,
                            projection,
                            through=through,
                        )
                        if through is not None:
                            await self._bus.set_checkpoint(
                                f"projection:{name}", through
                            )
                        await self._run_on_worker(
                            self._stamp_projection_metadata_for,
                            name,
                            projection,
                        )
                if start_bus:
                    await self._bus.start()
            except BaseException:
                self._started = False
                self._close_executor_after_failure()
                raise

    async def stop(self, *, stop_bus: bool = True) -> None:
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._started = False
            close_call: concurrent.futures.Future[Any] | None = None
            cancellation: asyncio.CancelledError | None = None
            async_failure: BaseException | None = None
            cleanup_task = asyncio.create_task(
                self._stop_async_components(stop_bus),
                name="projection-runner-async-stop",
            )
            try:
                # The caller may be cancelled while EventBus.stop() is waiting
                # for a native projection callback.  Keep the independently
                # owned cleanup task alive and settle it before closing its
                # checkpoint/projection connections.  Repeated cancellation is
                # remembered but does not abandon cleanup.
                while not cleanup_task.done():
                    try:
                        await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError as exc:
                        cancellation = cancellation or exc
                    except BaseException:
                        break
                try:
                    cleanup_task.result()
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                except BaseException as exc:
                    async_failure = exc
            finally:
                with self._submission_lock:
                    self._closing = True
                    close_call = self._executor.submit(self._close_all_connections)
                try:
                    await asyncio.shield(asyncio.wrap_future(close_call))
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                finally:
                    self._executor.shutdown(wait=True, cancel_futures=False)
                    self._parties_executor.shutdown(wait=True, cancel_futures=False)
                    if close_call is not None:
                        try:
                            close_call.result()
                        except Exception:
                            _log.exception("error closing projection connections")
                    self._closed = True
                    self._closing = False
                    if cancellation is not None:
                        _log.debug(
                            "projection stop completed after caller cancellation"
                        )
            if cancellation is not None:
                raise cancellation
            if async_failure is not None:
                raise async_failure

    async def _stop_async_components(self, stop_bus: bool) -> None:
        """Settle bus and projection hooks even if one component fails."""

        first_failure: BaseException | None = None
        if stop_bus:
            try:
                await self._bus.stop()
            except BaseException as exc:
                first_failure = exc
                _log.exception("error stopping event bus")
        for projection in self._projections.values():
            stop = getattr(projection, "stop", None)
            if stop is None:
                continue
            try:
                result = stop()
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:
                first_failure = first_failure or exc
                _log.exception("error stopping projection %s", projection.name)
        if first_failure is not None:
            raise first_failure

    # ------------------------------------------------------------------
    # rebuild
    # ------------------------------------------------------------------
    async def rebuild(self, projection_name: str) -> None:
        """Drop the projection's DB, recreate from schema.sql, replay the
        entire event log through apply(), reset the bus checkpoint.

        Per §2 invariant 1: post-rebuild state is byte-equivalent to live.
        """
        async with self._lifecycle_lock:
            if self._closing or self._closed or not self._started:
                raise RuntimeError("projection runner is not available for rebuild")
            if projection_name not in self._projections:
                raise KeyError(projection_name)
            projection = self._projections[projection_name]
            subscriber_id = f"projection:{projection_name}"

            async with self._write_locks.setdefault(projection_name, asyncio.Lock()):
                db_path = self._config.projection_db_path(projection_name)
                stage_path = Path(f"{db_path}.rebuild-{uuid.uuid4().hex}")
                through = await self._log.latest_event_id()
                try:
                    await self._run_on_worker(
                        self._open_staged_projection,
                        projection_name,
                        projection,
                        stage_path,
                    )
                    await self._replay_through(
                        projection_name,
                        projection,
                        through=through,
                        staged=True,
                    )
                    await self._run_on_worker(
                        self._publish_staged_projection,
                        projection_name,
                        projection,
                        stage_path,
                    )
                    await self._run_on_worker(
                        self._after_replay,
                        projection_name,
                        projection,
                        False,
                        True,
                    )
                    if through is not None:
                        await self._bus.set_checkpoint(subscriber_id, through)
                except BaseException:
                    self._discard_staged_projection_settled(
                        projection_name,
                        stage_path,
                    )
                    try:
                        self._submit(
                            self._after_replay,
                            projection_name,
                            projection,
                            False,
                            False,
                        ).result()
                    except Exception:
                        _log.exception("failed to settle projection replay hook")
                    raise

    async def _replay_through(
        self,
        name: str,
        projection: Projection,
        *,
        through: str | None,
        staged: bool = False,
    ) -> None:
        types_filter: list[str] | None
        if projection.subscribes_to == "*":
            types_filter = None
        else:
            types_filter = list(projection.subscribes_to)  # type: ignore[arg-type]

        await self._run_on_worker(
            self._before_replay,
            name,
            projection,
            staged,
        )
        succeeded = False
        batch: list[dict[str, Any]] = []
        try:
            if through is not None:
                async for envelope in self._log.read_since(
                    None,
                    through=through,
                    types=types_filter,
                    limit=500,
                ):
                    batch.append(envelope)
                    if len(batch) >= 500:
                        await self._run_on_worker(
                            self._apply_replay_batch,
                            name,
                            projection,
                            batch,
                            staged,
                        )
                        batch = []
            if batch:
                await self._run_on_worker(
                    self._apply_replay_batch,
                    name,
                    projection,
                    batch,
                    staged,
                )
            succeeded = True
        finally:
            if not (staged and succeeded):
                await self._run_on_worker(
                    self._after_replay,
                    name,
                    projection,
                    staged,
                    succeeded,
                )

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    async def status(
        self, *, include_row_counts: bool = True
    ) -> dict[str, dict[str, Any]]:
        """Per-projection dict: name, version, row_counts, checkpoint,
        lag_count, last_event_applied. Omit uncollected row counts when requested."""
        out: dict[str, dict[str, Any]] = {}
        for name, projection in self._projections.items():
            subscriber_id = f"projection:{name}"
            bus_status = await self._bus.subscriber_status(subscriber_id)
            out[name] = {
                "name": name,
                "version": projection.version,
                "checkpoint": bus_status.get("checkpoint_event_id"),
                "lag_count": bus_status.get("lag_count"),
                "last_event_applied": bus_status.get("checkpoint_event_id"),
                "degraded": bus_status.get("degraded", False),
                "last_success_at": bus_status.get("last_success_at"),
                "last_failure_at": bus_status.get("last_failure_at"),
                "consecutive_failures": bus_status.get("consecutive_failures", 0),
                "poison_event_id": bus_status.get("poison_event_id"),
                "retry_policy": bus_status.get("retry_policy"),
            }
            if include_row_counts:
                out[name]["row_counts"] = await self._run_on_worker(
                    self._collect_row_counts_for,
                    name,
                )
        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _make_callback(self, name: str, projection: Projection):
        write_locks = self._write_locks

        async def _cb(envelope: dict[str, Any]) -> None:
            lock = write_locks.setdefault(name, asyncio.Lock())
            async with lock:
                await self._run_on_worker(
                    self._apply_one_for,
                    name,
                    projection,
                    envelope,
                )

        return _cb

    def _apply_one_for(
        self,
        name: str,
        projection: Projection,
        envelope: dict[str, Any],
    ) -> None:
        conn = self._require_connection(name)
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            projection.apply(envelope, conn)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def _open_projection_db(
        self,
        name: str,
        projection: Projection,
        stamp_version: bool = True,
        *,
        db_path: Path | None = None,
    ) -> sqlcipher3.Connection:
        db_path = db_path or self._config.projection_db_path(name)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn: sqlcipher3.Connection | None = None
        try:
            conn = sqlcipher3.connect(str(db_path), isolation_level=None)
            conn.row_factory = sqlcipher3.Row
            conn.execute(f'PRAGMA key = "{self._key_pragma}"')
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            # Touch the DB to surface bad-key errors eagerly.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()

            # Per-projection hook for extension loading (e.g. sqlite-vec's
            # vec0) or extra PRAGMAs. Default no-op.
            projection.on_connection_opened(conn)

            schema_sql = self._read_schema(projection)
            cur = conn.cursor()
            cur.executescript(schema_sql)
            projection.migrate_schema(conn)
            # A version-changing startup stamps only after retained-event replay
            # succeeds, so a crash cannot make the next restart skip recovery.
            cur.executescript(
                "CREATE TABLE IF NOT EXISTS _projection_meta ("
                "  key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            )
            cur.execute(
                "INSERT OR REPLACE INTO _projection_meta(key, value) VALUES (?, ?)",
                ("name", projection.name),
            )
            if stamp_version:
                self._stamp_projection_metadata(conn, projection)
            return conn
        except BaseException:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    _log.exception("failed to close rejected projection connection")
            raise

    @staticmethod
    def _stamp_projection_metadata(
        conn: sqlcipher3.Connection,
        projection: Projection,
    ) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO _projection_meta(key, value) VALUES (?, ?)",
            ("version", str(projection.version)),
        )

    def _stored_projection_version(self, name: str) -> str | None:
        """Read metadata before opening code can stamp the candidate version."""

        db_path = self._config.projection_db_path(name)
        conn = sqlcipher3.connect(
            str(db_path),
            isolation_level=None,
        )
        try:
            conn.execute(f'PRAGMA key = "{self._key_pragma}"')
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_projection_meta'"
            ).fetchone()
            if table is None:
                return None
            row = conn.execute(
                "SELECT value FROM _projection_meta WHERE key='version'"
            ).fetchone()
            return str(row[0]) if row is not None else None
        finally:
            conn.close()

    @staticmethod
    def _read_schema(projection: Projection) -> str:
        path = Path(projection.schema_path)
        return path.read_text(encoding="utf-8")

    @staticmethod
    def _remove_if_exists(path: Path) -> None:
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                candidate.unlink()

    @staticmethod
    def _collect_row_counts(conn: sqlcipher3.Connection) -> dict[str, int]:
        cur = conn.cursor()
        tables = [
            r[0]
            for r in cur.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "AND name NOT LIKE '\\_%' ESCAPE '\\'"
            )
        ]
        out: dict[str, int] = {}
        for t in tables:
            row = cur.execute(f"SELECT count(*) FROM {t}").fetchone()
            out[t] = int(row[0])
        return out

    # ------------------------------------------------------------------
    # native worker ownership
    # ------------------------------------------------------------------
    def _capture_worker_thread(self) -> None:
        self._worker_thread_id = threading.get_ident()

    def _capture_parties_reader_thread(self) -> None:
        self._parties_reader_thread_id = threading.get_ident()

    def _submit(
        self, func: Callable[..., _T], *args: Any
    ) -> concurrent.futures.Future[_T]:
        with self._submission_lock:
            if self._closing or self._closed:
                raise RuntimeError("projection runner is closing")
            # Preserve the writer FIFO for already-enqueued authority changes.
            # Only a known query callback may bypass this mutation fence.
            parties_mutation = bool(
                args and args[0] == "parties" and func != self._call_query
            )
            if not parties_mutation:
                return self._executor.submit(func, *args)
            self._parties_pending_mutations += 1
            try:
                return self._executor.submit(self._run_parties_mutation, func, args)
            except BaseException:
                self._parties_pending_mutations -= 1
                raise

    def _run_parties_mutation(
        self, func: Callable[..., _T], args: tuple[Any, ...]
    ) -> _T:
        try:
            return func(*args)
        finally:
            with self._submission_lock:
                self._parties_pending_mutations -= 1

    async def _run_on_worker(self, func: Callable[..., _T], *args: Any) -> _T:
        if threading.get_ident() == self._worker_thread_id:
            return func(*args)
        call = self._submit(func, *args)
        try:
            return await asyncio.shield(asyncio.wrap_future(call))
        except asyncio.CancelledError:
            # The native call may already be running. Settle and retrieve it
            # before allowing lifecycle locks or staging ownership to escape.
            try:
                call.result()
            except Exception:
                _log.exception("projection worker call failed after cancellation")
            raise

    def _execute_sync(
        self,
        name: str,
        sql: str,
        parameters: Any,
        *,
        many: bool = False,
        read_only: bool = True,
    ) -> _DetachedCursor:
        if read_only and name == "parties":
            if threading.get_ident() == self._parties_reader_thread_id:
                return self._execute_statement(name, sql, parameters, many, read_only)
            # Serialize submissions with database publication: a request either
            # finishes against the prior inode before close, or opens the newly
            # published database afterwards. No grant values are cached.
            with self._submission_lock:
                on_writer = threading.get_ident() == self._worker_thread_id
                # A callback already accepted on the writer may finish its
                # nested reads before the queued close. Reject new callers.
                if self._closed or (self._closing and not on_writer):
                    raise RuntimeError("projection runner is closing")
                if self._parties_pending_mutations and not on_writer:
                    # Do not overtake a queued revocation, role change or
                    # publication. Once this reaches its original FIFO place,
                    # the writer delegates the actual SQL to the reader owner.
                    call = self._executor.submit(
                        self._execute_sync,
                        name,
                        sql,
                        parameters,
                        many=many,
                        read_only=read_only,
                    )
                else:
                    with self._parties_submission_lock:
                        call = self._parties_executor.submit(
                            self._execute_statement,
                            name,
                            sql,
                            parameters,
                            many,
                            read_only,
                        )
            return call.result()
        if threading.get_ident() == self._worker_thread_id:
            return self._execute_statement(name, sql, parameters, many, read_only)
        return self._submit(
            self._execute_statement,
            name,
            sql,
            parameters,
            many,
            read_only,
        ).result()

    def _execute_statement(
        self,
        name: str,
        sql: str,
        parameters: Any,
        many: bool,
        read_only: bool,
    ) -> _DetachedCursor:
        stable_authorizer = read_only and name == "parties"
        conn = (
            self._parties_reader()
            if stable_authorizer
            else self._require_connection(name)
        )
        if read_only and not stable_authorizer:
            conn.set_authorizer(self._query_only_authorizer)
        cursor = None
        try:
            cursor = (
                conn.executemany(sql, parameters)
                if many
                else conn.execute(sql, parameters)
            )
            description = cursor.description
            columns = tuple(str(column[0]) for column in description or ())
            rows = (
                [_DetachedRow(columns, tuple(row)) for row in cursor.fetchall()]
                if description is not None
                else []
            )
            return _DetachedCursor(
                rows,
                rowcount=cursor.rowcount,
                lastrowid=cursor.lastrowid,
                description=description,
            )
        finally:
            if cursor is not None:
                cursor.close()
            if read_only and not stable_authorizer:
                conn.set_authorizer(None)

    def _parties_reader(self) -> sqlcipher3.Connection:
        """One worker-owned current reader with a fixed SQL policy, not a cache.

        Resetting sqlite3_set_authorizer invalidates compiled statements. This
        separate handle keeps its read-only policy for its entire lifetime;
        every execute still reads the current committed WAL state. Its statement
        cache can never contain a statement prepared with writer permissions.
        """
        self._require_connection("parties")
        if self._parties_read_conn is not None:
            return self._parties_read_conn
        path = self._config.projection_db_path("parties")
        conn = sqlcipher3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            isolation_level=None,
            cached_statements=64,
        )
        try:
            conn.row_factory = sqlcipher3.Row
            conn.execute(f'PRAGMA key = "{self._key_pragma}"')
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA cache_size=-256")
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            conn.set_authorizer(self._query_only_authorizer)
        except BaseException:
            conn.close()
            raise
        self._parties_read_conn = conn
        return conn

    def _close_parties_reader(self) -> None:
        # Called only by writer lifecycle work, with admission excluded during
        # publication or after the runner is marked closing during shutdown.
        self._parties_executor.submit(self._close_parties_reader_native).result()

    def _close_parties_reader_native(self) -> None:
        conn, self._parties_read_conn = self._parties_read_conn, None
        if conn is not None:
            conn.close()

    @staticmethod
    def _query_only_authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database: str | None,
        source: str | None,
    ) -> int:
        del arg2, database, source
        if action == getattr(sqlcipher3, "SQLITE_PRAGMA", -1):
            return (
                _SQLITE_OK
                if (arg1 or "").casefold() in _READ_ONLY_PRAGMAS
                else _SQLITE_DENY
            )
        return _SQLITE_DENY if action in _READ_ONLY_DENIED_ACTIONS else _SQLITE_OK

    def _connection_control_sync(
        self,
        name: str,
        operation: Literal["commit", "rollback"],
    ) -> None:
        if threading.get_ident() == self._worker_thread_id:
            self._connection_control(name, operation)
            return
        self._submit(self._connection_control, name, operation).result()

    def _connection_control(
        self,
        name: str,
        operation: Literal["commit", "rollback"],
    ) -> None:
        getattr(self._require_connection(name), operation)()

    def _call_query(
        self,
        name: str,
        query: Callable[[sqlcipher3.Connection], _T],
    ) -> _T:
        return query(self.connection(name))

    def _call_many_query(
        self,
        names: tuple[str, ...],
        query: Callable[[dict[str, sqlcipher3.Connection]], _T],
    ) -> _T:
        return query({name: self.connection(name) for name in names})

    def _call_native_query(
        self,
        name: str,
        query: Callable[[sqlcipher3.Connection], _T],
    ) -> _T:
        return query(self._require_connection(name))

    @classmethod
    def _require_detached_result(
        cls,
        value: Any,
        *,
        _seen: set[int] | None = None,
    ) -> None:
        """Reject values that could carry native DB ownership off-worker."""

        if isinstance(value, (sqlcipher3.Connection, _SQLCIPHER_CURSOR_TYPE)):
            raise RuntimeError("projection query returned a native SQLCipher handle")
        if value is None or isinstance(
            value,
            (bool, int, float, str, bytes, Path, _DetachedRow, _DetachedCursor),
        ):
            return
        seen = _seen if _seen is not None else set()
        identity = id(value)
        if identity in seen:
            return
        seen.add(identity)
        if isinstance(value, dict):
            for key, item in value.items():
                cls._require_detached_result(key, _seen=seen)
                cls._require_detached_result(item, _seen=seen)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                cls._require_detached_result(item, _seen=seen)
            return
        raise RuntimeError(
            f"projection query returned unsupported live object {type(value).__name__}"
        )

    def _require_connection(self, name: str) -> sqlcipher3.Connection:
        conn = self._conns.get(name)
        if conn is None:
            raise RuntimeError(f"projection {name!r} connection unavailable")
        return conn

    def _open_and_store_projection(
        self,
        name: str,
        projection: Projection,
        stamp_version: bool,
    ) -> None:
        self._conns[name] = self._open_projection_db(
            name,
            projection,
            stamp_version,
        )

    def _stamp_projection_metadata_for(
        self,
        name: str,
        projection: Projection,
    ) -> None:
        self._stamp_projection_metadata(self._require_connection(name), projection)

    def _collect_row_counts_for(self, name: str) -> dict[str, int]:
        return self._collect_row_counts(self._require_connection(name))

    def _before_replay(
        self,
        name: str,
        projection: Projection,
        staged: bool,
    ) -> None:
        conn = self._staged_conns[name] if staged else self._require_connection(name)
        projection.before_replay(conn)

    def _after_replay(
        self,
        name: str,
        projection: Projection,
        staged: bool,
        succeeded: bool,
    ) -> None:
        conn = (
            self._staged_conns.get(name) if staged else self._conns.get(name)
        ) or self._conns.get(name)
        if conn is not None:
            projection.after_replay(conn, succeeded=succeeded)

    def _apply_replay_batch(
        self,
        name: str,
        projection: Projection,
        batch: list[dict[str, Any]],
        staged: bool,
    ) -> None:
        conn = self._staged_conns[name] if staged else self._require_connection(name)
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            for envelope in batch:
                projection.apply(envelope, conn)
            projection.after_batch(conn)
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

    def _open_staged_projection(
        self,
        name: str,
        projection: Projection,
        stage_path: Path,
    ) -> None:
        self._remove_if_exists(stage_path)
        self._staged_paths[name] = stage_path
        try:
            self._staged_conns[name] = self._open_projection_db(
                name,
                projection,
                db_path=stage_path,
            )
        except BaseException:
            self._staged_paths.pop(name, None)
            self._remove_if_exists(stage_path)
            raise

    def _publish_staged_projection(
        self,
        name: str,
        projection: Projection,
        stage_path: Path,
    ) -> None:
        if name == "parties":
            with self._parties_submission_lock:
                self._publish_staged_projection_locked(name, projection, stage_path)
        else:
            self._publish_staged_projection_locked(name, projection, stage_path)

    def _publish_staged_projection_locked(
        self,
        name: str,
        projection: Projection,
        stage_path: Path,
    ) -> None:
        stage = self._staged_conns.pop(name)
        self._staged_paths.pop(name, None)
        target = self._config.projection_db_path(name)
        backup = Path(f"{target}.pre-rebuild-{uuid.uuid4().hex}")
        old = self._require_connection(name)
        if name == "parties":
            self._close_parties_reader()
        stage.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        stage.close()
        old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        old.close()
        self._conns.pop(name, None)
        linked_backup = False
        try:
            if target.exists():
                os.link(target, backup)
                linked_backup = True
            os.replace(stage_path, target)
            self._remove_if_exists(stage_path)
            self._conns[name] = self._open_projection_db(name, projection)
        except BaseException:
            if linked_backup and backup.exists():
                os.replace(backup, target)
            if target.exists():
                self._conns[name] = self._open_projection_db(name, projection)
            raise
        finally:
            self._remove_if_exists(backup)

    def _discard_staged_projection_settled(self, name: str, stage_path: Path) -> None:
        if self._closed:
            return
        try:
            self._submit(self._discard_staged_projection, name, stage_path).result()
        except Exception:
            _log.exception("failed to discard staged projection rebuild")

    def _discard_staged_projection(self, name: str, stage_path: Path) -> None:
        conn = self._staged_conns.pop(name, None)
        tracked = self._staged_paths.pop(name, stage_path)
        if conn is not None:
            conn.close()
        self._remove_if_exists(tracked)

    def _close_all_connections(self) -> None:
        try:
            self._close_parties_reader()
        except Exception:
            _log.exception("error closing Parties query-only connection")
        for name, conn in list(self._staged_conns.items()):
            try:
                conn.close()
            except Exception:
                _log.exception("error closing staged projection %s", name)
        self._staged_conns.clear()
        for name, path in list(self._staged_paths.items()):
            try:
                self._remove_if_exists(path)
            except Exception:
                _log.exception("error removing staged projection %s", name)
        self._staged_paths.clear()
        for name, conn in list(self._conns.items()):
            try:
                conn.close()
            except Exception:
                _log.exception("error closing projection %s", name)
        self._conns.clear()

    def _copy_projection_snapshots(
        self,
        names: tuple[str, ...],
        destination: Path,
    ) -> None:
        for name in names:
            conn = self._require_connection(name)
            checkpoint = conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
            if checkpoint is None or int(checkpoint[0]) != 0:
                raise RuntimeError(
                    f"projection {name!r} could not checkpoint for snapshot"
                )
            source = self._config.projection_db_path(name)
            target = destination / f"{name}.db"
            source_descriptor = os.open(
                source,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            target_descriptor: int | None = None
            try:
                target_descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                with os.fdopen(source_descriptor, "rb", closefd=False) as source_file:
                    with os.fdopen(
                        target_descriptor,
                        "wb",
                        closefd=False,
                    ) as target_file:
                        shutil.copyfileobj(source_file, target_file)
                        target_file.flush()
                        os.fsync(target_file.fileno())
            finally:
                os.close(source_descriptor)
                if target_descriptor is not None:
                    os.close(target_descriptor)
            os.chmod(target, 0o600)

    def _call_snapshot_query(
        self,
        names: tuple[str, ...],
        snapshot_root: Path,
        query: Callable[[dict[str, sqlcipher3.Connection]], _T],
    ) -> _T:
        connections: dict[str, sqlcipher3.Connection] = {}
        try:
            for name in names:
                path = snapshot_root / f"{name}.db"
                conn = sqlcipher3.connect(
                    f"file:{path}?mode=ro",
                    uri=True,
                    isolation_level=None,
                )
                try:
                    conn.row_factory = sqlcipher3.Row
                    conn.execute(f'PRAGMA key = "{self._key_pragma}"')
                    conn.execute("PRAGMA query_only=ON")
                    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                    connections[name] = conn
                except BaseException:
                    conn.close()
                    raise
            return query(connections)
        finally:
            for name, conn in connections.items():
                try:
                    conn.close()
                except Exception:
                    _log.exception("error closing projection snapshot %s", name)

    def _close_executor_after_failure(self) -> None:
        with self._submission_lock:
            self._closing = True
            close_call = self._executor.submit(self._close_all_connections)
        try:
            close_call.result()
        finally:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._parties_executor.shutdown(wait=True, cancel_futures=False)
            self._closed = True
            self._closing = False


def register_default_projections(
    runner: ProjectionRunner,
    config: InstanceConfig,
    event_log: EventLog,
    event_sink: Any,
) -> XlsxWorkbooksProjection:
    """Register the one production projection catalog on ``runner``."""

    def _facade_context() -> XlsxQueryContext:
        return XlsxQueryContext(
            parties_conn=runner.connection("parties"),
            tasks_conn=runner.connection("tasks"),
            commitments_conn=runner.connection("commitments"),
            recurrences_conn=runner.connection("recurrences"),
            calendars_conn=runner.connection("calendars"),
            places_assets_accounts_conn=runner.connection("places_assets_accounts"),
            money_conn=runner.connection("money"),
        )

    async def _run_context(
        callback: Callable[[XlsxQueryContext, str], None],
        trigger_events: dict[str, str],
    ) -> str:
        def _invoke(connections: dict[str, sqlcipher3.Connection]) -> None:
            callback(
                XlsxQueryContext(
                    parties_conn=connections["parties"],
                    tasks_conn=connections["tasks"],
                    commitments_conn=connections["commitments"],
                    recurrences_conn=connections["recurrences"],
                    calendars_conn=connections["calendars"],
                    places_assets_accounts_conn=connections["places_assets_accounts"],
                    money_conn=connections["money"],
                ),
                represented_head,
            )

        requirements: dict[str, str] = {}
        for name in _XLSX_SOURCE_PROJECTIONS:
            subscribed = runner._projections[name].subscribes_to
            matching: Iterable[str]
            if subscribed == "*":
                matching = trigger_events.values()
            else:
                matching = (
                    event_id
                    for event_type, event_id in trigger_events.items()
                    if event_type in subscribed
                )
            required = max(matching, default="")
            if required:
                requirements[name] = required
        represented_head = await runner.wait_for_projection_snapshot(
            _XLSX_SOURCE_PROJECTIONS,
            requirements,
        )
        await runner.read_snapshot(_XLSX_SOURCE_PROJECTIONS, _invoke)
        return represented_head

    xlsx_projection: XlsxWorkbooksProjection | None = None
    for projection_class in DEFAULT_PROJECTION_CLASSES:
        projection: Projection
        if projection_class is XlsxWorkbooksProjection:
            xlsx_projection = XlsxWorkbooksProjection(
                config,
                None,
                event_log=event_log,
                event_sink=event_sink,
                query_context_factory=_facade_context,
                query_context_runner=_run_context,
            )
            projection = xlsx_projection
        else:
            projection = projection_class()
        runner.register(projection)
    assert xlsx_projection is not None
    return xlsx_projection
