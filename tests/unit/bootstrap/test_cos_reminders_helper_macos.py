# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
"""Hosted-only production sealed worker and native callback plumbing proof."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
from pathlib import Path

import pytest

from adminme.adapters import cos_reminders_helper as client
from adminme.adapters.cos_apple_reminders import (
    NativeHelperBinding,
    local_device_identity,
)
from bootstrap import cos_reminders_helper_app as helper
from bootstrap import cos_reminders_native_worker as worker
from tests.unit.bootstrap.test_cos_reminders_helper_app_macos import (
    ROOT,
    _request,
    release as _release_fixture,
)

release = _release_fixture

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="native macOS process proof"
)


def test_real_sealed_worker_proves_native_permission_identity_without_prompt(
    tmp_path, release, monkeypatch
):
    for name in ("adminme", "bootstrap", "packs"):
        shutil.copytree(
            ROOT / name,
            release / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    site = release / ".venv/lib/python3.14/site-packages"
    site.rmdir()
    site.symlink_to(Path(sysconfig.get_path("purelib")))
    sources = {name: (ROOT / name).read_bytes() for name in helper.BUILD_SOURCES}
    app = tmp_path.resolve() / "Production Reminders Helper.app"
    receipt = helper._build_app(
        app, sources, include_dir=Path(sysconfig.get_path("include"))
    )
    device, uid = local_device_identity()
    request, value = _request(
        tmp_path, release, device_identity_sha256=device, os_uid=uid
    )
    process = subprocess.run(
        ["/usr/bin/open", "-W", "-n", "-a", str(app), "--args", str(request)],
        capture_output=True,
        timeout=110,
    )
    assert process.returncode == 0, process.stderr.decode(errors="replace")
    result = json.loads(request.with_name("result.json").read_text())
    assert result["status"] == "ok", result
    assert result["operation"] == "status" and result["nonce"] == value["nonce"]
    assert set(result["result"]) == {"authorization_status"}
    assert result["result"]["authorization_status"] in {0, 1, 2, 3, 4}
    identity_status = result["result"]["authorization_status"]
    identity = result["identity"]
    assert {key: identity[key] for key in receipt} == receipt
    assert identity["native_executable"] == str(
        app / "Contents/MacOS" / helper.EXECUTABLE
    )
    assert identity["python_executable"] == str(release / ".venv/bin/python")
    assert identity["release_root"] == str(release)
    assert identity["device_identity_sha256"] == device
    assert identity["native_pid"] > 0
    assert worker.artifact_sha256(app) == helper.helper_artifact_sha256(app)

    # Exercise the real controller's default TemporaryDirectory transport under
    # the OS temp root, not pytest's RUNNER_TEMP under /Users. realpath resolves
    # this to /private/var; NSString standardizingPath would strip /private.
    platform_temp = os.confstr("CS_DARWIN_USER_TEMP_DIR")
    assert platform_temp
    assert str(Path(platform_temp).resolve()).startswith("/private/var/")
    coordinates = {
        key: value[key]
        for key in (
            "release_root",
            "release_commit",
            "python_executable",
            "python_library",
            "python_library_sha256",
        )
    }
    monkeypatch.setattr(client, "release_coordinates", lambda root=None: coordinates)
    monkeypatch.setattr(tempfile, "tempdir", platform_temp)
    native_launch = subprocess.run
    request_paths: list[Path] = []

    def record_launch(arguments, **kwargs):
        if arguments[:2] == ["/usr/bin/open", "-W"]:
            request_paths.append(Path(arguments[-1]))
        return native_launch(arguments, **kwargs)

    monkeypatch.setattr(client.subprocess, "run", record_launch)
    result = client.invoke(NativeHelperBinding.model_validate(receipt), "status", {})
    assert result == {"authorization_status": identity_status}
    assert len(request_paths) == 1
    assert str(request_paths[0]).startswith("/private/var/")
    assert not request_paths[0].parent.exists()


def test_native_main_loop_services_completion_while_operation_waits(monkeypatch):
    from Foundation import NSDefaultRunLoopMode, NSRunLoop, NSTimer

    delivered = threading.Event()
    timer = NSTimer.timerWithTimeInterval_repeats_block_(
        0.02, False, lambda timer: delivered.set()
    )
    NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSDefaultRunLoopMode)

    def perform(request, identity):
        assert threading.current_thread() is not threading.main_thread()
        assert delivered.wait(3), "main-thread callback was starved"
        return {"authorization_status": 0}

    monkeypatch.setattr(worker, "perform", perform)
    assert worker.run_with_main_loop(
        {"expires_at_ms": int(time.time() * 1000) + 5000}, {}
    ) == {
        "authorization_status": 0,
    }


def test_foundation_string_bridge_is_supported_on_native_wire():
    from Foundation import NSString

    notes = NSString.stringWithString_("Private fixture notes")
    assert worker._wire({"notes": notes}) == {"notes": "Private fixture notes"}
