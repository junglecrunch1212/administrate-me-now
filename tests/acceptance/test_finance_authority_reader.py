"""Fresh authority reads may reuse SQL plans, never grant values or write rights."""

from __future__ import annotations

import asyncio
import threading

import pytest
import sqlcipher3

from adminme.events.bus import EventBus
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.projections.parties import PartiesProjection
from adminme.projections.parties.queries import ServiceDelegationResolutionError
from adminme.projections.runner import ProjectionRunner
from tests.unit.test_finance_delegation_snapshot import approve, grant, resolve


async def start_runner(tmp_path):
    config = load_instance_config(tmp_path)
    log = EventLog(config, b"a" * 32)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=b"a" * 32)
    runner.register(PartiesProjection())
    await runner.start()
    return runner, log


@pytest.mark.asyncio
async def test_compiled_authority_query_reuse_still_observes_current_revocation(
    tmp_path, monkeypatch
):
    reads = []
    original = ProjectionRunner._query_only_authorizer

    def observed(action, arg1, arg2, database, source):
        if (
            action == getattr(sqlcipher3, "SQLITE_READ")
            and arg1 == "service_delegation_grants"
        ):
            reads.append(arg2)
        return original(action, arg1, arg2, database, source)

    monkeypatch.setattr(
        ProjectionRunner, "_query_only_authorizer", staticmethod(observed)
    )
    runner, log = await start_runner(tmp_path)
    try:
        writer = runner._unsafe_maintenance_connection("parties")
        grant(writer)
        approve(writer)

        async def current():
            return await runner.read(
                "parties", lambda conn: resolve(conn).audit_payload()
            )

        first = await current()
        first_preparation = len(reads)
        assert first_preparation > 0
        assert await current() == first
        # A stable read-only policy preserves the compiled SQL statement.
        # Grant values still come from the latest committed database state.
        assert len(reads) == first_preparation
        writer.execute(
            "UPDATE service_delegation_grants SET revoked_at='2026-01-02T00:00:00Z'"
        )
        with pytest.raises(ServiceDelegationResolutionError):
            await current()
        assert len(reads) == first_preparation
    finally:
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
async def test_authority_reader_cannot_reuse_writer_privileges_or_disable_read_only(
    tmp_path,
):
    runner, log = await start_runner(tmp_path)
    try:
        writer = runner._unsafe_maintenance_connection("parties")
        reader = runner.connection("parties")
        grant(writer)
        approve(writer)
        deletion = "DELETE FROM service_delegation_grants WHERE grant_id=?"
        writer.execute(deletion, ("not-the-grant",))
        for sql, parameters in (
            (deletion, ("grant-synthetic",)),
            ("PRAGMA query_only=OFF", ()),
            ("ATTACH DATABASE ':memory:' AS shadow", ()),
            ("CREATE TEMP TABLE forbidden(value TEXT)", ()),
            ("BEGIN", ()),
        ):
            with pytest.raises(getattr(sqlcipher3, "DatabaseError")):
                reader.execute(sql, parameters)
        assert resolve(reader).authority_grant_id == "grant-synthetic"
        approve(writer, member="member-extra")
        with pytest.raises(ServiceDelegationResolutionError):
            resolve(reader)
    finally:
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
async def test_authority_reader_preserves_failed_rebuild_and_reopens_after_publish(
    tmp_path, monkeypatch
):
    runner, log = await start_runner(tmp_path)
    threads = []
    original_reader = runner._parties_reader
    original_close = runner._close_parties_reader_native

    def observe_reader():
        threads.append(threading.get_ident())
        return original_reader()

    def observe_close():
        threads.append(threading.get_ident())
        return original_close()

    monkeypatch.setattr(runner, "_parties_reader", observe_reader)
    monkeypatch.setattr(runner, "_close_parties_reader_native", observe_close)
    try:
        writer = runner._unsafe_maintenance_connection("parties")
        grant(writer)
        approve(writer)
        reader = runner.connection("parties")
        assert resolve(reader).authority_grant_id == "grant-synthetic"
        original_publish = runner._publish_staged_projection

        def fail_publish(*args):
            raise RuntimeError("synthetic pre-publication failure")

        monkeypatch.setattr(runner, "_publish_staged_projection", fail_publish)
        with pytest.raises(RuntimeError, match="pre-publication failure"):
            await runner.rebuild("parties")
        assert resolve(reader).authority_grant_id == "grant-synthetic"
        monkeypatch.setattr(runner, "_publish_staged_projection", original_publish)
        await runner.rebuild("parties")
        assert runner._parties_read_conn is None
        # No persisted grant events were seeded: a successful replay is empty.
        # The same facade must reopen the new database, not read the old inode.
        with pytest.raises(ServiceDelegationResolutionError):
            resolve(reader)
        assert runner._parties_read_conn is not None
    finally:
        await runner.stop()
        await log.close()
    assert runner._parties_read_conn is None
    assert len(set(threads)) == 1 and threading.get_ident() not in threads


@pytest.mark.asyncio
async def test_fresh_authority_does_not_queue_behind_unrelated_writer_work(tmp_path):
    runner, log = await start_runner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    held = None
    try:
        writer = runner._unsafe_maintenance_connection("parties")
        grant(writer)
        approve(writer)
        reader = runner.connection("parties")
        assert resolve(reader).authority_grant_id == "grant-synthetic"

        def occupy_writer():
            entered.set()
            assert release.wait(10), "synthetic writer hold was not released"

        held = asyncio.create_task(runner._run_on_worker(occupy_writer))
        assert await asyncio.to_thread(entered.wait, 5)
        # The writer stays blocked throughout this fresh native read. An
        # ordinary writer FIFO implementation cannot finish before release.
        current = await asyncio.wait_for(asyncio.to_thread(resolve, reader), 2)
        assert current.authority_grant_id == "grant-synthetic"
        assert not held.done()
        release.set()
        await held
        writer.execute(
            "UPDATE service_delegation_grants SET revoked_at='2026-01-02T00:00:00Z'"
        )
        with pytest.raises(ServiceDelegationResolutionError):
            resolve(reader)
    finally:
        release.set()
        if held is not None:
            await held
        await runner.stop()
        await log.close()
    assert runner._parties_reader_thread_id not in {
        t.ident for t in threading.enumerate()
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("whole_callback", [False, True])
async def test_authority_read_during_publish_waits_for_new_database(
    tmp_path, monkeypatch, whole_callback
):
    import adminme.projections.runner as runner_module

    runner, log = await start_runner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    rebuilding = reading = None
    original_replace = runner_module.os.replace

    def held_replace(source, target):
        if target == runner._config.projection_db_path("parties"):
            entered.set()
            assert release.wait(10), "synthetic publish hold was not released"
        return original_replace(source, target)

    try:
        writer = runner._unsafe_maintenance_connection("parties")
        grant(writer)
        approve(writer)
        reader = runner.connection("parties")
        assert resolve(reader).authority_grant_id == "grant-synthetic"
        monkeypatch.setattr(runner_module.os, "replace", held_replace)
        rebuilding = asyncio.create_task(runner.rebuild("parties"))
        assert await asyncio.to_thread(entered.wait, 5)
        reading = asyncio.create_task(
            runner.read_authority(lambda conn: resolve(conn).audit_payload())
            if whole_callback
            else asyncio.to_thread(resolve, reader)
        )
        await asyncio.sleep(0.02)
        assert not reading.done()
        release.set()
        await rebuilding
        # The durable log contains no grant. Reopening an old inode here would
        # wrongly disclose the maintenance-only prior grant.
        with pytest.raises(ServiceDelegationResolutionError):
            await reading
    finally:
        release.set()
        if rebuilding is not None:
            await asyncio.gather(rebuilding, return_exceptions=True)
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("whole_callback", [False, True])
async def test_dedicated_reader_cannot_overtake_queued_revocation(
    tmp_path, whole_callback
):
    runner, log = await start_runner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    held = mutation = reading = None
    try:
        writer = runner._unsafe_maintenance_connection("parties")
        grant(writer)
        approve(writer)
        reader = runner.connection("parties")
        assert resolve(reader).authority_grant_id == "grant-synthetic"

        def occupy_writer():
            entered.set()
            assert release.wait(10), "synthetic writer hold was not released"

        held = asyncio.create_task(runner._run_on_worker(occupy_writer))
        assert await asyncio.to_thread(entered.wait, 5)
        mutation = asyncio.create_task(
            asyncio.to_thread(
                writer.execute,
                "UPDATE service_delegation_grants SET revoked_at='2026-01-02T00:00:00Z'",
            )
        )
        for _ in range(100):
            if runner._parties_pending_mutations:
                break
            await asyncio.sleep(0.001)
        assert runner._parties_pending_mutations
        reading = asyncio.create_task(
            runner.read_authority(lambda conn: resolve(conn).audit_payload())
            if whole_callback
            else asyncio.to_thread(resolve, reader)
        )
        await asyncio.sleep(0.02)
        assert not reading.done(), "fresh read overtook an accepted revocation"
        release.set()
        await held
        await mutation
        with pytest.raises(ServiceDelegationResolutionError):
            await reading
        assert runner._parties_pending_mutations == 0
    finally:
        release.set()
        if held is not None:
            await asyncio.gather(held, return_exceptions=True)
        if mutation is not None:
            await asyncio.gather(mutation, return_exceptions=True)
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
async def test_whole_authority_callback_bypasses_unrelated_writer_without_write_access(
    tmp_path,
):
    runner, log = await start_runner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    held = None
    try:
        writer = runner._unsafe_maintenance_connection("parties")
        grant(writer)
        approve(writer)

        def occupy_writer():
            entered.set()
            assert release.wait(10), "synthetic writer hold was not released"

        held = asyncio.create_task(runner._run_on_worker(occupy_writer))
        assert await asyncio.to_thread(entered.wait, 5)

        def authority(conn):
            assert threading.get_ident() == runner._parties_reader_thread_id
            return resolve(conn).audit_payload()

        result = await asyncio.wait_for(runner.read_authority(authority), 2)
        assert result["authority_grant_id"] == "grant-synthetic"
        assert not held.done()
        with pytest.raises(RuntimeError, match="cannot submit projection work"):
            await runner.read_authority(
                lambda _: writer.execute("DELETE FROM service_delegation_grants")
            )
        with pytest.raises(getattr(sqlcipher3, "DatabaseError")):
            await runner.read_authority(
                lambda conn: conn.execute("PRAGMA query_only=OFF")
            )
        with pytest.raises(RuntimeError, match="native SQLCipher handle"):
            await runner.read_authority(lambda _: runner._parties_read_conn)
        assert await runner.read_authority(authority) == result
    finally:
        release.set()
        if held is not None:
            await held
        await runner.stop()
        await log.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_after_cancel", [False, True])
async def test_cancelled_authority_callback_settles_without_blocking_loop_or_close(
    tmp_path,
    fail_after_cancel,
    caplog,
):
    runner, log = await start_runner(tmp_path)
    entered, release = threading.Event(), threading.Event()
    reading = closing = None
    try:

        def held_query(conn):
            entered.set()
            assert release.wait(10), "synthetic authority hold was not released"
            if fail_after_cancel:
                raise RuntimeError("synthetic authority failure after cancellation")
            return conn.execute(
                "SELECT COUNT(*) FROM service_delegation_grants"
            ).fetchone()[0]

        reading = asyncio.create_task(runner.read_authority(held_query))
        assert await asyncio.to_thread(entered.wait, 5)
        reading.cancel()
        await asyncio.sleep(0.01)
        reading.cancel()
        await asyncio.sleep(0.01)
        assert not reading.done(), (
            "cancelled callback escaped before native work settled"
        )
        closing = asyncio.create_task(runner.stop())
        for _ in range(100):
            if runner._closing:
                break
            await asyncio.sleep(0.001)
        assert runner._closing
        assert not closing.done()
        with pytest.raises(RuntimeError, match="closing"):
            await runner.read_authority(lambda conn: None)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await reading
        await closing
        if fail_after_cancel:
            assert "authority query failed after cancellation" in caplog.text
    finally:
        release.set()
        if reading is not None:
            await asyncio.gather(reading, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await runner.stop()
        await log.close()
    assert runner._parties_reader_thread_id not in {
        t.ident for t in threading.enumerate()
    }
