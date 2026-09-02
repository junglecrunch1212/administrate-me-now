"""Focused contracts for the G08 capability catalog and Settings migration."""

from __future__ import annotations

import json
from pathlib import Path

from adminme.capability_catalog import CATALOG_VERSION, VERBS, load_catalog
from adminme.settings_control import SettingsControlStore


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_catalog_is_manifest_derived_versioned_and_non_speculative() -> None:
    catalog = load_catalog(REPO_ROOT)
    assert catalog["catalog_version"] == CATALOG_VERSION
    assert catalog["tenant_neutral"] is True
    assert catalog["openclaw_plugin"]["host_version"] == "2026.7.2"
    assert catalog["openclaw_plugin"]["host_commit"] == (
        "ce67ffb70e83e5ebb56ea02e814e577153f84f61"
    )
    assert catalog["openclaw_plugin"]["newer_documentation_is_proof"] is False
    assert "llm-task" in catalog["openclaw_plugin"]["verified_surfaces"]
    ids = [item["capability_id"] for item in catalog["items"]]
    assert len(ids) == len(set(ids))
    assert "planned.apple_messages_observation" in ids
    assert "planned.apple_messages_reply" in ids
    assert all("twilio" not in value for value in ids)
    assert all("bluebubbles" not in value for value in ids)
    assert all("microsoft-graph-messaging" not in value for value in ids)
    by_id = {item["capability_id"]: item for item in catalog["items"]}
    assert by_id["adapter.financial-plaid"]["implementation_present"] is True
    assert by_id["adapter.financial-plaid"]["verbs"]["read"] == "supported"
    assert by_id["adapter.reminders-apple-eventkit"]["runtime_location"] == (
        "member_bridge"
    )
    assert by_id["bridge.reminders-apple-eventkit-bridge"]["runtime_location"] == (
        "member_bridge"
    )
    assert by_id["planned.apple_messages_observation"]["provider_options"] == [
        "apple_messages_member_bridge"
    ]
    for item in catalog["items"]:
        assert item["catalog_version"] == CATALOG_VERSION
        assert item["outcome"]
        assert item["owner_kinds"]
        assert set(item["verbs"]) == set(VERBS)
        assert isinstance(item["implementation_present"], bool)
        assert isinstance(item["prerequisites"], list)
        assert set(item["prerequisite_classes"]) == {
            "local_helper",
            "browser",
            "partner_service",
            "commercial_service",
            "human",
        }
        assert isinstance(item["terminal_result_support"], bool)
        assert isinstance(item["later_observation_support"], bool)
        assert isinstance(item["reconciliation_support"], bool)
        if item["source_kind"] == "domain_product":
            assert (REPO_ROOT / item["implementation_path"]).is_file()


def test_settings_v3_migration_preserves_existing_state_without_authority(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings-control.json"
    original = {
        "schema_version": 3,
        "revision": 9,
        "connections": {"adapter-a": {"state": "paused", "revision": 2}},
        "member_preferences": {"member-a": {"information_density": 2}},
        "bridge_requests": {
            "member-a": {"state": "requested", "capabilities": ["apple_notes"]}
        },
        "candidate_policies": {},
        "source_policies_v2": {},
        "source_policy_v2_revisions": {},
        "source_policy_v2_migrations": {},
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    store = SettingsControlStore(path)
    migrated = store.read()
    assert migrated["schema_version"] == 4
    assert migrated["revision"] == 9
    assert migrated["connections"] == original["connections"]
    assert migrated["member_preferences"] == original["member_preferences"]
    assert migrated["bridge_requests"] == original["bridge_requests"]
    assert migrated["capability_requests"] == {}
    assert migrated["capability_request_revisions"] == {}


def test_capability_request_cache_is_revisioned_and_inert(tmp_path: Path) -> None:
    store = SettingsControlStore(tmp_path / "settings-control.json")
    payload = {
        "member_id": "member-a",
        "capability_id": "planned.apple_messages_observation",
        "catalog_version": CATALOG_VERSION,
        "desired_outcome": "Observe an authorized message source.",
        "requested_scope": "member_private",
        "provider_option": None,
        "state": "requested",
        "revision": 1,
        "changed_by_member_id": "member-a",
        "changed_at": "2035-01-01T00:00:00Z",
        "installs_software": False,
        "pairs_bridge": False,
        "enrolls_account": False,
        "changes_openclaw_configuration": False,
        "accesses_credentials": False,
        "grants_authority": False,
        "provider_effects": False,
    }
    mutation = store.apply_capability_request_event(payload)
    assert mutation.revision == 1
    assert mutation.current["state"] == "requested"
    assert mutation.current["installs_software"] is False
    assert mutation.current["grants_authority"] is False
    assert mutation.current["provider_effects"] is False
