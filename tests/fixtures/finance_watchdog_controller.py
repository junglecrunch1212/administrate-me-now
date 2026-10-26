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
from tests.fixtures.finance_watchdog import notify, process_row, tree_rows  # noqa: E402


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


# Exact02aa collection order; only the selected execution differs between arms.
SUMMARY_NAMES = [
    "test_closed_worker_releases_cancelled_timer_without_gc",
    "test_timer_start_failure_preserves_error_and_reaps_child",
    "test_retry_callback_releases_timer_ownership_without_self_join",
    "test_timeout_abort_overlaps_close_reference_release",
    "test_owned_child_failure_closes_process_and_releases_admission[unknown_operation]",
    "test_owned_child_failure_closes_process_and_releases_admission[oversize]",
    "test_owned_child_failure_closes_process_and_releases_admission[timeout]",
    "test_uncertain_reap_keeps_admission_quarantined_until_confirmed[False-terminate]",
    "test_uncertain_reap_keeps_admission_quarantined_until_confirmed[False-kill]",
    "test_uncertain_reap_keeps_admission_quarantined_until_confirmed[False-wait]",
    "test_uncertain_reap_keeps_admission_quarantined_until_confirmed[True-terminate]",
    "test_uncertain_reap_keeps_admission_quarantined_until_confirmed[True-kill]",
    "test_uncertain_reap_keeps_admission_quarantined_until_confirmed[True-wait]",
    "test_summary_replay_and_idempotent_import",
    "test_summary_ingestion_keeps_revision_membership[first]",
    "test_summary_ingestion_keeps_revision_membership[last]",
    "test_summary_authority_revoked_after_read[native]",
    "test_summary_authority_revoked_after_read[member]",
    "test_summary_native_member_delivery_contract",
]
SUMMARY_IDS = [
    (
        "tests.acceptance.test_finance_summary_metadata"
        if name.startswith("test_summary_")
        else "tests.unit.test_finance_worker",
        name,
    )
    for name in SUMMARY_NAMES
]


def summary_arm_facts():
    import threading

    facts = {
        "pid": os.getpid(),
        "at": time.monotonic(),
        "address_space_limit": list(resource.getrlimit(resource.RLIMIT_AS)),
        "cpu_limit": list(resource.getrlimit(resource.RLIMIT_CPU)),
        "stack_limit": list(resource.getrlimit(resource.RLIMIT_STACK)),
        "python_stack_size_setting": threading.stack_size(),
        "glibc_version": os.confstr("CS_GNU_LIBC_VERSION"),
        "allocator_env": {
            key: os.environ.get(key)
            for key in (
                "MALLOC_ARENA_MAX",
                "GLIBC_TUNABLES",
                "PYTHONMALLOC",
                "LD_PRELOAD",
            )
        },
    }
    with open("/proc/self/status") as stream:
        status = stream.read(16385)
    assert len(status) <= 16384
    for line in status.splitlines():
        key, _, value = line.partition(":")
        if key in {"VmPeak", "VmSize", "VmRSS", "VmHWM", "VmStk", "Threads"}:
            facts[key] = int(value.split()[0]) * (1 if key == "Threads" else 1024)
    assert {"VmSize", "VmRSS", "Threads", "VmStk"} <= facts.keys()
    # Aggregate categories only: no raw addresses/paths, no anonymous=stack claim.
    categories = dict.fromkeys(
        (
            "anonymous_none",
            "anonymous_writable",
            "labelled_stack",
            "heap",
            "file_backed",
            "other",
        ),
        0,
    )
    total_bytes = count = 0
    with open("/proc/self/maps") as stream:
        for line in stream:
            total_bytes += len(line)
            count += 1
            assert total_bytes <= 256 * 1024 and count <= 4096
            fields = line.split(maxsplit=5)
            low, high = (int(v, 16) for v in fields[0].split("-"))
            name = fields[5].strip() if len(fields) == 6 else ""
            category = (
                "labelled_stack"
                if name == "[stack]" or name.startswith("[stack:")
                else "heap"
                if name == "[heap]"
                else "file_backed"
                if name.startswith("/")
                else "anonymous_none"
                if not name and fields[1].startswith("---")
                else "anonymous_writable"
                if not name and "w" in fields[1]
                else "other"
            )
            categories[category] += high - low
    facts["mapping_categories_bytes"] = categories
    facts["mapping_rows"] = count
    facts["mapping_complete"] = True
    return facts


def summary_comparison(root):
    import xml.etree.ElementTree as ET

    arms = []
    for arm in ("fresh", "sequence"):
        env = dict(os.environ, FINANCE_SUMMARY_ARM=arm)
        # Only this coordinator sends work_finished; arms cannot use control FDs.
        for key in ("FINANCE_WATCHDOG_FD", "FINANCE_WATCHDOG_ACK_FD"):
            env.pop(key, None)
        outcome = {"arm": arm, "started": time.monotonic()}
        child = subprocess.Popen(
            [sys.executable, "-I", "-B", __file__], env=env, cwd="/", close_fds=True
        )
        outcome["pid"] = child.pid
        # Existing outer frozen watchdog bounds both arms, including this wait.
        outcome["exit_code"] = child.wait()
        outcome["reaped_at"] = time.monotonic()
        try:
            supervisor = int(os.environ["FINANCE_WATCHDOG_PID"])
            assert process_row(supervisor)["start_ticks"] == int(
                os.environ["FINANCE_WATCHDOG_BIRTH"]
            )
            remaining = [
                row["pid"]
                for row in tree_rows(supervisor)
                if row["pid"] not in (supervisor, os.getpid())
            ]
            outcome["remaining_owned"] = remaining
            assert not remaining
            rows = ET.parse(root / f"results-{arm}.xml").findall(".//testcase")
            expected = SUMMARY_IDS[-1:] if arm == "fresh" else SUMMARY_IDS
            assert [
                (row.attrib["classname"], row.attrib["name"]) for row in rows
            ] == expected
            assert not any(
                row.find(kind) is not None
                for row in rows
                for kind in ("error", "skipped")
            )
            failures = [
                row.attrib["name"] for row in rows if row.find("failure") is not None
            ]
            assert failures in ([], [SUMMARY_IDS[-1][1]])
            assert outcome["exit_code"] == int(bool(failures))
            facts = json.loads((root / f"arm-facts-{arm}.json").read_text())
            assert set(facts) == {"before", "after"}
            assert facts["before"]["stack_limit"] == facts["after"]["stack_limit"]
            for value in facts.values():
                assert value["mapping_complete"] and value["pid"] == child.pid
                assert value["address_space_limit"] == [1024**3, 1024**3]
                assert value["cpu_limit"] == [60, 60]
                assert value["allocator_env"] == dict.fromkeys(
                    ("MALLOC_ARENA_MAX", "GLIBC_TUNABLES", "PYTHONMALLOC", "LD_PRELOAD")
                )
                assert value["python_stack_size_setting"] == 0
            outcome["failed_tests"] = failures
            outcome["evidence_complete"] = True
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AssertionError,
            ET.ParseError,
        ) as exc:
            outcome["evidence_complete"] = False
            outcome["stop_reason"] = type(exc).__name__
        source = root / "summary-results.jsonl"
        if source.exists():
            source.rename(root / f"summary-results-{arm}.jsonl")
        arms.append(outcome)
        (root / "comparison-results.json").write_text(json.dumps(arms, indent=2) + "\n")
        if not outcome["evidence_complete"]:
            break  # Missing evidence, unexpected failure or live descendants stops comparison.
    notify("work_finished", at=time.monotonic())
    return (
        2
        if len(arms) != 2 or not all(a["evidence_complete"] for a in arms)
        else int(any(a["exit_code"] for a in arms))
    )


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
    arm = os.environ.get("FINANCE_SUMMARY_ARM") if mode == "summary-metadata" else None
    if mode == "summary-metadata":
        if arm is None:
            return summary_comparison(root)
        assert arm in {"fresh", "sequence"}
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
    suffix = "-" + arm if arm else ""
    args = [
        "-q",
        "--runxfail",
        "--rootdir=" + str(REPO),
        "--basetemp=" + str(root / ("scratch/pytest" + suffix)),
        "--junitxml=" + str(root / ("results" + suffix + ".xml")),
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
    if arm:
        if arm == "fresh":
            args[-1] = "test_summary_native_member_delivery_contract"
        facts = {"before": summary_arm_facts()}
        (root / f"arm-facts-{arm}.json").write_text(json.dumps(facts, indent=2) + "\n")
    code = pytest.main(args)
    if arm:
        facts["after"] = summary_arm_facts()
        (root / f"arm-facts-{arm}.json").write_text(json.dumps(facts, indent=2) + "\n")
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
