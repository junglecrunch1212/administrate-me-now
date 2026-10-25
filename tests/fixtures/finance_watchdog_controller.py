"""Fixed synthetic watchdog children; no arbitrary executable or storage inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "bridge")]
from tests.fixtures.finance_watchdog import notify, process_row  # noqa: E402


def stub(role, mode):
    stubborn = mode != "contract-normal"
    if stubborn:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    if role == "executor":
        while True:
            time.sleep(0.05)
    child = subprocess.Popen(
        [sys.executable, "-I", "-B", __file__, "executor", mode], close_fds=True
    )
    if not stubborn:

        def stop(*args):
            child.terminate()
            child.wait(timeout=1)
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop)
    print(json.dumps({"ready": True, "executor": child.pid}), flush=True)
    while True:
        time.sleep(0.05)


def main():
    mode = os.environ["FINANCE_WATCHDOG_MODE"]
    root = Path(os.environ["FINANCE_WATCHDOG_ROOT"])
    if mode.startswith("contract-"):
        if mode != "contract-normal":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGUSR1, signal.SIG_IGN)
        runtime = subprocess.Popen(
            [sys.executable, "-I", "-B", __file__, "runtime", mode],
            stdout=subprocess.PIPE,
            close_fds=True,
        )
        assert json.loads(runtime.stdout.readline())["ready"]
        notify(
            "runtime",
            pid=runtime.pid,
            birth=process_row(runtime.pid)["start_ticks"]
            + (1 if mode == "contract-protocol" else 0),
        )
        if mode == "contract-orphan":
            return 7
        if mode == "contract-normal":
            time.sleep(0.5)
            notify("work_finished", at=time.monotonic())
            runtime.terminate()
            assert runtime.wait(timeout=1) == 0
            return 0
        while True:
            time.sleep(0.05)
    import pytest

    if mode == "full":
        os.environ["ADMINME_SYNTHETIC_STORAGE_MEASUREMENT"] = "1"
        selection = "tests/acceptance/test_finance_storage_measurement.py::test_fixed_schedule_representative_storage_measurement"
    elif mode.startswith("setup"):
        selection = "tests/acceptance/test_finance_watchdog.py::test_phased_setup_under_watchdog"
    else:
        selection = "tests/acceptance/test_finance_watchdog.py"
    args = [
        "-q",
        "--runxfail",
        "--rootdir=" + str(REPO),
        "--basetemp=" + str(root / "scratch/pytest"),
        "--junitxml=" + str(root / "results.xml"),
        "-o",
        "cache_dir=" + str(root / "scratch/cache"),
        str(REPO / selection),
    ]
    if mode == "contracts":
        args += ["-k", "not phased_setup"]
    code = pytest.main(args)
    if mode == "contracts":
        notify("work_finished", at=time.monotonic())
    return code


if __name__ == "__main__":
    if len(sys.argv) == 3:
        stub(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 1:
        raise SystemExit(main())
    else:
        raise SystemExit("fixed synthetic role required")
