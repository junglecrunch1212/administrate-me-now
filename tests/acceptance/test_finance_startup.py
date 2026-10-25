"""One predeclared setup-only cohort; no import or traffic evaluation."""

from __future__ import annotations

import asyncio
import io
import json
import os
import struct
import sys
import time

import pytest

from tests.acceptance.test_finance_storage_measurement import (
    MeasuredRun,
    cleanup_run,
    setup_runtime,
    settled,
)
from tests.fixtures.finance_startup import (
    STARTUP_LIMIT,
    StartupFrames,
    launch,
    store_name,
)
from tests.fixtures.finance_storage_isolation import SyntheticStorage

pytestmark = pytest.mark.skipif(
    os.getenv("ADMINME_SYNTHETIC_STORAGE_SETUP") != "1" or sys.platform != "linux",
    reason="explicit hosted synthetic startup cohort only",
)
TENANT = "tenant-synthetic-store-0123456789abcdef"
COORDINATES = {"fixture_coordinates": 1, "tenant": TENANT}
READY = {"ready": True, "tenant": TENANT, "url": "http://127.0.0.1:12345"}
BOUNDARIES = (
    "before_coordinates",
    "coordinates",
    "http_ready",
    "joined",
    "control",
    "approval_1",
    "approval_2",
    "settled",
)


def framed(value):
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    return io.BytesIO(struct.pack("!I", len(data)) + data)


@pytest.mark.parametrize(
    "bad",
    [
        b"{",
        b'{"fixture_coordinates":1,"fixture_coordinates":1,"tenant":"x"}',
        [],
        READY,
        {**COORDINATES, "fixture_coordinates": True},
        {**COORDINATES, "tenant": "tenant-foreign"},
        {**COORDINATES, "path": "/tmp/foreign"},
        {**COORDINATES, "key": "not-a-key"},
        {**COORDINATES, "database": "/tmp/foreign"},
    ],
)
def test_coordinates_reject_invalid_or_injected_inputs(bad):
    frames = StartupFrames()
    with pytest.raises(ValueError):
        frames.read(framed(bad))
    assert frames.count == 0 and frames.tenant is None


def test_oversized_frame_rejected_before_body_read():
    frames = StartupFrames()
    with pytest.raises(ValueError, match="frame_limit"):
        frames.read(io.BytesIO(struct.pack("!I", STARTUP_LIMIT + 1)))
    assert frames.count == 0


@pytest.mark.parametrize(
    "bad",
    [
        COORDINATES,
        {**READY, "tenant": "tenant-synthetic-store-fedcba9876543210"},
        {**READY, "ready": 1},
        {**READY, "path": "/tmp/foreign"},
        *[
            {**READY, "url": url}
            for url in (
                "https://127.0.0.1:12",
                "http://localhost:12",
                "http://127.0.0.1:0",
                "http://user@127.0.0.1:12",
                "http://127.0.0.1:12/path",
                "http://127.0.0.1:12?key=x",
                "http://127.0.0.1:99999",
            )
        ],
    ],
)
def test_readiness_rejects_mismatch_duplicates_and_foreign_coordinates(bad):
    frames = StartupFrames()
    assert frames.read(framed(COORDINATES)) == COORDINATES
    with pytest.raises(ValueError):
        frames.read(framed(bad))
    assert frames.count == 1 and not frames.ready


def test_only_two_startup_frames_and_then_control_results():
    frames = StartupFrames()
    with pytest.raises(ValueError, match="not_complete"):
        frames.control_result({})
    assert frames.read(framed(COORDINATES)) == COORDINATES
    assert frames.read(framed(READY)) == READY
    assert frames.control_result({"state": "passed"}) == {"state": "passed"}
    with pytest.raises(ValueError, match="duplicate_frame"):
        frames.read(framed(READY))
    for duplicate in (COORDINATES, READY):
        with pytest.raises(ValueError, match="duplicate_frame"):
            frames.control_result(duplicate)


@pytest.mark.parametrize("nonce", ["../foreign", "a" * 15, "g" * 16, 123, "A" * 16])
def test_constructor_rejects_foreign_namespace(tmp_path, nonce):
    with pytest.raises(ValueError, match="namespace_invalid"):
        SyntheticStorage(tmp_path, namespace_nonce=nonce)
    assert not list(tmp_path.iterdir())


def test_fresh_storage_keeps_internal_key_and_normal_tenant(tmp_path):
    first = SyntheticStorage(tmp_path, namespace_nonce="0123456789abcdef")
    assert first.settings().instance.tenant_id == TENANT
    assert first.root.name == store_name("0123456789abcdef")
    assert len(first.key) == 32
    sentinel = first.root / "sentinel"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        SyntheticStorage(tmp_path, namespace_nonce="0123456789abcdef")
    assert sentinel.read_text() == "preserve"
    second = SyntheticStorage(tmp_path)
    assert second.root != first.root and second.key != first.key
    for key in ("key", "database", "path"):
        with pytest.raises(TypeError):
            SyntheticStorage(tmp_path, **{key: "foreign"})


def test_launcher_rejects_unexpected_arguments_and_nonempty_root(tmp_path):
    for args in (
        [],
        ["foreign", str(tmp_path)],
        ["measured_lifetime", str(tmp_path), "key"],
    ):
        with pytest.raises(ValueError, match="launcher_arguments"):
            launch(args)
    with pytest.raises(ValueError, match="launcher_root"):
        launch(["measured_lifetime", "relative"])
    (tmp_path / "existing").write_text("preserve")
    with pytest.raises(ValueError, match="nonempty_root"):
        launch(["measured_lifetime", str(tmp_path)])


async def test_settings_mismatch_stops_before_application_open(tmp_path, monkeypatch):
    from tests.fixtures import finance_storage_measurement as runtime

    def forbidden(*args, **kwargs):
        pytest.fail("mismatched identity reached application creation")

    monkeypatch.setattr(runtime, "create_application", forbidden)
    with pytest.raises(ValueError, match="tenant_mismatch"):
        await runtime.serve(
            tmp_path,
            namespace_nonce="0123456789abcdef",
            announced_tenant="tenant-foreign",
        )
    assert not list(tmp_path.rglob("*.db"))


@pytest.mark.parametrize(
    "case", ["complete_1", "complete_2", "complete_3", *BOUNDARIES]
)
async def test_setup_only_margin_and_cancellation(tmp_path, case):
    run = MeasuredRun(tmp_path / "owned", name="startup_" + case)
    report = run.record
    report.update(
        faults=[],
        phases=[],
        imports=[],
        requests=[],
        evaluation="setup_only",
        useful_margin_seconds=2.0,
        qualification={"native": False, "full_workload": False},
    )
    active = set()
    cancellation = case in BOUNDARIES
    reached = False

    async def cancel_boundary(name):
        nonlocal reached
        if name == case:
            reached = True
            raise asyncio.CancelledError("fixed startup boundary: " + name)

    if cancellation:
        run.setup_hook = cancel_boundary
    try:
        if cancellation:
            with pytest.raises(asyncio.CancelledError, match="fixed startup boundary"):
                await setup_runtime(run, active)
            assert reached
            report["state"] = "expected_cancellation"
        else:
            result = await setup_runtime(run, active)
            (
                ready,
                manifest,
                successor,
                wire,
                next_wire,
                selection,
                expected,
                approvals,
                state,
            ) = result
            assert ready["tenant"] == run.startup.tenant
            assert len(approvals) == 2 and len(set(approvals)) == 2
            assert settled(state) and len(state["consumers"]) == 34
            assert all(value == 0 for value in state["counts"].values())
            elapsed = report["setup"]["finished"] - run.started
            report["setup_margin_seconds"] = 10 - elapsed
            report["useful_margin"] = elapsed <= 8
            assert elapsed <= 10
            assert wire and next_wire and expected[0] != expected[2]
            assert manifest["binding"] == successor["binding"]
            report["state"] = "setup_passed"
            del result, wire, next_wire
    except BaseException as exc:
        report["failure"] = {
            "class": type(exc).__name__,
            "message": str(exc)[:1000],
            "at": time.monotonic(),
        }
        raise
    finally:
        await cleanup_run(run, active)
    terminal = report["cleanup_shutdown"]
    assert settled(terminal) and len(terminal["consumers"]) == 34
    assert not terminal["finance_events"] and not terminal["lifetimes"]
    assert not report["cleanup"]["forced"]
    assert report["cleanup"]["runtime_exit"] == 0
    assert report["cleanup"]["scratch_removed"]
    assert not report.get("unsettled_owned_calls", 0)
    control = terminal["authority"]["control"]
    enabled = not cancellation or case in {
        "control",
        "approval_1",
        "approval_2",
        "settled",
    }
    assert control["revision"] == int(enabled)
    assert control["books_enabled"] is enabled
    assert control["cos_analysis_enabled"] is False
    approvals_expected = (
        2
        if not cancellation or case in {"approval_2", "settled"}
        else int(case == "approval_1")
    )
    assert len(report.get("setup_approval_ids", [])) == approvals_expected
    trace = [
        json.loads(line)
        for line in (run.output / (run.name + "-measurement-trace.jsonl"))
        .read_text()
        .splitlines()
    ]
    identity_at = next(
        row["at"]
        for row in report["setup_stages"]
        if row["stage"] == "runtime.startup_receipt" and row["frame"] == 1
    )
    runtime_at = next(row["at"] for row in trace if row["kind"] == "runtime.start")
    assert identity_at < runtime_at
    assert any(row["kind"] == "runtime.namespace_verified" for row in trace)
    assert trace[-1]["kind"] == "runtime.stopped"
