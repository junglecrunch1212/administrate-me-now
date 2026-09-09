"""Fixed monotonic capacity and recovery reserves for one deployment attempt."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass


CONTROLLER_SECONDS = 13 * 60 * 60
SETTLEMENT_SECONDS = 6 * 60 * 60
RECOVERY_SECONDS = 6 * 60 * 60
FINALIZATION_RESERVE_SECONDS = 10 * 60
START_ENV = "ADMINME_CONTROLLER_STARTED_MONOTONIC"
DEADLINE_ENV = "ADMINME_CONTROLLER_DEADLINE_MONOTONIC"


def deployment_clock() -> float:
    """Read the same OS clock in system Python and the selected runtime.

    macOS Python before 3.10 gives time.monotonic() a process-local origin:
    https://docs.python.org/3/library/time.html#time.monotonic
    Inherited deadlines must therefore name CLOCK_MONOTONIC explicitly.
    """
    return time.clock_gettime(time.CLOCK_MONOTONIC)


class DeploymentBudgetError(RuntimeError):
    """The attempt cannot preserve its fixed capacity and recovery reserve."""


@dataclass(frozen=True)
class DeploymentBudget:
    started_at: float
    deadline: float

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str], *, now: float, supervised: bool
    ) -> DeploymentBudget:
        if not math.isfinite(now) or now < 0:
            raise DeploymentBudgetError("deployment monotonic clock is invalid")
        raw_start = environment.get(START_ENV)
        raw_deadline = environment.get(DEADLINE_ENV)
        if not supervised:
            if raw_start is not None or raw_deadline is not None:
                raise DeploymentBudgetError("untrusted controller deadline environment")
            return cls(now, now + CONTROLLER_SECONDS)
        try:
            start = float(raw_start) if raw_start is not None else float("nan")
            deadline = float(raw_deadline) if raw_deadline is not None else float("nan")
        except ValueError as exc:
            raise DeploymentBudgetError("controller deadline is invalid") from exc
        if (
            not math.isfinite(start)
            or not math.isfinite(deadline)
            or start < 0
            or start > now
            or deadline <= now
            or not math.isclose(
                deadline - start, CONTROLLER_SECONDS, rel_tol=0, abs_tol=0.001
            )
        ):
            raise DeploymentBudgetError("controller deadline is expired or invalid")
        return cls(start, deadline)

    def candidate_deadline(self, *, now: float) -> float:
        required = SETTLEMENT_SECONDS + RECOVERY_SECONDS + FINALIZATION_RESERVE_SECONDS
        if not math.isfinite(now) or self.deadline - now < required:
            raise DeploymentBudgetError(
                "insufficient controller time before switch for settlement and recovery"
            )
        return min(
            now + SETTLEMENT_SECONDS,
            self.deadline - RECOVERY_SECONDS - FINALIZATION_RESERVE_SECONDS,
        )

    def recovery_deadline(self, *, now: float) -> float:
        deadline = min(
            now + RECOVERY_SECONDS, self.deadline - FINALIZATION_RESERVE_SECONDS
        )
        if not math.isfinite(now) or deadline <= now:
            raise DeploymentBudgetError("controller recovery reserve is exhausted")
        return deadline
