"""The additive Apple permission never changes read enrollment or old intent."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from adminme.apple_service_permissions import (
    PACKS,
    bind_command_permission,
    command_permission,
    command_proof,
    digest,
    permission_coordinates,
    permission_options,
    service_permission,
    validate_edge_proof,
)
from adminme.bridge_authorization import BRIDGE_PACK_POLICIES, authorization_from_record
from adminme.events.schemas.apple_service_permissions import (
    AppleServicePermissionControlledV1,
)


@pytest.fixture
def rig():
    pack = PACKS["apple_calendar"]
    member, enrollment = "member-fixture", "enrollment-fixture"
    config = {
        "member_id": member,
        "provider_account_id": "provider-account:" + "a" * 32,
        "deployment_target": "bridge",
        "workspace": "personal",
        "binding_provenance": "member-bridge-enrollment",
        "ownership_attested": True,
        "calendar_identifier": "calendar-one",
        "allowed_container_ids": ["calendar-one", "calendar-two"],
        "shared_container_ids": [],
        "permission_scope": sorted(BRIDGE_PACK_POLICIES[pack].permission_scopes),
    }
    record = {
        "status": "active",
        "member_id": member,
        "enrollment_id": enrollment,
        "configured_packs": [pack],
        "enabled_packs": [pack],
        "authorized_packs": [pack],
        "pack_config": {pack: config},
        "pack_capability_states": {pack: "attested"},
        "capability_attestations": {pack: {"event_id": "native-permission"}},
        "provider_capability_state": "attested",
        "device_identity_sha256": "b" * 64,
        "acquisition_policy": "cos-member-source-observation-v1",
        "observation_only": True,
    }
    state = {"apple_service_permissions": {member: {}}}
    wizard = {
        "enrollment_id": enrollment,
        "state": "settling",
        "capability_states": {"apple_calendar": {"state": "observing"}},
    }
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            bridge_enrollments={"fixture.ts.net": record},
            instance=SimpleNamespace(tenant_id="tenant-fixture"),
        ),
        projections=None,
        settings_control=SimpleNamespace(
            read=lambda: state, bridge_request_for=lambda _member: wizard
        ),
    )
    options = permission_options(runtime, member, "apple_calendar")
    coords = permission_coordinates(
        runtime, member, "apple_calendar", scope_ids=[options["scopes"][0]["id"]]
    )
    event = AppleServicePermissionControlledV1(
        member_id=member,
        provider="apple_calendar",
        request_id="grant-request-one",
        action="enable",
        enabled=True,
        revision=1,
        scope_ids=[options["scopes"][0]["id"]],
        coordinates=coords,
        coordinates_sha256=digest(coords),
        changed_by_member_id=member,
        changed_at=datetime.now(timezone.utc).isoformat(),
    ).model_dump()
    state["apple_service_permissions"][member]["apple_calendar"] = {
        **event,
        "event_id": "grant-event-one",
    }
    command = {
        "schema_version": 2,
        "event_id": "command-event-one",
        "command_id": "command-one",
        "member_id": member,
        "provider": "apple_calendar",
        "provider_account_id": config["provider_account_id"],
        "operation": "create_event",
        "payload": {"calendar_id": "calendar-one", "summary": "Permission fixture"},
        "domain_approval_event_id": "domain-one",
        "openclaw_approval_event_id": "native-one",
        "request_fingerprint": "c" * 64,
    }
    edge = SimpleNamespace(
        member_id=member,
        enrollment_id=enrollment,
        pack_config=record["pack_config"],
        enabled_packs=[pack],
    )
    return runtime, record, state, command, edge


def test_enabling_does_not_rewrite_enrollment_or_release_old_command(rig):
    runtime, record, _, command, _ = rig
    before = copy.deepcopy(record)
    assert authorization_from_record(record) is not None
    assert (
        service_permission(runtime, record["member_id"], "apple_calendar") is not None
    )
    with pytest.raises(ValueError, match="new approval"):
        command_permission(runtime, record["member_id"], command)
    bind_command_permission(runtime, record["member_id"], command)
    assert command_permission(runtime, record["member_id"], command)["revision"] == 1
    assert record == before


@pytest.mark.parametrize(
    "change", ["revoke", "revision", "device", "config", "member", "disabled"]
)
def test_revocation_and_stale_bindings_cannot_dispatch(rig, change):
    runtime, record, state, command, _ = rig
    member = record["member_id"]
    bind_command_permission(runtime, member, command)
    stored = state["apple_service_permissions"][member]["apple_calendar"]
    if change == "revoke":
        stored["enabled"] = False
    elif change == "revision":
        stored.update(revision=2, event_id="new-grant")
    elif change == "device":
        record["device_identity_sha256"] = "d" * 64
    elif change == "config":
        record["pack_config"][PACKS["apple_calendar"]]["allowed_container_ids"].append(
            "new-calendar"
        )
    elif change == "member":
        command["member_id"] = "other-member"
    else:
        record["enabled_packs"] = []
    with pytest.raises(ValueError):
        command_permission(runtime, member, command)


def test_scope_cannot_widen_or_conflict_in_aliases(rig):
    runtime, record, _, command, _ = rig
    for payload in (
        {"calendar_id": "calendar-two"},
        {"calendar_id": "calendar-one", "calendar_identifier": "calendar-two"},
        {"calendar_id": "calendar-one", "destination_calendar_id": "calendar-two"},
    ):
        candidate = copy.deepcopy(command)
        candidate["payload"] = payload
        with pytest.raises(ValueError):
            bind_command_permission(runtime, record["member_id"], candidate)


def test_exact_short_lived_edge_proof_and_independent_approval_fields(rig):
    runtime, record, _, command, edge = rig
    bind_command_permission(runtime, record["member_id"], command)
    now = datetime.now(timezone.utc)
    command["apple_service_authorization"] = command_proof(
        runtime, record["member_id"], command, now=now
    )
    assert (
        validate_edge_proof(edge, command, device_identity_sha256="b" * 64, now=now)[
            "revision"
        ]
        == 1
    )
    with pytest.raises(ValueError):
        validate_edge_proof(edge, command, device_identity_sha256="d" * 64, now=now)
    with pytest.raises(ValueError):
        validate_edge_proof(
            edge,
            command,
            device_identity_sha256="b" * 64,
            now=now + timedelta(seconds=16),
        )
    altered_grant = copy.deepcopy(command)
    altered_grant["apple_service_authorization"]["grant"][
        "allowed_container_ids"
    ].append("unselected")
    with pytest.raises(ValueError, match="coordinates changed"):
        validate_edge_proof(
            edge, altered_grant, device_identity_sha256="b" * 64, now=now
        )
    for key in (
        "domain_approval_event_id",
        "openclaw_approval_event_id",
        "request_fingerprint",
    ):
        changed = copy.deepcopy(command)
        changed[key] = "different"
        with pytest.raises(ValueError):
            validate_edge_proof(edge, changed, device_identity_sha256="b" * 64, now=now)


def test_legacy_calendar_command_keeps_existing_governance(rig):
    runtime, record, state, command, _ = rig
    record.pop("acquisition_policy")
    record.pop("observation_only")
    state["apple_service_permissions"] = {}
    bind_command_permission(runtime, record["member_id"], command)
    assert "apple_service_permission" not in command["payload"]


def test_grant_schema_rejects_cross_owner_and_false_digest(rig):
    _, record, state, _, _ = rig
    raw = dict(
        state["apple_service_permissions"][record["member_id"]]["apple_calendar"]
    )
    raw.pop("event_id")
    for changes in (
        {"changed_by_member_id": "other-member"},
        {"coordinates_sha256": "f" * 64},
        {"enabled": False},
    ):
        with pytest.raises(ValueError):
            AppleServicePermissionControlledV1.model_validate({**raw, **changes})
