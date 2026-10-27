"""Admission invariants independent of hosted latency measurements."""

import asyncio

import pytest

from adminme.finance.admission import (
    FinanceAdmissionGate,
    FinancePriorityMiddleware,
)


async def test_foreground_is_never_queued_and_finance_waits() -> None:
    gate = FinanceAdmissionGate(quiet_seconds=0)
    entered = asyncio.Event()
    release = asyncio.Event()
    gate.foreground_started()

    async def finance() -> None:
        async with gate.slot(("fixture-tenant", "member-a")) as admitted:
            assert admitted
            entered.set()
            await release.wait()

    task = asyncio.create_task(finance())
    await asyncio.sleep(0)
    assert not entered.is_set()
    gate.foreground_finished()
    await asyncio.wait_for(entered.wait(), timeout=1)
    # New general work still enters immediately while finance holds its lane.
    gate.foreground_started()
    gate.foreground_finished()
    release.set()
    await task


async def test_one_active_four_waiting_fifo_and_member_fairness() -> None:
    gate = FinanceAdmissionGate(quiet_seconds=0)
    observed: list[int] = []

    async def waiter(index: int) -> None:
        async with gate.slot(("fixture-tenant", f"member-{index}")) as admitted:
            assert admitted
            observed.append(index)

    async with gate.slot(("fixture-tenant", "member-active")) as admitted:
        assert admitted
        tasks = [asyncio.create_task(waiter(index)) for index in range(4)]
        await asyncio.sleep(0)
        assert observed == []
        async with gate.slot(("fixture-tenant", "member-overflow")) as overflow:
            assert overflow is False
        async with gate.slot(("fixture-tenant", "member-0")) as duplicate:
            assert duplicate is False
    await asyncio.gather(*tasks)
    assert observed == [0, 1, 2, 3]


async def test_cancelled_waiter_and_active_request_release_capacity() -> None:
    gate = FinanceAdmissionGate(quiet_seconds=0)
    active = asyncio.Event()

    async def held(actor: str) -> None:
        async with gate.slot(("fixture-tenant", actor)) as admitted:
            assert admitted
            active.set()
            await asyncio.Event().wait()

    first = asyncio.create_task(held("member-active"))
    await asyncio.wait_for(active.wait(), timeout=1)
    waiting = asyncio.create_task(held("member-waiting"))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    async with gate.slot(("fixture-tenant", "member-waiting")) as admitted:
        assert admitted


async def test_sustained_general_work_returns_bounded_busy() -> None:
    gate = FinanceAdmissionGate(quiet_seconds=0, wait_seconds=0.001)
    gate.foreground_started()
    async with gate.slot(("fixture-tenant", "member-a")) as admitted:
        assert admitted is False
    gate.foreground_finished()
    async with gate.slot(("fixture-tenant", "member-a")) as admitted:
        assert admitted is True


async def test_streaming_foreground_releases_at_response_headers() -> None:
    gate = FinanceAdmissionGate(quiet_seconds=0)
    headers_sent = asyncio.Event()
    close_stream = asyncio.Event()

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream; charset=utf-8")],
            }
        )
        headers_sent.set()
        await close_stream.wait()
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    middleware = FinancePriorityMiddleware(app, gate)
    stream = asyncio.create_task(
        middleware({"type": "http", "path": "/api/member/v1/chat"}, receive, send)
    )
    await asyncio.wait_for(headers_sent.wait(), timeout=1)
    async with gate.slot(("fixture-tenant", "member-a")) as admitted:
        assert admitted is True
    close_stream.set()
    await stream


async def test_foreground_failure_cleans_observation() -> None:
    gate = FinanceAdmissionGate(quiet_seconds=0)

    async def failing(scope, receive, send):
        raise ValueError("synthetic failure")

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    middleware = FinancePriorityMiddleware(failing, gate)
    with pytest.raises(ValueError, match="synthetic failure"):
        await middleware(
            {"type": "http", "path": "/api/core/v1/what-now"}, receive, send
        )
    async with gate.slot(("fixture-tenant", "member-a")) as admitted:
        assert admitted


async def test_ordinary_response_keeps_priority_through_body_send():
    gate = FinanceAdmissionGate(quiet_seconds=0, wait_seconds=0.001)
    observed = []

    async def send(message):
        async with gate.slot(("fixture", "member")) as admitted:
            observed.append(admitted)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b""}

    await FinancePriorityMiddleware(app, gate)(
        {"type": "http", "path": "/api/core/v1/what-now"}, receive, send
    )
    assert observed == [False, False]
    async with gate.slot(("fixture", "member")) as admitted:
        assert admitted


async def test_active_finance_delivery_yields_to_new_foreground_without_losing_lane():
    gate = FinanceAdmissionGate(quiet_seconds=0)
    async with gate.slot(("fixture", "member-a")) as admitted:
        assert admitted
        gate.foreground_started()
        resumed = asyncio.create_task(gate.yield_to_foreground())
        await asyncio.sleep(0)
        assert not resumed.done()
        gate.foreground_finished()
        assert await asyncio.wait_for(resumed, timeout=1)
        assert gate._active
    assert not gate._active


async def test_delivery_yield_deadline_and_cancellation_preserve_capacity():
    gate = FinanceAdmissionGate(quiet_seconds=0, wait_seconds=0.001)
    gate.foreground_started()
    assert not await gate.yield_to_foreground()
    waiting = asyncio.create_task(gate.yield_to_foreground())
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    gate.foreground_finished()
    assert await gate.yield_to_foreground()
    async with gate.slot(("fixture", "member-a")) as admitted:
        assert admitted
