"""All lifecycle routes distinguish preparation from numerical model readiness."""

from __future__ import annotations

import json

import pytest

from adminme.finance.budgets import enforce_context
from adminme.finance.capabilities import (
    CAPABILITIES,
    capability,
    core_pointer,
    readiness,
    skill_bundle,
)


def test_all_thirty_one_routes_are_explicit_and_bounded() -> None:
    assert set(CAPABILITIES) == {f"F{i:02}" for i in range(1, 32)}
    for key, row in CAPABILITIES.items():
        assert row.evidence and row.excluded_intents and row.supported_intents
        assert row.implementation_state == "implemented"
        assert (
            row.public()["implementation_state_applies_to"] == "routing_and_preparation"
        )
        assert row.sensitivity == "privileged"
        assert row.audience == "authorized_owner_private"
        enforce_context(skill_bundle(key), kind="bundle")
    assert len(json.dumps(core_pointer(enabled=True)).encode()) <= 400


@pytest.mark.parametrize("key", sorted(CAPABILITIES))
def test_every_route_has_missing_and_permission_paths(key: str) -> None:
    permitted = readiness(key, dependencies={}, enabled=True, permitted=True)
    assert permitted["readiness"] == "missing_evidence"
    assert permitted["missing_evidence"] == list(capability(key).evidence)
    denied = readiness(
        key, dependencies={"private-account-canary": {"accepted": True}}, enabled=True
    )
    assert denied["readiness"] == "permission_required"
    assert "private-account-canary" not in json.dumps(denied)
    assert denied["required_evidence"] == []
    assert (
        readiness(key, dependencies={}, permitted=True)["readiness"]
        == "temporarily_unavailable"
    )


@pytest.mark.parametrize("key", sorted(CAPABILITIES))
def test_each_dependency_must_pass_every_dimension(key: str) -> None:
    item = capability(key)
    full = {
        name: dict(accepted=True, current=True, complete=True, authorized=True)
        for name in item.evidence
    }
    good = readiness(
        key,
        dependencies=full,
        available_calculators=frozenset(item.calculator_ids),
        enabled=True,
        permitted=True,
    )
    assert good["readiness"] in {"ready", "qualified", "professional_review"}
    for dimension in ("accepted", "current", "complete", "authorized"):
        bad = {name: dict(value) for name, value in full.items()}
        bad[item.evidence[0]][dimension] = False
        result = readiness(
            key,
            dependencies=bad,
            available_calculators=frozenset(item.calculator_ids),
            enabled=True,
            permitted=True,
        )
        assert result["readiness"] == "missing_evidence"
    if item.calculator_ids:
        result = readiness(key, dependencies=full, enabled=True, permitted=True)
        assert result["readiness"] == "model_unavailable"


@pytest.mark.parametrize("key", ["F00", "F32", "__import__", "F01; SELECT *", ""])
def test_unrecognized_routes_reject_without_evaluation(key: str) -> None:
    with pytest.raises(ValueError, match="unknown_finance_capability"):
        capability(key)


def test_professional_boundary_and_unimplemented_retirement_are_explicit() -> None:
    assert capability("F19").professional_review
    assert "tax_estimate" in capability("F19").calculator_ids
    assert "retirement_withdrawals" in capability("F23").calculator_ids
    assert "grant_emergency_access" in capability("F27").excluded_intents
    assert "optional_reserve_as_debt" in capability("F26").excluded_intents
