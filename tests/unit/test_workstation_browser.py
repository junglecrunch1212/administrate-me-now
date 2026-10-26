"""Canonical identity and explicit native route, never ambient browser access."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from adminme.workstation_browser import (
    BrowserPolicy,
    assert_single_extension_profile,
    browser_resource,
    prepare_browser_gateway,
    url_in_scope,
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
    assert prepared["browser"]["profiles"] == {"chrome": {"driver": "extension"}}
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
    for field in ("userDataDir", "cdpUrl", "mcpCommand", "color"):
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
                "authorized_url_prefix": "https://work.example.invalid/",
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
    assert "identity_verifier" not in resolved  # Preserve generic historical digests.
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


def test_browser_source_prefix_keeps_account_origin_and_path_boundaries():
    scope = "https://work.example.invalid/planning"
    assert url_in_scope(scope, scope)
    assert url_in_scope(scope, scope + "/weekly")
    assert not url_in_scope(scope, "https://work.example.invalid/planning-other")
    assert not url_in_scope(scope, "https://other.example.invalid/planning")
    assert not url_in_scope(scope, "http://work.example.invalid/planning")
    with pytest.raises(ValueError, match="denial"):
        prepare_browser_gateway({"plugins": {"deny": ["browser"]}})
    prepared = prepare_browser_gateway({"plugins": {"allow": ["administrateme"]}})
    assert prepared["plugins"]["allow"] == ["administrateme", "browser"]
    assert prepared["plugins"]["entries"]["browser"]["enabled"] is True


def gmail_resource():
    return {
        "resource_id": "own-inbox",
        "inventory_binding_id": "cos-browser",
        "inventory_binding_revision": 1,
        "native_target_id": "real-native-inbox",
        "target_evidence_reference": "operator:verified-inbox-provider-chrome",
        "url": "https://mail.google.com/mail/u/0/#inbox",
        "authorized_url_prefix": "https://mail.google.com/mail/u/0/",
        "identity_verifier": "gmail_self_mailbox_v1",
        "audience_member_ids": ["member-a"],
    }


def test_gmail_verifier_retains_canonical_identity_without_custom_probes():
    from adminme.workstation_sources import identity_matches

    raw = document(datetime.now(timezone.utc))
    binding = raw["bindings"][0]
    binding["expected_identity"]["role"] = "account user"
    binding["observed_identity"]["role"] = "account user"
    inventory = IdentityInventory.model_validate(raw)
    policy = BrowserPolicy.model_validate(
        {"revision": 1, "enabled": True, "resources": [gmail_resource()]}
    )
    resolved = browser_resource(policy, inventory, "own-inbox")
    assert resolved["identity_verifier"] == "gmail_self_mailbox_v1"
    assert resolved["identity_probes"] == {}
    expected = binding["expected_identity"]
    assert resolved["expected_identity"] == expected
    assert identity_matches(
        resolved, {**expected, "account": expected["account"].upper()}
    )
    for wrong in (
        {**expected, "account": "another@fixture.invalid"},
        {**expected, "workspace": "another tenant"},
        {**expected, "role": "delegate"},
        {"account": expected["account"]},
    ):
        assert not identity_matches(resolved, wrong)
    for field in ("workspace", "role"):
        incomplete = inventory.model_copy(deep=True)
        setattr(incomplete.bindings[0].expected_identity, field, None)
        incomplete.bindings[0].observed_identity = incomplete.bindings[
            0
        ].expected_identity
        with pytest.raises(ValueError, match="account-user"):
            browser_resource(policy, incomplete, "own-inbox")
    wrong_role = inventory.model_copy(deep=True)
    wrong_role.bindings[0].expected_identity.role = "administrator"
    wrong_role.bindings[0].observed_identity = wrong_role.bindings[0].expected_identity
    with pytest.raises(ValueError, match="account-user"):
        browser_resource(policy, wrong_role, "own-inbox")


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://mail.google.com/mail/u/0/#sent"},
        {"url": "https://mail.google.com/mail/u/0/#inbox/message"},
        {"url": "https://mail.google.com/mail/u/0/?authuser=0#inbox"},
        {"url": "https://mail.google.com:443/mail/u/0/#inbox"},
        {"url": "https://another.invalid/mail/u/0/#inbox"},
        {"identity_probes": {"role": {"selector": "#fake-role"}}},
        {"identity_verifier": "operator_attested"},
    ],
)
def test_gmail_verifier_rejects_other_routes_or_unreviewed_proof(changes):
    with pytest.raises(ValueError):
        BrowserPolicy.model_validate(
            {
                "revision": 1,
                "enabled": True,
                "resources": [{**gmail_resource(), **changes}],
            }
        )
