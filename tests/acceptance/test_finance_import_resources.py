"""One bounded hosted Linux import with synthetic actual-size content.

Measures real HTTP upload, validators, encrypted archival and both projections.
This is not native-device qualification or a replacement for query-load gates.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import gc
import json
import math
import multiprocessing
import os
from pathlib import Path
import platform
import resource
import shutil
import socket
import threading
import time
import tracemalloc
from uuid import uuid4

import httpx
import pytest
import uvicorn

from adminme.runtime.application import create_application
from adminme.openclaw_topology import member_agent_id
from tests.acceptance.test_finance_activation import (
    APPROVALS,
    CONTROL,
    IMPORTS,
    configured,
    packet_for,
)
from tests.acceptance.test_finance_authority_memory import _memory_snapshot
from tests.acceptance.test_openclaw_native_seam import (
    DEMO_HEADERS,
    PRINCIPAL,
    OPENCLAW_HEADERS,
    _provenance,
)
from tests.fixtures.finance_import_load import sized_package

# Exercises shipping availability; kill-switch tests explicitly disable it.
pytestmark = pytest.mark.usefixtures("unreleased_finance_advisor")


SERVER_CPU_SECONDS = 90
SERVER_RSS_BYTES = 512 * 1024 * 1024
SERVER_WALL_SECONDS = 180


def _server(root, listener, channel, telemetry, allocation_profile=False):
    resource.setrlimit(resource.RLIMIT_CPU, (SERVER_CPU_SECONDS, SERVER_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from adminme.finance.worker import worker_usage
    from adminme.events.writer import EventWriter
    from adminme.products.member import finance_import

    started = time.monotonic()
    write_lock = threading.Lock()
    stop = threading.Event()
    violations = []
    allocation_baseline = None
    allocator_baseline = None
    allocator = attribution = None
    trace_python = (
        allocation_profile and os.getenv("ADMINME_FINANCE_IMPORT_PYTHON_TRACE") == "1"
    )
    if allocation_profile:
        from tests.fixtures.finance_import_attribution import (
            AllocatorProbe,
            CommitAttribution,
            request_kind,
        )

        allocator = AllocatorProbe(telemetry.parent)
        attribution = CommitAttribution(telemetry.with_name("import-attribution.jsonl"))

    def sample(event="sample", stage="runtime", **extra):
        usage = resource.getrusage(resource.RUSAGE_SELF)
        row = {
            "at": time.monotonic(),
            "event": event,
            "stage": stage,
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "peak_rss_bytes": usage.ru_maxrss * 1024,
            **_memory_snapshot(),
            "finance_workers": worker_usage(),
            **(
                {
                    "traced_python_bytes": tracemalloc.get_traced_memory()[0],
                    "traced_python_peak_bytes": tracemalloc.get_traced_memory()[1],
                    "tracer_metadata_bytes": tracemalloc.get_tracemalloc_memory(),
                }
                if tracemalloc.is_tracing()
                else {}
            ),
            **extra,
        }
        if (
            allocator is not None
            and attribution is not None
            and attribution.enabled
            and event in {"start", "end"}
            and stage in {"prepare_import", "atomic_finance_commit"}
        ):
            # Four bounded native snapshots; their diagnostic overhead is not
            # part of the preserved uninstrumented acceptance measurement.
            row["native_allocator"] = allocator.snapshot(stage + "_" + event)
        reaped = resource.getrusage(resource.RUSAGE_CHILDREN)
        row["reaped_child_cpu_seconds"] = reaped.ru_utime + reaped.ru_stime
        child = row["finance_workers"]["active"] or {}
        row["aggregate_rss_bytes"] = row["VmRSS_bytes"] + child.get("rss_bytes", 0)
        row["aggregate_cpu_seconds"] = row["cpu_seconds"] + max(
            row["finance_workers"]["completed_cpu_seconds"]
            + child.get("cpu_seconds", 0),
            row["reaped_child_cpu_seconds"],
        )
        with write_lock, telemetry.open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
        return row

    def sync_stage(original, name):
        def wrapped(*args, **kwargs):
            sample("start", name)
            try:
                return original(*args, **kwargs)
            finally:
                sample("end", name)

        return wrapped

    def async_stage(original, name):
        async def wrapped(*args, **kwargs):
            sample("start", name)
            try:
                return await original(*args, **kwargs)
            finally:
                sample("end", name)

        return wrapped

    # Observe the production boundaries; child work remains in aggregate
    # telemetry and is never relabelled as parent CPU or missing work.
    finance_import.prepare_import = async_stage(
        finance_import.prepare_import, "prepare_import"
    )
    EventWriter._write_finance_batch = async_stage(
        EventWriter._write_finance_batch, "atomic_finance_commit"
    )
    original_lock = finance_import.control_lock

    @asynccontextmanager
    async def measured_lock(runtime, session):
        lock = runtime.settings_command_locks.get("finance-closed-package-import")
        importing = lock is not None and lock.locked()
        async with original_lock(runtime, session):
            if importing:
                sample("start", "owner_control_critical_section")
            try:
                yield
            finally:
                if importing:
                    sample("end", "owner_control_critical_section")

    finance_import.control_lock = measured_lock
    from adminme.finance import release

    release.ADVISOR_AVAILABLE = (
        True  # Isolated legacy advisor diagnostic, not release evidence.
    )
    app = create_application(configured(root))
    lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def measured_lifespan(application):
        async with lifespan(application):
            runtime = application.state.runtime
            if attribution is not None:
                attribution.install(runtime)

            async def commands():
                nonlocal allocation_baseline, allocator_baseline
                while True:
                    if channel.poll():
                        command = channel.recv()
                        if command == "stop":
                            server.should_exit = True
                            return
                        if command == "begin_allocation_profile":
                            assert allocation_profile and allocator_baseline is None
                            assert allocator is not None and attribution is not None
                            allocator_baseline = allocator.snapshot("before_import")
                            attribution.enabled = True
                            if trace_python:
                                tracemalloc.start(1)
                                allocation_baseline = tracemalloc.take_snapshot()
                            channel.send({"allocation_profile_started": True})
                        if command == "snapshot":
                            before = sample("snapshot", "retained_before_gc")
                            gc.collect()
                            after = sample("snapshot", "retained_after_gc")

                            # Keep the original post-GC reading, then separately
                            # observe bounded subscriber settlement and collect again.
                            # No checkpoint, history or consumer is changed here.
                            async def subscriber_state():
                                return {
                                    name: await runtime.bus.subscriber_status(name)
                                    for name in tuple(runtime.bus._subscribers)
                                }

                            drain_started = time.monotonic()
                            initial = await subscriber_state()
                            latest = initial
                            while (
                                any(v["lag_count"] for v in latest.values())
                                and time.monotonic() - drain_started < 5
                            ):
                                await asyncio.sleep(0.05)
                                latest = await subscriber_state()
                            await asyncio.sleep(0.05)
                            gc.collect()
                            drained = sample(
                                "snapshot", "retained_after_subscriber_drain_gc"
                            )
                            retained = {
                                "before_gc": before,
                                "after_gc": after,
                                "subscriber_initial": initial,
                                "subscriber_final": latest,
                                "subscriber_observation_seconds": time.monotonic()
                                - drain_started,
                                "after_subscriber_drain_gc": drained,
                            }
                            if allocator_baseline is not None:
                                assert allocator is not None and attribution is not None
                                allocator_retained = allocator.snapshot("after_drain")
                                retained["python_tracing_enabled"] = trace_python
                                if allocation_baseline is not None:
                                    allocation_now = tracemalloc.take_snapshot()

                                    def detail(stat):
                                        return {
                                            "location": str(stat.traceback[0]),
                                            "bytes": stat.size,
                                            "count": stat.count,
                                            "bytes_diff": getattr(
                                                stat, "size_diff", None
                                            ),
                                            "count_diff": getattr(
                                                stat, "count_diff", None
                                            ),
                                        }

                                    retained["allocation_profile"] = {
                                        "diagnostic_only": True,
                                        "tracing_depth": 1,
                                        "python_tracked_bytes": sum(
                                            x.size
                                            for x in allocation_now.statistics("lineno")
                                        ),
                                        "baseline_python_tracked_bytes": sum(
                                            x.size
                                            for x in allocation_baseline.statistics(
                                                "lineno"
                                            )
                                        ),
                                        "largest_live_allocations": [
                                            detail(x)
                                            for x in allocation_now.statistics(
                                                "lineno"
                                            )[:40]
                                        ],
                                        "largest_retained_differences": [
                                            detail(x)
                                            for x in allocation_now.compare_to(
                                                allocation_baseline, "lineno"
                                            )[:40]
                                        ],
                                        "native_allocations_and_allocator_free_pages": "not attributed by tracemalloc",
                                    }
                                # A separate causal probe, after every original
                                # retention observation. Never use these values
                                # to qualify the unmodified production workload.
                                assert all(
                                    not value["lag_count"]
                                    and not value["draining"]
                                    and not value["degraded"]
                                    for value in latest.values()
                                )
                                assert worker_usage()["active"] is None

                                def release_owned_cache(conn, name):
                                    if conn.in_transaction:
                                        raise AssertionError(
                                            "cache probe needs a settled connection"
                                        )

                                    def query(sql):
                                        cursor = conn.execute(sql)
                                        try:
                                            return cursor.fetchall()
                                        finally:
                                            cursor.close()

                                    assert "shrink_memory" in {
                                        row[0] for row in query("PRAGMA pragma_list")
                                    }
                                    settings = {
                                        key: query("PRAGMA " + key)[0][0]
                                        for key in (
                                            "cache_size",
                                            "page_size",
                                            "cache_spill",
                                        )
                                    }
                                    allocator_before = allocator.snapshot(
                                        name + "_before"
                                    )
                                    before_release = sample(
                                        "diagnostic",
                                        "cache_release_" + name + "_before",
                                    )
                                    query("PRAGMA shrink_memory")
                                    after_release = sample(
                                        "diagnostic", "cache_release_" + name + "_after"
                                    )
                                    assert settings == {
                                        key: query("PRAGMA " + key)[0][0]
                                        for key in settings
                                    }
                                    return {
                                        "connection": name,
                                        "settings": settings,
                                        "before": before_release,
                                        "after": after_release,
                                        "allocator_before": allocator_before,
                                        "allocator_after": allocator.snapshot(
                                            name + "_after"
                                        ),
                                    }

                                native_cache = [
                                    await runtime.event_log._run_locked(
                                        lambda: release_owned_cache(
                                            runtime.event_log._conn, "event_log"
                                        )
                                    ),
                                    await runtime.projections._read_native(
                                        "money",
                                        lambda conn: release_owned_cache(conn, "money"),
                                    ),
                                ]
                                retained["native_cache_diagnostic"] = {
                                    "diagnostic_only": True,
                                    "after_original_retention_observations": True,
                                    "connections": native_cache,
                                }
                                retained["commit_attribution"] = attribution.finish()
                                allocator_before_trim = allocator.snapshot(
                                    "before_trim"
                                )
                                before_trim = sample(
                                    "diagnostic", "allocator_trim_before"
                                )
                                trim_result = allocator.trim_once()
                                after_trim = sample(
                                    "diagnostic", "allocator_trim_after"
                                )
                                retained["allocator_probe"] = {
                                    "diagnostic_only": True,
                                    "after_original_retention_observations": True,
                                    "baseline": allocator_baseline,
                                    "after_drain": allocator_retained,
                                    "before_trim": allocator_before_trim,
                                    "after_trim": allocator.snapshot("after_trim"),
                                    "trim_result": trim_result,
                                    "rss_before_trim": before_trim["VmRSS_bytes"],
                                    "rss_after_trim": after_trim["VmRSS_bytes"],
                                }
                            channel.send({"snapshot": retained})
                    tick_started = time.monotonic()
                    await asyncio.sleep(0.02)
                    if attribution is not None and attribution.enabled:
                        attribution.record(
                            "event_loop_tick_20ms", tick_started, context="observer"
                        )

            command_task = asyncio.create_task(commands())
            actor, session_key = runtime.member_chat_binding(PRINCIPAL)
            channel.send(
                {
                    "ready": True,
                    "tenant": runtime.settings.instance.tenant_id,
                    "actor": actor,
                    "session_key": session_key,
                }
            )
            sample("ready")
            try:
                yield
            finally:
                command_task.cancel()
                try:
                    await command_task
                except asyncio.CancelledError:
                    pass
                receipts = [
                    row
                    async for row in runtime.event_log.read_since(
                        types=["finance.import_result"]
                    )
                ]

                def durable_outputs(conn):
                    counts = {
                        name: conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                        for name in (
                            "finance_provenance_blobs",
                            "finance_publications",
                            "finance_dated_projections",
                        )
                    }
                    outputs = {}
                    for kind, table, identifier in (
                        ("accounting", "finance_publications", "publication_id"),
                        ("dated", "finance_dated_projections", "projection_id"),
                    ):
                        outputs[kind] = [
                            {"id": row[0], "event_id": row[1]}
                            for row in conn.execute(
                                f"SELECT {identifier},last_event_id FROM {table}"
                            ).fetchall()
                        ]
                    return counts, outputs

                counts, outputs = await runtime.projections.read(
                    "money", durable_outputs
                )
                channel.send(
                    {
                        "terminal": {
                            "receipts": [
                                {"event_id": r["event_id"], "payload": r["payload"]}
                                for r in receipts
                            ],
                            "counts": counts,
                            "outputs": outputs,
                            "violations": violations,
                        }
                    }
                )

    app.router.lifespan_context = measured_lifespan

    class UploadObservation:
        async def __call__(self, scope, receive, send):
            upload = scope.get("path", "").startswith(IMPORTS)
            first = True

            async def observed_receive():
                nonlocal first
                message = await receive()
                if upload and first and message["type"] == "http.request":
                    first = False
                    sample("start", "upload")
                if (
                    upload
                    and message["type"] == "http.request"
                    and not message.get("more_body")
                ):
                    sample("end", "upload")
                return message

            if (
                attribution is not None
                and scope.get("path") == "/api/member/v1/surfaces/reminders"
            ):
                token = request_kind.set("core_reminders")
                try:
                    await attribution.async_call(
                        "http_core_reminders", app, scope, observed_receive, send
                    )
                finally:
                    request_kind.reset(token)
            else:
                await app(scope, observed_receive, send)

    server = uvicorn.Server(
        uvicorn.Config(UploadObservation(), log_level="error", access_log=False)
    )

    def monitor():
        while not stop.wait(0.05):
            current = sample()
            if (
                current["aggregate_rss_bytes"] > SERVER_RSS_BYTES
                or current["aggregate_cpu_seconds"] > SERVER_CPU_SECONDS
                or time.monotonic() - started > SERVER_WALL_SECONDS
            ):
                violations.append("cooperative_runtime_resource_limit")
                sample("limit", reason=violations[-1])
                server.should_exit = True
                return

    thread = threading.Thread(target=monitor, name="synthetic-import-sampler")
    thread.start()
    try:
        server.run(sockets=[listener])
    finally:
        stop.set()
        thread.join(2)
        sample("stopped", sampler_stopped=not thread.is_alive())
        if attribution is not None:
            attribution.close()
        channel.close()
        listener.close()


def _records(path):
    if not path.exists():
        return []
    # A sampler may be appending the last line. Read only complete records.
    return [
        json.loads(line)
        for line in path.read_text().splitlines(keepends=True)
        if line.endswith("\n")
    ]


async def _receive(channel, timeout=30):
    assert await asyncio.to_thread(channel.poll, timeout), (
        "owned runtime result missing"
    )
    return channel.recv()


async def _stage(path, name, timeout=30, event="start"):
    async with asyncio.timeout(timeout):
        while True:
            found = [
                r for r in _records(path) if r["event"] == event and r["stage"] == name
            ]
            if found:
                return found[-1]
            await asyncio.sleep(0.02)


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_IMPORT_RESOURCES") != "1"
    or platform.system() != "Linux",
    reason="Explicit hosted Linux synthetic import-size measurement only",
)
async def test_actual_size_closed_import_core_responsiveness_and_revocation(tmp_path):
    await _measure_import(tmp_path, allocation_profile=False)


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_IMPORT_ALLOCATIONS") != "1"
    or platform.system() != "Linux",
    reason="Explicit separate hosted import allocation diagnostic only",
)
async def test_closed_import_allocation_diagnostic(tmp_path):
    await _measure_import(tmp_path, allocation_profile=True)


async def _measure_import(tmp_path, *, allocation_profile):
    output = Path(os.environ["FINANCE_IMPORT_ARTIFACT_DIR"])
    output.mkdir(parents=True, exist_ok=True)
    telemetry = output / "import-runtime-samples.jsonl"
    assert not telemetry.exists(), "one cohort per fresh evidence directory"
    root = tmp_path / "owned-synthetic-import"
    report = {
        "state": "incomplete",
        "candidate": os.environ["FINANCE_CANDIDATE_SHA"],
        "checkout": os.environ["GITHUB_SHA"],
        "environment": "hosted-linux",
        "native_qualification": False,
        "allocation_profile": allocation_profile,
        "python_tracing_enabled": allocation_profile
        and os.getenv("ADMINME_FINANCE_IMPORT_PYTHON_TRACE") == "1",
        "allocation_overhead_excluded_from_acceptance": allocation_profile,
        "diagnostic_containment_not_acceptance_policy": True,
        "sustained_load_qualification": False,
        "core_cadence": "one in-flight request, 4 Hz target; raw gaps retained; no retry",
        "whole_os_tree_accounting": False,
        "limits": {
            "server_cpu_seconds": SERVER_CPU_SECONDS,
            "server_rss_cooperative_bytes": SERVER_RSS_BYTES,
            "server_wall_cooperative_seconds": SERVER_WALL_SECONDS,
            "import_http_seconds": 120,
            "core_request_timeout_seconds": 2,
            "concurrent_imports": 1,
            "core_inflight": 1,
        },
        "errors": [],
        "core": [],
        "cleanup": {},
    }
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    listener = socket.socket()
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    process = context.Process(
        target=_server, args=(root, listener, child, telemetry, allocation_profile)
    )
    process.start()
    child.close()
    listener.close()
    core_task = None
    network_tasks = []
    release_upload = asyncio.Event()
    finished = asyncio.Event()
    try:
        ready = await _receive(parent)
        assert ready["ready"]
        # Fixture generation belongs to the caller, never the runtime's RSS/CPU.
        p, d, package = await asyncio.to_thread(
            sized_package, ready["tenant"], PRINCIPAL
        )
        manifest, packet = packet_for(p, d, package)
        wire = json.dumps(packet, separators=(",", ":")).encode()
        report["sizes"] = {
            "provenance_blob_count": len(package.blobs),
            "provenance_blobs_bytes": sum(map(len, package.blobs.values())),
            "provenance_index_bytes": len(package.index),
            "accounting_envelope_bytes": manifest["publication"]["bytes"],
            "dated_payload_bytes": manifest["projection"]["bytes"],
            "encoded_request_bytes": len(wire),
            "accepted_units": len(p["accepted_journal_revision_ids"]),
            "dated_events": len(d["detail_events"]),
            "shared_variants": len(d.get("shared_event_inventory", [])),
            "source_event_ids": len(
                {row["event_id"] for row in d.get("shared_event_inventory", [])}
            ),
            "context_references": sum(
                len(context.get("record_ids", [])) for context in d["detail_manifest"]
            ),
        }
        report["expected_outputs"] = {
            "accounting": p["publication_id"],
            "dated": d["projection_id"],
        }
        del p, d, package, packet
        gc.collect()
        async with httpx.AsyncClient(
            base_url=url,
            trust_env=False,
            timeout=120,
            limits=httpx.Limits(max_connections=5),
        ) as client:
            control_body = {
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "binding": manifest["binding"],
                "books_enabled": True,
                "cos_analysis_enabled": True,
            }
            enabled = await client.put(CONTROL, headers=DEMO_HEADERS, json=control_body)
            assert enabled.status_code == 200, enabled.text
            approval = await client.post(
                APPROVALS,
                headers=DEMO_HEADERS,
                json={
                    "request_id": str(uuid4()),
                    "expected_policy_revision": enabled.json()["revision"],
                    "manifest": manifest,
                },
            )
            assert approval.status_code == 200, approval.text
            target = IMPORTS + approval.json()["approval_event_id"]
            grant = await client.post(
                "/api/member/v1/cos/delegations",
                headers=DEMO_HEADERS,
                json={
                    "request_id": str(uuid4()),
                    "context_scope": "member_private",
                    "permissions": ["read_context"],
                },
            )
            assert grant.status_code in {200, 201}, grant.text
            before_grants = (
                await client.get("/api/member/v1/cos/delegations", headers=DEMO_HEADERS)
            ).json()
            phase = "before"

            async def core():
                while not finished.is_set():
                    began = time.monotonic()
                    row = {"at": began, "phase": phase}
                    try:
                        response = await client.get(
                            "/api/member/v1/surfaces/reminders",
                            headers=DEMO_HEADERS,
                            timeout=2,
                        )
                        row["status"] = response.status_code
                    except Exception as exc:
                        row["error"] = type(exc).__name__
                    row["milliseconds"] = (time.monotonic() - began) * 1000
                    report["core"].append(row)
                    await asyncio.sleep(max(0, 0.25 - (time.monotonic() - began)))

            core_task = asyncio.create_task(core())
            await asyncio.sleep(2)
            phase = "import"

            async def body():
                yield wire[:65536]
                await release_upload.wait()
                for offset in range(65536, len(wire), 65536):
                    yield wire[offset : offset + 65536]
                    await asyncio.sleep(0)

            began = time.monotonic()
            if allocation_profile:
                parent.send("begin_allocation_profile")
                assert (await _receive(parent))["allocation_profile_started"]
            importing = asyncio.create_task(
                client.post(
                    target,
                    headers={**DEMO_HEADERS, "Content-Type": "application/json"},
                    content=body(),
                )
            )
            network_tasks.append(importing)
            await _stage(telemetry, "upload", 5)
            busy = await client.post(
                target,
                headers={**DEMO_HEADERS, "Content-Type": "application/json"},
                content=b"{}",
                timeout=2,
            )
            report["concurrent_admission"] = {
                "status": busy.status_code,
                "response": busy.json(),
            }
            assert (
                busy.status_code == 409
                and busy.json()["detail"] == "finance_import_busy"
            )
            report["upload_admission_probe_hold_seconds"] = time.monotonic() - began
            release_upload.set()
            await _stage(telemetry, "atomic_finance_commit", 60, event="end")
            report["revocation_phase"] = (
                "after_atomic_commit_during_projection_settlement"
            )

            async def revoke():
                started_at = time.monotonic()
                report["owner_revocation_started_at"] = started_at
                try:
                    return await client.put(
                        CONTROL,
                        headers=DEMO_HEADERS,
                        json={
                            **control_body,
                            "request_id": str(uuid4()),
                            "expected_revision": 1,
                            "books_enabled": False,
                            "cos_analysis_enabled": False,
                        },
                    )
                finally:
                    report["owner_revocation_completed_at"] = time.monotonic()
                    report["owner_revocation_wait_seconds"] = (
                        report["owner_revocation_completed_at"] - started_at
                    )

            revoking = asyncio.create_task(revoke())
            network_tasks.append(revoking)
            import_response = await asyncio.wait_for(importing, 120)
            report["import_seconds"] = time.monotonic() - began
            report["import_status"] = import_response.status_code
            report["import_result"] = import_response.json()
            revoked = await asyncio.wait_for(revoking, 120)
            assert revoked.status_code == 200, revoked.text
            # The commit precedes revocation, but disclosure follows settlement.
            # Verify committed outputs through the owned fixture's terminal
            # channel; the revoked HTTP caller must receive no output receipt.
            assert import_response.status_code == 409, import_response.text
            assert import_response.json() == {"detail": "finance_import_policy_changed"}
            control = (await client.get(CONTROL, headers=DEMO_HEADERS)).json()
            assert not control["books_enabled"] and not control["cos_analysis_enabled"]
            denied = await client.get(
                "/api/member/v1/finance/choices", headers=DEMO_HEADERS
            )
            assert denied.status_code == 403
            phase = "after"
            await asyncio.sleep(2)
            finished.set()
            await core_task
            core_task = None
            # Read the final denial without competing with the foreground
            # capacity gate; a busy response would not prove revocation.
            provenance = _provenance("sized-import-revoked")
            provenance.update(
                actor_id=ready["actor"],
                session_key=ready["session_key"],
                agent_id=member_agent_id(PRINCIPAL),
                channel_id="administrateme-portal",
                account_id="member-portal",
                message_id="finance-run:sized-import-revoked",
                thread_id=None,
            )
            cos = await client.post(
                "/api/openclaw/v1/tools/finance-status",
                headers=OPENCLAW_HEADERS,
                json={"provenance": provenance},
            )
            assert cos.status_code == 200, cos.text
            assert (
                cos.json()["state"] == "permission_required"
                and "publication" not in cos.json()
            )
            assert (
                await client.get("/api/member/v1/cos/delegations", headers=DEMO_HEADERS)
            ).json() == before_grants
            report["revocation_readback"] = {
                "revision": control["revision"],
                "books_enabled": False,
                "cos_analysis_enabled": False,
                "choices_status": denied.status_code,
                "cos_state": cos.json()["state"],
                "independent_grant_preserved": True,
            }
            parent.send("snapshot")
            report["retained"] = (await _receive(parent))["snapshot"]
            if not allocation_profile:
                assert all(r.get("status") == 200 for r in report["core"]), report[
                    "core"
                ]
            # Traced timing remains raw diagnostic evidence, never a resource pass.
            assert len([r for r in report["core"] if r["phase"] == "import"]) >= 4
            report["state"] = (
                "diagnostic_completed" if allocation_profile else "completed"
            )
    except BaseException as exc:
        report["errors"].append(type(exc).__name__ + ": " + str(exc)[:500])
        raise
    finally:
        finished.set()
        release_upload.set()
        for task in network_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*network_tasks, return_exceptions=True)
        if core_task is not None:
            core_task.cancel()
            await asyncio.gather(core_task, return_exceptions=True)
        forced = False
        if process.is_alive():
            try:
                parent.send("stop")
                if await asyncio.to_thread(parent.poll, 15):
                    report["terminal"] = parent.recv()
            except (EOFError, BrokenPipeError, OSError) as exc:
                report["errors"].append("terminal_channel:" + type(exc).__name__)
        await asyncio.to_thread(process.join, 15)
        if process.is_alive():
            forced = True
            process.terminate()
            await asyncio.to_thread(process.join, 5)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join, 5)
        parent.close()
        report["cleanup"] = {
            "child_exitcode": process.exitcode,
            "forced": forced,
            "child_stopped": not process.is_alive(),
        }
        if not process.is_alive():
            shutil.rmtree(root, ignore_errors=True)
        report["cleanup"]["instance_removed"] = not root.exists()
        rows = _records(telemetry)
        report["stage_samples"] = [
            r for r in rows if r["event"] in {"start", "end", "limit", "stopped"}
        ]
        report["stages"] = {}
        for row in report["stage_samples"]:
            name = row["stage"]
            if row["event"] == "start":
                report["stages"][name] = {"start": row}
            elif row["event"] == "end" and name in report["stages"]:
                start = report["stages"][name]["start"]
                report["stages"][name].update(
                    end=row,
                    wall_seconds=row["at"] - start["at"],
                    process_cpu_seconds=row["cpu_seconds"] - start["cpu_seconds"],
                    sampled_peak_rss_bytes=max(
                        r["VmRSS_bytes"]
                        for r in rows
                        if start["at"] <= r["at"] <= row["at"]
                    ),
                )
        report["core_summary"] = {}
        for phase_name in ("before", "import", "after"):
            group = [r for r in report["core"] if r["phase"] == phase_name]
            values = sorted(r["milliseconds"] for r in group)
            report["core_summary"][phase_name] = {
                "count": len(group),
                "successful": sum(r.get("status") == 200 for r in group),
                "p95_ms": values[math.ceil(len(values) * 0.95) - 1] if values else None,
                "max_ms": max(values, default=None),
                "largest_start_gap_seconds": max(
                    (b["at"] - a["at"] for a, b in zip(group, group[1:])), default=None
                ),
            }
        if "sizes" in report:
            report["sizes"]["provenance_total_bytes"] = (
                report["sizes"]["provenance_blobs_bytes"]
                + report["sizes"]["provenance_index_bytes"]
            )
        report["server_peak_rss_bytes"] = max(
            (r["peak_rss_bytes"] for r in rows), default=0
        )
        report["server_cpu_seconds"] = max((r["cpu_seconds"] for r in rows), default=0)
        report["aggregate_cpu_seconds"] = max(
            (r["aggregate_cpu_seconds"] for r in rows), default=0
        )
        report["aggregate_sampled_peak_rss_bytes"] = max(
            (r["aggregate_rss_bytes"] for r in rows), default=0
        )
        workers = rows[-1]["finance_workers"] if rows else {}
        report["finance_workers"] = workers
        # Conservative peak bound also includes a short child peak between
        # samples; it deliberately does not subtract or hide the interpreter.
        report["aggregate_peak_rss_upper_bound_bytes"] = report[
            "server_peak_rss_bytes"
        ] + workers.get("peak_child_rss_bytes", 0)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["caller"] = {
            "peak_rss_bytes": usage.ru_maxrss * 1024,
            "cpu_seconds": usage.ru_utime + usage.ru_stime,
            "includes_fixture_generation": True,
        }
        terminal = report.get("terminal", {}).get("terminal", {})
        try:
            assert (
                not forced
                and process.exitcode == 0
                and terminal
                and not terminal.get("violations")
            )
            assert terminal["counts"] == {
                "finance_provenance_blobs": 99,
                "finance_publications": 1,
                "finance_dated_projections": 1,
            }
            receipts = terminal["receipts"]
            assert len(receipts) == 2
            assert receipts[0]["event_id"] != receipts[1]["event_id"]
            committed, withheld = (receipt["payload"] for receipt in receipts)
            assert committed["outcome"] == "accepted"
            assert committed["reason"] == "exact_approved_package"
            assert withheld["outcome"] == "incomplete"
            assert withheld["reason"] == "finance_import_authorization_changed"
            assert committed["approval_event_id"] == withheld["approval_event_id"]
            for kind, event_key in (
                ("accounting", "publication_event_id"),
                ("dated", "projection_event_id"),
            ):
                assert terminal["outputs"][kind] == [
                    {
                        "id": report["expected_outputs"][kind],
                        "event_id": committed[event_key],
                    }
                ]
                assert withheld[event_key] == committed[event_key]
            assert all(
                receipt["payload"]["provider_effects"] is False for receipt in receipts
            )
            assert (
                report["owner_revocation_started_at"]
                >= report["stages"]["atomic_finance_commit"]["end"]["at"]
            )
            assert workers["active"] is None and workers["failed_count"] == 0
            assert workers["completed_count"] == 2
            assert all(
                row["complete"] and row["exitcode"] == 0 for row in workers["lifetimes"]
            )
            assert report["cleanup"]["instance_removed"]
            assert report["aggregate_peak_rss_upper_bound_bytes"] <= SERVER_RSS_BYTES
            assert report["aggregate_cpu_seconds"] <= SERVER_CPU_SECONDS
            assert all(
                "end" in report["stages"].get(name, {})
                for name in (
                    "upload",
                    "prepare_import",
                    "atomic_finance_commit",
                    "owner_control_critical_section",
                )
            )
            assert any(r["event"] == "stopped" and r["sampler_stopped"] for r in rows)
        except (AssertionError, KeyError) as exc:
            report["errors"].append(
                "terminal_receipt_or_resource_check:" + type(exc).__name__
            )
        if report["errors"]:
            report["state"] = "incomplete"
        (output / "import-summary.json").write_text(json.dumps(report, indent=2) + "\n")
        print(
            "SYNTHETIC_ACTUAL_SIZE_IMPORT "
            + json.dumps(
                {k: v for k, v in report.items() if k not in {"core", "stage_samples"}}
            ),
            flush=True,
        )
    expected_state = "diagnostic_completed" if allocation_profile else "completed"
    assert report["state"] == expected_state, report["errors"]
