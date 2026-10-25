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
    assert catalog["openclaw_plugin"]["host_version"] == "2026.10.7"
    assert catalog["openclaw_plugin"]["host_commit"] == (
        "157d8c2bc3ea2fa183afdc11088e69690bf8e64e"
    )
    assert catalog["openclaw_plugin"]["newer_documentation_is_proof"] is False
    assert "llm-task" in catalog["openclaw_plugin"]["verified_surfaces"]
    ids = [item["capability_id"] for item in catalog["items"]]
    assert len(ids) == len(set(ids))
    assert "planned.apple_messages_observation" in ids
    assert "planned.apple_messages_reply" in ids
    assert all("twilio" not in value for value in ids)
    assert "bridge.messaging-bluebubbles" in ids
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
        "messaging-bluebubbles"
    ]
    messages = by_id["bridge.messaging-bluebubbles"]
    assert messages["implementation_present"] is True
    assert messages["runtime_location"] == "member_bridge"
    assert messages["verbs"] == {
        "read": "supported",
        "observe": "supported",
        "draft": "supported",
        "prepare": "supported",
        "hold": "supported",
        "execute": "ask_every_time",
    }
    assert messages["terminal_result_support"] is True
    assert messages["reconciliation_support"] is True
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
    assert migrated["schema_version"] == 6
    assert migrated["revision"] == 9
    assert migrated["connections"] == original["connections"]
    assert migrated["member_preferences"] == original["member_preferences"]
    bridge = migrated["bridge_requests"]["member-a"]
    assert bridge["member_id"] == "member-a"
    assert bridge["state"] == "requested"
    assert bridge["capabilities"] == ["apple_notes"]
    assert bridge["capability_states"]["apple_notes"]["state"] == "requested"
    assert migrated["bridge_request_revisions"] == {"member-a": 1}
    assert migrated["capability_requests"] == {}
    assert migrated["capability_request_revisions"] == {}
    assert migrated["message_reply_controls"] == {}
    assert migrated["message_reply_control_revisions"] == {}


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


def _apple_catalog_runtime(
    monkeypatch,
    *,
    provider="apple_calendar",
    enabled=True,
    observation=False,
    settled=True,
    permission=True,
    permissions=(),
):
    from types import SimpleNamespace
    import adminme.apple_service_permissions as service
    import adminme.capability_catalog as catalog_module

    catalog = load_catalog(REPO_ROOT)
    catalog["items"] = [
        row
        for row in catalog["items"]
        if row.get("request_key") == provider
        and row["source_kind"] == "bridge_manifest"
    ]
    monkeypatch.setattr(catalog_module, "load_catalog", lambda _root: catalog)
    monkeypatch.setattr(
        catalog_module,
        "_active_authority",
        lambda _runtime, context: (
            [],
            set(permissions)
            if context.auth_member_id == context.view_member_id
            else set(),
        ),
    )
    member = "catalog-owner"
    capability = {
        "state": "settled" if settled else "observing",
        "last_successful_observation": "2035-01-01T00:00:00Z" if settled else None,
    }
    wizard = {"state": "settling", "capability_states": {provider: capability}}
    calls = []

    def options(_runtime, owner, source):
        calls.append((owner, source))
        if not permission:
            raise ValueError("Native permission has not been verified")
        return {"enrollment": {"enrollment_id": "catalog-enrollment"}, "scopes": []}

    def grant(_runtime, owner, source):
        assert owner == member and source == provider
        return (
            {
                "event_id": "catalog-write-receipt",
                "operations": [
                    "create_note" if provider == "apple_notes" else "create_event"
                ],
            }
            if enabled
            else None
        )

    monkeypatch.setattr(service, "permission_options", options)
    monkeypatch.setattr(service, "service_permission", grant)
    runtime = SimpleNamespace(
        settings=SimpleNamespace(repository_root=REPO_ROOT, bridge_enrollments={}),
        settings_control=SimpleNamespace(
            bridge_request_for=lambda owner: wizard if owner == member else None
        ),
        projections=None,
        started=True,
        _observation_active=observation,
        effective_openclaw_state=lambda: "healthy",
    )
    session = SimpleNamespace(auth_member_id=member, view_member_id=member)
    return runtime, session, calls


def test_self_service_catalog_reads_do_not_depend_on_observation_mode(
    monkeypatch,
) -> None:
    from adminme.capability_catalog import compose_member_catalog

    runtime, session, _calls = _apple_catalog_runtime(
        monkeypatch, enabled=False, observation=False, settled=False
    )
    item = compose_member_catalog(runtime, session, settings_state={})["items"][0]
    assert item["account_setup"] == "paired"
    assert item["provider_permission"] == "verified"
    assert item["runtime_health"] == "active"
    assert item["freshness"] == "unknown"  # An empty calendar is not an observed event.
    assert item["effective"]["read_available"] is True
    assert item["effective"]["execute_available"] is False
    assert item["verbs"]["execute"] == "unsupported"
    assert item["bridge_state"]["write_permission"] == "disabled"
    assert item["verification"]["last_verified_result"] is None


def test_self_service_catalog_write_grant_is_separate_from_observation_pause(
    monkeypatch,
) -> None:
    from adminme.capability_catalog import compose_member_catalog

    for provider in ("apple_calendar", "apple_reminders", "apple_notes"):
        for observation in (False, True):
            with monkeypatch.context() as patch:
                runtime, session, _calls = _apple_catalog_runtime(
                    patch, provider=provider, observation=observation
                )
                item = compose_member_catalog(runtime, session, settings_state={})[
                    "items"
                ][0]
                assert item["effective"]["read_available"] is True
                assert item["freshness"] == "unknown"
                assert item["bridge_state"]["last_successful_observation"]
                assert (
                    item["bridge_state"]["freshness_basis"]
                    == "historical_source_observation_only"
                )
                assert item["verbs"]["execute"] == "ask_every_time"
                assert item["effective"]["execute_available"] is (not observation)
                assert (
                    item["administrateme_authority"]["state"]
                    == "owner_write_grant_approval_required"
                )
                assert (
                    item["administrateme_authority"]["provider_permission_event_id"]
                    == "catalog-write-receipt"
                )
                assert item["openclaw"]["approval_required_for_side_effects"] is True
                assert item["verification"]["terminal_result_support"] is True
                assert item["verification"]["last_verified_result"] is None


def test_native_reminder_tools_separate_confirmation_execution_and_readback(
    monkeypatch,
) -> None:
    from adminme.capability_catalog import compose_member_catalog

    runtime, session, _calls = _apple_catalog_runtime(
        monkeypatch,
        provider="apple_reminders",
        permissions=("read_context", "prepare_external_actions"),
    )
    catalog = compose_member_catalog(runtime, session, settings_state={})
    item = catalog["items"][0]
    tools = item["openclaw"]
    assert tools["plugin_declared"] is True
    assert tools["effective_tools"] == sorted(
        [
            "administrateme_confirm_apple_reminder",
            "administrateme_execute_pending_provider_action",
            "administrateme_apple_reminder_status",
        ]
    )
    confirm = tools["native_tool_authority"]["administrateme_confirm_apple_reminder"]
    execute = tools["native_tool_authority"][
        "administrateme_execute_pending_provider_action"
    ]
    status = tools["native_tool_authority"]["administrateme_apple_reminder_status"]
    assert confirm["required_permission"] == "prepare_external_actions"
    assert confirm["stage"] == "domain_confirmation"
    assert confirm["independent_openclaw_approval_required"] is False
    assert execute["independent_openclaw_approval_required"] is True
    assert execute["stage"] == "independent_execution_approval"
    assert status["stage"] == "terminal_readback"
    assert status["provider_write_grant_required"] is False
    assert tools["domain_confirmation_is_execution_approval"] is False
    assert tools["execution_authority_modes"] == [
        "authenticated_current_request_policy",
        "legacy_independent_allow_once",
    ]
    assert tools["policy_receipt_is_human_approval"] is False
    assert tools["historical_pending_auto_resume"] is False
    bridge = item["bridge_state"]
    assert bridge["write_execution"] == "ask_every_time"
    assert (
        bridge["write_execution_basis"]
        == "legacy_grant_mode_not_current_request_decision"
    )
    current = bridge["current_request_authorization"]
    assert current["rule_id"] == "current-human-request.apple-reminder-create.v1"
    assert current["manual_repeat_required"] is False
    assert current["catalog_authorizes_execution"] is False
    assert item["trust_stage"] == "not_established"
    assert item["trust_stage_basis"] == "effective_operating_agreement"
    assert item["consent"]["member_state"] == "complete"
    assert item["required_actions"]["member"] == "none"
    assert item["verification"]["last_verified_result"] is None
    assert item["verification"]["accepted_is_success"] is False
    assert item["verification"]["status_tool"] == "administrateme_apple_reminder_status"
    assert catalog["provider_effect_count"] == 0
    assert catalog["provider_effect_count_scope"] == "this_read_only_catalog_request"


def test_native_reminder_status_survives_revocation_and_observation_mode(
    monkeypatch,
) -> None:
    from adminme.capability_catalog import compose_member_catalog

    for enabled, observation in ((False, False), (True, True)):
        with monkeypatch.context() as patch:
            runtime, session, _calls = _apple_catalog_runtime(
                patch,
                provider="apple_reminders",
                enabled=enabled,
                observation=observation,
                permissions=("read_context", "prepare_external_actions"),
            )
            item = compose_member_catalog(runtime, session, settings_state={})["items"][
                0
            ]
            assert item["openclaw"]["effective_tools"] == [
                "administrateme_apple_reminder_status"
            ]
            assert item["effective"]["execute_available"] is False


def test_native_reminder_tools_do_not_infer_delegation_from_native_permission(
    monkeypatch,
) -> None:
    from adminme.capability_catalog import compose_member_catalog

    for permissions, expected in (
        ((), []),
        (
            ("read_context",),
            [
                "administrateme_apple_reminder_status",
                "administrateme_execute_pending_provider_action",
            ],
        ),
        (("prepare_external_actions",), ["administrateme_confirm_apple_reminder"]),
    ):
        with monkeypatch.context() as patch:
            runtime, session, _calls = _apple_catalog_runtime(
                patch, provider="apple_reminders", permissions=permissions
            )
            item = compose_member_catalog(runtime, session, settings_state={})["items"][
                0
            ]
            assert item["openclaw"]["effective_tools"] == expected
            session.view_member_id = "other-catalog-owner"
            item = compose_member_catalog(runtime, session, settings_state={})["items"][
                0
            ]
            assert item["openclaw"]["effective_tools"] == []


def test_self_service_catalog_unverified_and_view_as_do_not_claim_authority(
    monkeypatch,
) -> None:
    from adminme.capability_catalog import compose_member_catalog

    runtime, session, calls = _apple_catalog_runtime(monkeypatch, permission=False)
    item = compose_member_catalog(runtime, session, settings_state={})["items"][0]
    assert item["effective"]["read_available"] is False
    assert item["effective"]["execute_available"] is False
    calls.clear()
    session.view_member_id = "another-catalog-owner"
    item = compose_member_catalog(runtime, session, settings_state={})["items"][0]
    assert calls == []
    assert item["bridge_state"] is None
    assert item["effective"]["read_available"] is False
    assert item["effective"]["execute_available"] is False
    assert item["administrateme_authority"]["provider_permission_event_id"] is None


def test_cos_reminders_catalog_is_service_owned_read_only_and_local() -> None:
    catalog = load_catalog(REPO_ROOT)
    source = next(
        item
        for item in catalog["items"]
        if item["capability_id"] == "adapter.reminders-cos-apple-eventkit"
    )
    assert source["owner_kinds"] == ["service_principal"]
    assert source["runtime_location"] == "central"
    assert source["implementation_present"] is True
    assert source["verbs"] == {
        "read": "supported",
        "observe": "supported",
        "draft": "unsupported",
        "prepare": "unsupported",
        "hold": "unsupported",
        "execute": "unsupported",
    }
    assert source["prerequisites"] == ["member_consent", "local_permission"]
    assert source["prerequisite_classes"]["partner_service"] == "not_required"
    assert source["terminal_result_support"] is False


def test_cos_owned_reminder_catalog_is_distinct_from_member_permission(
    tmp_path, monkeypatch
):
    from adminme import capability_catalog as catalog_module
    from tests.unit.adapters.reminders.test_cos_reminders_write_policy import (
        _configuration_pair,
    )

    catalog = load_catalog(REPO_ROOT)
    catalog["items"] = [
        row
        for row in catalog["items"]
        if row["capability_id"] == "domain.cos_owned_apple_reminders"
    ]
    runtime, session, _ = _apple_catalog_runtime(
        monkeypatch,
        provider="apple_reminders",
        permissions=("read_context", "prepare_external_actions"),
    )
    monkeypatch.setattr(catalog_module, "load_catalog", lambda _: catalog)
    _, runtime.settings.adapter_config = _configuration_pair(tmp_path)
    composed = catalog_module.compose_member_catalog(
        runtime, session, settings_state={}
    )
    assert composed["summary"]["execute_available"] == 0
    item = composed["items"][0]
    assert item["native_source"]["resource_owner"] == "service-principal:chief-of-staff"
    assert (
        item["native_source"]["current_native_permission"] == "not_inspected_by_catalog"
    )
    assert item["native_source"]["member_bridge_permission_is_authority"] is False
    assert item["effective"]["prepare_available"] is True
    assert item["effective"]["execute_available"] is None
    assert item["administrateme_authority"]["execute_authorized"] is False
    assert (
        item["openclaw"]["current_request_rule"]
        == "current-human-request.cos-reminder-create.v1"
    )
    assert (
        "administrateme_confirm_apple_reminder"
        not in item["openclaw"]["declared_tools"]
    )
    runtime.settings.adapter_config["cos_apple_reminders_writes"]["enabled"] = False
    disabled = catalog_module.compose_member_catalog(
        runtime, session, settings_state={}
    )["items"][0]
    assert disabled["effective"]["execute_available"] is False
    assert disabled["openclaw"]["effective_tools"] == [
        "administrateme_cos_apple_reminder_status"
    ]


def test_requested_email_catalog_separates_confirmation_execution_and_readback() -> (
    None
):
    catalog = load_catalog(REPO_ROOT)
    row = next(
        item
        for item in catalog["items"]
        if item["capability_id"] == "domain.cos_requested_email"
    )
    assert row["openclaw_tools"] == [
        "administrateme_confirm_requested_email",
        "administrateme_cos_response",
        "administrateme_requested_email_status",
    ]
    assert row["verbs"]["execute"] == "current_authenticated_request"
    assert row["terminal_result_support"] is True
    assert row["later_observation_support"] is False
