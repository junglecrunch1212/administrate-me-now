"""Bounded low-priority admission for light finance HTTP requests.

All methods run on the application's ASGI loop. Non-finance requests never
wait for this gate. This cannot preempt synchronous Python or reserve Gateway
model capacity; expensive work remains disabled independently.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_WAITING_LIGHT_REQUESTS = 4
MAX_ADMISSION_WAIT_SECONDS = 0.250
FOREGROUND_QUIET_SECONDS = 0.005
FINANCE_PATH_PREFIX = "/api/openclaw/v1/tools/finance-"


@dataclass(eq=False)
class _Ticket:
    actor: tuple[str, str]
    earliest_at: float


class FinanceAdmissionGate:
    def __init__(
        self,
        *,
        quiet_seconds: float = FOREGROUND_QUIET_SECONDS,
        wait_seconds: float = MAX_ADMISSION_WAIT_SECONDS,
    ) -> None:
        if not 0 <= quiet_seconds <= FOREGROUND_QUIET_SECONDS:
            raise ValueError("invalid_finance_quiet_period")
        if not 0 < wait_seconds <= MAX_ADMISSION_WAIT_SECONDS:
            raise ValueError("invalid_finance_admission_deadline")
        self._quiet_seconds = quiet_seconds
        self._wait_seconds = wait_seconds
        self._foreground = 0
        self._foreground_finished_at = 0.0
        self._active = False
        self._waiting: deque[_Ticket] = deque()
        self._changed = asyncio.Event()

    def foreground_started(self) -> None:
        self._foreground += 1
        self._changed.set()

    def foreground_finished(self) -> None:
        if self._foreground <= 0:
            raise RuntimeError("unbalanced_finance_foreground_observation")
        self._foreground -= 1
        self._foreground_finished_at = time.monotonic()
        self._changed.set()

    @asynccontextmanager
    async def slot(self, actor: tuple[str, str]) -> AsyncIterator[bool]:
        """After authentication, admit one light request or yield false.

        Each member may occupy at most one waiting slot. FIFO admission avoids
        a busy member repeatedly overtaking other members. The active request
        retains the lane through its required audit and delivery checks.
        """
        if (
            not all(isinstance(value, str) and value for value in actor)
            or len(actor) != 2
        ):
            raise ValueError("verified_finance_actor_required")
        if len(self._waiting) >= MAX_WAITING_LIGHT_REQUESTS or any(
            pending.actor == actor for pending in self._waiting
        ):
            yield False
            return
        requested_at = time.monotonic()
        ticket = _Ticket(actor, requested_at + self._quiet_seconds)
        self._waiting.append(ticket)
        acquired = False
        try:
            deadline = requested_at + self._wait_seconds
            while True:
                now = time.monotonic()
                if now >= deadline:
                    break
                ready_at = max(
                    ticket.earliest_at,
                    self._foreground_finished_at + self._quiet_seconds,
                )
                if (
                    not self._active
                    and self._foreground == 0
                    and self._waiting[0] is ticket
                    and now >= ready_at
                ):
                    self._waiting.popleft()
                    self._active = acquired = True
                    break
                self._changed.clear()
                pause = deadline - now
                if not self._active and self._foreground == 0 and ready_at > now:
                    pause = min(pause, ready_at - now)
                try:
                    await asyncio.wait_for(self._changed.wait(), timeout=pause)
                except TimeoutError:
                    pass
            if not acquired:
                self._waiting.remove(ticket)
                self._changed.set()
            yield acquired
        finally:
            if acquired:
                self._active = False
            elif ticket in self._waiting:
                self._waiting.remove(ticket)
            self._changed.set()


class FinancePriorityMiddleware:
    """Observe ordinary HTTP work without queuing it behind finance.

    Response-start ends the foreground interval, so a long-lived chat stream
    does not deadlock its own finance tools. WebSocket/lifespan scopes pass
    through and are not treated as proof of Gateway compute capacity.
    """

    def __init__(self, app: ASGIApp, gate: FinanceAdmissionGate) -> None:
        self.app = app
        self.gate = gate

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").startswith(
            FINANCE_PATH_PREFIX
        ):
            await self.app(scope, receive, send)
            return
        self.gate.foreground_started()
        finished = False

        async def observed_send(message: Message) -> None:
            nonlocal finished
            if not finished and message["type"] == "http.response.start":
                finished = True
                self.gate.foreground_finished()
            await send(message)

        try:
            await self.app(scope, receive, observed_send)
        finally:
            if not finished:
                self.gate.foreground_finished()


def admission_busy_result() -> dict[str, Any]:
    return {
        "state": "temporarily_unavailable",
        "reason": "finance_core_capacity_busy",
        "next_step": "General household work has priority. Retry this finance request shortly.",
        "provider_effect_count": 0,
    }
