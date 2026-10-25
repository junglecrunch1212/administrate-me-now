"""Counter/ownership tests use fakes; no OS metrics or process discovery."""

from __future__ import annotations

import ctypes
import hmac
import inspect
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.fixtures import finance_native_resources as native

IDENTITY = {"candidate": "a" * 40, "checkout": "b" * 40}


class Buffer:
    def __init__(self):
        self.frames = []

    def poll(self, timeout=0):
        return bool(self.frames)

    def close(self):
        pass

    def send_bytes(self, frame):
        self.frames.append(frame)

    def recv_bytes(self, limit):
        frame = self.frames.pop(0)
        if len(frame) > limit:
            raise OSError("bad message length")
        return frame


def wire_pair():
    connection = Buffer()
    return (
        native.Wire(connection, b"k" * 32, "birth", "metrics", "child"),
        native.Wire(connection, b"k" * 32, "birth", "metrics", "parent"),
    )


def sample(at=1.0, cpu=1.0, rss=1024, peak=2048):
    return {
        "at": at,
        "user_cpu_seconds": cpu,
        "system_cpu_seconds": 0.0,
        "cpu_seconds": cpu,
        "rss_bytes": rss,
        "peak_rss_bytes": peak,
        "mach_peak_rss_bytes": peak,
    }


def member(birth="birth", pid=1234, target="worker"):
    return native.Member(
        birth,
        target,
        pid,
        IDENTITY,
        SimpleNamespace(exitcode=0),
        Mock(),
        Mock(),
        Mock(),
    )


def hello(m):
    return {
        "kind": "hello",
        "birth": m.birth,
        "pid": m.pid,
        "target": m.target,
        "identity": IDENTITY,
    }


def terminal(m, **overrides):
    return {
        "kind": "terminal",
        "birth": m.birth,
        "pid": m.pid,
        "metrics": sample(1.1, 2.0),
        "faults": [],
        "outcome": "returned",
        **overrides,
    }


def test_darwin_counter_units_and_structure():
    result = native.metric_record(1.25, 0.75, 4096, 8192, 8192)
    assert result["cpu_seconds"] == 2
    assert result["rss_bytes"] == 4096
    assert result["peak_rss_bytes"] == 8192  # Darwin is bytes, never times 1024.
    assert ctypes.sizeof(native._MachBasicInfo) == 48
    assert ctypes.sizeof(native._MachBasicInfo) // ctypes.sizeof(ctypes.c_int) == 12
    assert not inspect.signature(native.DarwinSelfMetrics).parameters
    assert list(inspect.signature(native.DarwinSelfMetrics.sample).parameters) == [
        "self"
    ]


@pytest.mark.parametrize(
    "values",
    [
        (-1, 0, 1, 1, 1),
        (float("nan"), 0, 1, 1, 1),
        (0, float("inf"), 1, 1, 1),
        (True, 0, 1, 1, 1),
        (0, 0, 1.0, 2, 2),
        (0, 0, 5, 4, 4),
    ],
)
def test_invalid_counter_fails(values):
    with pytest.raises(native.EvidenceError):
        native.metric_record(*values)


def test_native_denial_has_no_fallback(monkeypatch):
    provider = native.DarwinSelfMetrics.__new__(native.DarwinSelfMetrics)
    provider.task, provider.info = 123, Mock(return_value=5)
    usage = Mock(side_effect=AssertionError("no fallback after denial"))
    monkeypatch.setattr(native.resource, "getrusage", usage)
    with pytest.raises(native.EvidenceError, match="no fallback"):
        provider.sample()
    usage.assert_not_called()


def test_wire_round_trip_replay_and_bounded_frame():
    sender, receiver = wire_pair()
    sender.send({"kind": "sample"})
    first = sender.connection.frames[0]
    assert receiver.recv() == {"kind": "sample"}
    sender.connection.frames.append(first)
    with pytest.raises(native.EvidenceError, match="sequence"):
        receiver.recv()
    with pytest.raises(native.EvidenceError, match="oversize"):
        sender.send("x" * native.FRAME_LIMIT)
    sender.connection.frames.append(b"x" * (native.FRAME_LIMIT + 1))
    with pytest.raises(OSError, match="length"):
        receiver.recv()


@pytest.mark.parametrize(
    "attribute,value",
    [("key", b"other"), ("birth", "other"), ("channel", "work"), ("side", "child")],
)
def test_wire_rejects_wrong_binding(attribute, value):
    sender, receiver = wire_pair()
    sender.send({"kind": "sample"})
    setattr(receiver, attribute, value)
    with pytest.raises(native.EvidenceError):
        receiver.recv()


def test_signed_malformed_envelope_is_not_accepted():
    sender, receiver = wire_pair()
    body = native.canonical({"birth": "birth"})
    sender.connection.frames.append(hmac.digest(sender.key, body, "sha256") + body)
    with pytest.raises(native.EvidenceError, match="invalid IPC"):
        receiver.recv()


@pytest.mark.parametrize(
    "field,value",
    [
        ("pid", 99),
        ("birth", "old"),
        ("target", "runtime"),
        ("identity", {**IDENTITY, "candidate": "c" * 40}),
    ],
)
def test_registration_binds_exact_owned_process(field, value):
    m = member()
    with pytest.raises(native.EvidenceError, match="mismatch"):
        m.accept({**hello(m), field: value})
    assert not m.verdict()["complete"]


def test_exit_and_pid_reuse_require_new_birth():
    first, reused = member(), member("second")
    first.accept(hello(first))
    first.accept({"kind": "sample", "metrics": sample()})
    first.accept(terminal(first))
    assert first.verdict()["complete"]
    with pytest.raises(native.EvidenceError, match="mismatch"):
        reused.accept(hello(first))
    with pytest.raises(native.EvidenceError, match="duplicate terminal"):
        first.accept(terminal(first))
    assert not reused.verdict()["complete"]


@pytest.mark.parametrize(
    "value",
    [
        sample(0.9),
        sample(1.1, 0.5),
        sample(1.1, peak=1024),
        sample(1.1, rss=1.5),
        sample(1.1, rss=4096),
        {**sample(1.1), "cpu_seconds": 2.0},
    ],
)
def test_missing_or_invalid_counters_cannot_be_reported_as_zero(value):
    m = member()
    m.accept(hello(m))
    m.accept({"kind": "sample", "metrics": sample()})
    with pytest.raises(native.EvidenceError):
        m.accept({"kind": "sample", "metrics": value})
    assert not m.verdict()["complete"]


def test_gap_fault_missing_terminal_and_unaccounted_exit_are_incomplete():
    m = member()
    m.accept(hello(m))
    m.accept({"kind": "sample", "metrics": sample()})
    m.accept({"kind": "sample", "metrics": sample(4)})
    m.accept({"kind": "fault", "reason": "counter unavailable"})
    m.process.exitcode = None
    errors = m.verdict()["errors"]
    assert "sampling gap" in errors
    assert "counter unavailable" in errors
    assert "exit not accounted" in errors
    assert any("missing" in error for error in errors)


def test_retention_failure_marks_scope_incomplete(tmp_path, monkeypatch):
    registry = native.Registry(tmp_path, 49152, IDENTITY)
    monkeypatch.setattr(native.Path, "open", Mock(side_effect=OSError("full")))
    with pytest.raises(native.EvidenceError, match="retention failed"):
        registry._retain({"kind": "sample"})
    assert registry.snapshot("failure")["cpu_seconds"] is None


def test_exited_worker_cpu_is_retained_and_rss_is_zero(tmp_path):
    registry = native.Registry(tmp_path, 49152, IDENTITY)
    m = member()
    m.accept(hello(m))
    m.accept({"kind": "sample", "metrics": sample()})
    m.accept(terminal(m))
    registry.members.append(m)
    snapshot = registry.snapshot("after-exit")
    assert snapshot["cpu_seconds"] == 2.0
    assert snapshot["rss_bytes"] == 0
    assert snapshot["sum_lifetime_peaks_upper_bound_bytes"] == 2048
    m.errors.append("unaccounted child")
    assert registry.snapshot("incomplete")["cpu_seconds"] is None


@pytest.mark.parametrize(
    "event,args",
    [
        ("subprocess.Popen", ("ps",)),
        ("os.system", ("sysctl",)),
        ("os.fork", ()),
        ("socket.connect", (None, ("127.0.0.1", 49153))),
        ("socket.connect", (None, ("192.0.2.1", 49152))),
        ("socket.getaddrinfo", ("provider.invalid", 443)),
        ("open", ("/private/production/state.sqlite", "r", 0)),
        ("open", ("/Library/Keychains/System.keychain", "r", 0)),
    ],
)
def test_fixture_guard_blocks_process_provider_production_paths(tmp_path, event, args):
    faults: list[str] = []
    guard = native.FixtureGuard(tmp_path, 49152, faults)
    with pytest.raises(PermissionError):
        guard(event, args)  # Invoke validation only, never the denied OS operation.
    assert faults


def test_fixture_guard_allows_only_exact_loopback_and_owned_files(tmp_path):
    guard = native.FixtureGuard(tmp_path, 49152, [])
    guard("socket.connect", (None, ("127.0.0.1", 49152)))
    guard("open", (str(tmp_path / "raw.jsonl"), "a", native.os.O_WRONLY))
    assert not guard.faults


@pytest.mark.parametrize(
    "target,arguments",
    [
        ("pid", {"pid": 1234}),
        ("worker", {"pid": 1234}),
        (
            "core",
            {
                "url": "http://127.0.0.1:49153",
                "actor": "synthetic",
                "session": "synthetic",
            },
        ),
    ],
)
def test_registry_has_no_arbitrary_pid_or_service_attachment(
    tmp_path, target, arguments
):
    registry = native.Registry(tmp_path, 49152, IDENTITY)
    registry.context = Mock()
    with pytest.raises(native.EvidenceError):
        registry.spawn(target, **arguments)
    registry.context.Process.assert_not_called()


async def test_expired_watchdog_does_not_prevent_owned_cleanup(tmp_path):
    registry = native.Registry(tmp_path, 49152, IDENTITY)
    registry.created -= native.WALL_SECONDS + 1
    m = member()
    m.process = Mock(exitcode=-9)
    m.process.is_alive.return_value = False
    m.wire.poll.return_value = False
    registry.members.append(m)
    with pytest.raises(native.EvidenceError, match="watchdog"):
        registry.drain()
    result = await registry.close()
    assert result["all_registered_processes_stopped"]
    assert not result["registered_coverage_complete"]
    assert not result["whole_os_process_tree_coverage_proven"]
    m.process.join.assert_called_once()
    m.wire.close.assert_called_once()
    m.stop.set.assert_not_called()
    m.stop.close.assert_called_once()


def test_owned_stop_is_single_frame_and_never_waits_for_dead_worker():
    sender, receiver = wire_pair()
    stop = native.StopSender(sender)
    child = native.StopReceiver(receiver)
    assert not child.is_set()
    stop.set()
    stop.set()
    assert len(sender.connection.frames) == 1
    assert child.is_set()
    assert not sender.connection.frames
    stop.close()
    child.close()


def test_dead_owned_stop_pipe_is_terminal_without_fallback():
    wire = Mock()
    wire.send.side_effect = BrokenPipeError()
    stop = native.StopSender(wire)
    stop.set()
    stop.set()
    wire.send.assert_called_once_with({"stop": True})


def test_each_darwin_peak_must_cover_current_rss():
    for peaks in ((1, 8192), (8192, 1)):
        with pytest.raises(native.EvidenceError, match="RSS"):
            native.metric_record(0, 0, 4096, *peaks)


def test_three_registered_children_bound_including_coordinator_and_spawn_tracker(
    tmp_path,
):
    registry = native.Registry(tmp_path, 49152, IDENTITY)
    registry.members = [member(str(i)) for i in range(3)]
    registry.context = Mock()
    with pytest.raises(native.EvidenceError, match="process cap"):
        registry.spawn("worker")
    registry.context.Process.assert_not_called()


def test_synthetic_mime_setup_does_not_read_host_configuration(monkeypatch):
    import mimetypes

    monkeypatch.setattr(mimetypes, "knownfiles", ["/private/production/mime.types"])
    monkeypatch.setattr(mimetypes, "inited", False)
    monkeypatch.setattr(mimetypes, "_db", None)
    # Save all globals mutated by init so this test cannot change other fixtures.
    for name in ("suffix_map", "types_map", "encodings_map", "common_types"):
        monkeypatch.setattr(mimetypes, name, getattr(mimetypes, name))
    reader = Mock(side_effect=AssertionError("host MIME access forbidden"))
    monkeypatch.setattr(mimetypes.MimeTypes, "read", reader)
    monkeypatch.setattr(mimetypes.MimeTypes, "read_windows_registry", Mock())
    native.prepare_synthetic_mime_database()
    assert mimetypes.guess_type("synthetic.txt") == ("text/plain", None)
    reader.assert_not_called()
    assert mimetypes.knownfiles == []


def test_parent_stop_channel_closure_stops_the_owned_child():
    wire = Mock()
    wire.poll.return_value = True
    wire.recv.side_effect = EOFError()
    child = native.StopReceiver(wire)
    assert child.is_set()
    assert child.is_set()
    wire.recv.assert_called_once()


async def test_loopback_client_ignores_ambient_proxy_and_certificate_settings(
    tmp_path, monkeypatch
):
    certificate = tmp_path / "not-a-certificate.pem"
    certificate.write_text("synthetic invalid certificate")
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8888")
    import mimetypes

    for name in (
        "knownfiles",
        "inited",
        "_db",
        "suffix_map",
        "types_map",
        "encodings_map",
        "common_types",
    ):
        monkeypatch.setattr(mimetypes, name, getattr(mimetypes, name))
    native.prepare_synthetic_mime_database()
    from tests.acceptance.test_finance_mixed_load import _client

    # Construction would fail loading the invalid ambient certificate if the
    # transport trusted environment settings. No network request is made.
    async with _client("http://127.0.0.1:49152") as client:
        assert client.base_url.host == "127.0.0.1"


@pytest.mark.parametrize("checkout", ["stale", "a" * 39, None])
def test_synthetic_runtime_rejects_unbound_release_identity(tmp_path, checkout):
    with pytest.raises(native.EvidenceError, match="checkout identity"):
        native.prepare_synthetic_runtime(tmp_path, checkout)
    assert not (tmp_path / "temporary").exists()


def test_synthetic_runtime_pins_tempfiles_and_release_without_host_lookup(
    tmp_path, monkeypatch
):
    import tempfile
    from adminme import operating_agreements

    for name in ("TMPDIR", "TMP", "TEMP", "ADMINME_REVIEWED_COMMIT"):
        monkeypatch.setenv(name, "/private/production/not-the-fixture")
    monkeypatch.setattr(tempfile, "tempdir", "/private/production/not-the-fixture")
    mime_setup = Mock()
    monkeypatch.setattr(native, "prepare_synthetic_mime_database", mime_setup)
    launch = Mock(side_effect=AssertionError("fixture cannot launch git"))
    monkeypatch.setattr(operating_agreements.subprocess, "run", launch)
    native.prepare_synthetic_runtime(tmp_path, IDENTITY["checkout"])
    expected = tmp_path.resolve() / "temporary"
    with tempfile.NamedTemporaryFile() as worksheet:
        assert Path(worksheet.name).parent == expected
    assert all(
        native.os.environ[name] == str(expected) for name in ("TMPDIR", "TMP", "TEMP")
    )
    assert (
        operating_agreements._release_commit(tmp_path, "b" * 64) == IDENTITY["checkout"]
    )
    launch.assert_not_called()
    mime_setup.assert_called_once_with()


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "executable",
        "script",
        "flags",
        "cwd",
        "env",
        "admission",
        "active",
        "limit",
        "client",
    ],
)
def test_native_guard_allows_only_registered_runtime_calculation(
    monkeypatch, tmp_path, change
):
    from adminme.finance import worker

    executable = str(Path(native.sys.executable).absolute())
    script = str(Path(worker.__file__).with_name("worker_child.py").resolve())
    args = [
        executable,
        [executable, "-I", "-B", script],
        "/",
        {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
    ]
    guard = native.FixtureGuard(tmp_path, 49152, [], finance_workers=change != "client")
    monkeypatch.setattr(
        worker, "_admission", SimpleNamespace(locked=lambda: change != "admission")
    )
    monkeypatch.setattr(
        worker, "worker_usage", lambda: {"active": {} if change == "active" else None}
    )
    if change == "executable":
        args[0] = "/usr/bin/python3"
    if change == "script":
        args[1][-1] = "/tmp/other.py"
    if change == "flags":
        args[1].remove("-I")
    if change == "cwd":
        args[2] = str(tmp_path)
    if change == "env":
        args[3]["HOME"] = str(tmp_path)
    if change == "limit":
        guard.finance_launches = native.MAX_FINANCE_WORKERS
    if change == "none":
        guard("subprocess.Popen", args)
        assert guard.finance_launches == 1 and not guard.faults
    else:
        with pytest.raises(PermissionError):
            guard("subprocess.Popen", args)
        assert guard.faults


def test_native_owned_metrics_include_child_cpu_rss_reaping_and_terminal(monkeypatch):
    from adminme.finance import worker

    ledger = {
        "active": {
            "user_cpu_seconds": 2.0,
            "system_cpu_seconds": 1.0,
            "rss_bytes": 100,
            "peak_rss_bytes": 150,
        },
        "completed_user_cpu_seconds": 0.0,
        "completed_system_cpu_seconds": 0.0,
        "completed_count": 0,
        "failed_count": 0,
        "peak_child_rss_bytes": 0,
        "lifetimes": [],
    }
    monkeypatch.setattr(worker, "worker_usage", lambda: ledger)
    reaped = SimpleNamespace(ru_utime=0.0, ru_stime=0.0)
    monkeypatch.setattr(native.resource, "getrusage", lambda _: reaped)
    provider = native.OwnedFinanceMetrics(
        SimpleNamespace(sample=lambda: sample(cpu=3.0, rss=1000, peak=2000)),
        SimpleNamespace(finance_launches=1),
    )
    first = provider.sample()
    assert first["cpu_seconds"] == 6 and first["rss_bytes"] == 1100
    assert first["peak_rss_bytes"] == 2150
    with pytest.raises(native.EvidenceError, match="terminal coverage"):
        provider.terminal()
    # Reaped CPU can arrive while the last active telemetry still exists.
    reaped.ru_utime, reaped.ru_stime = 2.2, 1.1
    second = provider.sample()
    assert second["cpu_seconds"] == pytest.approx(6.3)
    ledger.update(
        active=None,
        completed_count=1,
        completed_user_cpu_seconds=2.0,
        completed_system_cpu_seconds=1.0,
        peak_child_rss_bytes=150,
        lifetimes=[{"complete": True, "terminal_received": True, "exitcode": 0}],
    )
    third = provider.sample()
    assert third["cpu_seconds"] == second["cpu_seconds"] and third["rss_bytes"] == 1000
    provider.terminal()
    ledger["lifetimes"][0]["complete"] = False
    provider.sample()
    with pytest.raises(native.EvidenceError, match="terminal coverage"):
        provider.terminal()
