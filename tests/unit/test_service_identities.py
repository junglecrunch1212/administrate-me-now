"""Non-human CoS service/channel identity separation and routing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.schemas.service_delegation import ServiceDelegationRequestedV1
from adminme.events.schemas.ingest import MessagingReceivedV5, MessagingSentV5
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    ServiceRequestContext,
    public_service_identity_inventory,
    resolve_service_channel_route,
    validate_service_identity_config,
)


def test_typed_service_context_keeps_actor_operator_member_and_scope_distinct() -> None:
    context = ServiceRequestContext(
        tenant_id="tenant-fixture",
        actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
        operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
        represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        context_scope="member_private",
        context_member_id="member-a",
        on_behalf_of_member_id="member-a",
        authority_grant_id="grant-a",
        resource_owner_principal_id="member-a",
        responsible_party_id=COS_PARTY_ID,
        assignee_party_id=COS_PARTY_ID,
        beneficiary_party_id="member-a",
        approving_member_ids=("member-a",),
        approval_quorum="member_owner",
        permissions=frozenset({"read_context"}),
    )
    assert context.allowed_scopes == {
        f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
        "private:member-a",
    }
    assert "private:member-b" not in context.allowed_scopes
    assert "shared:household" not in context.allowed_scopes

    with pytest.raises(ValueError, match="represented service principal"):
        ServiceRequestContext(
            tenant_id="tenant-fixture",
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
            represented_service_principal_id="service-principal:other",
            context_scope="service_private",
        )
    with pytest.raises(ValueError, match="must not impersonate"):
        ServiceRequestContext(
            tenant_id="tenant-fixture",
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id="member-a",
            represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            context_scope="member_private",
            context_member_id="member-a",
            on_behalf_of_member_id="member-a",
            authority_grant_id="grant-a",
            resource_owner_principal_id="member-a",
            approving_member_ids=("member-a",),
            approval_quorum="member_owner",
        )


def test_unresolved_service_context_has_no_member_private_scope() -> None:
    context = ServiceRequestContext(
        tenant_id="tenant-fixture",
        actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
        operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
        represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        context_scope="unresolved",
    )
    assert context.allowed_scopes == {f"service-private:{COS_SERVICE_PRINCIPAL_ID}"}
    assert not any(scope.startswith("private:") for scope in context.allowed_scopes)


def test_non_read_delegation_cannot_expose_member_private_scope() -> None:
    context = ServiceRequestContext(
        tenant_id="tenant-fixture",
        actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
        operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
        represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        context_scope="member_private",
        context_member_id="member-a",
        on_behalf_of_member_id="member-a",
        authority_grant_id="grant-create-only",
        resource_owner_principal_id="member-a",
        approving_member_ids=("member-a",),
        approval_quorum="member_owner",
        permissions=frozenset({"create_internal_work"}),
    )
    assert context.allowed_scopes == {f"service-private:{COS_SERVICE_PRINCIPAL_ID}"}


def test_external_delegation_schema_requires_the_exact_member_resource_owner() -> None:
    with pytest.raises(ValidationError, match="one member owner"):
        ServiceDelegationRequestedV1.model_validate(
            {
                "grant_id": "grant-external",
                "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "service_party_id": COS_PARTY_ID,
                "context_scope": "external_contact",
                "context_member_id": "member-a",
                "resource_owner_principal_id": "member-b",
                "owner_party_id": "party-external",
                "requested_by_member_id": "member-a",
                "required_approving_member_ids": ["member-a"],
                "approval_quorum": "member_owner",
                "permissions": ["read_context"],
                "requested_at": "2026-08-31T12:00:00Z",
            }
        )


def _configured_channel(*, state: str = "observation") -> dict[str, object]:
    return {
        "principals": [
            {
                "service_principal_id": "service-principal:chief-of-staff",
                "kind": "chief_of_staff",
                "party_id": "service-party:chief-of-staff",
            }
        ],
        "channels": [
            {
                "channel_id": "channel:chief-of-staff:apple-imessage",
                "display_name": "CoS Apple service identity",
                "provider": "apple_imessage",
                "service_principal_id": "service-principal:chief-of-staff",
                "state": state,
                "provider_account_reference": "cos-apple-account:" + "a" * 32,
                "endpoint_reference": "cos-channel-account:" + "b" * 32,
                "enrolled": True,
                "observation_active": True,
                "sending_enabled": False,
                "required_for_activation": False,
                "routes": [
                    {
                        "route_key": "route:" + "1" * 64,
                        "scope": "member_private",
                        "target_id": "member-a",
                    },
                    {
                        "route_key": "route:" + "2" * 64,
                        "scope": "household_shared",
                        "target_id": None,
                    },
                    {
                        "route_key": "route:" + "3" * 64,
                        "scope": "external_contact",
                        "target_id": "party-external-a",
                    },
                ],
            }
        ],
    }


def test_default_cos_apple_identity_is_planned_without_claiming_an_account() -> None:
    principals, channels = validate_service_identity_config(
        None, member_ids={"member-a"}, household_party_ids=set()
    )
    inventory = public_service_identity_inventory(principals, channels)
    assert inventory["state_counts"] == {"planned": 1}
    assert inventory["channels"] == [
        {
            "display_name": "CoS Apple service identity",
            "provider": "apple_imessage",
            "state": "planned",
            "account_reference_present": False,
            "endpoint_reference_present": False,
            "enrolled": False,
            "observation_active": True,
            "sending_enabled": False,
            "required_for_activation": False,
            "route_count": 0,
            "intake_mode": "conversation",
            "content_exposed": False,
        }
    ]
    rendered = str(inventory)
    assert "service-principal" not in rendered
    assert "channel:chief" not in rendered


def test_exact_channel_routes_never_infer_an_unknown_conversation_scope() -> None:
    _, [channel] = validate_service_identity_config(
        _configured_channel(),
        member_ids={"member-a", "member-b"},
        household_party_ids={"party-external-a"},
    )
    assert resolve_service_channel_route(channel, "route:" + "1" * 64) == {
        "state": "resolved",
        "route_scope": "member_private",
        "routing_target_id": "member-a",
        "owner_scope": "private:member-a",
        "visibility_scope": "private:member-a",
        "content_exposed": False,
    }
    assert (
        resolve_service_channel_route(channel, "route:" + "2" * 64)["owner_scope"]
        == "shared:household"
    )
    assert (
        resolve_service_channel_route(channel, "route:" + "3" * 64)["owner_scope"]
        == "external-contact:party-external-a"
    )
    unresolved = resolve_service_channel_route(channel, "route:" + "9" * 64)
    assert unresolved["state"] == "unresolved"
    assert unresolved["visibility_scope"] == "unresolved:service-channel"


def test_service_identity_cannot_collapse_into_a_member_or_overclaim_state() -> None:
    collapsed = _configured_channel()
    principals = collapsed["principals"]
    assert isinstance(principals, list)
    principals[0]["service_principal_id"] = "member-a"
    with pytest.raises(ValueError, match="distinct from every member"):
        validate_service_identity_config(
            collapsed,
            member_ids={"member-a"},
            household_party_ids={"party-external-a"},
        )

    party_collision = _configured_channel()
    principals = party_collision["principals"]
    assert isinstance(principals, list)
    principals[0]["party_id"] = "party-external-a"
    with pytest.raises(ValueError, match="must not reuse an existing Party"):
        validate_service_identity_config(
            party_collision,
            member_ids={"member-a"},
            household_party_ids={"party-external-a"},
        )

    plaintext_reference = _configured_channel()
    channels = plaintext_reference["channels"]
    assert isinstance(channels, list)
    channels[0]["provider_account_reference"] = "cos-apple-account:literal-name"
    with pytest.raises(ValueError, match="must contain a stable hash"):
        validate_service_identity_config(
            plaintext_reference,
            member_ids={"member-a"},
            household_party_ids={"party-external-a"},
        )

    premature_send = _configured_channel(state="healthy")
    channels = premature_send["channels"]
    assert isinstance(channels, list)
    channels[0]["observation_active"] = False
    channels[0]["sending_enabled"] = True
    with pytest.raises(ValueError, match="send command contract is not implemented"):
        validate_service_identity_config(
            premature_send,
            member_ids={"member-a"},
            household_party_ids={"party-external-a"},
        )


def test_service_message_schemas_require_explicit_owner_route_and_sender() -> None:
    received = MessagingReceivedV5.model_validate(
        {
            "source_channel": "service_channel",
            "from_identifier": "provider-account:" + "1" * 32,
            "to_identifier": "cos-channel-account:" + "2" * 32,
            "thread_id": "thread-fixture",
            "subject": None,
            "body_text": None,
            "body_html": None,
            "received_at": "2026-08-26T12:00:00Z",
            "attachments": [],
            "native_message_id": "message-fixture",
            "member_id": None,
            "service_principal_id": "service-principal:chief-of-staff",
            "provider": "bluebubbles",
            "provider_account_id": "cos-apple-account:" + "3" * 32,
            "workspace": "service",
            "routing_scope": "unresolved",
            "routing_target_id": None,
        }
    )
    assert received.routing_scope == "unresolved"
    with pytest.raises(ValidationError, match="exactly one owner"):
        MessagingReceivedV5.model_validate(
            {
                **received.model_dump(),
                "member_id": "member-a",
            }
        )
    with pytest.raises(ValidationError, match="must remain member-private"):
        MessagingReceivedV5.model_validate(
            {
                **received.model_dump(),
                "member_id": "member-a",
                "service_principal_id": None,
                "workspace": "personal",
                "routing_scope": "household_shared",
            }
        )

    sent_payload = {
        "source_channel": "service_channel",
        "to_identifier": "provider-account:" + "4" * 32,
        "thread_id": None,
        "subject": None,
        "body_text": None,
        "sent_at": "2026-08-26T12:01:00Z",
        "delivery_status": "queued",
        "member_id": None,
        "service_principal_id": "service-principal:chief-of-staff",
        "provider": "bluebubbles",
        "provider_account_id": "cos-apple-account:" + "3" * 32,
        "workspace": "service",
        "native_message_id": None,
        "in_reply_to_interaction_id": None,
        "send_as": "service_principal",
        "routing_scope": "household_shared",
        "routing_target_id": None,
    }
    assert MessagingSentV5.model_validate(sent_payload).send_as == "service_principal"
    with pytest.raises(ValidationError, match="service send_as"):
        MessagingSentV5.model_validate(
            {
                **sent_payload,
                "service_principal_id": None,
                "member_id": "member-a",
            }
        )
    with pytest.raises(ValidationError, match="must remain member-private"):
        MessagingSentV5.model_validate(
            {
                **sent_payload,
                "service_principal_id": None,
                "member_id": "member-a",
                "workspace": "personal",
                "send_as": "member",
            }
        )

    planned = _configured_channel(state="planned")
    with pytest.raises(ValueError, match="cannot claim account evidence"):
        validate_service_identity_config(
            planned,
            member_ids={"member-a"},
            household_party_ids={"party-external-a"},
        )
