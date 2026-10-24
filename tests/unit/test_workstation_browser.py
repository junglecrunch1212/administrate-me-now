"""Canonical identity and explicit native route, never ambient browser access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from adminme.workstation_browser import (
    BrowserPolicy,
    assert_single_extension_profile,
    browser_resource,
    prepare_browser_gateway,
)
from adminme.workstation_inventory import IdentityInventory
from tests.unit.test_workstation_inventory import document


def test_native_pairing_preparation_preserves_tools_and_rejects_ambiguous_profile():
    original = {
        "agents": {"list": [{"id": "service", "tools": {"allow": ["read"]}}]},
        "gateway": {
            "nodes": {"browser": {"mode": "manual", "node": "another-workstation"}}
        },
    }
    prepared = prepare_browser_gateway(original)
    assert original.get("browser") is None
    assert prepared["agents"] == original["agents"]
    assert prepared["browser"]["allowSystemProfileImport"] is False
    assert prepared["gateway"] == original["gateway"]
    assert_single_extension_profile(prepared)
    for alternate in ({"driver": "extension"}, {"driver": "existing-session"}):
        ambiguous = deepcopy(prepared)
        if alternate["driver"] == "extension":
            ambiguous["browser"]["profiles"]["other"] = alternate
        else:
            ambiguous["browser"]["profiles"]["chrome"] = alternate
        with pytest.raises(ValueError):
            prepare_browser_gateway(ambiguous)
    for field in ("userDataDir", "cdpUrl", "mcpCommand"):
        changed = deepcopy(prepared)
        changed["browser"]["profiles"]["chrome"][field] = "unapproved-session"
        with pytest.raises(ValueError):
            assert_single_extension_profile(changed)


def test_browser_resource_reuses_canonical_identity_and_requires_all_site_checks():
    inventory = IdentityInventory.model_validate(document(datetime.now(timezone.utc)))
    raw = {
        "revision": 1,
        "enabled": True,
        "resources": [
            {
                "resource_id": "own-page",
                "inventory_binding_id": "cos-browser",
                "inventory_binding_revision": 1,
                "native_target_id": "real-native-tab",
                "target_evidence_reference": "operator-observation:verified-profile-tab",
                "url": "https://work.example.invalid/planning",
                "identity_probes": {
                    key: {"selector": f"#{key}"}
                    for key in ("account", "workspace", "role")
                },
                "audience_member_ids": ["member-a", "member-b"],
            }
        ],
    }
    policy = BrowserPolicy.model_validate(raw)
    resolved = browser_resource(policy, inventory, "own-page")
    assert (
        resolved["expected_identity"]
        == inventory.bindings[0].expected_identity.model_dump()
    )
    assert resolved["binding_revision"] == 1
    assert resolved["audience_member_ids"] == ["member-a", "member-b"]
    del raw["resources"][0]["identity_probes"]["role"]
    with pytest.raises(ValueError, match="probe"):
        browser_resource(BrowserPolicy.model_validate(raw), inventory, "own-page")
    member = inventory.model_copy(deep=True)
    member.bindings[0].operating_principal_id = "member-a"
    with pytest.raises(ValueError, match="CoS"):
        browser_resource(policy, member, "own-page")

    changed = inventory.model_copy(deep=True)
    changed.bindings[0].revision += 1
    with pytest.raises(ValueError, match="current verified"):
        browser_resource(policy, changed, "own-page")
    with pytest.raises(ValueError, match="current verified"):
        browser_resource(
            policy,
            inventory,
            "own-page",
            now=datetime.now(timezone.utc) + timedelta(days=30),
        )
