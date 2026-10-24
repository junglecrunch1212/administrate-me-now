"""Identity evidence never becomes a provider or workstation permission."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from adminme.workstation_inventory import (
    IdentityInventory,
    identity_bindings,
    workstation_tools,
)
from bootstrap.workstation_inventory import provision

OWNER = "service-principal:chief-of-staff"


def document(now: datetime, *, owner: str = OWNER) -> dict:
    return {
        "schema_version": 1,
        "revision": 1,
        "updated_at": now.isoformat(),
        "bindings": [
            {
                "binding_id": "cos-browser",
                "revision": 1,
                "operating_principal_id": OWNER,
                "resource_owner_principal_id": owner,
                "expected_identity": {
                    "account": "cos@fixture.invalid",
                    "workspace": "fixture",
                    "role": "staff",
                },
                "observed_identity": {
                    "account": "cos@fixture.invalid",
                    "workspace": "fixture",
                    "role": "staff",
                },
                "route": {
                    "kind": "browser",
                    "device": "workstation",
                    "os_user": "service",
                    "browser_profile": "Profile CoS",
                },
                "verification": {
                    "observed_at": now.isoformat(),
                    "valid_until": (now + timedelta(hours=1)).isoformat(),
                    "binding_revision": 1,
                    "evidence_reference": "evidence:actual-destination-account",
                },
                "credential_store_references": ["apple-passwords-ref:cos-google"],
            }
        ],
    }


def test_binding_mismatch_expiry_and_revision_are_distinct_from_access(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    now = datetime.now(timezone.utc)
    value = document(now)
    provision(tmp_path, value, expected_revision=0)

    def read(at: datetime) -> dict:
        return identity_bindings(tmp_path, allowed_owners={OWNER}, now=at)

    row = read(now)["bindings"][0]
    assert row["identity_state"] == "verified_record"
    assert row["states"]["authenticated"]["value"] is None
    assert row["states"]["authorized"]["value"] is None
    assert row["states"]["executable"]["value"] is None
    assert (
        read(now + timedelta(hours=2))["bindings"][0]["identity_state"]
        == "stale_evidence"
    )
    value["revision"] = 2
    value["bindings"][0]["observed_identity"]["account"] = "other@fixture.invalid"
    provision(tmp_path, value, expected_revision=1)
    row = read(now)["bindings"][0]
    assert row["identity_state"] == "identity_mismatch"
    assert row["states"]["authenticated"]["value"] is False
    assert row["states"]["executable"]["value"] is False
    assert (
        identity_bindings(tmp_path, allowed_owners={"another-member"}, now=now)[
            "bindings"
        ]
        == []
    )
    with pytest.raises(ValueError, match="revision"):
        provision(tmp_path, value, expected_revision=1)


def test_private_schema_rejects_secret_fields_and_unsafe_files(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    value = document(now)
    value["bindings"][0]["password"] = "must-not-be-retained"
    with pytest.raises(ValueError):
        IdentityInventory.model_validate(value)
    path = tmp_path / "workstation-identities.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    result = identity_bindings(tmp_path, allowed_owners={OWNER}, now=now)
    assert result == {"state": "invalid_private_inventory", "bindings": []}
    assert "must-not-be-retained" not in str(result)
    path.write_text(json.dumps(document(now)))
    path.chmod(0o644)
    assert (
        identity_bindings(tmp_path, allowed_owners={OWNER}, now=now)["state"]
        == "invalid_private_inventory"
    )
    path.unlink()
    target = tmp_path / "other.json"
    target.write_text(json.dumps(document(now)))
    target.chmod(0o600)
    path.symlink_to(target)
    assert (
        identity_bindings(tmp_path, allowed_owners={OWNER}, now=now)["state"]
        == "invalid_private_inventory"
    )


def test_literal_tool_denial_is_not_provider_disconnection_or_native_registration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.json"
    config = {
        "agents": {
            "list": [
                {"id": "cos", "tools": {"allow": ["administrateme", "memory_get"]}}
            ]
        }
    }
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(openclaw_runtime_config_path=path)
    )
    result = workstation_tools(runtime, "cos")
    tools = {
        tool["tool"]: tool["states"]
        for row in result["capabilities"]
        for tool in row["tools"]
    }
    assert tools["browser"]["tool_accessible"]["value"] is False
    assert tools["browser"]["connected"]["value"] is None
    assert tools["memory_get"]["tool_accessible"]["value"] is None
    assert result["loaded_catalog_verified"] is False
