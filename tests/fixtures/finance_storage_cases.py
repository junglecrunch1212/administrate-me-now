"""Fixed synthetic runtime targets; invoked only by the bounded hosted controller."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import resource
import socket
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
import pytest
from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
from adminme.finance.importer import _settled
from adminme.finance.worker_protocol import FINANCE_TYPES, read_json, write_json
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_activation import (
    CONTROL,
    IMPORTS,
    OTHER_HEADERS,
    approve,
    control,
    fixture,
    packet_for,
    upload,
)
from tests.acceptance.test_openclaw_native_seam import DEMO_HEADERS, PRINCIPAL
from tests.fixtures.finance_storage_isolation import SyntheticStorage
from tests.fixtures.finance_provenance import seal_publication

CASES = (
    "atomic_proof",
    "event_before_commit",
    "event_after_commit",
    "money_before_commit",
    "money_after_commit",
    "money_after_ack",
    "revoke_prepared",
    "role_before_commit",
    "revoke_settlement",
    "disclosure",
    "cancel_commit",
    "isolation",
    "ipc_malformed",
    "ipc_oversized",
    "reap_ownership",
    "shared_money",
)


async def inspect(runtime, storage):
    await _settled(runtime)
    counts = await runtime.projections.read(
        "money",
        lambda conn: {
            table: conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
            for table in (
                "finance_provenance_blobs",
                "finance_publications",
                "finance_dated_projections",
                "money_flows",
            )
        },
    )
    receipts = [
        row
        async for row in runtime.event_log.read_since(types=["finance.import_result"])
    ]
    rows = [
        row
        async for row in runtime.event_log.read_since(
            types=sorted(FINANCE_TYPES), metadata_only_types=FINANCE_TYPES
        )
    ]
    return {
        "counts": counts,
        "audit": [
            {"event_id": r["event_id"], "payload": r["payload"]} for r in receipts
        ],
        "finance_events": [
            {k: r[k] for k in ("event_id", "type", "owner_scope", "source_account_id")}
            for r in rows
        ],
        "lifetimes": list(storage.lifetimes),
        "active_executor": storage.process is not None,
        "guards": storage.guards,
        "boundaries": storage.boundaries,
    }


async def replay(runtime, storage):
    # At-least-once delivery uses real durable envelopes and the same single
    # Money write lane. No destructive table reset or second writer is used.
    async with runtime.projections._write_locks["money"]:
        async for event in runtime.event_log.read_since(
            types=list(runtime.bus._subscribers["projection:money"].types),
            metadata_only_types=frozenset(
                runtime.bus._subscribers["projection:money"].types
            ),
        ):
            await storage.apply(event)
    await storage.close()


def small_case(name, root):
    storage = SyntheticStorage(root)
    settings = storage.settings()
    p, d, package = fixture(settings.instance.tenant_id, total_events=24)
    manifest, packet = packet_for(p, d, package)
    patches = pytest.MonkeyPatch()
    app = create_application(settings)
    storage.install(app, patches)
    outcome = {"case": name, "state": "incomplete"}
    try:
        with TestClient(app) as client:
            runtime = app.state.runtime
            control(client, manifest["binding"])
            approval = approve(client, manifest)
            if name in {
                "event_before_commit",
                "event_after_commit",
                "money_before_commit",
                "money_after_commit",
                "money_after_ack",
            }:
                storage.fault = name
            if name in {"revoke_prepared", "role_before_commit", "revoke_settlement"}:
                fired = False

                async def hook(stage):
                    nonlocal fired
                    selected = {
                        "revoke_prepared": "prepared",
                        "role_before_commit": "before_commit",
                        "revoke_settlement": "applying",
                    }[name]
                    if stage != selected or fired:
                        return
                    fired = True
                    if name == "role_before_commit":
                        runtime.settings.member_roles[PRINCIPAL] = "member"
                        return
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app), base_url="http://synthetic"
                    ) as other:
                        result = await other.put(
                            CONTROL,
                            headers=DEMO_HEADERS,
                            json={
                                "request_id": "synthetic-revocation-" + name,
                                "expected_revision": 1,
                                "binding": manifest["binding"],
                                "books_enabled": False,
                                "cos_analysis_enabled": False,
                            },
                        )
                        assert result.status_code == 200, result.text

                storage.hook = hook
            if name == "disclosure":
                from adminme.products.member import finance_import

                audit = finance_import._audit

                async def revoke_at_disclosure(*args, **kwargs):
                    result = await audit(*args, **kwargs)
                    if args[4] == "accepted":
                        runtime.settings.member_roles[PRINCIPAL] = "member"
                    return result

                patches.setattr(finance_import, "_audit", revoke_at_disclosure)
            if name == "cancel_commit":

                async def cancel_owned_commit():
                    entered, release = asyncio.Event(), asyncio.Event()

                    async def hook(stage):
                        if stage == "before_commit":
                            entered.set()
                            await asyncio.wait_for(release.wait(), 5)

                    storage.hook = hook
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app), base_url="http://synthetic"
                    ) as inner:
                        task = asyncio.create_task(
                            inner.post(
                                IMPORTS + approval, headers=DEMO_HEADERS, json=packet
                            )
                        )
                        await asyncio.wait_for(entered.wait(), 10)
                        busy = await inner.post(
                            IMPORTS + approval, headers=DEMO_HEADERS, json={}
                        )
                        assert (
                            busy.status_code == 409
                            and busy.json()["detail"] == "finance_import_busy"
                        )
                        task.cancel()
                        await asyncio.sleep(0)
                        assert runtime.settings_command_locks[
                            "finance-closed-package-import"
                        ].locked()
                        assert storage.process is not None
                        release.set()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass
                        async with asyncio.timeout(10):
                            while (
                                runtime.settings_command_locks[
                                    "finance-closed-package-import"
                                ].locked()
                                or storage.imports
                            ):
                                await asyncio.sleep(0.01)
                        await storage.close()

                client.portal.call(cancel_owned_commit)
                result = None
            elif name in {"ipc_malformed", "ipc_oversized", "reap_ownership"}:

                async def bad_channel():
                    async with storage.lock:
                        await asyncio.to_thread(storage._spawn)
                        process = storage.process
                        if name == "reap_ownership":
                            wait = process.wait

                            def uncertain(*_args, **_kwargs):
                                raise OSError("synthetic wait failure")

                            patches.setattr(process, "wait", uncertain)
                            with pytest.raises(OSError):
                                await asyncio.to_thread(storage._reap, False)
                            assert storage.quarantined and storage.process is process
                            with pytest.raises(
                                Exception, match="synthetic_reap_uncertain"
                            ):
                                storage._spawn()
                            patches.setattr(process, "wait", wait)
                            await asyncio.to_thread(storage._reap, False)
                            assert process.returncode is not None
                            storage.quarantined = (
                                False  # Explicit fixture recovery after verified wait.
                            )
                        else:
                            if name == "ipc_oversized":
                                import struct

                                process.stdin.write(struct.pack("!I", 128 * 1024 + 1))
                                process.stdin.flush()
                            else:
                                write_json(
                                    process.stdin,
                                    {"birth": "wrong", "seq": 1, "op": "commit"},
                                )
                            with pytest.raises((ValueError, OSError)):
                                await asyncio.to_thread(storage._receive)
                            await asyncio.to_thread(storage._reap, False)

                client.portal.call(bad_channel)
                result = None
            else:
                if name == "isolation":
                    denied = upload(client, approval, packet, headers=OTHER_HEADERS)
                    assert denied.status_code == 403, denied.text
                    assert not storage.lifetimes and storage.process is None
                result = upload(client, approval, packet)
            storage.hook = None
            runtime.settings.member_roles[PRINCIPAL] = "principal"
            state = client.portal.call(inspect, runtime, storage)
            before_commit_denied = name in {
                "revoke_prepared",
                "role_before_commit",
                "event_before_commit",
            }
            if before_commit_denied:
                assert result.status_code in {403, 409}, result.text
                assert state["counts"]["finance_publications"] == 0
                assert not state["finance_events"]
                assert state["audit"][-1]["payload"]["outcome"] in {
                    "rejected",
                    "incomplete",
                }
                if name == "event_before_commit":
                    # The failed executor was reaped; fresh admission may retry
                    # the same exact approved package with no partial batch.
                    retry = upload(client, approval, packet)
                    assert retry.status_code == 200, retry.text
            elif name in {"ipc_malformed", "ipc_oversized", "reap_ownership"}:
                assert not state["finance_events"] and not state["audit"]
            else:
                if result is not None:
                    assert result.status_code == (
                        403
                        if name == "disclosure"
                        else 409
                        if name == "revoke_settlement"
                        else 200
                    ), result.text
                assert state["counts"]["finance_publications"] == 1, state
                assert state["counts"]["finance_dated_projections"] == 1
                assert len(state["finance_events"]) == len(package.blobs) + 3
                assert state["audit"], state
                assert all(
                    r["payload"]["provider_effects"] is False for r in state["audit"]
                )
                if name in {"revoke_settlement", "disclosure", "cancel_commit"}:
                    assert state["audit"][-1]["payload"]["outcome"] == "incomplete", (
                        state["audit"]
                    )
                else:
                    receipt = result.json()
                    audit = next(
                        r["payload"]
                        for r in state["audit"]
                        if r["event_id"] == receipt["audit_event_id"]
                    )
                    assert (
                        audit["publication_event_id"]
                        == receipt["accounting"]["event_id"]
                    )
                    assert audit["projection_event_id"] == receipt["dated"]["event_id"]
                    assert audit["outcome"] == "accepted"
                    assert receipt["audit_event_id"] > max(
                        r["event_id"] for r in state["finance_events"]
                    )
                if name in {"atomic_proof", "isolation", "shared_money"}:

                    async def durable_proof():
                        async with storage.lock:
                            ids = await storage._rpc(
                                {
                                    "op": "recover",
                                    "references": [r.wire() for r in storage.refs],
                                }
                            )
                        await storage.close()
                        assert set(ids) == {
                            r["event_id"] for r in state["finance_events"]
                        }
                        from adminme.finance.provenance_store import verify_archived

                        await runtime.projections._read_native(
                            "money", lambda conn: verify_archived(conn, p)
                        )

                    client.portal.call(durable_proof)
                    duplicate = upload(client, approval, packet)
                    assert duplicate.status_code == 200, duplicate.text
                    assert (
                        duplicate.json()["accounting"]["event_id"]
                        == result.json()["accounting"]["event_id"]
                    )
                    client.portal.call(replay, runtime, storage)
                    after = client.portal.call(inspect, runtime, storage)
                    assert after["counts"] == state["counts"]
                    assert after["finance_events"] == state["finance_events"]
                if name == "atomic_proof":
                    # A failed successor has old proof rows already present.
                    # Recovery must distinguish those rows from a partial new
                    # transaction, preserve the old head, then admit a retry.
                    later = deepcopy(p)
                    later["prior_publication_id"] = p["publication_id"]
                    seal_publication(later)
                    new_manifest, new_packet = packet_for(later, None, package)
                    new_approval = approve(client, new_manifest)
                    storage.fault = "event_before_commit"
                    rejected = upload(client, new_approval, new_packet)
                    assert rejected.status_code == 409, rejected.text
                    preserved = client.portal.call(inspect, runtime, storage)
                    assert preserved["finance_events"] == state["finance_events"]
                    assert preserved["counts"] == state["counts"]
                    resumed = upload(client, new_approval, new_packet)
                    assert resumed.status_code == 200, resumed.text
                    assert (
                        resumed.json()["accounting"]["publication_id"]
                        == later["publication_id"]
                    )
                if name == "isolation":
                    from dataclasses import replace

                    async def mismatched():
                        forged = [
                            replace(r, book_id="different-book") for r in storage.refs
                        ]
                        with pytest.raises(
                            Exception, match="synthetic_reference_scope_denied"
                        ):
                            await storage.append(
                                runtime.settings.instance.tenant_id,
                                PRINCIPAL,
                                "different-book",
                                forged,
                                commit_guard=lambda: None,
                            )
                        row = state["finance_events"][-1]
                        coordinates = {
                            **row,
                            "tenant_id": runtime.settings.instance.tenant_id,
                            "visibility_scope": "private:other",
                            "owner_scope": "private:other",
                        }
                        async with storage.lock:
                            with pytest.raises(Exception, match="synthetic_ValueError"):
                                await storage._rpc(
                                    {"op": "apply", "coordinates": coordinates}
                                )
                        await storage.close()

                    client.portal.call(mismatched)
                if name == "shared_money":

                    async def shared():
                        event_id = await runtime.events.write(
                            EventEnvelope(
                                event_at_ms=int(time.time() * 1000),
                                tenant_id=runtime.settings.instance.tenant_id,
                                type="money_flow.recorded",
                                schema_version=1,
                                occurred_at=EventEnvelope.now_utc_iso(),
                                source_adapter="synthetic-ledger",
                                source_account_id="synthetic-account",
                                owner_scope="shared:household",
                                visibility_scope="shared:household",
                                sensitivity="normal",
                                payload={
                                    "flow_id": "synthetic-shared-flow",
                                    "amount_minor": 12500,
                                    "currency": "USD",
                                    "occurred_at": EventEnvelope.now_utc_iso(),
                                    "kind": "received",
                                    "source_adapter": "synthetic-ledger",
                                },
                            )
                        )
                        await _settled(runtime)
                        await replay(runtime, storage)
                        rows = await runtime.projections.read(
                            "money",
                            lambda conn: [
                                tuple(row)
                                for row in conn.execute(
                                    "SELECT flow_id, amount_minor, last_event_id FROM money_flows"
                                )
                            ],
                        )
                        assert rows == [("synthetic-shared-flow", 12500, event_id)], (
                            rows
                        )

                    client.portal.call(shared)
            if name == "event_after_commit":
                assert any(
                    r["stage"] == "commit_ack_recovery" and r["complete"]
                    for r in storage.boundaries
                )
            if name == "event_before_commit":
                assert any(
                    r["stage"] == "commit_ack_recovery" and not r["complete"]
                    for r in storage.boundaries
                )
            if name == "money_after_ack":
                assert any(
                    r["stage"] == "money_ack_before_checkpoint"
                    for r in storage.boundaries
                )
            client.portal.call(storage.close)
            outcome.update(
                state="passed", result=client.portal.call(inspect, runtime, storage)
            )
        assert storage.process is None
        assert all(row["reaped"] for row in storage.lifetimes)
        outcome["lifetimes"] = storage.lifetimes
        return outcome
    finally:
        patches.undo()


async def serve(root):
    import uvicorn

    storage = SyntheticStorage(root)
    settings = storage.settings()
    patches = pytest.MonkeyPatch()
    app = create_application(settings)
    storage.install(app, patches)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, lifespan="on")
    )
    running = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(20):
            while not server.started:
                if running.done():
                    await running
                    raise RuntimeError("synthetic server did not start")
                await asyncio.sleep(0.01)
        write_json(
            sys.stdout.buffer,
            {
                "ready": True,
                "url": f"http://127.0.0.1:{listener.getsockname()[1]}",
                "tenant": settings.instance.tenant_id,
            },
        )
        while True:
            message = await asyncio.to_thread(read_json, sys.stdin.buffer)
            if message == {"op": "inspect"}:
                result = await inspect(app.state.runtime, storage)
            elif message == {"op": "replay"}:
                await replay(app.state.runtime, storage)
                result = await inspect(app.state.runtime, storage)
            elif message == {"op": "shutdown"}:
                await storage.close()
                result = await inspect(app.state.runtime, storage)
                write_json(sys.stdout.buffer, result)
                break
            else:
                raise ValueError("synthetic control command denied")
            write_json(sys.stdout.buffer, result)
    finally:
        await storage.close()
        server.should_exit = True
        await running
        listener.close()
        patches.undo()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    name, root_arg = sys.argv[1:]
    root = Path(root_arg).resolve(strict=True)
    try:
        if name == "large_lifetime":
            asyncio.run(serve(root))
        elif name in CASES:
            write_json(sys.stdout.buffer, small_case(name, root))
        else:
            raise ValueError("unknown fixed synthetic target")
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        raise
