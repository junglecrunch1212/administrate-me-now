"""Fresh authority reads may reuse SQL plans, never grant values or write rights."""

from __future__ import annotations

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
        if action == sqlcipher3.SQLITE_READ and arg1 == "service_delegation_grants":
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
            with pytest.raises(sqlcipher3.DatabaseError):
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
    original_close = runner._close_parties_reader

    def observe_reader():
        threads.append(threading.get_ident())
        return original_reader()

    def observe_close():
        threads.append(threading.get_ident())
        return original_close()

    monkeypatch.setattr(runner, "_parties_reader", observe_reader)
    monkeypatch.setattr(runner, "_close_parties_reader", observe_close)
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
