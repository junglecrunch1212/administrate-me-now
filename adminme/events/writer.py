"""The single production event-write contract.

``EventLog`` is encrypted storage and ``EventBus`` is durable delivery.  This
module is the only production seam that joins them: a write is committed to
the log before subscribers are notified.  Consequently an append failure can
never be observed as a committed event, while a crash after commit and before
notification is recovered by the bus from its durable checkpoint on restart.

Delivery is intentionally at-least-once.  Consumers must be idempotent and the
bus advances a checkpoint only after a callback succeeds.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog


class EventWriterClosed(RuntimeError):
    """Raised when new writes are attempted during or after shutdown."""


@runtime_checkable
class EventSink(Protocol):
    """Typed asynchronous contract used by every event-producing component."""

    @property
    def tenant_id(self) -> str | None: ...

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str: ...

    async def write_batch(
        self,
        envelopes: list[EventEnvelope],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[str]: ...


class EventWriter(EventSink):
    """Commit encrypted events, then notify the durable in-process bus.

    The lock keeps a batch's commit/notify boundary ordered relative to other
    writers.  A notification is only a wake-up hint: the bus always reads the
    authoritative committed log from each consumer's checkpoint.
    """

    def __init__(self, event_log: EventLog, bus: EventBus) -> None:
        self._event_log = event_log
        self._bus = bus
        self._write_lock = asyncio.Lock()
        self._accepting = True

    @property
    def tenant_id(self) -> str | None:
        return self._event_log.tenant_id

    @property
    def accepting(self) -> bool:
        return self._accepting

    def stop_accepting(self) -> None:
        """Fail closed for new writes while the runtime drains consumers."""

        self._accepting = False

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        ids = await self.write_batch(
            [envelope],
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return ids[0]

    async def write_batch(
        self,
        envelopes: list[EventEnvelope],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[str]:
        if not envelopes:
            return []
        async with self._write_lock:
            if not self._accepting:
                raise EventWriterClosed("runtime is not accepting event writes")
            # EventLog.append_batch owns one transaction.  If it raises, this
            # method exits before notify and no consumer can mistake the event
            # for committed.
            event_ids = await self._event_log.append_batch(
                envelopes,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            # One wake-up is sufficient because consumers drain the committed
            # log rather than consuming the id supplied to notify().
            await self._bus.notify(
                event_ids[-1], event_types=frozenset(event.type for event in envelopes)
            )
            return event_ids

    async def _write_finance_batch(
        self,
        tenant_id,
        owner_id,
        book_id,
        events,
        *,
        before_commit,
        commit_guard,
        correlation_id=None,
    ):
        async with self._write_lock:
            if not self._accepting:
                raise EventWriterClosed("runtime is not accepting event writes")
            await before_commit()
            ids = await self._event_log._append_finance_batch(
                tenant_id,
                owner_id,
                book_id,
                events,
                commit_guard=commit_guard,
                correlation_id=correlation_id,
            )
            if ids:
                await self._bus.notify(
                    ids[-1], event_types=frozenset(e.type for e in events)
                )
            return ids


__all__ = ["EventSink", "EventWriter", "EventWriterClosed"]
