"""Fixed synthetic watchdog children; no arbitrary executable or storage inputs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import resource
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
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            notify(
                "runtime_reaped",
                pid=runtime.pid,
                cpu_seconds=usage.ru_utime + usage.ru_stime,
            )
            return 0
        while True:
            time.sleep(0.05)
    import pytest

    if mode == "full":
        os.environ["ADMINME_SYNTHETIC_STORAGE_MEASUREMENT"] = "1"
        selection = "tests/acceptance/test_finance_storage_measurement.py::test_fixed_schedule_representative_storage_measurement"
    elif mode == "staged-bounded":
        os.environ["ADMINME_SYNTHETIC_STAGED_MEASUREMENT"] = "1"
        selection = "tests/acceptance/test_finance_staging_measurement.py::test_one_staged_import_replay_and_loaded_queries"
    elif mode == "summary-metadata":
        selection = "tests/acceptance/test_finance_summary_metadata.py"
    elif mode == "allocation-profile":
        selection = "tests/acceptance/test_finance_allocation_profile.py::test_bounded_three_import_allocation_profile"
    elif mode == "focused-import":
        selection = "tests/acceptance/test_finance_import_correction.py::test_focused_import_cancellation_replay_and_late_shutdown"
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
    if mode == "summary-metadata":
        # Timer ownership/recovery checks and the six affected application cases
        # share this single owned workload budget and interpreter exit.
        args.insert(-1, str(REPO / "tests/unit/test_finance_worker.py"))
        args += [
            "--maxfail=1",
            "-p",
            "no:cacheprovider",
            "-k",
            "test_closed_worker_releases_cancelled_timer_without_gc or "
            "test_timer_start_failure_preserves_error_and_reaps_child or "
            "test_retry_callback_releases_timer_ownership_without_self_join or "
            "test_timeout_abort_overlaps_close_reference_release or "
            "test_owned_child_failure_closes_process_and_releases_admission or "
            "test_uncertain_reap_keeps_admission_quarantined_until_confirmed or "
            "test_summary_replay_and_idempotent_import or "
            "test_summary_ingestion_keeps_revision_membership or "
            "test_summary_authority_revoked_after_read or "
            "test_summary_native_member_delivery_contract",
        ]
    if mode == "contracts":
        args += ["-k", "not phased_setup"]
    code = pytest.main(args)
    if mode in {"contracts", "summary-metadata"}:
        notify("work_finished", at=time.monotonic())
    return code


if __name__ == "__main__":
    if len(sys.argv) == 3:
        stub(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 1:
        raise SystemExit(main())
    else:
        raise SystemExit("fixed synthetic role required")
