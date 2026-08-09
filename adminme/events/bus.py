"""
L2 Event Bus — in-process pub/sub on top of the event log.

Per ADMINISTRATEME_BUILD.md §"L2: THE EVENT BUS" and SYSTEM_INVARIANTS.md §1
invariant 10.

Design:
- One `EventBus` instance per process.
- Subscribers register with `(subscriber_id, types_of_interest, callback)`.
  `types="*"` subscribes to every event type.
- `start()` spins one asyncio.Task per subscriber. Each task reads from the
  log forward of its durable checkpoint (persisted in a separate unencrypted
  sqlite DB — checkpoint state is not secret), delivers events in order, and
  only advances the checkpoint after the callback returns without raising.
- `notify(event_id)` is called by `EventWriter` after a committed append and wakes every
  subscriber's task so they pull the new event immediately.
- On callback failure the checkpoint is NOT advanced, so the event is
  re-delivered. After `failure_threshold` (default 5) consecutive failures
  the subscriber is marked degraded and receives no further events until
  `reset_subscriber()` clears the state. Subscribers must be idempotent.

This is the `InProcessBus` described in SYSTEM_INVARIANTS.md §1 invariant 10.
A future `RedisStreamsBus` satisfies the same contract for scale-out.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from adminme.events.log import CursorNotFound, EventLog

_log = logging.getLogger(__name__)

DEFAULT_FAILURE_THRESHOLD = 5
LAG_WARN_THRESHOLD = 10_000
RETRY_BACKOFF_S = 0.2
READ_BATCH_SIZE = 500

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class _SubscriberState:
    subscriber_id: str
    types: list[str] | Literal["*"]
    callback: EventCallback
    consumer_version: str | None
    checkpoint: str | None
    degraded: bool
    consecutive_failures: int = 0
    last_success_at_ms: int | None = None
    last_failure_at_ms: int | None = None
    trigger: asyncio.Event = field(default_factory=asyncio.Event)
    lag_warned: bool = False
    poison_event_id: str | None = None

    @property
    def types_filter(self) -> list[str] | None:
        if self.types == "*":
            return None
        return list(self.types)


class EventBus:
    def __init__(
        self,
        log: EventLog,
        checkpoint_db: Path,
        *,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        shutdown_timeout_s: float = 5.0,
    ) -> None:
        self._log = log
        self._checkpoint_db = Path(checkpoint_db)
        self._checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        self._failure_threshold = failure_threshold
        self._shutdown_timeout_s = shutdown_timeout_s
        self._subscribers: dict[str, _SubscriberState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False
        self._closed = False
        self._stopping = asyncio.Event()
        self._ckpt_conn = self._open_checkpoint_db()
        self._ckpt_lock = asyncio.Lock()

    def _open_checkpoint_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._checkpoint_db),
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS bus_consumer_offsets ("
            "  subscriber_id TEXT PRIMARY KEY,"
            "  checkpoint_event_id TEXT,"
            "  last_success_at_ms INTEGER,"
            "  last_failure_at_ms INTEGER,"
            "  consecutive_failures INTEGER NOT NULL DEFAULT 0,"
            "  degraded INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(bus_consumer_offsets)")
        }
        if "poison_event_id" not in columns:
            conn.execute(
                "ALTER TABLE bus_consumer_offsets ADD COLUMN poison_event_id TEXT"
            )
        if "consumer_version" not in columns:
            conn.execute(
                "ALTER TABLE bus_consumer_offsets ADD COLUMN consumer_version TEXT"
            )
        return conn

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def subscribe(
        self,
        subscriber_id: str,
        types: list[str] | Literal["*"],
        callback: EventCallback,
        *,
        consumer_version: str | None = None,
        replay_on_version_change: bool = False,
    ) -> None:
        if self._running:
            raise RuntimeError("subscribe() must be called before start()")
        if self._closed:
            raise RuntimeError("event bus is closed")
        if subscriber_id in self._subscribers:
            raise ValueError(f"subscriber_id already registered: {subscriber_id}")
        if types != "*" and not isinstance(types, list):
            raise TypeError('types must be list[str] or "*"')

        row = self._ckpt_conn.execute(
            "SELECT * FROM bus_consumer_offsets WHERE subscriber_id = ?",
            (subscriber_id,),
        ).fetchone()
        if row is None:
            self._ckpt_conn.execute(
                "INSERT INTO bus_consumer_offsets (subscriber_id, consumer_version) "
                "VALUES (?, ?)",
                (subscriber_id, consumer_version),
            )
            checkpoint: str | None = None
            degraded = False
            failures = 0
            last_ok: int | None = None
            last_fail: int | None = None
            poison_event_id: str | None = None
        else:
            checkpoint = row["checkpoint_event_id"]
            degraded = bool(row["degraded"])
            failures = int(row["consecutive_failures"] or 0)
            last_ok = row["last_success_at_ms"]
            last_fail = row["last_failure_at_ms"]
            poison_event_id = row["poison_event_id"]
            stored_version = row["consumer_version"]
            if stored_version != consumer_version and replay_on_version_change:
                # Full replay is opt-in because only consumers with reviewed,
                # versioned idempotency contracts can safely rebuild derived
                # event generations.  Source facts and prior outputs remain
                # append-only; only this operational cursor is reset.
                checkpoint = None
                degraded = False
                failures = 0
                last_ok = None
                last_fail = None
                poison_event_id = None
                self._ckpt_conn.execute(
                    "UPDATE bus_consumer_offsets "
                    "SET checkpoint_event_id=NULL, degraded=0, "
                    "consecutive_failures=0, poison_event_id=NULL, "
                    "last_success_at_ms=NULL, last_failure_at_ms=NULL, "
                    "consumer_version=? WHERE subscriber_id=?",
                    (consumer_version, subscriber_id),
                )
            elif (
                consumer_version is not None
                and stored_version != consumer_version
                and degraded
            ):
                # A reviewed consumer upgrade is the safe automatic recovery
                # boundary for a durable poison state.  Preserve the last good
                # checkpoint and replay forward under the new version.
                degraded = False
                failures = 0
                poison_event_id = None
                self._ckpt_conn.execute(
                    "UPDATE bus_consumer_offsets "
                    "SET degraded=0, consecutive_failures=0, poison_event_id=NULL, "
                    "consumer_version=? WHERE subscriber_id=?",
                    (consumer_version, subscriber_id),
                )
            elif stored_version != consumer_version:
                self._ckpt_conn.execute(
                    "UPDATE bus_consumer_offsets SET consumer_version=? "
                    "WHERE subscriber_id=?",
                    (consumer_version, subscriber_id),
                )

        self._subscribers[subscriber_id] = _SubscriberState(
            subscriber_id=subscriber_id,
            types=types,
            callback=callback,
            consumer_version=consumer_version,
            checkpoint=checkpoint,
            degraded=degraded,
            consecutive_failures=failures,
            last_success_at_ms=last_ok,
            last_failure_at_ms=last_fail,
            poison_event_id=poison_event_id,
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        if self._closed:
            raise RuntimeError("event bus cannot be restarted after close")
        self._running = True
        self._stopping.clear()
        for sid, state in self._subscribers.items():
            state.trigger.set()
            self._tasks[sid] = asyncio.create_task(
                self._run_subscriber(state), name=f"eventbus:{sid}"
            )

    async def stop(self) -> None:
        if self._closed:
            return
        self._running = False
        self._stopping.set()
        for state in self._subscribers.values():
            state.trigger.set()
        tasks = list(self._tasks.values())
        self._tasks.clear()
        # Let callbacks finish and checkpoint before resorting to cancellation.
        # This prevents a projection SQLCipher operation from racing connection
        # close during normal shutdown.
        _done, pending = (
            await asyncio.wait(tasks, timeout=self._shutdown_timeout_s)
            if tasks
            else (set(), set())
        )
        for task in pending:
            task.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        async with self._ckpt_lock:
            await asyncio.to_thread(self._ckpt_conn.close)
        self._closed = True

    async def notify(self, event_id: str) -> None:
        if not self._running:
            return
        for state in self._subscribers.values():
            if not state.degraded:
                state.trigger.set()

    async def reset_subscriber(self, subscriber_id: str) -> None:
        state = self._subscribers.get(subscriber_id)
        if state is None:
            raise KeyError(subscriber_id)
        state.degraded = False
        state.consecutive_failures = 0
        state.poison_event_id = None
        await self._persist_state(state)
        if self._running:
            # relaunch the task if it exited
            task = self._tasks.get(subscriber_id)
            if task is None or task.done():
                self._tasks[subscriber_id] = asyncio.create_task(
                    self._run_subscriber(state), name=f"eventbus:{subscriber_id}"
                )
        state.trigger.set()

    async def set_checkpoint(
        self,
        subscriber_id: str,
        event_id: str | None,
    ) -> None:
        """Forcibly move a subscriber's checkpoint to ``event_id`` (or NULL).

        Used by ProjectionRunner.rebuild(): after inline-replaying the whole
        log into a freshly-recreated projection DB, we advance the bus
        checkpoint to the latest event so the live worker does not
        re-apply every event it was going to deliver.
        """
        state = self._subscribers.get(subscriber_id)
        if state is None:
            raise KeyError(subscriber_id)
        state.checkpoint = event_id
        await self._persist_state(state)

    async def subscriber_status(self, subscriber_id: str) -> dict[str, Any]:
        state = self._subscribers.get(subscriber_id)
        if state is None:
            raise KeyError(subscriber_id)
        lag = await self._log.count_since(state.checkpoint, types=state.types_filter)
        return {
            "checkpoint_event_id": state.checkpoint,
            "lag_count": lag,
            "last_success_at": state.last_success_at_ms,
            "last_failure_at": state.last_failure_at_ms,
            "consecutive_failures": state.consecutive_failures,
            "degraded": state.degraded,
            "poison_event_id": state.poison_event_id,
            "consumer_version": state.consumer_version,
            "retry_policy": {
                "delivery": "at_least_once",
                "failure_threshold": self._failure_threshold,
                "backoff_seconds": RETRY_BACKOFF_S,
            },
        }

    # ------------------------------------------------------------------
    # subscriber worker loop
    # ------------------------------------------------------------------
    async def _run_subscriber(self, state: _SubscriberState) -> None:
        try:
            while self._running:
                await state.trigger.wait()
                state.trigger.clear()
                if self._stopping.is_set() or not self._running:
                    return
                if state.degraded:
                    return
                await self._drain_once(state)
        except asyncio.CancelledError:
            return

    async def _drain_once(self, state: _SubscriberState) -> None:
        try:
            async for event in self._log.read_since(
                state.checkpoint,
                types=state.types_filter,
                limit=READ_BATCH_SIZE,
            ):
                if self._stopping.is_set() or not self._running:
                    return
                if state.degraded:
                    return
                try:
                    await state.callback(event)
                except Exception:
                    state.consecutive_failures += 1
                    state.last_failure_at_ms = int(time.time() * 1000)
                    _log.exception(
                        "subscriber %s callback raised; checkpoint not advanced",
                        state.subscriber_id,
                    )
                    if state.consecutive_failures >= self._failure_threshold:
                        state.degraded = True
                        state.poison_event_id = event["event_id"]
                        await self._persist_state(state)
                        _log.error(
                            "subscriber %s degraded after %d consecutive failures",
                            state.subscriber_id,
                            state.consecutive_failures,
                        )
                        return
                    await self._persist_state(state)
                    await asyncio.sleep(RETRY_BACKOFF_S)
                    state.trigger.set()
                    return

                state.checkpoint = event["event_id"]
                state.consecutive_failures = 0
                state.last_success_at_ms = int(time.time() * 1000)
                await self._persist_state(state)

            # drained; warn on lag (events mismatched by filter still count as lag)
            lag = await self._log.count_since(
                state.checkpoint, types=state.types_filter
            )
            if lag > LAG_WARN_THRESHOLD and not state.lag_warned:
                _log.warning(
                    "subscriber %s lag=%d exceeds threshold=%d",
                    state.subscriber_id,
                    lag,
                    LAG_WARN_THRESHOLD,
                )
                state.lag_warned = True
            elif lag <= LAG_WARN_THRESHOLD:
                state.lag_warned = False
        except CursorNotFound:
            _log.error(
                "subscriber %s checkpoint %r not found in log; marking degraded",
                state.subscriber_id,
                state.checkpoint,
            )
            state.degraded = True
            state.poison_event_id = state.checkpoint
            await self._persist_state(state)

    async def _persist_state(self, state: _SubscriberState) -> None:
        async with self._ckpt_lock:
            await asyncio.to_thread(self._persist_state_sync, state)

    def _persist_state_sync(self, state: _SubscriberState) -> None:
        self._ckpt_conn.execute(
            "UPDATE bus_consumer_offsets"
            "   SET checkpoint_event_id=?,"
            "       last_success_at_ms=?,"
            "       last_failure_at_ms=?,"
            "       consecutive_failures=?,"
            "       degraded=?,"
            "       poison_event_id=?,"
            "       consumer_version=?"
            " WHERE subscriber_id=?",
            (
                state.checkpoint,
                state.last_success_at_ms,
                state.last_failure_at_ms,
                state.consecutive_failures,
                1 if state.degraded else 0,
                state.poison_event_id,
                state.consumer_version,
                state.subscriber_id,
            ),
        )
