"""Disposable hosted diagnosis, not a production permission path or release gate.

No reminders are read or written. The real OS request may display a consent
dialog in the disposable runner; this probe never accepts it. Instrumentation
is sealed into a fixture component and cannot be supplied to the public builder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import pytest

from adminme.adapters.cos_apple_reminders import local_device_identity
from bootstrap import cos_reminders_helper_app as helper
from tests.unit.bootstrap.test_cos_reminders_helper_app_macos import (
    ROOT,
    _request,
    release as _release_fixture,
)

release = _release_fixture

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin"
    or os.environ.get("GITHUB_ACTIONS") != "true"
    or os.environ.get("ADMINME_ISOLATED_CONSENT_DIAGNOSTIC") != "1",
    reason="explicit disposable hosted consent diagnosis only",
)

TRACE = """
import builtins
def phase(name, **fields):
    data = {"phase": name, "seconds": time.monotonic(), "pid": os.getpid(),
            "main_thread": threading.current_thread() is threading.main_thread(), **fields}
    path = Path(sys.argv[1]).with_name("diagnostic.jsonl")
    with path.open("a") as stream:
        stream.write(json.dumps(data) + "\\n")
builtins._consent_diagnostic_phase = phase
phase("worker_started")
"""


@pytest.mark.parametrize("mode", ["baseline", "thread_pool", "appkit"])
def test_observe_consent_boundary(tmp_path, release, mode):
    for name in ("adminme", "bootstrap", "packs"):
        destination = release / name
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(
            ROOT / name,
            destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    site = release / ".venv/lib/python3.14/site-packages"
    if not site.is_symlink():
        site.rmdir()
        site.symlink_to(Path(sysconfig.get_path("purelib")))

    shim = release / "packs/adapters/reminders/apple-eventkit/_eventkit_shim.py"
    code = shim.read_text()
    code = code.replace(
        "    complete = threading.Event()\n",
        "    from builtins import _consent_diagnostic_phase as phase\n"
        '    phase("permission_function_entered")\n'
        "    complete = threading.Event()\n",
        1,
    )
    code = code.replace(
        "        result.update(granted=bool(granted), error=error)\n",
        '        phase("permission_callback", granted=bool(granted), error_type=type(error).__name__)\n'
        "        result.update(granted=bool(granted), error=error)\n",
        1,
    )
    code = code.replace(
        "        modern(callback)\n",
        '        phase("modern_request_entered")\n'
        "        modern(callback)\n"
        '        phase("modern_request_returned")\n',
        1,
    )
    shim.write_text(code)

    sources = {name: (ROOT / name).read_bytes() for name in helper.BUILD_SOURCES}
    worker = sources[helper.WORKER_SOURCE].decode()
    worker = worker.replace("\nBUNDLE_ID = ", TRACE + "\nBUNDLE_ID = ", 1)
    worker = worker.replace(
        "    thread.start()\n",
        "    from Foundation import NSOperationQueue\n"
        '    NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: phase("main_queue_delivered"))\n'
        '    phase("background_start")\n'
        "    thread.start()\n",
        1,
    )
    if mode == "thread_pool":
        worker = worker.replace(
            "    def invoke() -> None:\n",
            "    def invoke() -> None:\n"
            "        from Foundation import NSAutoreleasePool\n"
            "        pool = NSAutoreleasePool.alloc().init()\n"
            '        phase("thread_pool_created")\n',
            1,
        ).replace(
            "            complete.set()\n",
            "            complete.set()\n            del pool\n",
            1,
        )
    if mode == "appkit":
        worker = worker.replace(
            "        result = run_with_main_loop(request, identity)\n",
            "        from AppKit import NSApplication\n"
            "        application = NSApplication.sharedApplication()\n"
            "        application.finishLaunching()\n"
            '        phase("application_finished_launching")\n'
            "        result = run_with_main_loop(request, identity)\n",
            1,
        )
    sources[helper.WORKER_SOURCE] = worker.encode()
    app = tmp_path.resolve() / "Consent Diagnostic.app"
    helper._build_app(app, sources, include_dir=Path(sysconfig.get_path("include")))
    device, uid = local_device_identity()
    request, _ = _request(
        tmp_path,
        release,
        operation="request",
        device_identity_sha256=device,
        os_uid=uid,
    )
    process = subprocess.Popen(
        ["/usr/bin/open", "-W", "-n", "-a", str(app), "--args", str(request)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    trace = request.with_name("diagnostic.jsonl")
    deadline = time.monotonic() + 15
    while not trace.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    if trace.exists():
        time.sleep(2)
        pid = json.loads(trace.read_text().splitlines()[0])["pid"]
        sample = subprocess.run(
            ["/usr/bin/sample", str(pid), "1", "1"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        request.with_name("sample.txt").write_text(sample.stdout + sample.stderr)
    stdout, stderr = process.communicate(timeout=100)
    log = subprocess.run(
        [
            "sudo",
            "/usr/bin/log",
            "show",
            "--last",
            "2m",
            "--style",
            "compact",
            "--info",
            "--debug",
            "--predicate",
            'process == "tccd" OR process == "CoSRemindersHelper" OR process == "remindd"',
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    request.with_name("tcc.log").write_text(log.stdout + log.stderr)
    print(
        json.dumps(
            {
                "mode": mode,
                "open_exit": process.returncode,
                "stderr": stderr.decode(errors="replace"),
            }
        )
    )
    assert trace.exists(), stdout + stderr
    print(trace.read_text())
    result = json.loads(request.with_name("result.json").read_text())
    print(
        json.dumps(
            {
                "mode": mode,
                "status": result["status"],
                "error": result.get("error"),
                "result": result.get("result"),
            }
        )
    )
    assert "main_queue_delivered" in trace.read_text()
