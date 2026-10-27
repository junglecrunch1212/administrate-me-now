"""Service-self identity, scope and event boundaries remain independent of members."""

from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest
from pydantic import ValidationError

from adminme.events.schemas.domain import (
    CoSWorkStateChangedV1,
    CoSWorkStateChangedV2,
    ServiceSourceAuthority,
    TaskCreatedV3,
    TaskCreatedV4,
    TaskCreatedV5,
    TaskCreatedV6,
)
from adminme.events.registry import registry
from adminme.lib.principal import PrincipalIdentity, ServiceSession
from adminme.lib.scope import allowed_read, filter_rows, privacy_filter
from adminme.lib.session import Session
from adminme.service_identities import (
    COS_AUTOMATION_OPERATOR_ID,
    COS_PARTY_ID,
    COS_SERVICE_PRINCIPAL_ID,
    ServiceRequestContext,
    service_private_scope,
)


def _session() -> ServiceSession:
    return ServiceSession(
        tenant_id="tenant-fixture",
        principal=PrincipalIdentity(
            kind="service_principal",
            principal_id=COS_SERVICE_PRINCIPAL_ID,
            party_id=COS_PARTY_ID,
        ),
        service_context=ServiceRequestContext(
            tenant_id="tenant-fixture",
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
            represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            context_scope="service_private",
            permissions=frozenset(
                {"read_context", "create_internal_work", "manage_internal_work"}
            ),
        ),
    )


def _created() -> dict[str, Any]:
    return {
        "task_id": "cos-task:fixture-self-work",
        "title": "Inspect an internal reconciliation result",
        "work_kind": "service_operational",
        "owner_party_id": COS_PARTY_ID,
        "responsible_party_id": COS_PARTY_ID,
        "assignee_party_id": COS_PARTY_ID,
        "created_by": COS_SERVICE_PRINCIPAL_ID,
        "service_assignment_role": "internal",
        "service_party_id": COS_PARTY_ID,
        "authority_kind": "service_self",
        "authority_grant_id": None,
        "service_work_type": "reconciliation",
        "source_invocation_id": "fixture-invocation",
    }


def _transition() -> dict[str, Any]:
    return {
        "task_id": "cos-task:fixture-self-work",
        "action": "complete",
        "resulting_status": "done",
        "changed_at": "2030-01-01T12:00:00Z",
        "authority_kind": "service_self",
        "authority_grant_id": None,
        "actor_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "actor_service_party_id": COS_PARTY_ID,
        "operator_principal_id": COS_AUTOMATION_OPERATOR_ID,
        "reason_code": "completed",
        "expected_last_event_id": "fixture-created-event",
    }


def test_service_session_has_canonical_identity_without_member_aliases() -> None:
    session = _session()
    assert session.event_actor_identity == COS_SERVICE_PRINCIPAL_ID
    assert session.operator_principal_id == COS_AUTOMATION_OPERATOR_ID
    assert session.allowed_scopes == {service_private_scope(COS_SERVICE_PRINCIPAL_ID)}
    assert not hasattr(session, "auth_member_id")
    assert not hasattr(session, "view_member_id")
    assert not hasattr(session, "auth_role")
    with pytest.raises(FrozenInstanceError):
        setattr(session, "tenant_id", "other-tenant")
    with pytest.raises(FrozenInstanceError):
        setattr(session.principal, "principal_id", "member-fixture")
    for kind in ("human_member", "system_device"):
        with pytest.raises(ValueError, match="impersonate"):
            PrincipalIdentity(kind=kind, principal_id=COS_SERVICE_PRINCIPAL_ID)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("permissions", frozenset({"read_context", "prepare_external_actions"})),
        ("resource_owner_principal_id", "member-fixture"),
        ("owner_party_id", "party:other-service"),
        ("beneficiary_party_id", "member-fixture"),
        ("provider_account_owner_principal_id", COS_SERVICE_PRINCIPAL_ID),
        ("audience", ("member-fixture",)),
        ("visibility", ("shared:household",)),
        ("approving_member_ids", ("member-fixture",)),
    ],
)
def test_service_session_cannot_gain_context_from_unvalidated_axes(
    field: str, value: Any
) -> None:
    session = _session()
    with pytest.raises(ValueError):
        replace(
            session, service_context=replace(session.service_context, **{field: value})
        )
    with pytest.raises(ValueError, match="tenant"):
        replace(session, tenant_id="other-tenant")


def test_service_private_projection_filter_has_no_member_or_shared_fallback() -> None:
    session = _session()
    own = {
        "tenant_id": session.tenant_id,
        "owner_scope": session.private_scope,
        "visibility_scope": session.private_scope,
        "sensitivity": "privileged",
        "title": "Service-private fixture",
    }
    other_scopes = [
        "private:member-a",
        "private:member-b",
        "shared:household",
        "org:fixture",
        service_private_scope("service-principal:other"),
    ]
    canaries = [
        {**own, "owner_scope": scope, "visibility_scope": scope}
        for scope in other_scopes
    ]
    canaries.extend(
        [
            {**own, "tenant_id": "other-tenant"},
            {**own, "visibility_scope": "shared:household"},
            {**own, "owner_scope": "private:member-a"},
            {"tenant_id": session.tenant_id, "title": "Unscoped structural row"},
        ]
    )
    assert filter_rows(session, [own, *canaries]) == [own]
    assert privacy_filter(session, own) == own
    assert allowed_read(session, "privileged", session.private_scope)
    assert not allowed_read(session, "unknown", session.private_scope)

    for member in ("member-a", "member-b"):
        delegated = ServiceRequestContext(
            tenant_id=session.tenant_id,
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id=COS_AUTOMATION_OPERATOR_ID,
            represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            context_scope="member_private",
            context_member_id=member,
            on_behalf_of_member_id=member,
            authority_grant_id=f"grant:{member}",
            resource_owner_principal_id=member,
            approving_member_ids=(member,),
            approval_quorum="member_owner",
            permissions=frozenset({"read_context"}),
        )
        member_session = Session(
            tenant_id=session.tenant_id,
            auth_member_id=member,
            auth_role="principal",
            view_member_id=member,
            view_role="principal",
            dm_scope="per_account_channel_peer",
            source="openclaw_plugin",
            service_context=delegated,
        )
        assert filter_rows(member_session, [{**own, "sensitivity": "normal"}]) == []


def test_new_service_self_events_preserve_identity_and_no_effect_semantics() -> None:
    created = TaskCreatedV4.model_validate(_created())
    assert created.owner_member_id is None
    assert created.owner_party_id == created.assignee_party_id == COS_PARTY_ID
    assert created.created_by == COS_SERVICE_PRINCIPAL_ID
    assert created.authority_grant_id is None
    assert created.provider_effect_count == 0
    completed = CoSWorkStateChangedV2.model_validate(_transition())
    assert completed.actor_service_principal_id == COS_SERVICE_PRINCIPAL_ID
    assert completed.active_member_id is None
    assert completed.on_behalf_of_member_id is None
    assert completed.authority_grant_id is None
    assert completed.provider_effect_count == 0
    follow_up = CoSWorkStateChangedV2.model_validate(
        {
            **_transition(),
            "action": "follow_up",
            "resulting_status": "deferred",
            "reason_code": "scheduled_hold",
            "follow_up_at": "2030-01-02T12:00:00Z",
        }
    )
    assert follow_up.follow_up_at is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("authority_kind", "member_owner"),
        ("authority_grant_id", "grant:member"),
        ("owner_member_id", "member-fixture"),
        ("owner_party_id", "party:other"),
        ("assignee_party_id", "member-fixture"),
        ("created_by", "member-fixture"),
        ("beneficiary_party_id", "member-fixture"),
        ("on_behalf_of_member_id", "member-fixture"),
        ("delegated_by_member_id", "member-fixture"),
        ("service_assignment_role", "delegatee"),
        ("source_commitment_id", "member-commitment"),
        ("linked_item_id", "member-task"),
        ("source_event_id", "member-event"),
        ("coordination_id", "shared-coordination"),
        ("recurring_id", "scheduler-job"),
        ("provider_id", "google_tasks"),
        ("provider_account_reference", "provider-account:fixture"),
        ("provider_representation_owner_kind", "service"),
        ("provider_effect_count", 1),
        ("waiting_on_party_id", "member-fixture"),
    ],
)
def test_self_owned_creation_rejects_other_authority_axes(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError):
        TaskCreatedV4.model_validate({**_created(), field: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"authority_grant_id": "grant:member"},
        {"actor_service_principal_id": "member-fixture"},
        {"operator_principal_id": "member-fixture"},
        {"actor_service_party_id": "party:other"},
        {"active_member_id": "member-fixture"},
        {"on_behalf_of_member_id": "member-fixture"},
        {"provider_effect_count": 1},
        {
            "action": "wait_on_member",
            "resulting_status": "waiting_on",
            "waiting_on_member_id": "member-fixture",
        },
        {
            "action": "wait_on_other",
            "resulting_status": "waiting_on",
            "waiting_on_party_id": "party:external",
        },
        {"action": "complete", "resulting_status": "next"},
        {
            "action": "follow_up",
            "resulting_status": "deferred",
            "follow_up_at": "2029-12-31T12:00:00Z",
        },
    ],
)
def test_service_self_lifecycle_rejects_authority_and_state_contradictions(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        CoSWorkStateChangedV2.model_validate({**_transition(), **changes})


@pytest.mark.parametrize(
    "field",
    [
        "work_kind",
        "owner_party_id",
        "responsible_party_id",
        "assignee_party_id",
        "created_by",
        "service_party_id",
        "service_assignment_role",
        "authority_kind",
        "authority_grant_id",
    ],
)
def test_raw_creation_requires_projection_identity_fields(field: str) -> None:
    payload = _created()
    payload.pop(field)
    with pytest.raises(ValidationError):
        TaskCreatedV4.model_validate(payload)


@pytest.mark.parametrize(
    "field",
    [
        "actor_service_principal_id",
        "actor_service_party_id",
        "operator_principal_id",
        "authority_kind",
        "authority_grant_id",
    ],
)
def test_raw_transition_requires_projection_identity_fields(field: str) -> None:
    payload = _transition()
    payload.pop(field)
    with pytest.raises(ValidationError):
        CoSWorkStateChangedV2.model_validate(payload)


def test_old_event_versions_still_require_delegation_grants() -> None:
    created = TaskCreatedV4.model_validate(_created()).model_dump(mode="json")
    created.pop("authority_kind")
    created.pop("record_kind")
    created.pop("source_authorities")
    with pytest.raises(ValidationError):
        TaskCreatedV3.model_validate(created)
    created["authority_grant_id"] = "grant:fixture-member"
    assert (
        TaskCreatedV3.model_validate(created).authority_grant_id
        == "grant:fixture-member"
    )
    transition = CoSWorkStateChangedV2.model_validate(_transition()).model_dump(
        mode="json"
    )
    transition.pop("authority_kind")
    with pytest.raises(ValidationError):
        CoSWorkStateChangedV1.model_validate(transition)
    transition["authority_grant_id"] = "grant:fixture-member"
    assert (
        CoSWorkStateChangedV1.model_validate(transition).authority_grant_id
        == "grant:fixture-member"
    )


def _source_authority() -> dict[str, Any]:
    return {
        "source_event_id": "member-canonical-source-event",
        "authority_grant_id": "grant:source-member",
        "context_scope": "member_private",
        "context_member_id": "member-source-fixture",
        "source_owner_scope": "private:member-source-fixture",
        "source_visibility_scope": "private:member-source-fixture",
        "source_sensitivity": "privileged",
        "source_adapter": "fixture-canonical-product",
        "source_account_id": "fixture-account",
        "source_policy_key": "spv2_" + "a" * 64,
        "source_policy_owner_id": "member-source-fixture",
        "source_audience": ["member-source-fixture"],
        "restriction": "internal_processing_only",
    }


@pytest.mark.parametrize("record_kind", ["work", "note", "scratchpad"])
def test_internal_records_retain_client_source_authority(record_kind: str) -> None:
    payload = {
        **_created(),
        "record_kind": record_kind,
        "source_authorities": [_source_authority()],
    }
    record = TaskCreatedV4.model_validate(payload)
    assert record.record_kind == record_kind
    source = record.source_authorities[0]
    assert source.authority_grant_id == "grant:source-member"
    assert source.source_sensitivity == "privileged"
    assert source.source_audience == ["member-source-fixture"]
    assert source.restriction == "internal_processing_only"
    assert record.authority_grant_id is None
    assert record.owner_member_id is None
    assert record.owner_party_id == COS_PARTY_ID
    assert record.provider_effect_count == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"authority_grant_id": None},
        {"context_member_id": None},
        {"owner_party_id": "unrelated-external-party"},
        {"source_owner_scope": "private:other-member"},
        {"source_visibility_scope": "shared:household"},
        {"restriction": "unrestricted"},
        {"source_audience": ["member-fixture", "member-fixture"]},
        {"source_audience": [""]},
        {"context_scope": "service_private"},
    ],
)
def test_source_authority_rejects_lost_or_conflicting_restrictions(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        ServiceSourceAuthority.model_validate({**_source_authority(), **changes})


def test_source_authorities_are_bounded_and_cannot_duplicate_a_fact() -> None:
    with pytest.raises(ValidationError, match="distinct source"):
        TaskCreatedV4.model_validate(
            {
                **_created(),
                "source_authorities": [_source_authority(), _source_authority()],
            }
        )
    with pytest.raises(ValidationError):
        TaskCreatedV4.model_validate(
            {
                **_created(),
                "source_authorities": [
                    {**_source_authority(), "source_event_id": f"source-{index}"}
                    for index in range(21)
                ],
            }
        )


def test_task_created_v6_only_expands_the_source_authority_bound() -> None:
    twenty = [
        {**_source_authority(), "source_event_id": f"source-{index}"}
        for index in range(20)
    ]
    v5 = TaskCreatedV5.model_validate({**_created(), "source_authorities": twenty})
    v6 = TaskCreatedV6.model_validate({**_created(), "source_authorities": twenty})
    assert v6.model_dump(mode="json") == v5.model_dump(mode="json")
    with pytest.raises(ValidationError):
        TaskCreatedV5.model_validate(
            {
                **_created(),
                "source_authorities": [
                    {**_source_authority(), "source_event_id": f"source-{index}"}
                    for index in range(21)
                ],
            }
        )
    assert (
        len(
            TaskCreatedV6.model_validate(
                {
                    **_created(),
                    "source_authorities": [
                        {**_source_authority(), "source_event_id": f"source-{index}"}
                        for index in range(256)
                    ],
                }
            ).source_authorities
        )
        == 256
    )
    with pytest.raises(ValidationError):
        TaskCreatedV6.model_validate(
            {
                **_created(),
                "source_authorities": [
                    {**_source_authority(), "source_event_id": f"source-{index}"}
                    for index in range(257)
                ],
            }
        )
    assert registry.get("task.created", 6) is TaskCreatedV6


@pytest.mark.parametrize("count", [20, 21, 256, 257])
def test_v6_boundaries_preserve_legacy_source_capacity(count: int) -> None:
    payload = {
        **_created(),
        "source_authorities": [
            {**_source_authority(), "source_event_id": f"boundary-source-{index}"}
            for index in range(count)
        ],
    }
    for version in (4, 5, 6):
        model = registry.get("task.created", version)
        assert model is not None
        if count > (256 if version == 6 else 20):
            with pytest.raises(ValidationError):
                model.model_validate(payload)
        else:
            actual = model.model_validate(payload).model_dump(mode="json")
            assert actual["source_authorities"] == [
                ServiceSourceAuthority.model_validate(source).model_dump(mode="json")
                for source in payload["source_authorities"]
            ]


@pytest.mark.parametrize(
    "change",
    [
        {"owner_member_id": "not-a-service-owner"},
        {"provider_effect_count": 1},
        {"source_authorities": [_source_authority(), _source_authority()]},
        {
            "source_authorities": [
                {**_source_authority(), "restriction": "unrestricted"}
            ]
        },
    ],
)
def test_v6_capacity_does_not_change_authority_boundaries(
    change: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        TaskCreatedV6.model_validate({**_created(), **change})
