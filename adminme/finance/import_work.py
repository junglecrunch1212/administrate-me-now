"""Keep import admission until already-started native work or audit settles."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


async def await_settled(work: Awaitable[T]) -> T:
    """Cancellation is delivered only after the admitted work actually stops.

    Threads cannot be killed by cancelling their asyncio waiter. Shield and
    drain the owned task, including repeated cancellation; never release the
    outer import lock while a detached decoder still owns package buffers.
    """
    worker = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(worker)
    except BaseException:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        with suppress(BaseException):
            worker.result()
        raise


async def run_cpu(function: Callable[..., T], *args: Any) -> T:
    return await await_settled(asyncio.to_thread(function, *args))
