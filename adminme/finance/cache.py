"""Bounded volatile finance result cache; domain authority is never cached.

No persisted private cache or second source of truth. Every key dimension is
explicit, including hypotheticals and permission revisions. A retrieved handle
must pass a second authorization immediately at delivery.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from adminme.finance.budgets import FinanceBudgetError, json_bytes

MAX_CACHE_ENTRIES = 50
MAX_CACHE_BYTES = 10 * 1024 * 1024
CACHE_TTL_SECONDS = 86_400.0
MAX_CACHE_MEMBERS = 128
MAX_GLOBAL_CACHE_BYTES = 32 * 1024 * 1024


def parameters_hash(parameters: dict[str, Any]) -> str:
    return hashlib.sha256(json_bytes(parameters)).hexdigest()


@dataclass(frozen=True)
class ResultCacheKey:
    tenant_id: str
    actor_id: str
    audience_id: str
    grant_revision: str
    policy_revision: str
    book_id: str
    entity_id: str
    publication_id: str
    operation_id: str
    calculator_version: str
    model_version: str
    rules_version: str
    parameters_sha256: str
    as_of: str
    coverage_revision: str
    valuation_revision: str
    chosen_input_revision: str
    hypothetical_id: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 512
            for value in vars(self).values()
        ):
            raise FinanceBudgetError("incomplete_finance_cache_identity")
        if len(self.parameters_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.parameters_sha256
        ):
            raise FinanceBudgetError("invalid_finance_parameter_hash")

    @property
    def member(self) -> tuple[str, str]:
        return (self.tenant_id, self.actor_id)


Authorizer = Callable[[ResultCacheKey], bool]


@dataclass(frozen=True)
class _Entry:
    payload: bytes
    expires_at: float


class CacheDelivery:
    """An opaque, one-use handle; delivery rechecks current policy and expiry."""

    def __init__(self, cache: FinanceResultCache, key: ResultCacheKey) -> None:
        self._cache = cache
        self._key = key
        self._used = False
        self._lock = threading.Lock()

    def deliver(
        self, authorize: Authorizer, *, now: float | None = None
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._used:
                raise FinanceBudgetError("finance_cache_handle_used")
            self._used = True
        return self._cache._deliver(self._key, authorize, now=now)


class FinanceResultCache:
    def __init__(self) -> None:
        self._entries: OrderedDict[ResultCacheKey, _Entry] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _time(now: float | None) -> float:
        value = time.monotonic() if now is None else now
        if not math.isfinite(value):
            raise FinanceBudgetError("invalid_finance_clock")
        return value

    @staticmethod
    def _authorized(key: ResultCacheKey, authorize: Authorizer) -> bool:
        try:
            return authorize(key) is True
        except Exception:
            # Policy provider failure is a refusal, not a cached grant.
            return False

    def _purge(self, tick: float) -> None:
        for key, entry in tuple(self._entries.items()):
            if entry.expires_at <= tick:
                del self._entries[key]

    def put(
        self,
        key: ResultCacheKey,
        value: dict[str, Any],
        authorize: Authorizer,
        *,
        now: float | None = None,
        ttl: float = CACHE_TTL_SECONDS,
    ) -> bool:
        tick = self._time(now)
        if not 0 < ttl <= CACHE_TTL_SECONDS:
            raise FinanceBudgetError("invalid_finance_cache_ttl")
        if not self._authorized(key, authorize):
            self.invalidate(lambda candidate: candidate == key)
            return False
        payload = json_bytes(value)
        if len(payload) > MAX_CACHE_BYTES:
            return False
        with self._lock:
            self._purge(tick)
            previous = self._entries.get(key)
            if previous is not None and previous.payload != payload:
                raise FinanceBudgetError("conflicting_finance_cache_result")
            members = {candidate.member for candidate in self._entries}
            if key.member not in members and len(members) >= MAX_CACHE_MEMBERS:
                return False
            self._entries.pop(key, None)
            members_entries = [
                candidate
                for candidate in self._entries
                if candidate.member == key.member
            ]
            total = sum(
                len(self._entries[candidate].payload) for candidate in members_entries
            )
            while members_entries and (
                len(members_entries) >= MAX_CACHE_ENTRIES
                or total + len(payload) > MAX_CACHE_BYTES
            ):
                oldest = members_entries.pop(0)
                total -= len(self._entries.pop(oldest).payload)
            while self._entries and (
                sum(len(entry.payload) for entry in self._entries.values())
                + len(payload)
                > MAX_GLOBAL_CACHE_BYTES
            ):
                self._entries.popitem(last=False)
            self._entries[key] = _Entry(payload, tick + ttl)
        return True

    def get(
        self,
        key: ResultCacheKey,
        authorize: Authorizer,
        *,
        now: float | None = None,
    ) -> CacheDelivery | None:
        if not self._authorized(key, authorize):
            self.invalidate(lambda candidate: candidate == key)
            return None
        tick = self._time(now)
        with self._lock:
            self._purge(tick)
            if key not in self._entries:
                return None
            self._entries.move_to_end(key)
            return CacheDelivery(self, key)

    def _deliver(
        self,
        key: ResultCacheKey,
        authorize: Authorizer,
        *,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not self._authorized(key, authorize):
            self.invalidate(lambda candidate: candidate == key)
            return None
        tick = self._time(now)
        with self._lock:
            self._purge(tick)
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            result: dict[str, Any] = json.loads(entry.payload)
            return result

    def invalidate(self, predicate: Callable[[ResultCacheKey], bool]) -> None:
        """Remove affected pointers after publication/policy/input corrections."""
        with self._lock:
            for key in tuple(self._entries):
                if predicate(key):
                    del self._entries[key]
