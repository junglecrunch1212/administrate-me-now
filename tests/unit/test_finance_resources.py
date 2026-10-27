"""Independent resource-boundary cases; all identities and contents synthetic."""

from dataclasses import fields, replace

import pytest

from adminme.finance.budgets import (
    FinanceBudgetError,
    TurnBudgetStore,
    TurnIdentity,
    enforce_context,
    validate_page_size,
)
from adminme.finance.cache import FinanceResultCache, ResultCacheKey, parameters_hash
from adminme.finance.jobs import finance_job_status


def identity(**overrides: str) -> TurnIdentity:
    return TurnIdentity(
        **{
            "tenant_id": "synthetic-tenant",
            "actor_id": "synthetic-member-a",
            "audience_id": "private:synthetic-member-a",
            "session_id": "session-a",
            "turn_id": "message-a",
            **overrides,
        }
    )


def key(**overrides: str) -> ResultCacheKey:
    values = {field.name: f"synthetic-{field.name}" for field in fields(ResultCacheKey)}
    values["parameters_sha256"] = parameters_hash({"period": "2026-01"})
    values.update(overrides)
    return ResultCacheKey(**values)


def allow(_: ResultCacheKey) -> bool:
    return True


def test_context_caps_include_json_overhead_and_never_truncate() -> None:
    assert enforce_context("a" * 398, kind="core") == 400
    with pytest.raises(FinanceBudgetError, match="evidence_too_large"):
        enforce_context("a" * 399, kind="core")
    assert enforce_context("a" * 1198, kind="bundle") == 1200
    assert enforce_context("a" * 1998) == 2000
    with pytest.raises(FinanceBudgetError, match="evidence_too_large"):
        enforce_context("é" * 1000)


@pytest.mark.parametrize("value", [0, -1, 101, True, 2.5, "50"])
def test_rows_are_bounded_and_typed(value: object) -> None:
    with pytest.raises(FinanceBudgetError, match="page_size"):
        validate_page_size(value)  # type: ignore[arg-type]


def test_default_and_maximum_rows() -> None:
    assert validate_page_size() == 50
    assert validate_page_size(100) == 100


def test_missing_verified_turn_fails_closed() -> None:
    with pytest.raises(FinanceBudgetError, match="verified_finance_turn_required"):
        identity(turn_id="")


def test_unattested_rounds_each_consume_one_round() -> None:
    store = TurnBudgetStore()
    for tick in (0, 1, 2):
        receipt = store.admit(identity(), now=tick)
        assert store.charge(receipt, {"ready": False}, now=tick) == {"ready": False}
    with pytest.raises(FinanceBudgetError, match="call_budget"):
        store.admit(identity(), now=3)
    # One member cannot consume the other member's own admission state.
    store.admit(identity(actor_id="synthetic-member-b"), now=3)


def test_attested_rounds_still_allow_at_most_six_calls() -> None:
    store = TurnBudgetStore()
    for tick in range(6):
        store.admit(identity(), round_id=f"round:{tick // 2}", now=tick)
    with pytest.raises(FinanceBudgetError, match="call_budget"):
        store.admit(identity(), round_id="round:2", now=7)


def test_fourth_attested_round_rejected_before_sixth_call() -> None:
    store = TurnBudgetStore()
    for tick in range(3):
        store.admit(identity(), round_id=str(tick), now=tick)
    with pytest.raises(FinanceBudgetError, match="call_budget"):
        store.admit(identity(), round_id="3", now=4)


def test_evidence_is_cumulative_and_receipt_cannot_be_reused() -> None:
    store = TurnBudgetStore()
    for tick in range(3):
        receipt = store.admit(identity(), round_id="attested-round", now=tick)
        store.charge(receipt, {"evidence": "a" * 1980}, now=tick)
    receipt = store.admit(identity(), round_id="attested-round", now=3)
    with pytest.raises(FinanceBudgetError, match="evidence_budget"):
        store.charge(receipt, {"evidence": "a" * 100}, now=3)
    receipt = store.admit(identity(turn_id="new-message"), now=4)
    store.charge(receipt, {}, now=4)
    with pytest.raises(FinanceBudgetError, match="already_delivered"):
        store.charge(receipt, {}, now=4)


def test_query_and_turn_deadlines_do_not_return_late_success() -> None:
    store = TurnBudgetStore()
    receipt = store.admit(identity(), now=0)
    with pytest.raises(FinanceBudgetError, match="query_deadline"):
        store.charge(receipt, {"value": 1}, now=2)
    with pytest.raises(FinanceBudgetError, match="turn_deadline"):
        store.admit(identity(), now=30)


def test_admission_store_full_does_not_evict_active_limits() -> None:
    store = TurnBudgetStore()
    for i in range(1024):
        store.admit(identity(turn_id=f"message-{i}"), now=0)
    with pytest.raises(FinanceBudgetError, match="admission_busy"):
        store.admit(identity(turn_id="overflow"), now=1)


def test_two_bundle_limit_and_cumulative_evidence() -> None:
    store = TurnBudgetStore()
    store.admit(identity(), now=0)
    store.charge_bundle(identity(), "a" * 1198, now=0)
    store.charge_bundle(identity(), "b" * 1198, now=0)
    with pytest.raises(FinanceBudgetError, match="bundle_budget"):
        store.charge_bundle(identity(), "c", now=0)


def test_every_cache_identity_dimension_is_distinct() -> None:
    cache = FinanceResultCache()
    base = key()
    assert cache.put(base, {"amount_minor": 123}, allow, now=0)
    for field in fields(ResultCacheKey):
        value = "0" * 64 if field.name == "parameters_sha256" else "different"
        changed = replace(base, **{field.name: value})
        assert cache.get(changed, allow, now=1) is None


def test_cache_rechecks_authority_at_retrieval_and_delivery() -> None:
    cache = FinanceResultCache()
    scope = key()
    cache.put(scope, {"amount_minor": 123}, allow, now=0)
    handle = cache.get(scope, allow, now=1)
    assert handle is not None
    assert handle.deliver(lambda _: False, now=2) is None
    assert cache.get(scope, allow, now=3) is None
    with pytest.raises(FinanceBudgetError, match="handle_used"):
        handle.deliver(allow, now=3)


def test_cache_policy_provider_failure_is_refusal() -> None:
    def broken(_: ResultCacheKey) -> bool:
        raise RuntimeError("private policy internals")

    cache = FinanceResultCache()
    cache.put(key(), {"amount_minor": 123}, allow, now=0)
    assert cache.get(key(), broken, now=1) is None


def test_cache_lru_per_member_and_expiry() -> None:
    cache = FinanceResultCache()
    for i in range(50):
        cache.put(key(operation_id=f"op-{i}"), {"value": i}, allow, now=0)
    assert cache.get(key(operation_id="op-0"), allow, now=1) is not None
    cache.put(key(operation_id="op-50"), {"value": 50}, allow, now=2)
    assert cache.get(key(operation_id="op-1"), allow, now=3) is None
    assert cache.get(key(operation_id="op-0"), allow, now=86400) is None


def test_cache_is_copy_isolated_immutable_and_expiry_checked_at_delivery() -> None:
    cache = FinanceResultCache()
    value = {"metrics": [{"amount_minor": 123}]}
    cache.put(key(), value, allow, now=0, ttl=10)
    value["metrics"][0]["amount_minor"] = 999
    handle = cache.get(key(), allow, now=1)
    assert handle is not None
    assert handle.deliver(allow, now=2) == {"metrics": [{"amount_minor": 123}]}
    with pytest.raises(FinanceBudgetError, match="conflicting_finance_cache_result"):
        cache.put(key(), value, allow, now=3)
    handle = cache.get(key(), allow, now=4)
    assert handle is not None
    assert handle.deliver(allow, now=10) is None


def test_affected_cache_invalidation_and_hypothetical_isolation() -> None:
    cache = FinanceResultCache()
    cache.put(key(hypothetical_id="baseline"), {"value": 1}, allow, now=0)
    cache.put(key(hypothetical_id="what-if"), {"value": 2}, allow, now=0)
    cache.invalidate(lambda item: item.hypothetical_id == "what-if")
    assert cache.get(key(hypothetical_id="baseline"), allow, now=1) is not None
    assert cache.get(key(hypothetical_id="what-if"), allow, now=1) is None


def test_heavy_jobs_do_not_claim_acceptance_or_spawn_workers() -> None:
    result = finance_job_status()
    assert result["accepted"] is False
    assert result["job_id"] is None
    assert result["readiness"] == "temporarily_unavailable"
    assert result["implementation_state"] == "planned"
    assert result["proposed_limits"]["active_workers"] == 1
    assert result["proposed_limits"]["queue_size"] == 4
