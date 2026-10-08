"""An owner-selected device check omission never becomes fabricated acceptance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from adminme.deployment_readiness import evaluate_deployment_readiness


def _inputs() -> dict[str, Any]:
    binding = {
        "release_commit": "a" * 40,
        "configuration_fingerprint": "b" * 64,
        "instance_fingerprint": "c" * 64,
        "host_fingerprint": "d" * 64,
    }
    return {
        "required": True,
        "host_topology": {"deployment_binding": dict(binding)},
        "deployment_evidence": {
            "deployment_binding": dict(binding),
            "identity_acceptance": {
                "member_ids": ["member-a"],
                "distinct_identities": True,
                "tested_at": datetime.now(timezone.utc).isoformat(),
                "device_check_exceptions": {
                    "member_ids": ["member-b"],
                    "owner_requested": True,
                    "reason": "owner_requested_device_check_omission",
                },
            },
        },
        "member_roles": {"member-a": "principal", "member-b": "principal"},
        "portal_member_ids": {"member-a", "member-b"},
        "portal_identity_count": 2,
        "provider_bindings": (),
        "service_channel_accounts": (),
        "bridge_enrollments": {},
        "openclaw_enabled": False,
        "openclaw_member_ids": set(),
        "openclaw_topology": {},
        "observation_active": True,
    }


def test_explicit_device_coverage_exception_keeps_tested_and_omitted_counts_distinct():
    inputs = _inputs()
    result = evaluate_deployment_readiness(**inputs)
    identity = result["dimensions"]["identity_acceptance"]
    assert identity["state"] == "healthy"
    assert identity["evidence"]["accepted_member_count"] == 1
    assert identity["evidence"]["required_member_count"] == 2
    assert identity["evidence"]["device_check_exception_count"] == 1
    assert identity["evidence"]["all_member_devices_tested"] is False
    assert "coverage exception" in identity["reason"]
    # This grants no other readiness or provider authority.
    assert result["ready"] is False
    assert "service_account_recovery" in result["blocking_dimensions"]
    assert "backup_rollback" in result["blocking_dimensions"]


@pytest.mark.parametrize(
    "defect",
    [
        "unknown",
        "duplicate",
        "all_omitted",
        "overlap",
        "owner_not_requested",
        "reason",
        "extra_field",
        "null",
        "stale",
        "future",
        "binding_drift",
        "identity_collision",
        "distinct_unproved",
    ],
)
def test_device_coverage_exception_fails_closed(defect: str) -> None:
    inputs = _inputs()
    identity = inputs["deployment_evidence"]["identity_acceptance"]
    exception = identity["device_check_exceptions"]
    if defect == "unknown":
        exception["member_ids"] = ["member-unknown"]
    elif defect == "duplicate":
        exception["member_ids"] = ["member-b", "member-b"]
    elif defect == "all_omitted":
        identity["member_ids"] = []
        exception["member_ids"] = ["member-a", "member-b"]
    elif defect == "overlap":
        exception["member_ids"] = ["member-a"]
    elif defect == "owner_not_requested":
        exception["owner_requested"] = False
    elif defect == "reason":
        exception["reason"] = "assumed"
    elif defect == "extra_field":
        exception["implicit"] = True
    elif defect == "null":
        identity["device_check_exceptions"] = None
    elif defect == "stale":
        identity["tested_at"] = (
            datetime.now(timezone.utc) - timedelta(days=32)
        ).isoformat()
    elif defect == "future":
        identity["tested_at"] = (
            datetime.now(timezone.utc) + timedelta(days=1)
        ).isoformat()
    elif defect == "binding_drift":
        inputs["host_topology"]["deployment_binding"]["configuration_fingerprint"] = (
            "f" * 64
        )
    elif defect == "identity_collision":
        inputs["portal_identity_count"] = 1
    elif defect == "distinct_unproved":
        identity["distinct_identities"] = False
    result = evaluate_deployment_readiness(**inputs)
    assert result["dimensions"]["identity_acceptance"]["state"] == "blocked"


def test_default_still_requires_every_configured_member_device() -> None:
    inputs = _inputs()
    identity = inputs["deployment_evidence"]["identity_acceptance"]
    identity.pop("device_check_exceptions")
    result = evaluate_deployment_readiness(**inputs)
    assert result["dimensions"]["identity_acceptance"]["state"] == "blocked"
    identity["member_ids"].append("member-b")
    result = evaluate_deployment_readiness(**inputs)
    assert result["dimensions"]["identity_acceptance"]["state"] == "healthy"
    assert (
        result["dimensions"]["identity_acceptance"]["evidence"][
            "all_member_devices_tested"
        ]
        is True
    )
