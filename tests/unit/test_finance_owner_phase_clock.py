"""Keep fixed owner phase budgets independent of supervisor IPC polling delay."""

import pytest

from tests.fixtures.finance_watchdog import OwnerPhaseClock


def edge(name, kind, at):
    return {"kind": "phase", "name": name, "edge": kind, "at": at}


def test_delayed_receipt_of_on_time_end_does_not_cancel_next_phase():
    clock = OwnerPhaseClock({"baseline": 10, "import": 120}, 75.893925104)
    clock.accept(edge("baseline", "start", 95.496596817), 95.505416491)
    # The failed hosted run completed all 200 scheduled baseline requests before
    # its deadline; its end and next start reached the polling supervisor later.
    clock.accept(edge("baseline", "end", 105.457925733), 105.510826564)
    clock.accept(edge("import", "start", 105.458225361), 105.510845189)
    assert clock.overdue(105.510845189) is None
    assert clock.active == "import" and not clock.complete
    clock.accept(edge("import", "end", 225.458225361), 225.6)
    assert clock.complete and clock.overdue(400) is None


def test_genuinely_late_end_and_missing_end_still_exceed_unchanged_budget():
    clock = OwnerPhaseClock({"baseline": 10}, 0)
    clock.accept(edge("baseline", "start", 1), 1.1)
    assert clock.overdue(11) is None
    assert clock.overdue(11.001) == "baseline"
    with pytest.raises(TimeoutError, match="phase_deadline:baseline"):
        clock.accept(edge("baseline", "end", 11.001), 11.2)
    assert not clock.complete


@pytest.mark.parametrize("at", [-1, float("nan"), float("inf"), True, "1", 2])
def test_invalid_or_future_clock_cannot_extend_phase(at):
    clock = OwnerPhaseClock({"baseline": 10}, 0)
    with pytest.raises(ValueError, match="phase_clock"):
        clock.accept(edge("baseline", "start", at), 1)
    assert clock.active is None and not clock.complete


def test_regressing_clock_duplicate_edges_and_wrong_order_remain_closed():
    clock = OwnerPhaseClock({"baseline": 10, "import": 120}, 0)
    with pytest.raises(ValueError, match="phase_order"):
        clock.accept(edge("import", "start", 1), 1)
    clock.accept(edge("baseline", "start", 1), 1)
    with pytest.raises(ValueError, match="phase_edge"):
        clock.accept(edge("baseline", "start", 2), 2)
    with pytest.raises(ValueError, match="phase_clock"):
        clock.accept(edge("baseline", "end", 0.9), 2)
    clock.accept(edge("baseline", "end", 10), 10.2)
    with pytest.raises(ValueError, match="phase_order"):
        clock.accept(edge("baseline", "end", 10), 10.2)
    with pytest.raises(ValueError, match="phase_clock"):
        clock.accept(edge("import", "start", 9.9), 10.2)


def test_missing_clock_is_not_replaced_with_receipt_time():
    clock = OwnerPhaseClock({"baseline": 10}, 0)
    with pytest.raises(ValueError, match="phase_fields"):
        clock.accept({"kind": "phase", "name": "baseline", "edge": "start"}, 1)
