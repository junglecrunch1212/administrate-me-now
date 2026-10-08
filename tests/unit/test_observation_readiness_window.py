"""Fresh effect proofs retain and match the complete historical observation window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from adminme.deployment_readiness import evaluate_deployment_readiness


def _inputs() -> dict[str, Any]:
    window = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    return {
        "required": True,
        "host_topology": {},
        "deployment_evidence": {"provider_effect_window": {"started_at": window}},
        "member_roles": {},
        "portal_member_ids": set(),
        "portal_identity_count": 0,
        "provider_bindings": (),
        "service_channel_accounts": (),
        "bridge_enrollments": {},
        "openclaw_enabled": False,
        "openclaw_member_ids": set(),
        "openclaw_topology": {},
        "observation_active": True,
        "source_assurance": {
            "state": "healthy",
            "freshness": {
                "state": "fresh",
                "revision_current": True,
                "refresh_error_type": None,
            },
            "provider_effects": {
                "state": "healthy",
                "counts": {},
                "observation_window": {
                    "state": "healthy",
                    "started_at_configured": True,
                    "started_at": window,
                    "counts": {},
                },
            },
        },
    }


def test_fresh_effect_proof_does_not_expire_or_reset_historical_window() -> None:
    inputs = _inputs()
    original = inputs["deployment_evidence"]["provider_effect_window"]["started_at"]
    readiness = evaluate_deployment_readiness(**inputs)
    dimension = readiness["dimensions"]["observation_effects"]
    assert dimension["state"] == "healthy"
    assert dimension["evidence"]["window_matches_attestation"] is True
    assert inputs["deployment_evidence"]["provider_effect_window"]["started_at"] == (
        original
    )
    # The observation proof grants neither owner/device nor backup authority.
    assert readiness["ready"] is False
    assert "service_account_recovery" in readiness["blocking_dimensions"]
    assert "identity_acceptance" in readiness["blocking_dimensions"]
    assert "backup_rollback" in readiness["blocking_dimensions"]


@pytest.mark.parametrize("mode", ["missing", "invalid", "naive", "future", "drifted"])
def test_effect_proof_requires_the_exact_valid_attested_window(mode: str) -> None:
    inputs = _inputs()
    boundary = {
        "missing": None,
        "invalid": "not-a-time",
        "naive": "2025-01-01T00:00:00",
        "future": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "drifted": (datetime.now(timezone.utc) - timedelta(days=399)).isoformat(),
    }[mode]
    audit = inputs["source_assurance"]["provider_effects"]["observation_window"]
    audit["started_at"] = boundary
    if mode != "drifted":
        inputs["deployment_evidence"]["provider_effect_window"]["started_at"] = boundary
    result = evaluate_deployment_readiness(**inputs)
    assert result["dimensions"]["observation_effects"]["state"] == "blocked"
    assert (
        result["dimensions"]["observation_effects"]["evidence"][
            "window_matches_attestation"
        ]
        is False
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("state", "stale"),
        ("revision_current", False),
        ("refresh_error_type", "OSError"),
    ],
)
def test_old_window_still_requires_current_successful_source_proof(
    field: str, value: object
) -> None:
    inputs = _inputs()
    inputs["source_assurance"]["freshness"][field] = value
    result = evaluate_deployment_readiness(**inputs)
    assert result["dimensions"]["observation_effects"]["state"] == "blocked"


@pytest.mark.parametrize(
    "field",
    ["provider_called_true", "provider_called_unknown", "suppressed_false_or_unknown"],
)
def test_old_window_preserves_failed_effect_accounting(field: str) -> None:
    inputs = _inputs()
    audit = inputs["source_assurance"]["provider_effects"]["observation_window"]
    audit["state"] = "degraded"
    audit["counts"][field] = 1
    result = evaluate_deployment_readiness(**inputs)
    dimension = result["dimensions"]["observation_effects"]
    assert dimension["state"] == "blocked"
    assert dimension["evidence"][f"window_{field}"] == 1
