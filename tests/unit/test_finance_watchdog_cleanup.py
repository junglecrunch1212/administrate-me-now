"""Clock-controlled checks for the synthetic summary supervisor's exit budget."""

from copy import deepcopy
import signal

import pytest

from tests.fixtures import finance_watchdog as watchdog


def receipt(first=50.0, finished=51.0):
    return {
        "started": 0.0,
        "work_finished_at": first,
        "finished": finished,
        "cleanup_schedule": watchdog.summary_cleanup_deadlines(125.0, first),
        "cleanup_seconds": finished - first,
        "elapsed_seconds": finished,
        "absolute_bound_met": finished <= 125,
        "forced": False,
        "faults": [],
        "controller_exit": 0,
        "controller_pid": 123,
        "reaped": [{"pid": 123, "at": finished}],
        "remaining_owned": [],
        "scratch_removed": True,
    }


@pytest.mark.parametrize(
    "delay", [0.75, 1.9], ids=["past_old_grace", "before_normal_deadline"]
)
def test_summary_cleanup_delayed_natural_exit(delay):
    report = receipt(finished=50 + delay)
    signals = []
    assert watchdog.summary_cleanup_step(
        report["cleanup_schedule"], report["finished"], False, False, signals.append
    ) == (False, False, False)
    assert signals == []
    assert watchdog.summary_cleanup_complete(report, report["finished"])


def test_summary_cleanup_stuck_exit_is_forced_failure():
    window = watchdog.summary_cleanup_deadlines(125.0, 50.0)
    signals = []
    term = kill = False
    for now in (51.9, 52.0, 52.9, 53.0, 53.9, 54.0):
        term, kill, reserve = watchdog.summary_cleanup_step(
            window, now, term, kill, signals.append
        )
        assert reserve is (now == 54.0)
    assert signals == [signal.SIGTERM, signal.SIGKILL, signal.SIGKILL]
    report = receipt(finished=54.25)
    report.update(forced=True, controller_exit=-9)
    assert not watchdog.summary_cleanup_complete(report, 54.25)
    report["controller_exit"] = 0
    assert not watchdog.summary_cleanup_complete(report, 54.25)
    # A stalled loop cannot start a fresh TERM phase inside the final reserve.
    signals.clear()
    assert watchdog.summary_cleanup_step(
        window, 54.0, False, False, signals.append
    ) == (False, True, True)
    assert signals == [signal.SIGKILL]


def test_summary_cleanup_fault_before_finish_keeps_first_origin():
    window = watchdog.summary_cleanup_deadlines(125.0, 49.0, 49.0)
    assert window == {
        "origin": 49.0,
        "term": 49.5,
        "kill": 50.5,
        "reserve": 53.0,
        "reap": 53.5,
        "end": 54.0,
    }
    assert watchdog.summary_cleanup_deadlines(125.0, 50.0, 49.0) == window
    signals = []
    assert watchdog.summary_cleanup_step(
        window, 49.5, False, False, signals.append
    ) == (True, False, False)
    assert signals == [signal.SIGTERM]
    report = receipt(49.0, 49.75)
    report["faults"] = ["containment:whole_tree_cpu_seconds"]
    assert not watchdog.summary_cleanup_complete(report, 49.75)


@pytest.mark.parametrize(
    "fault_at,term_at",
    [(50.25, 50.75), (51.75, 52.0), (52.75, 52.0)],
    ids=["early_fault", "late_fault", "after_term_deadline"],
)
def test_summary_cleanup_late_fault_cannot_reset_window(fault_at, term_at):
    normal = watchdog.summary_cleanup_deadlines(125.0, 50.0)
    fault = watchdog.summary_cleanup_deadlines(125.0, 50.0, fault_at)
    assert fault["term"] == term_at <= fault_at + 0.5
    assert fault["kill"] == term_at + 1 <= 53.0
    for key in ("origin", "reserve", "reap", "end"):
        assert fault[key] == normal[key]
    signals = []
    term, kill, reserve = watchdog.summary_cleanup_step(
        fault, max(fault_at, term_at), False, False, signals.append
    )
    assert (term, kill, reserve) == (True, False, False)
    assert watchdog.summary_cleanup_step(
        fault, fault["kill"], term, kill, signals.append
    ) == (True, True, False)
    assert signals == [signal.SIGTERM, signal.SIGKILL]


@pytest.mark.parametrize(
    "first,end,accepted",
    [(119.75, 124.75, True), (120.0, 125.0, True), (120.25, 125.0, False)],
    ids=["before_work", "at_work", "late_finish"],
)
def test_summary_cleanup_near_absolute_boundary(first, end, accepted):
    window = watchdog.summary_cleanup_deadlines(125.0, first)
    assert window["end"] == end <= 125
    assert window["term"] == end - 3
    assert window["kill"] == end - 2
    assert window["reserve"] == end - 1
    assert window["reap"] == end - 0.5
    report = receipt(first, first + 0.75)
    assert watchdog.summary_cleanup_complete(report, first + 0.75) is accepted
    late_fault = watchdog.summary_cleanup_deadlines(125.0, first, first + 3.5)
    assert late_fault == window


def test_summary_cleanup_rejects_missing_late_or_unclean_receipt():
    good = receipt()
    for changes in (
        {"forced": True},
        {"faults": ["cleanup_deadline"]},
        {"controller_exit": 1},
        {"controller_exit": False},
        {"remaining_owned": [123]},
        {"reaped": []},
        {"reaped": [{"pid": 123, "at": 52.001}]},
        {"scratch_removed": False},
        {"work_finished_at": None},
        {"finished": float("nan")},
        {"cleanup_seconds": 5.01},
        {"absolute_bound_met": False},
        {"cleanup_schedule": {"end": 56.0}},
    ):
        report = deepcopy(good)
        report.update(changes)
        assert not watchdog.summary_cleanup_complete(report, 51.0)
    assert not watchdog.summary_cleanup_complete(good, 55.001)
    assert not watchdog.summary_cleanup_complete(good, float("nan"))
    assert not watchdog.summary_cleanup_complete({}, 51.0)
