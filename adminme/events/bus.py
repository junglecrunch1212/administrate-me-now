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
- `notify(event_id)` is called after `EventLog.append()` and wakes every
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
    checkpoint: str | None
    degraded: bool
    consecutive_failures: int = 0
    last_success_at_ms: int | None = None
    last_failure_at_ms: int | None = None
    trigger: asyncio.Event = field(default_factory=asyncio.Event)
    lag_warned: bool = False

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
    ) -> None:
        self._log = log
        self._checkpoint_db = Path(checkpoint_db)
        self._checkpoint_db.parent.mkdir(parents=True, exist_ok=True)
        self._failure_threshold = failure_threshold
        self._subscribers: dict[str, _SubscriberState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False
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
        return conn

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def subscribe(
        self,
        subscriber_id: str,
        types: list[str] | Literal["*"],
        callback: EventCallback,
    ) -> None:
        if self._running:
            raise RuntimeError("subscribe() must be called before start()")
        if subscriber_id in self._subscribers:
            raise ValueError(f"subscriber_id already registered: {subscriber_id}")
        if types != "*" and not isinstance(types, list):
            raise TypeError("types must be list[str] or \"*\"")

        row = self._ckpt_conn.execute(
            "SELECT * FROM bus_consumer_offsets WHERE subscriber_id = ?",
            (subscriber_id,),
        ).fetchone()
        if row is None:
            self._ckpt_conn.execute(
                "INSERT INTO bus_consumer_offsets (subscriber_id) VALUES (?)",
                (subscriber_id,),
            )
            checkpoint: str | None = None
            degraded = False
            failures = 0
            last_ok: int | None = None
            last_fail: int | None = None
        else:
            checkpoint = row["checkpoint_event_id"]
            degraded = bool(row["degraded"])
            failures = int(row["consecutive_failures"] or 0)
            last_ok = row["last_success_at_ms"]
            last_fail = row["last_failure_at_ms"]

        self._subscribers[subscriber_id] = _SubscriberState(
            subscriber_id=subscriber_id,
            types=types,
            callback=callback,
            checkpoint=checkpoint,
            degraded=degraded,
            consecutive_failures=failures,
            last_success_at_ms=last_ok,
            last_failure_at_ms=last_fail,
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stopping.clear()
        for sid, state in self._subscribers.items():
            state.trigger.set()
            self._tasks[sid] = asyncio.create_task(
                self._run_subscriber(state), name=f"eventbus:{sid}"
            )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stopping.set()
        for state in self._subscribers.values():
            state.trigger.set()
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        await asyncio.to_thread(self._ckpt_conn.close)

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
        lag = await self._log.count_since(state.checkpoint)
        return {
            "checkpoint_event_id": state.checkpoint,
            "lag_count": lag,
            "last_success_at": state.last_success_at_ms,
            "last_failure_at": state.last_failure_at_ms,
            "consecutive_failures": state.consecutive_failures,
            "degraded": state.degraded,
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
            lag = await self._log.count_since(state.checkpoint)
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
            "       degraded=?"
            " WHERE subscriber_id=?",
            (
                state.checkpoint,
                state.last_success_at_ms,
                state.last_failure_at_ms,
                state.consecutive_failures,
                1 if state.degraded else 0,
                state.subscriber_id,
            ),
        )
