"""Money ownership, non-cyclic queries and repeated-cancellation settlement."""

import asyncio
import threading

import pytest

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.money import MoneyProjection
from adminme.projections.parties import PartiesProjection
from adminme.projections.tasks import TasksProjection
from adminme.projections.runner import ProjectionRunner


async def start(tmp_path):
    config = load_instance_config(tmp_path)
    log = EventLog(config, b"w" * 32)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=b"w" * 32)
    for projection in (MoneyProjection(), PartiesProjection(), TasksProjection()):
        runner.register(projection)
    await runner.start()
    return runner, log


@pytest.mark.asyncio
async def test_money_native_lifecycle_and_cross_projection_snapshots(
    tmp_path, monkeypatch
):
    observed = []
    original = ProjectionRunner._open_projection_db

    def opening(self, name, *args, **kwargs):
        if name == "money":
            observed.append(threading.get_ident())
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(ProjectionRunner, "_open_projection_db", opening)
    runner, log = await start(tmp_path)
    try:
        assert (
            await runner.read(
                "money",
                lambda c: c.execute(
                    "SELECT count(*) FROM finance_publications"
                ).fetchone()[0],
            )
            == 0
        )
        await runner.rebuild("money")
        await runner.status()
        snapshot = await runner.read_snapshot(
            ("money", "tasks"),
            lambda cs: [
                cs[n].execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
                for n in cs
            ],
        )
        assert len(snapshot) == 2 and all(snapshot)
        assert observed and set(observed) == {runner._money_thread_id}
        assert runner._money_thread_id != runner._worker_thread_id
        assert (
            await runner.read_many(
                ("money", "tasks"),
                lambda cs: (
                    cs["money"]
                    .execute("SELECT count(*) FROM finance_publications")
                    .fetchone()[0]
                ),
            )
            == 0
        )
    finally:
        await runner.stop()
        await log.close()
    assert runner._money_thread_id not in {t.ident for t in threading.enumerate()}


@pytest.mark.asyncio
async def test_money_callback_cannot_invert_authority_mutation_fifo(tmp_path):
    runner, log = await start(tmp_path)
    money_entered, release_money = threading.Event(), threading.Event()
    general_entered, release_general = threading.Event(), threading.Event()
    try:

        def held_money(conn):
            money_entered.set()
            assert release_money.wait(5)
            return (
                runner.connection("parties")
                .execute("SELECT count(*) FROM parties")
                .fetchone()
            )

        money = asyncio.create_task(runner.read("money", held_money))
        assert await asyncio.to_thread(money_entered.wait, 2)

        def held_general():
            general_entered.set()
            assert release_general.wait(5)
            # General -> Money is the supported direction.
            return (
                runner.connection("money")
                .execute("SELECT count(*) FROM finance_publications")
                .fetchone()[0]
            )

        general = asyncio.create_task(runner._run_on_worker(held_general))
        assert await asyncio.to_thread(general_entered.wait, 2)
        mutation = runner._submit(
            runner._execute_statement,
            "parties",
            "UPDATE parties SET display_name=display_name",
            (),
            False,
            False,
        )
        assert runner._parties_pending_mutations == 1
        release_general.set()
        release_money.set()
        with pytest.raises(RuntimeError, match="Money callback cannot"):
            await asyncio.wait_for(money, 2)
        assert await asyncio.wait_for(general, 2) == 0
        await asyncio.wrap_future(mutation)
        assert runner._parties_pending_mutations == 0
    finally:
        release_money.set()
        release_general.set()
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
async def test_cancelled_money_work_settles_without_occupying_http_loop(tmp_path):
    runner, log = await start(tmp_path)
    entered, release = threading.Event(), threading.Event()
    try:

        def held(conn):
            entered.set()
            assert release.wait(5)
            return conn.execute("SELECT 1").fetchone()[0]

        reading = asyncio.create_task(runner.read("money", held))
        assert await asyncio.to_thread(entered.wait, 2)
        reading.cancel()
        await asyncio.sleep(0)
        reading.cancel()
        assert (
            await asyncio.wait_for(
                runner.read("tasks", lambda c: c.execute("SELECT 1").fetchone()[0]), 1
            )
            == 1
        )
        assert not reading.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await reading
    finally:
        release.set()
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
async def test_shutdown_waits_for_owned_money_child_without_blocking_loop(tmp_path):
    from adminme.finance.worker import FinanceWorker, worker_usage

    runner, log = await start(tmp_path)
    entered, release = threading.Event(), threading.Event()
    children = []

    def owned_work(conn):
        child = FinanceWorker("shutdown-test", timeout=10)
        children.append(child)
        try:
            entered.set()
            assert release.wait(5)
        finally:
            child.close()

    working = asyncio.create_task(runner.read("money", owned_work))
    stopping = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        stopping = asyncio.create_task(runner.stop())
        await asyncio.sleep(0.02)
        stopping.cancel()
        await asyncio.sleep(0)
        stopping.cancel()
        # A synchronous Future.result()/executor.shutdown on this loop would
        # prevent this timer from completing until the native five-second hold.
        await asyncio.wait_for(asyncio.sleep(0.02), 0.5)
        assert not stopping.done()
        release.set()
        await working
        with pytest.raises(asyncio.CancelledError):
            await stopping
        assert children[0].closed and children[0].process.poll() is not None
        assert worker_usage()["active"] is None
    finally:
        release.set()
        if not working.done():
            await working
        if stopping is not None and not stopping.done():
            with pytest.raises(asyncio.CancelledError):
                await stopping
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
async def test_money_settlement_failure_still_closes_other_projection_handles(
    tmp_path, monkeypatch
):
    runner, log = await start(tmp_path)

    def failed():
        raise RuntimeError("synthetic Money terminal failure")

    monkeypatch.setattr(runner._projections["money"], "finish_delivery", failed)
    try:
        with pytest.raises(RuntimeError, match="Money terminal failure"):
            await runner.stop()
        assert runner._closed and not runner._conns and not runner._staged_conns
        assert runner._money_thread_id not in {t.ident for t in threading.enumerate()}
    finally:
        await log.close()
