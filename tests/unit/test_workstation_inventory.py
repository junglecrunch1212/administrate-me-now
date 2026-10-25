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
    provider_bindings,
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
    rows = [tool for group in result["capabilities"] for tool in group["tools"]]
    assert all(row["runtime_surface"] == "openclaw_dynamic" for row in rows)
    assert (
        tools["exec"]["tool_accessible"]["basis"]
        == "explicit_openclaw_dynamic_tool_policy"
    )
    assert tools["memory_get"]["tool_accessible"]["value"] is None
    assert result["effective_toolset_verified"] is False
    assert result["declaration_catalog_proves_access"] is False
    assert tools["memory_get"]["tool_accessible"]["basis"] == (
        "native_effective_toolset_or_invocation_required"
    )


def test_native_additive_allow_and_scheduler_alias_do_not_report_false_absence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "agents": {
                    "list": [
                        {
                            "id": "cos",
                            "tools": {
                                "allow": ["administrateme", "cron"],
                                "alsoAllow": ["browser"],
                                "deny": ["BASH"],
                            },
                        }
                    ]
                }
            }
        )
    )
    path.chmod(0o600)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(openclaw_runtime_config_path=path)
    )
    result = workstation_tools(runtime, "cos")
    values = {
        tool["tool"]: tool["states"]["tool_accessible"]["value"]
        for row in result["capabilities"]
        for tool in row["tools"]
    }
    assert values["browser"] is None and values["automations"] is None
    assert values["exec"] is False


def test_member_browser_inventory_never_becomes_cos_operating_account(
    tmp_path: Path,
) -> None:
    tmp_path.chmod(0o700)
    now = datetime.now(timezone.utc)
    value = document(now, owner="member-a")
    value["bindings"][0]["operating_principal_id"] = "member-a"
    provision(tmp_path, value, expected_revision=0)
    row = identity_bindings(tmp_path, allowed_owners={"member-a"}, now=now)["bindings"][
        0
    ]
    assert row["identity_state"] == "verified_record"
    assert (
        row["workstation_identity_requirement"]
        == "member_account_is_not_a_cos_workstation_route"
    )
    assert row["states"]["executable"]["value"] is False


def test_service_account_does_not_expose_another_members_binding() -> None:
    binding = SimpleNamespace(
        owner_id=OWNER, resource_owner_principal_id="member-b", on_behalf_of="member-b"
    )
    runtime = SimpleNamespace(
        adapters=None, settings=SimpleNamespace(provider_bindings=[binding])
    )
    assert provider_bindings(runtime, {OWNER, "member-a"}) == []


def realistic_inventory() -> dict:
    """Large synthetic map also crosses the actual pinned native tool seam."""
    from adminme.workstation_inventory import states, state

    rows = []
    for index in range(18):
        binding = document(datetime(2026, 1, 1, tzinfo=timezone.utc))["bindings"][0]
        binding["binding_id"] = f"fixture-route-{index:02d}"
        binding.update(
            {
                "identity_state": "verified_record",
                "supported_operations": [f"operation-{i}" for i in range(25)],
                "resource_boundaries": [
                    f"Private synthetic resource {i}: " + "x" * 180 for i in range(30)
                ],
                "states": states(configured=state(True, "owner_private_inventory")),
            }
        )
        rows.append(binding)
    return {
        "contract": "administrateme-workstation-inventory-v2",
        "as_of": "2026-01-01T00:00:00Z",
        "identity_inventory": {"state": "available", "revision": 1, "bindings": rows},
        "provider_bindings": [],
        "member_bridges": [],
        "workstation_tools": {"state": "policy_read", "capabilities": []},
        "observation_mode": False,
    }


def test_realistic_map_and_detail_pages_fit_entire_native_result_without_data_loss() -> (
    None
):
    from adminme.workstation_inventory import inventory_page

    source = realistic_inventory()
    assert len(json.dumps(source)) > 64000
    first = inventory_page(source)
    assert first["tool_rows_scope"] == "openclaw_dynamic_only"
    assert first["native_base_tools_scope"] == "shell_files_mcp_apps"
    assert first["counts"] == {
        "identities": 18,
        "providers": 0,
        "bridges": 0,
        "tools": 0,
    }
    assert len(first["items"]) == 18
    assert first["page"]["next_offset"] is None
    for view in ("overview", "details"):
        query = {"view": view, "section": "identities"}
        if view == "details":
            query["binding_id"] = "fixture-route-17"
        offset = 0
        values = []
        while True:
            page = inventory_page(
                source, **query, offset=offset, snapshot=first["snapshot"]
            )
            text = json.dumps(page, ensure_ascii=False, separators=(",", ":"))
            # The native result repeats the document in text and details.
            native = {"content": [{"type": "text", "text": text}], "details": page}
            assert len(json.dumps(native, ensure_ascii=False).encode()) < 9500
            assert page["page"]["returned"] == len(page["items"])
            values.extend(page["items"])
            next_offset = page["page"]["next_offset"]
            if next_offset is None:
                break
            assert next_offset > offset
            offset = next_offset
        assert len(values) == page["page"]["total_items"]
        if view == "details":
            boundaries = [
                row["value"]
                for row in values
                if row["path"][0] == "resource_boundaries"
            ]
            assert (
                boundaries
                == source["identity_inventory"]["bindings"][17]["resource_boundaries"]
            )
            assert any(
                row["path"] == ["states", "authenticated", "value"]
                and row["value"] is None
                for row in values
            )


def test_long_unicode_detail_paging_and_changed_snapshot_are_explicit() -> None:
    from adminme.workstation_inventory import InventoryChanged, inventory_page

    source = realistic_inventory()
    source["identity_inventory"]["bindings"][0]["recovery"] = "🗂" * 1800
    first = inventory_page(source)
    parts = []
    offset = 0
    while True:
        page = inventory_page(
            source,
            view="details",
            section="identities",
            binding_id="fixture-route-00",
            offset=offset,
            snapshot=first["snapshot"],
        )
        assert (
            len(json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode())
            <= 3800
        )
        parts.extend(row for row in page["items"] if row["path"] == ["recovery"])
        if page["page"]["next_offset"] is None:
            break
        offset = page["page"]["next_offset"]
    assert "".join(row["value"] for row in parts) == "🗂" * 1800
    source["identity_inventory"]["bindings"].pop()
    with pytest.raises(InventoryChanged):
        inventory_page(source, offset=1, snapshot=first["snapshot"])
