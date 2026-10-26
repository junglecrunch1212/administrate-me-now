"""Synthetic launch environment seams only; no child process is started."""

from contextlib import contextmanager
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tests.acceptance import test_finance_storage_isolation as ownership
from tests.fixtures import finance_watchdog as watchdog

FLAG = "ADMINME_SYNTHETIC_CALLBACK_DIAGNOSTICS"
pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or os.getenv("ADMINME_SYNTHETIC_CALLBACK_TRANSPORT_PREFLIGHT") != "1",
    reason="exact synthetic callback transport preflight admission required",
)


class LaunchCaptured(Exception):
    pass


@contextmanager
def capture_launch(monkeypatch, module):
    """Stop at the real Popen call; neutralize OS limits and close test handles."""
    calls, handles, descriptors = [], [], []
    original_open, original_pipe = Path.open, os.pipe

    def opened(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        handles.append(handle)
        return handle

    def pipe():
        pair = original_pipe()
        descriptors.extend(pair)
        return pair

    def launch(argv, **kwargs):
        calls.append((argv, kwargs))
        raise LaunchCaptured

    with monkeypatch.context() as patches:
        patches.setattr(Path, "open", opened)
        patches.setattr(os, "pipe", pipe)
        patches.setattr(module.resource, "setrlimit", lambda *args: None)
        patches.setattr(module.subprocess, "Popen", launch)
        try:
            yield calls
        finally:
            for handle in handles:
                handle.close()
            for descriptor in descriptors:
                os.close(descriptor)
    assert all(handle.closed for handle in handles)


def set_flag(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(FLAG, raising=False)
    else:
        monkeypatch.setenv(FLAG, value)
    monkeypatch.setenv("UNRELATED_SYNTHETIC_ENV", "must-not-propagate")


@pytest.mark.parametrize(
    "mode,value,enabled",
    [
        pytest.param("staged-bounded", "1", True, id="staged-on"),
        pytest.param("staged-bounded", None, False, id="absent"),
        pytest.param("staged-bounded", "0", False, id="disabled"),
        pytest.param("staged-bounded", "true", False, id="invalid"),
        pytest.param("full", "1", False, id="wrong-full-mode"),
        pytest.param("contract-normal", "1", False, id="wrong-contract-mode"),
    ],
)
def test_controller_launch_preserves_exact_allowlist(
    tmp_path, monkeypatch, mode, value, enabled
):
    set_flag(monkeypatch, value)
    monkeypatch.setenv("GITHUB_SHA", "synthetic-callback-transport")
    monkeypatch.setattr(
        watchdog.ctypes,
        "CDLL",
        lambda *args, **kwargs: SimpleNamespace(prctl=lambda *args: 0),
    )
    monkeypatch.setattr(watchdog, "process_row", lambda pid: {"start_ticks": 42})
    monkeypatch.setattr(
        watchdog,
        "sample",
        lambda *args: {
            "at": watchdog.STARTED,
            "pids": [],
            **dict.fromkeys(watchdog.LIMITS, 0),
        },
    )
    with capture_launch(monkeypatch, watchdog) as calls:
        with pytest.raises(LaunchCaptured):
            watchdog.main(mode, str(tmp_path))
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [
        sys.executable,
        "-I",
        "-B",
        str(Path(watchdog.__file__).with_name("finance_watchdog_controller.py")),
    ]
    write_fd, ack_read_fd = kwargs["pass_fds"]
    expected = {
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "ADMINME_TEST_MODE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GITHUB_SHA": "synthetic-callback-transport",
        "FINANCE_STORAGE_ARTIFACT_DIR": str(tmp_path),
        "FINANCE_WATCHDOG_ROOT": str(tmp_path),
        "FINANCE_WATCHDOG_PID": str(os.getpid()),
        "FINANCE_WATCHDOG_BIRTH": "42",
        "FINANCE_WATCHDOG_STARTED": str(watchdog.STARTED),
        "FINANCE_WATCHDOG_FD": str(write_fd),
        "FINANCE_WATCHDOG_ACK_FD": str(ack_read_fd),
        "FINANCE_WATCHDOG_MODE": mode,
    }
    assert kwargs["env"] == expected | ({FLAG: "1"} if enabled else {})
    assert kwargs["cwd"] == "/" and kwargs["close_fds"] and kwargs["start_new_session"]


@pytest.mark.parametrize(
    "mode,script,case,value,enabled",
    [
        pytest.param(
            "staged-bounded",
            "finance_staging_measurement.py",
            "staged_lifetime",
            "1",
            True,
            id="staged-on",
        ),
        pytest.param(
            "staged-bounded",
            "finance_staging_measurement.py",
            "staged_lifetime",
            None,
            False,
            id="absent",
        ),
        pytest.param(
            "staged-bounded",
            "finance_staging_measurement.py",
            "staged_lifetime",
            "0",
            False,
            id="disabled",
        ),
        pytest.param(
            "staged-bounded",
            "finance_staging_measurement.py",
            "staged_lifetime",
            "true",
            False,
            id="invalid",
        ),
        pytest.param(
            "full",
            "finance_staging_measurement.py",
            "staged_lifetime",
            "1",
            False,
            id="wrong-mode",
        ),
        pytest.param(
            "staged-bounded",
            "finance_storage_cases.py",
            "staged_lifetime",
            "1",
            False,
            id="wrong-script",
        ),
        pytest.param(
            "staged-bounded",
            "finance_staging_measurement.py",
            "measured_lifetime",
            "1",
            False,
            id="wrong-case",
        ),
    ],
)
def test_runtime_launch_preserves_exact_allowlist(
    tmp_path, monkeypatch, mode, script, case, value, enabled
):
    set_flag(monkeypatch, value)
    monkeypatch.setenv("FINANCE_WATCHDOG_MODE", mode)
    monkeypatch.setenv("GITHUB_SHA", "synthetic-callback-transport")
    output, root = tmp_path / "evidence", tmp_path / "scratch"
    monkeypatch.setenv("FINANCE_STORAGE_ARTIFACT_DIR", str(output))
    monkeypatch.setattr(
        ownership,
        "process_row",
        lambda pid: {"cpu_seconds": 0, "reaped_cpu_seconds": 0},
    )

    class SyntheticRun(ownership.OwnedRun):
        runtime_script = script
        runtime_case = case

    with capture_launch(monkeypatch, ownership) as calls:
        with pytest.raises(LaunchCaptured):
            SyntheticRun("synthetic_transport", root)
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [
        sys.executable,
        "-I",
        "-B",
        str(Path(ownership.__file__).resolve().parents[1] / "fixtures" / script),
        case,
        str(root),
    ]
    expected = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "ADMINME_TEST_MODE": "1"}
    assert kwargs["env"] == expected | ({FLAG: "1"} if enabled else {})
    assert kwargs["cwd"] == "/" and kwargs["close_fds"] and kwargs["start_new_session"]
