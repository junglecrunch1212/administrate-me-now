"""Fail-closed, process-local finance turn admission and disclosure budgets.

Identity must come from authenticated transport and resolved domain authority.
A model-supplied correlation/round identifier is not a turn boundary. Until the
pinned gateway attests round identifiers, each call consumes a separate round.
No content is truncated: oversized material requires a smaller query.
"""

from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal

CORE_TOKENS = 400
BUNDLE_TOKENS = 1_200
RESULT_TOKENS = 2_000
TURN_EVIDENCE_TOKENS = 6_000
MAX_LIGHT_CALLS = 6
MAX_ROUNDS = 3
MAX_BUNDLES = 2
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
QUERY_DEADLINE_SECONDS = 2.0
TURN_DEADLINE_SECONDS = 30.0
TURN_RETENTION_SECONDS = 86_400.0
MAX_TRACKED_TURNS = 1_024


class FinanceBudgetError(ValueError):
    """Stable safe error; never includes evidence, identifiers or input values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise FinanceBudgetError("invalid_finance_payload") from None


def enforce_context(
    value: Any, *, kind: Literal["core", "bundle", "result"] = "result"
) -> int:
    """Use bytes as a conservative token ceiling for byte-backed tokenizers.

    The entire serialized object, including required caveats, counts. This
    deliberately admits fewer tokens than the proposed ceiling, avoiding an
    unpinned tokenizer dependency or silently dropping policy to meet a cap.
    """
    limits = {"core": CORE_TOKENS, "bundle": BUNDLE_TOKENS, "result": RESULT_TOKENS}
    if kind not in limits:
        raise FinanceBudgetError("invalid_finance_budget_kind")
    size = len(json_bytes(value))
    if size > limits[kind]:
        raise FinanceBudgetError("finance_evidence_too_large")
    return size


def validate_page_size(limit: int = DEFAULT_PAGE_SIZE) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise FinanceBudgetError("invalid_finance_page_size")
    return limit


@dataclass(frozen=True)
class TurnIdentity:
    tenant_id: str
    actor_id: str
    audience_id: str
    session_id: str
    turn_id: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 512
            for value in vars(self).values()
        ):
            raise FinanceBudgetError("verified_finance_turn_required")


@dataclass(frozen=True)
class TurnReceipt:
    identity: TurnIdentity
    call_number: int
    admitted_at: float


@dataclass
class _TurnState:
    started_at: float
    calls: int = 0
    rounds: set[str] = field(default_factory=set)
    charged_calls: set[int] = field(default_factory=set)
    call_times: dict[int, float] = field(default_factory=dict)
    evidence_bytes: int = 0
    bundles: int = 0


class TurnBudgetStore:
    """Bound state and admission without evicting an active turn's limits.

    A process restart cannot prove continuity of an in-flight model turn.
    Deployment must stop that turn; this store is not a durable scheduler.
    """

    def __init__(self) -> None:
        self._states: dict[TurnIdentity, _TurnState] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _time(now: float | None) -> float:
        value = time.monotonic() if now is None else now
        if not math.isfinite(value):
            raise FinanceBudgetError("invalid_finance_clock")
        return value

    def admit(
        self,
        identity: TurnIdentity,
        *,
        round_id: str | None = None,
        now: float | None = None,
    ) -> TurnReceipt:
        """Admit a light call; optional round_id must be gateway-attested."""
        if not isinstance(identity, TurnIdentity):
            raise FinanceBudgetError("verified_finance_turn_required")
        if round_id is not None and (
            not isinstance(round_id, str) or not round_id or len(round_id) > 128
        ):
            raise FinanceBudgetError("invalid_finance_round")
        tick = self._time(now)
        with self._lock:
            for key, value in tuple(self._states.items()):
                if tick - value.started_at > TURN_RETENTION_SECONDS:
                    del self._states[key]
            state = self._states.get(identity)
            if state is None:
                if len(self._states) >= MAX_TRACKED_TURNS:
                    raise FinanceBudgetError("finance_admission_busy")
                state = _TurnState(started_at=tick)
                self._states[identity] = state
            if (
                tick < state.started_at
                or tick - state.started_at >= TURN_DEADLINE_SECONDS
            ):
                raise FinanceBudgetError("finance_turn_deadline")
            next_round = round_id or f"call:{state.calls + 1}"
            if state.calls >= MAX_LIGHT_CALLS or (
                next_round not in state.rounds and len(state.rounds) >= MAX_ROUNDS
            ):
                raise FinanceBudgetError("finance_turn_call_budget")
            state.calls += 1
            state.rounds.add(next_round)
            state.call_times[state.calls] = tick
            return TurnReceipt(identity, state.calls, tick)

    def charge(
        self,
        receipt: TurnReceipt,
        result: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Charge once after work and before delivery; failed work still costs a call."""
        size = enforce_context(result, kind="result")
        tick = self._time(now)
        with self._lock:
            state = self._states.get(receipt.identity)
            if (
                state is None
                or state.call_times.get(receipt.call_number) != receipt.admitted_at
            ):
                raise FinanceBudgetError("invalid_finance_receipt")
            if receipt.call_number in state.charged_calls:
                raise FinanceBudgetError("finance_receipt_already_delivered")
            if (
                tick < receipt.admitted_at
                or tick - state.started_at >= TURN_DEADLINE_SECONDS
            ):
                raise FinanceBudgetError("finance_turn_deadline")
            if tick - receipt.admitted_at >= QUERY_DEADLINE_SECONDS:
                raise FinanceBudgetError("finance_query_deadline")
            if state.evidence_bytes + size > TURN_EVIDENCE_TOKENS:
                raise FinanceBudgetError("finance_turn_evidence_budget")
            state.evidence_bytes += size
            state.charged_calls.add(receipt.call_number)
        return result

    def charge_bundle(
        self,
        identity: TurnIdentity,
        bundle: Any,
        *,
        now: float | None = None,
    ) -> None:
        size = enforce_context(bundle, kind="bundle")
        tick = self._time(now)
        with self._lock:
            state = self._states.get(identity)
            if state is None:
                raise FinanceBudgetError("verified_finance_turn_required")
            if (
                tick < state.started_at
                or tick - state.started_at >= TURN_DEADLINE_SECONDS
            ):
                raise FinanceBudgetError("finance_turn_deadline")
            if state.bundles >= MAX_BUNDLES:
                raise FinanceBudgetError("finance_bundle_budget")
            if state.evidence_bytes + size > TURN_EVIDENCE_TOKENS:
                raise FinanceBudgetError("finance_turn_evidence_budget")
            state.bundles += 1
            state.evidence_bytes += size
