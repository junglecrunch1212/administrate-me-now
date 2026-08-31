"""Exact-coordinate, migration, and routing proofs for source policy V2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adminme.settings_control import SettingsControlStore
from adminme.source_policy import (
    SignalAssessment,
    SourceCoordinate,
    SourcePolicyV2,
    assess_source_signals,
    policy_for_preset,
    route_for_policy,
    source_coordinate_for_event,
    source_policy_event_types,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _coordinate(
    *,
    owner: str = "member-a",
    account: str = "provider-account-a",
    workspace: str = "personal",
    adapter: str = "gmail-instance-a",
) -> SourceCoordinate:
    return SourceCoordinate(
        tenant_id="tenant-fixture",
        owner_principal_id=owner,
        owner_kind="member",
        context_member_id=owner,
        adapter_category="communication",
        adapter_kind="messaging-gmail-api",
        adapter_instance_id=adapter,
        provider="gmail",
        provider_account_id=account,
        workspace=workspace,
        source_kind="adapter:gmail",
        source_class="passive_communication",
        trusted_route=f"member_private:{owner}",
    )


def test_policy_key_distinguishes_principal_account_workspace_and_instance() -> None:
    baseline = _coordinate()
    assert (
        len(
            {
                baseline.key,
                _coordinate(owner="member-b").key,
                _coordinate(account="provider-account-b").key,
                _coordinate(workspace="work").key,
                _coordinate(adapter="gmail-instance-b").key,
            }
        )
        == 5
    )


def test_service_owned_source_needs_no_fake_member() -> None:
    coordinate = source_coordinate_for_event(
        {
            "tenant_id": "tenant-fixture",
            "type": "messaging.received",
            "source_adapter": "service-mail-instance",
            "source_account_id": "service-provider-account",
            "owner_scope": "service-private:service-principal:cos",
            "payload": {
                "service_principal_id": "service-principal:cos",
                "provider": "gmail",
                "provider_account_id": "service-provider-account",
                "workspace": "service",
                "routing_scope": "household_shared",
            },
        }
    )
    assert coordinate.owner_kind == "service_principal"
    assert coordinate.owner_principal_id == "service-principal:cos"
    assert coordinate.context_member_id is None
    assert coordinate.trusted_route == "household_shared"


def test_private_envelope_cannot_be_reassigned_by_payload() -> None:
    with pytest.raises(ValueError, match="contradicts its private envelope owner"):
        source_coordinate_for_event(
            {
                "tenant_id": "tenant-fixture",
                "type": "messaging.received",
                "source_adapter": "gmail-instance-a",
                "source_account_id": "provider-account-a",
                "owner_scope": "private:member-a",
                "payload": {
                    "member_id": "member-b",
                    "provider": "gmail",
                    "provider_account_id": "provider-account-a",
                    "workspace": "personal",
                },
            }
        )


def test_context_only_and_ignore_keep_planes_independent() -> None:
    coordinate = _coordinate()
    signals = SignalAssessment(direction="inbound")
    context_only = SourcePolicyV2(
        preset="custom",
        triage_mode="context_only",
        alert_mode="none",
        context_mode="typed_facts",
        candidate_kinds=["task"],
    )
    context_route = route_for_policy(coordinate, context_only, signals)
    assert context_route.triage == "none"
    assert context_route.alert == "none"
    assert context_route.context == "typed_facts"
    assert "policy_context_only_without_triage" in context_route.reason_codes

    ignored = context_only.model_copy(
        update={"triage_mode": "ignore", "context_mode": "typed_facts_safe_snippet"}
    )
    ignore_route = route_for_policy(coordinate, ignored, signals)
    assert ignore_route.triage == "none"
    assert ignore_route.alert == "none"
    assert ignore_route.context == "none"
    assert coordinate.key in ignore_route.reason_codes[0]


def test_direct_authenticated_intent_cannot_be_suppressed() -> None:
    coordinate = SourceCoordinate(
        tenant_id="tenant-fixture",
        owner_principal_id="member-a",
        owner_kind="member",
        context_member_id="member-a",
        adapter_category="member_surface",
        adapter_kind="member-surface",
        adapter_instance_id="surface:triage",
        provider="administrateme",
        provider_account_id="member-a",
        workspace="private_portal",
        source_kind="surface:triage",
        source_class="direct_authenticated_intent",
        trusted_route="member_private:member-a",
    )
    suppressive = SourcePolicyV2(
        preset="custom",
        acquisition_state="paused",
        triage_mode="ignore",
        alert_mode="none",
        context_mode="none",
        candidate_kinds=["task"],
    )
    route = route_for_policy(
        coordinate,
        suppressive,
        SignalAssessment(
            authenticated_member_instruction=True,
            direction="member_authored",
        ),
    )
    assert route.triage == "direct_work_order_proposal"
    assert "authenticated_member_intent_cannot_be_suppressed" in route.reason_codes


def test_provider_labels_urgency_and_llm_claims_are_non_authoritative() -> None:
    event = {
        "type": "messaging.received",
        "recorded_at": "2026-08-31T12:00:00Z",
        "payload": {
            "provider_labels": ["IMPORTANT", "STARRED"],
            "provider_urgency": "urgent",
            "llm_extracted_claims": ["security alert"],
        },
    }
    signals = assess_source_signals(event)
    route = route_for_policy(_coordinate(), policy_for_preset("balanced"), signals)
    assert signals.provider_labels_non_authoritative == ["IMPORTANT", "STARRED"]
    assert signals.provider_or_sender_urgency_non_authoritative == "urgent"
    assert signals.llm_extracted_claims_non_authoritative == ["security alert"]
    assert signals.security_alert is False
    assert route.alert == "none"
    assert route.triage == "review"


def test_provider_execution_is_not_a_source_policy_plane() -> None:
    event_types = source_policy_event_types(REPO_ROOT)
    assert "messaging.received" in event_types
    assert "triage.intent.submitted" in event_types
    assert "action.executed" not in event_types
    assert "action.failed" not in event_types


def test_v1_migration_preserves_live_behavior_without_account_wide_copy(
    tmp_path,
) -> None:
    path = tmp_path / "settings-control.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "revision": 4,
                "connections": {
                    "gmail-instance-a": {
                        "state": "paused",
                        "changed_by_member_id": "member-a",
                        "changed_at": "2026-08-31T12:00:00Z",
                    }
                },
                "member_preferences": {},
                "bridge_requests": {},
                "candidate_policies": {
                    "member-a": {
                        "adapter:gmail": {
                            "mode": "quiet",
                            "language_enabled": False,
                            "candidate_kinds": ["task"],
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = SettingsControlStore(path)
    assert store.connection_lifecycle("gmail-instance-a") == "paused"
    assert store.effective_candidate_policy("member-a", "adapter:gmail")["mode"] == (
        "quiet"
    )
    first = _coordinate(account="provider-account-a")
    second = _coordinate(account="provider-account-b", adapter="gmail-instance-b")
    first_effective = store.effective_source_policy_v2("member-a", first)
    second_effective = store.effective_source_policy_v2("member-a", second)
    assert first_effective["origin"] == second_effective["origin"] == "migration_needed"
    assert first_effective["policy"]["triage_mode"] == "review"
    assert second_effective["policy"]["triage_mode"] == "review"
    assert first_effective["migration"]["adapter_wide_expansion"] is False

    revision = store.prepare_source_policy_v2_change(
        "member-a", first, expected_revision=0
    )
    store.apply_source_policy_v2_event(
        {
            "owner_principal_id": "member-a",
            "policy_key": first.key,
            "coordinate": first.model_dump(mode="json"),
            "action": "set",
            "policy": policy_for_preset("focused").model_dump(mode="json"),
            "revision": revision,
            "changed_by_principal_id": "member-a",
            "changed_at": "2026-08-31T12:00:00Z",
        }
    )
    assert store.effective_source_policy_v2("member-a", first)["origin"] == (
        "exact_override"
    )
    assert store.effective_source_policy_v2("member-a", second)["origin"] == (
        "migration_needed"
    )


class _FakeLog:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = payloads

    async def read_since(self, **_: object):
        for payload in self.payloads:
            yield {"payload": payload}


async def test_replay_rebuilds_policy_and_reset_from_events(tmp_path) -> None:
    coordinate = _coordinate()
    common = {
        "owner_principal_id": "member-a",
        "policy_key": coordinate.key,
        "coordinate": coordinate.model_dump(mode="json"),
        "changed_by_principal_id": "member-a",
        "changed_at": "2026-08-31T12:00:00Z",
    }
    log = _FakeLog(
        [
            {
                **common,
                "action": "set",
                "policy": policy_for_preset("focused").model_dump(mode="json"),
                "revision": 1,
            },
            {**common, "action": "reset", "policy": None, "revision": 2},
        ]
    )
    store = SettingsControlStore(tmp_path / "settings-control.json")
    assert await store.replay_source_policy_v2_events(log) == 2
    effective = store.effective_source_policy_v2("member-a", coordinate)
    assert effective["origin"] == "recommended_default"
    assert effective["revision"] == 2
