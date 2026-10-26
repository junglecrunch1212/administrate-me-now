"""Four proposed diagnostics-only cases; never a representative measurement."""

import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.acceptance import test_finance_storage_isolation as ownership
from tests.fixtures import finance_staging_measurement as diagnostic

pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or os.getenv("ADMINME_SYNTHETIC_DIAGNOSTIC_PREFLIGHT") != "1",
    reason="separate exact diagnostic preflight admission required",
)


@pytest.fixture(autouse=True)
def exact_reviewed_v2_sources():
    # No source extraction or copied algorithms: exercise the reviewed functions.
    for module, expected in (
        (
            diagnostic,
            "da1e3e0c1b2d1d25e35f0295e45252a173e3efcefb054aaf657c796e21ba78ee",
        ),
        (ownership, "30c1b19cba97ec0cf193d42fc95e839014e3f2c7857356c9139d2aea83a2b5b5"),
    ):
        assert (
            hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() == expected
        )


def close_stubbed_owner(root, output, monkeypatch, *, retained, complete):
    """Real receipt/copy/delete path; process lifecycle itself is explicitly stubbed."""
    run = object.__new__(ownership.OwnedRun)
    run.name, run.root, run.output = "staged_bounded", root, output
    run.runtime_script = "finance_staging_measurement.py"
    run.process = SimpleNamespace(
        wait=lambda timeout: None,
        returncode=0,
        stdin=io.BytesIO(),
        stdout=io.BytesIO(),
    )
    run.stop = SimpleNamespace(set=lambda: None)
    run.monitor = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: False)
    run.known = {}
    run.error_file = io.BytesIO()
    run.record = {
        "samples": [{key: 0 for key in run.limits}],
        "violations": [],
    }
    run.kill = lambda: pytest.fail("stubbed owner must not send a signal")
    target = output / "staged_bounded-diagnostic-observer.json"
    remove = ownership.shutil.rmtree
    checked = []

    def remove_after_copy(path):
        assert Path(path) == root
        assert target.exists() is retained
        if retained:
            assert (
                target.read_bytes() == (root / "diagnostic-observer.json").read_bytes()
            )
        checked.append(True)
        remove(path)

    monkeypatch.setattr(ownership.shutil, "rmtree", remove_after_copy)
    if complete:
        run.close()
    else:
        with pytest.raises(AssertionError, match="diagnostic_observer_incomplete"):
            run.close()
    assert checked == [True] and not root.exists()
    assert run.record["cleanup"]["runtime_exit"] == 0
    assert run.record["cleanup"]["scratch_removed"] is True
    assert run.record["diagnostic_observer"]["complete"] is complete
    assert run.record["violations"] == (
        [] if complete else ["diagnostic_observer_incomplete"]
    )
    saved = json.loads((output / "staged_bounded.json").read_text())
    assert saved["diagnostic_observer"] == run.record["diagnostic_observer"]
    return run.record["diagnostic_observer"]


@pytest.mark.parametrize("mode", ["healthy", "early_failure"])
async def test_probe_actual_lifetime_and_receipt_completeness(
    tmp_path, monkeypatch, mode
):
    root, output = tmp_path / "scratch", tmp_path / "evidence"
    root.mkdir()
    output.mkdir()
    first_tick = asyncio.Event()
    traces, frames, listeners = [], [], []
    base_trace = diagnostic.DiagnosticTrace

    class ObservedTrace(base_trace):
        def __init__(self, path):
            super().__init__(path)
            traces.append(self)

        def mark(self, kind, **fields):
            super().mark(kind, **fields)
            if kind == "loop.delay":
                first_tick.set()
                if mode == "early_failure":
                    raise RuntimeError("synthetic diagnostic probe failure")

    def executor():
        return SimpleNamespace(
            submit=lambda *args, **kwargs: pytest.fail(
                "no worker execution in probe test"
            )
        )

    runtime = SimpleNamespace(
        event_log=SimpleNamespace(
            _executor=executor(), _metadata_reader=SimpleNamespace(_executor=executor())
        ),
        projections=SimpleNamespace(
            _executor=executor(),
            _money_executor=executor(),
            _parties_executor=executor(),
        ),
        settings_command_locks={},
    )
    app = SimpleNamespace(
        state=SimpleNamespace(runtime=runtime),
        routes=[
            SimpleNamespace(path=path, app=None)
            for path in (
                "/api/member/v1/surfaces/{surface}",
                "/api/member/v1/finance/planning",
            )
        ],
        add_middleware=lambda *args, **kwargs: None,
    )

    class Storage:
        def __init__(self, path, *, namespace_nonce, trace):
            self.trace = trace

        def settings(self):
            return SimpleNamespace(
                instance=SimpleNamespace(tenant_id="synthetic-diagnostic")
            )

        def install(self, app, patches):
            pass

        async def close(self):
            pass

    class Listener:
        def __init__(self):
            self.closed = False
            listeners.append(self)

        def bind(self, address):
            assert address == ("127.0.0.1", 0)

        def listen(self, size):
            assert size == 128

        def getsockname(self):
            return ("127.0.0.1", 0)

        def close(self):
            self.closed = True

    class Server:
        def __init__(self, config):
            self.started = False
            self.stopped = asyncio.Event()

        @property
        def should_exit(self):
            return self.stopped.is_set()

        @should_exit.setter
        def should_exit(self, value):
            assert value is True
            self.stopped.set()

        async def serve(self, *, sockets):
            self.started = True
            await self.stopped.wait()

    async def inline(function, *args):
        return function(*args)

    async def snapshot(runtime, storage):
        await asyncio.wait_for(first_tick.wait(), timeout=1)
        return {"state": "synthetic-diagnostic-test-only"}

    monkeypatch.setattr(diagnostic, "DiagnosticTrace", ObservedTrace)
    monkeypatch.setattr(diagnostic, "TracedStorage", Storage)
    monkeypatch.setattr(diagnostic, "instrument", lambda *args: None)
    monkeypatch.setattr(diagnostic, "create_application", lambda settings: app)
    monkeypatch.setattr(diagnostic, "socket", SimpleNamespace(socket=Listener))
    monkeypatch.setattr(
        diagnostic,
        "uvicorn",
        SimpleNamespace(Config=lambda *args, **kwargs: None, Server=Server),
    )
    monkeypatch.setattr(diagnostic, "read_json", lambda stream: {"op": "shutdown"})
    monkeypatch.setattr(
        diagnostic, "write_json", lambda stream, value: frames.append(value)
    )
    monkeypatch.setattr(
        diagnostic,
        "sys",
        SimpleNamespace(
            stdin=SimpleNamespace(buffer=None), stdout=SimpleNamespace(buffer=None)
        ),
    )
    monkeypatch.setattr(
        diagnostic,
        "asyncio",
        SimpleNamespace(
            **{
                name: getattr(asyncio, name)
                for name in ("Lock", "create_task", "sleep", "gather", "CancelledError")
            },
            to_thread=inline,
        ),
    )
    monkeypatch.setattr(diagnostic, "durable_snapshot", snapshot)
    await asyncio.wait_for(
        diagnostic.serve(root, "synthetic", "synthetic-diagnostic"), timeout=2
    )
    assert len(traces) == 1 and traces[0].closed
    assert len(listeners) == 1 and listeners[0].closed
    assert len(frames) == 2 and frames[0]["ready"] is True
    receipt = json.loads((root / "diagnostic-observer.json").read_text())
    health = receipt["loop_probe"]
    assert health["ticks"] >= 1
    assert (
        health["started_at"]
        <= health["runtime_ready_at"]
        <= health["cancel_requested_at"]
    )
    assert receipt["executors_instrumented"] == [
        "event_log",
        "event_metadata",
        "projection",
        "money_read",
        "authority",
    ]
    assert receipt["diagnostic_complete"] is (mode == "healthy")
    if mode == "healthy":
        assert not health["done_before_cancel"] and health["cancel_accepted"]
        assert health["outcome"] == health["gather_outcome"] == "CancelledError"
        assert (
            health["stopped_at"] >= health["cancel_requested_at"] and health["complete"]
        )
    else:
        assert health["done_before_cancel"] and not health["cancel_accepted"]
        assert health["outcome"] == health["gather_outcome"] == "RuntimeError"
        assert (
            health["stopped_at"] < health["cancel_requested_at"]
            and not health["complete"]
        )
    assert receipt["costs"]["trace_mark"]["calls"] > 0
    assert receipt["costs"]["trace_close"]["calls"] == 1
    assert 0 < receipt["trace_encoded_bytes"] <= receipt["trace_limit_bytes"]
    close_stubbed_owner(
        root, output, monkeypatch, retained=True, complete=mode == "healthy"
    )


@pytest.mark.parametrize("fault", ["missing", "oversize"])
def test_missing_or_oversize_observer_cannot_pass_after_cleanup(
    tmp_path, monkeypatch, fault
):
    root, output = tmp_path / "scratch", tmp_path / "evidence"
    root.mkdir()
    output.mkdir()
    if fault == "oversize":
        (root / "diagnostic-observer.json").write_bytes(b"x" * 65537)
    evidence = close_stubbed_owner(
        root, output, monkeypatch, retained=False, complete=False
    )
    assert evidence["error"] == (
        "FileNotFoundError" if fault == "missing" else "ValueError"
    )
    if fault == "oversize":
        assert evidence["read_bytes"] == 65537 and evidence["limit_bytes"] == 65536
