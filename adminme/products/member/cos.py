"""Member-controlled authority API for the non-human household CoS."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.events.schemas.coordination import CoordinationFieldV1, CoordinationKind
from adminme.events.schemas.operating_agreement import (
    AgreementAuthorityScope,
    AgreementLayer,
    OperatingRuleV1,
)
from adminme.delegated_execution import (
    ACTION_SPECS,
    ActionClass,
    RecurrenceStrategy,
    RepresentationKind,
    action_fingerprint,
)
from adminme.lib.session import Session
from adminme.products.auth import require_principal, session_from_request
from adminme.projections.parties.queries import (
    get_operating_agreement,
    list_operating_agreements,
    list_service_delegations,
)
from adminme.projections.tasks.queries import (
    coordination_conflicts,
    get_delegated_action,
    get_coordination_record,
    list_delegated_actions,
    list_execution_envelopes,
    list_coordination_records,
)
from adminme.service_identities import COS_PARTY_ID, COS_SERVICE_PRINCIPAL_ID


router = APIRouter(prefix="/api/member/v1/cos", tags=["member-cos"])
SessionDep = Annotated[Session, Depends(session_from_request)]


class DelegationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    context_scope: Literal["member_private", "household_shared", "external_contact"]
    context_member_id: str | None = Field(default=None, min_length=1)
    owner_party_id: str | None = Field(default=None, min_length=1)
    permissions: list[
        Literal[
            "read_context",
            "create_internal_work",
            "manage_internal_work",
            "manage_member_obligations",
            "coordinate_household_work",
            "prepare_external_actions",
            "execute_fixture_actions",
        ]
    ] = Field(min_length=1, max_length=7)
    expires_at: AwareDatetime | None = None


class DelegationDecision(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


class OperatingAgreementProposalRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    supersedes_revision: int | None = Field(default=None, ge=1)
    layer: AgreementLayer
    authority_scope: AgreementAuthorityScope
    effective_from: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    one_time_use_limit: Literal[1] | None = None
    rule: OperatingRuleV1
    provenance_event_ids: list[str] = Field(default_factory=list, max_length=100)
    provenance_digests: list[str] = Field(default_factory=list, max_length=100)


class OperatingAgreementDecisionRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["approve", "reject", "suspend", "revoke"]
    reason_code: str | None = Field(default=None, max_length=80)


class CoordinationCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    coordination_kind: CoordinationKind
    participant_member_ids: list[str] = Field(min_length=1, max_length=20)
    obligation_owner_kind: Literal["member", "household", "none"]
    obligation_owner_id: str | None = Field(default=None, min_length=1, max_length=200)
    responsible_party_id: str = Field(min_length=1, max_length=200)
    beneficiary_party_id: str | None = Field(default=None, min_length=1, max_length=200)
    linked_task_id: str | None = Field(default=None, min_length=1, max_length=200)
    linked_commitment_id: str | None = Field(default=None, min_length=1, max_length=200)
    cos_assignment_task_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    fields: list[CoordinationFieldV1] = Field(default_factory=list, max_length=20)
    expires_at: AwareDatetime | None = None


class CoordinationHandoffRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["propose", "accept", "reject", "withdraw", "reassign"]
    destination_member_id: str | None = Field(
        default=None, min_length=1, max_length=200
    )
    expires_at: AwareDatetime | None = None
    reason_code: Literal["capacity", "ownership", "timing", "other"] | None = None


class CoordinationWaitingRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    action: Literal["set", "clear"]
    waiting_on_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    follow_up_at: AwareDatetime | None = None


class CoordinationConversationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    conversation_reference: str = Field(min_length=1, max_length=240)


class CoordinationRevocationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    reason_code: Literal["withdrawn", "resolved_elsewhere", "privacy", "other"]


class ExecutionEnvelopeRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    envelope_id: str = Field(min_length=1, max_length=200)
    authority_grant_id: str = Field(min_length=1, max_length=200)
    action_class: ActionClass
    canonical_object_kind: str = Field(min_length=1, max_length=80)
    canonical_object_id: str = Field(min_length=1, max_length=240)
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=240)
    series_id: str | None = Field(default=None, min_length=1, max_length=240)
    recurrence_strategy: RecurrenceStrategy = "none"
    desired_representation_id: str = Field(min_length=1, max_length=240)
    provider_account_id: str = Field(min_length=1, max_length=500)
    provider_account_owner_principal_id: str = Field(min_length=1, max_length=200)
    destination_id: str = Field(min_length=1, max_length=500)
    audience_ids: list[str] = Field(min_length=1, max_length=20)
    representation_kind: RepresentationKind = "member_delegated"
    target_external_id: str | None = Field(default=None, max_length=500)
    expected_provider_revision: str | None = Field(default=None, max_length=500)
    desired_payload: dict[str, Any]
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_exact_shape(self) -> "ExecutionEnvelopeRequest":
        if len(self.audience_ids) != len(set(self.audience_ids)):
            raise ValueError("execution envelope audience must be unique")
        if self.recurrence_strategy == "provider_series" and not self.series_id:
            raise ValueError("provider recurrence requires exact series identity")
        if (
            self.recurrence_strategy == "individual_occurrence"
            and not self.occurrence_id
        ):
            raise ValueError("variable recurrence requires exact occurrence identity")
        if self.recurrence_strategy == "none" and (
            self.series_id or self.occurrence_id
        ):
            raise ValueError("non-recurring envelope cannot carry recurrence identity")
        return self


class DelegatedActionApprovalRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    expected_action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expires_at: AwareDatetime | None = None


class ExecutionEnvelopeRevocationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    reason_code: Literal["member_request", "policy_change", "scope_change", "other"]


class DelegatedActionWithdrawalRequest(BaseModel):
    model_config = {"extra": "forbid"}

    request_id: str = Field(min_length=8, max_length=120)
    expected_last_event_id: str = Field(min_length=1, max_length=240)
    reason_code: Literal["member_request", "superseded", "no_longer_needed", "other"]


def _runtime(request: Request) -> Any:
    return request.app.state.runtime


def _require_self_principal(session: Session) -> None:
    require_principal(session)
    if session.auth_member_id != session.view_member_id:
        raise HTTPException(403, "CoS authority cannot be changed while viewing-as")


def _envelope(
    session: Session,
    *,
    event_type: str,
    payload: dict[str, Any],
    request_id: str,
    owner_scope: str,
) -> EventEnvelope:
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=session.tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="member-cos-authority",
        source_account_id=session.auth_member_id,
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity="normal",
        correlation_id=session.correlation_id,
        actor_identity=session.auth_member_id,
        idempotency_key=(
            f"member-cos:{event_type}:{session.auth_member_id}:{request_id}"
        ),
        raw_ref=f"request-sha256:{fingerprint}",
        payload=payload,
    )


@router.get("/delegations")
async def delegations(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "CoS delegation projection is unavailable")
    return {
        "contract": "household-cos-delegation-v1",
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "service_party_id": COS_PARTY_ID,
        "items": list_service_delegations(
            runtime.projections.connection("parties"),
            session,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        ),
        "provider_authority": False,
        "external_write_requests": 0,
    }


@router.post("/delegations", status_code=201)
async def request_delegation(
    body: DelegationRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "CoS delegation authority is unavailable")
    context_member_id = body.context_member_id
    owner_party_id = body.owner_party_id
    principals = sorted(
        member_id
        for member_id, role in runtime.settings.member_roles.items()
        if role == "principal"
    )
    if body.context_scope == "member_private":
        if context_member_id not in {None, session.auth_member_id} or owner_party_id:
            raise HTTPException(403, "member-private authority is self-owned")
        context_member_id = session.auth_member_id
        required = [session.auth_member_id]
        quorum = "member_owner"
        owner_scope = f"private:{session.auth_member_id}"
        resource_owner = session.auth_member_id
    elif body.context_scope == "household_shared":
        if context_member_id or owner_party_id:
            raise HTTPException(400, "household-shared authority has no private owner")
        if len(principals) < 2:
            raise HTTPException(409, "household authority requires multiple principals")
        required = principals
        quorum = "all_adult_principals"
        owner_scope = "shared:household"
        resource_owner = None
    else:
        if (
            context_member_id not in {None, session.auth_member_id}
            or not owner_party_id
        ):
            raise HTTPException(403, "external-contact authority requires a self owner")
        context_member_id = session.auth_member_id
        party = (
            runtime.projections.connection("parties")
            .execute(
                "SELECT party_id, qualification FROM parties "
                "WHERE tenant_id=? AND party_id=?",
                (session.tenant_id, owner_party_id),
            )
            .fetchone()
        )
        if (
            party is None
            or party["qualification"] != "qualified"
            or owner_party_id in set(runtime.settings.member_roles) | {COS_PARTY_ID}
        ):
            raise HTTPException(
                409, "external contact must be a reviewed non-member Party"
            )
        required = [session.auth_member_id]
        quorum = "member_owner"
        owner_scope = f"private:{session.auth_member_id}"
        resource_owner = session.auth_member_id

    grant_digest = hashlib.sha256(
        f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
    ).hexdigest()[:32]
    grant_id = f"cos-grant:{grant_digest}"
    now = EventEnvelope.now_utc_iso()
    payload = {
        "grant_id": grant_id,
        "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "service_party_id": COS_PARTY_ID,
        "context_scope": body.context_scope,
        "context_member_id": context_member_id,
        "resource_owner_principal_id": resource_owner,
        "owner_party_id": owner_party_id,
        "requested_by_member_id": session.auth_member_id,
        "required_approving_member_ids": required,
        "approval_quorum": quorum,
        "permissions": sorted(set(body.permissions)),
        "audience": (
            required
            if body.context_scope == "household_shared"
            else [session.auth_member_id]
        ),
        "visibility": [owner_scope],
        "requested_at": now,
        "expires_at": body.expires_at.isoformat() if body.expires_at else None,
    }
    envelopes = [
        _envelope(
            session,
            event_type="service.delegation.requested",
            payload=payload,
            request_id=body.request_id,
            owner_scope=owner_scope,
        ),
        _envelope(
            session,
            event_type="service.delegation.approved",
            payload={
                "grant_id": grant_id,
                "approved_by_member_id": session.auth_member_id,
                "approved_at": now,
            },
            request_id=f"{body.request_id}:self-approval",
            owner_scope=owner_scope,
        ),
    ]
    try:
        event_ids = await runtime.events.write_batch(envelopes)
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", max(event_ids))
    projected = next(
        item
        for item in list_service_delegations(
            runtime.projections.connection("parties"),
            session,
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        )
        if item["grant_id"] == grant_id
    )
    return {
        "grant_id": grant_id,
        "event_ids": event_ids,
        "active": projected["active"],
        "required_approving_member_ids": required,
        "provider_authority": False,
        "external_write_requests": 0,
    }


def _grant_for_decision(runtime: Any, session: Session, grant_id: str) -> Any:
    if runtime.projections is None:
        raise HTTPException(503, "CoS delegation projection is unavailable")
    row = (
        runtime.projections.connection("parties")
        .execute(
            "SELECT * FROM service_delegation_grants WHERE tenant_id=? AND grant_id=?",
            (session.tenant_id, grant_id),
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(404, "CoS delegation not found")
    return row


@router.post("/delegations/{grant_id}/approvals")
async def approve_delegation(
    grant_id: str,
    body: DelegationDecision,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None:
        raise HTTPException(503, "CoS delegation authority is unavailable")
    grant = _grant_for_decision(runtime, session, grant_id)
    required = set(json.loads(str(grant["required_approvals_json"])))
    if session.auth_member_id not in required or grant["revoked_at"] is not None:
        raise HTTPException(403, "member is not an active required approver")
    payload = {
        "grant_id": grant_id,
        "approved_by_member_id": session.auth_member_id,
        "approved_at": EventEnvelope.now_utc_iso(),
    }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type="service.delegation.approved",
                payload=payload,
                request_id=body.request_id,
                owner_scope=str(grant["owner_scope"]),
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", event_id)
    return {"grant_id": grant_id, "event_id": event_id, "approved": True}


@router.delete("/delegations/{grant_id}")
async def revoke_delegation(
    grant_id: str,
    body: DelegationDecision,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None:
        raise HTTPException(503, "CoS delegation authority is unavailable")
    grant = _grant_for_decision(runtime, session, grant_id)
    authorized = set(json.loads(str(grant["required_approvals_json"]))) | {
        str(grant["requested_by_member_id"])
    }
    if session.auth_member_id not in authorized:
        raise HTTPException(403, "member cannot revoke this CoS delegation")
    payload = {
        "grant_id": grant_id,
        "revoked_by_member_id": session.auth_member_id,
        "revoked_at": EventEnvelope.now_utc_iso(),
        "reason": body.reason,
    }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type="service.delegation.revoked",
                payload=payload,
                request_id=body.request_id,
                owner_scope=str(grant["owner_scope"]),
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", event_id)
    return {"grant_id": grant_id, "event_id": event_id, "revoked": True}


def _adult_principals(runtime: Any) -> list[str]:
    return sorted(
        member_id
        for member_id, role in runtime.settings.member_roles.items()
        if role == "principal"
    )


def _principal_set_digest(principals: list[str]) -> str:
    encoded = json.dumps(sorted(principals), separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _agreement_owner_scope(authority_scope: str, owner_member_id: str | None) -> str:
    return (
        f"private:{owner_member_id}"
        if authority_scope == "member_private" and owner_member_id
        else "shared:household"
    )


@router.get("/agreements")
async def operating_agreements(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "operating agreement projection is unavailable")
    return {
        "contract": "household-cos-operating-agreement-v1",
        "items": list_operating_agreements(
            runtime.projections.connection("parties"),
            session,
            as_of_iso=EventEnvelope.now_utc_iso(),
            include_inactive=True,
        ),
        "source_policies": {"contract": "separate", "embedded": False},
        "global_trust_score": None,
        "provider_effect_count": 0,
    }


@router.post("/agreements", status_code=201)
async def propose_operating_agreement(
    body: OperatingAgreementProposalRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "operating agreement authority is unavailable")
    principals = _adult_principals(runtime)
    if body.authority_scope == "member_private":
        if body.layer not in {"member_private", "exception"}:
            raise HTTPException(422, "private authority requires a private layer")
        owner_member_id = session.auth_member_id
        required = [session.auth_member_id]
        quorum = "member_owner"
    else:
        if body.layer not in {"shared_household", "exception"}:
            raise HTTPException(422, "shared authority requires a shared layer")
        if len(principals) < 2:
            raise HTTPException(409, "shared agreement requires multiple principals")
        owner_member_id = None
        required = principals
        quorum = "all_adult_principals"
    trust = body.rule.trust_scope
    if sorted(trust.granting_member_ids) != sorted(required):
        raise HTTPException(422, "trust scope must name the exact granting members")
    if body.rule.represented_service_principal_id != COS_SERVICE_PRINCIPAL_ID:
        raise HTTPException(422, "agreement must represent the household CoS")
    if COS_SERVICE_PRINCIPAL_ID not in body.rule.acting_principal_ids:
        raise HTTPException(422, "agreement must name the CoS actor")
    if body.authority_scope == "member_private":
        other_members = set(runtime.settings.member_roles) - {session.auth_member_id}
        member_bearing_coordinates = (
            body.rule.acting_principal_ids,
            body.rule.active_member_ids,
            body.rule.on_behalf_of_member_ids,
            body.rule.resource_owner_principal_ids,
            body.rule.provider_account_owner_principal_ids,
            body.rule.responsible_party_ids,
            body.rule.beneficiary_party_ids,
            body.rule.destination_ids,
            body.rule.audience_ids,
            body.rule.trust_scope.destination_ids,
            body.rule.ceilings.permitted_destination_ids,
        )
        if any(
            other_members.intersection(values) for values in member_bearing_coordinates
        ):
            raise HTTPException(
                403, "private agreement cannot name another household member"
            )
    if body.layer == "exception" and not (
        body.expires_at is not None or body.one_time_use_limit == 1
    ):
        raise HTTPException(422, "an exception must expire or be one-time")
    existing = get_operating_agreement(
        runtime.projections.connection("parties"),
        session,
        agreement_id=body.agreement_id,
        revision=body.revision,
        as_of_iso=EventEnvelope.now_utc_iso(),
    )
    if existing is not None:
        raise HTTPException(409, "operating agreement revision already exists")
    if body.supersedes_revision is not None:
        prior = get_operating_agreement(
            runtime.projections.connection("parties"),
            session,
            agreement_id=body.agreement_id,
            revision=body.supersedes_revision,
            as_of_iso=EventEnvelope.now_utc_iso(),
        )
        if prior is None:
            raise HTTPException(409, "superseded agreement revision is unavailable")
    now = EventEnvelope.now_utc_iso()
    payload = {
        "agreement_id": body.agreement_id,
        "revision": body.revision,
        "supersedes_revision": body.supersedes_revision,
        "layer": body.layer,
        "authority_scope": body.authority_scope,
        "owner_member_id": owner_member_id,
        "proposed_by_member_id": session.auth_member_id,
        "proposed_at": now,
        "effective_from": body.effective_from.isoformat()
        if body.effective_from
        else None,
        "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        "one_time_use_limit": body.one_time_use_limit,
        "required_approving_member_ids": required,
        "approval_quorum": quorum,
        "principal_set_digest": _principal_set_digest(required),
        "rule": body.rule.model_dump(mode="json"),
        "provenance_event_ids": body.provenance_event_ids,
        "provenance_digests": body.provenance_digests,
        "self_activates": False,
        "provider_effect_count": 0,
    }
    owner_scope = _agreement_owner_scope(body.authority_scope, owner_member_id)
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type="operating_agreement.proposed",
                payload=payload,
                request_id=body.request_id,
                owner_scope=owner_scope,
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", event_id)
    item = get_operating_agreement(
        runtime.projections.connection("parties"),
        session,
        agreement_id=body.agreement_id,
        revision=body.revision,
        as_of_iso=now,
    )
    return {
        "event_id": event_id,
        "item": item,
        "active": False,
        "self_activated": False,
        "provider_effect_count": 0,
    }


def _agreement_row(
    runtime: Any, session: Session, agreement_id: str, revision: int
) -> Any:
    if runtime.projections is None:
        raise HTTPException(503, "operating agreement projection is unavailable")
    row = (
        runtime.projections.connection("parties")
        .execute(
            "SELECT * FROM operating_agreements WHERE tenant_id=? "
            "AND agreement_id=? AND revision=?",
            (session.tenant_id, agreement_id, revision),
        )
        .fetchone()
    )
    if row is None or row["layer"] == "constitutional":
        raise HTTPException(404, "mutable operating agreement revision not found")
    if (
        row["authority_scope"] == "member_private"
        and row["owner_member_id"] != session.auth_member_id
    ):
        raise HTTPException(404, "mutable operating agreement revision not found")
    return row


@router.post("/agreements/{agreement_id}/{revision}/decision")
async def decide_operating_agreement(
    agreement_id: str,
    revision: int,
    body: OperatingAgreementDecisionRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None:
        raise HTTPException(503, "operating agreement authority is unavailable")
    row = _agreement_row(runtime, session, agreement_id, revision)
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "operating agreement revision changed")
    required = set(json.loads(str(row["required_approvals_json"])))
    if session.auth_member_id not in required:
        raise HTTPException(403, "member is not an agreement authority")
    if body.action == "approve" and row["state"] != "proposed":
        raise HTTPException(409, "only a proposal can be approved")
    if body.action == "reject" and row["state"] != "proposed":
        raise HTTPException(409, "only a proposal can be rejected")
    if body.action == "suspend" and row["state"] != "active":
        raise HTTPException(409, "only an active agreement can be suspended")
    if body.action == "revoke" and row["state"] in {"revoked", "superseded"}:
        raise HTTPException(409, "agreement is already terminal")
    now = EventEnvelope.now_utc_iso()
    common = {
        "agreement_id": agreement_id,
        "revision": revision,
        "authority_scope": row["authority_scope"],
        "owner_member_id": row["owner_member_id"],
    }
    if body.action == "approve":
        event_type = "operating_agreement.approved"
        payload = {
            **common,
            "approved_by_member_id": session.auth_member_id,
            "approved_at": now,
            "proposal_event_id": row["proposal_event_id"],
            "principal_set_digest": row["principal_set_digest"],
        }
    elif body.action == "reject":
        event_type = "operating_agreement.rejected"
        reason = body.reason_code or "other"
        if reason not in {"scope", "privacy", "burden", "timing", "other"}:
            raise HTTPException(422, "invalid rejection reason")
        payload = {
            **common,
            "rejected_by_member_id": session.auth_member_id,
            "rejected_at": now,
            "reason_code": reason,
        }
    elif body.action == "suspend":
        event_type = "operating_agreement.suspended"
        reason = body.reason_code or "member_request"
        if reason not in {
            "privacy",
            "scope_violation",
            "verification_failure",
            "reconciliation_failure",
            "runtime_degradation",
            "member_request",
        }:
            raise HTTPException(422, "invalid suspension reason")
        payload = {
            **common,
            "suspended_by_member_id": session.auth_member_id,
            "suspended_at": now,
            "reason_code": reason,
        }
    else:
        event_type = "operating_agreement.revoked"
        reason = body.reason_code or "member_request"
        if reason not in {
            "member_request",
            "scope_violation",
            "superseded",
            "no_longer_needed",
            "other",
        }:
            raise HTTPException(422, "invalid revocation reason")
        payload = {
            **common,
            "revoked_by_member_id": session.auth_member_id,
            "revoked_at": now,
            "reason_code": reason,
        }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type=event_type,
                payload=payload,
                request_id=body.request_id,
                owner_scope=str(row["owner_scope"]),
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("parties", event_id)
    item = get_operating_agreement(
        runtime.projections.connection("parties"),
        session,
        agreement_id=agreement_id,
        revision=revision,
        as_of_iso=now,
    )
    return {"event_id": event_id, "item": item, "provider_effect_count": 0}


def _household_party_id(runtime: Any) -> str:
    mapping = runtime.settings.household_mapping
    return str(
        mapping.get("household_party_id")
        or (
            "household_"
            + hashlib.sha256(runtime.settings.instance.tenant_id.encode()).hexdigest()[
                :20
            ]
        )
    )


def _coordination_row(runtime: Any, session: Session, coordination_id: str) -> Any:
    if runtime.projections is None:
        raise HTTPException(503, "coordination projection is unavailable")
    row = (
        runtime.projections.connection("tasks")
        .execute(
            "SELECT * FROM coordination_records WHERE tenant_id=? AND coordination_id=?",
            (session.tenant_id, coordination_id),
        )
        .fetchone()
    )
    if row is None:
        raise HTTPException(404, "coordination record not found")
    participant = (
        runtime.projections.connection("tasks")
        .execute(
            "SELECT 1 FROM coordination_participants WHERE tenant_id=? "
            "AND coordination_id=? AND member_id=?",
            (session.tenant_id, coordination_id, session.auth_member_id),
        )
        .fetchone()
    )
    if participant is None:
        raise HTTPException(404, "coordination record not found")
    return row


@router.get("/coordination")
async def coordination_list(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "coordination projection is unavailable")
    return {
        "contract": "household-cos-coordination-v1",
        "items": list_coordination_records(
            runtime.projections.connection("tasks"),
            session,
            as_of_iso=EventEnvelope.now_utc_iso(),
        ),
        "private_evidence_included": False,
        "provider_effect_count": 0,
    }


@router.post("/coordination", status_code=201)
async def create_coordination(
    body: CoordinationCreateRequest, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "coordination state is unavailable")
    principals = set(_adult_principals(runtime))
    participants = sorted(set(body.participant_member_ids))
    if session.auth_member_id not in participants:
        raise HTTPException(422, "originator must be a coordination participant")
    if not set(participants).issubset(principals):
        raise HTTPException(422, "coordination participants must be adult principals")
    if body.obligation_owner_kind == "member" and (
        body.obligation_owner_id != session.auth_member_id
    ):
        raise HTTPException(403, "a member may coordinate only their own obligation")
    household_party_id = _household_party_id(runtime)
    if body.obligation_owner_kind == "household" and (
        body.obligation_owner_id != household_party_id
    ):
        raise HTTPException(422, "household obligation requires the household Party")
    if body.obligation_owner_kind == "none" and body.obligation_owner_id is not None:
        raise HTTPException(422, "non-obligation coordination cannot name an owner")
    permitted_parties = principals | {COS_PARTY_ID, household_party_id}
    if body.responsible_party_id not in permitted_parties:
        raise HTTPException(422, "responsible Party is outside household coordination")
    if body.beneficiary_party_id and body.beneficiary_party_id not in permitted_parties:
        raise HTTPException(422, "beneficiary Party is outside household coordination")
    field_names = {field.name for field in body.fields}
    if body.coordination_kind == "busy_availability" and field_names != {
        "start_at",
        "end_at",
        "availability",
    }:
        raise HTTPException(422, "busy availability exposes only time and busy state")
    for field in body.fields:
        if not set(field.audience_member_ids).issubset(participants):
            raise HTTPException(422, "field audience must be a participant subset")
    digest = hashlib.sha256(
        f"{session.tenant_id}|{session.auth_member_id}|{body.request_id}".encode()
    ).hexdigest()[:32]
    coordination_id = f"coordination:{digest}"
    now = EventEnvelope.now_utc_iso()
    shared_payload = {
        "coordination_id": coordination_id,
        "coordination_kind": body.coordination_kind,
        "originator_member_id": session.auth_member_id,
        "participant_member_ids": participants,
        "obligation_owner_kind": body.obligation_owner_kind,
        "obligation_owner_id": body.obligation_owner_id,
        "responsible_party_id": body.responsible_party_id,
        "beneficiary_party_id": body.beneficiary_party_id,
        "linked_task_id": body.linked_task_id,
        "linked_commitment_id": body.linked_commitment_id,
        "cos_assignment_task_id": body.cos_assignment_task_id,
        "fields": [field.model_dump(mode="json") for field in body.fields],
        "created_at": now,
        "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        "private_evidence_included": False,
        "provider_projection_authorized": False,
    }
    receipt_payload = {
        "coordination_id": coordination_id,
        "originator_member_id": session.auth_member_id,
        "receipt_status": "recorded",
        "recorded_at": now,
        "private_evidence_included": False,
    }
    try:
        event_ids = await runtime.events.write_batch(
            [
                _envelope(
                    session,
                    event_type="coordination.created",
                    payload=shared_payload,
                    request_id=body.request_id,
                    owner_scope="shared:household",
                ),
                _envelope(
                    session,
                    event_type="coordination.originator_receipt_recorded",
                    payload=receipt_payload,
                    request_id=f"{body.request_id}:receipt",
                    owner_scope=f"private:{session.auth_member_id}",
                ),
            ]
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("tasks", max(event_ids))
    item = get_coordination_record(
        runtime.projections.connection("tasks"),
        session,
        coordination_id=coordination_id,
        as_of_iso=now,
    )
    return {
        "coordination_id": coordination_id,
        "event_ids": event_ids,
        "item": item,
        "originator_receipt": item["originator_receipt"] if item else None,
        "private_evidence_included": False,
        "provider_effect_count": 0,
    }


@router.get("/coordination/{coordination_id}")
async def coordination_detail(
    coordination_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "coordination projection is unavailable")
    item = get_coordination_record(
        runtime.projections.connection("tasks"),
        session,
        coordination_id=coordination_id,
        as_of_iso=EventEnvelope.now_utc_iso(),
    )
    if item is None:
        raise HTTPException(404, "coordination record not found")
    return {"item": item, "private_evidence_included": False}


@router.get("/coordination/{coordination_id}/conflicts")
async def coordination_conflict_list(
    coordination_id: str, request: Request, session: SessionDep
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "coordination projection is unavailable")
    _coordination_row(runtime, session, coordination_id)
    return {
        "coordination_id": coordination_id,
        "conflicts": coordination_conflicts(
            runtime.projections.connection("tasks"),
            session,
            coordination_id=coordination_id,
            as_of_iso=EventEnvelope.now_utc_iso(),
        ),
        "busy_only": True,
        "private_evidence_included": False,
    }


@router.post("/coordination/{coordination_id}/handoff")
async def coordinate_handoff(
    coordination_id: str,
    body: CoordinationHandoffRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "coordination state is unavailable")
    row = _coordination_row(runtime, session, coordination_id)
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "coordination record changed")
    participants = {
        str(item[0])
        for item in runtime.projections.connection("tasks")
        .execute(
            "SELECT member_id FROM coordination_participants "
            "WHERE tenant_id=? AND coordination_id=?",
            (session.tenant_id, coordination_id),
        )
        .fetchall()
    }
    now = EventEnvelope.now_utc_iso()
    now_dt = datetime.now(timezone.utc)
    destination = body.destination_member_id
    if body.action == "propose":
        if session.auth_member_id not in {
            row["originator_member_id"],
            row["responsible_party_id"],
        }:
            raise HTTPException(403, "member cannot propose this handoff")
        if not destination or destination not in participants:
            raise HTTPException(422, "handoff destination must be a participant")
        if body.expires_at is not None and body.expires_at <= now_dt:
            raise HTTPException(422, "handoff expiry must be in the future")
        event_type = "coordination.handoff_proposed"
        payload = {
            "coordination_id": coordination_id,
            "proposed_by_member_id": session.auth_member_id,
            "destination_member_id": destination,
            "proposed_at": now,
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        }
    elif body.action in {"accept", "reject"}:
        proposed = str(row["proposed_destination_member_id"] or "")
        if session.auth_member_id != proposed or destination not in {None, proposed}:
            raise HTTPException(403, "only the proposed destination may decide")
        if (
            row["handoff_expires_at"]
            and datetime.fromisoformat(
                str(row["handoff_expires_at"]).replace("Z", "+00:00")
            )
            <= now_dt
        ):
            raise HTTPException(409, "pending handoff has expired")
        event_type = "coordination.handoff_decided"
        payload = {
            "coordination_id": coordination_id,
            "destination_member_id": session.auth_member_id,
            "decision": "accepted" if body.action == "accept" else "rejected",
            "decided_at": now,
            "reason_code": body.reason_code,
        }
    elif body.action == "withdraw":
        if session.auth_member_id not in {
            row["originator_member_id"],
            row["handoff_proposed_by_member_id"],
        }:
            raise HTTPException(403, "member cannot withdraw this handoff")
        event_type = "coordination.handoff_withdrawn"
        payload = {
            "coordination_id": coordination_id,
            "withdrawn_by_member_id": session.auth_member_id,
            "withdrawn_at": now,
        }
    else:
        if session.auth_member_id not in {
            row["originator_member_id"],
            row["responsible_party_id"],
        }:
            raise HTTPException(403, "member cannot reassign this handoff")
        if not destination or destination not in participants:
            raise HTTPException(422, "new destination must be a participant")
        if body.expires_at is not None and body.expires_at <= now_dt:
            raise HTTPException(422, "handoff expiry must be in the future")
        event_type = "coordination.handoff_reassigned"
        payload = {
            "coordination_id": coordination_id,
            "reassigned_by_member_id": session.auth_member_id,
            "previous_destination_member_id": row["proposed_destination_member_id"],
            "destination_member_id": destination,
            "reassigned_at": now,
            "expires_at": body.expires_at.isoformat() if body.expires_at else None,
        }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type=event_type,
                payload=payload,
                request_id=body.request_id,
                owner_scope="shared:household",
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("tasks", event_id)
    item = get_coordination_record(
        runtime.projections.connection("tasks"),
        session,
        coordination_id=coordination_id,
        as_of_iso=now,
    )
    return {"event_id": event_id, "item": item, "provider_effect_count": 0}


@router.post("/coordination/{coordination_id}/waiting")
async def coordinate_waiting(
    coordination_id: str,
    body: CoordinationWaitingRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "coordination state is unavailable")
    row = _coordination_row(runtime, session, coordination_id)
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "coordination record changed")
    now = EventEnvelope.now_utc_iso()
    if body.action == "set":
        if not body.waiting_on_member_id:
            raise HTTPException(422, "waiting member is required")
        event_type = "coordination.waiting_set"
        payload = {
            "coordination_id": coordination_id,
            "waiting_on_member_id": body.waiting_on_member_id,
            "set_by_party_id": session.auth_member_id,
            "waiting_since": now,
            "follow_up_at": body.follow_up_at.isoformat()
            if body.follow_up_at
            else None,
        }
    else:
        event_type = "coordination.waiting_cleared"
        payload = {
            "coordination_id": coordination_id,
            "cleared_by_party_id": session.auth_member_id,
            "cleared_at": now,
        }
    event_id = await runtime.events.write(
        _envelope(
            session,
            event_type=event_type,
            payload=payload,
            request_id=body.request_id,
            owner_scope="shared:household",
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {"event_id": event_id, "provider_effect_count": 0}


@router.post("/coordination/{coordination_id}/conversation")
async def link_coordination_conversation(
    coordination_id: str,
    body: CoordinationConversationRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "coordination state is unavailable")
    row = _coordination_row(runtime, session, coordination_id)
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "coordination record changed")
    payload = {
        "coordination_id": coordination_id,
        "member_id": session.auth_member_id,
        "conversation_reference": body.conversation_reference,
        "linked_at": EventEnvelope.now_utc_iso(),
        "transcript_included": False,
    }
    event_id = await runtime.events.write(
        _envelope(
            session,
            event_type="coordination.conversation_linked",
            payload=payload,
            request_id=body.request_id,
            owner_scope=f"private:{session.auth_member_id}",
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    item = get_coordination_record(
        runtime.projections.connection("tasks"),
        session,
        coordination_id=coordination_id,
        as_of_iso=EventEnvelope.now_utc_iso(),
    )
    return {"event_id": event_id, "item": item, "transcript_included": False}


@router.post("/coordination/{coordination_id}/revoke")
async def revoke_coordination(
    coordination_id: str,
    body: CoordinationRevocationRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "coordination state is unavailable")
    row = _coordination_row(runtime, session, coordination_id)
    if row["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "coordination record changed")
    if session.auth_member_id not in {
        row["originator_member_id"],
        row["responsible_party_id"],
        row["accepted_destination_member_id"],
    }:
        raise HTTPException(403, "member cannot revoke this coordination")
    payload = {
        "coordination_id": coordination_id,
        "revoked_by_member_id": session.auth_member_id,
        "revoked_at": EventEnvelope.now_utc_iso(),
        "reason_code": body.reason_code,
    }
    event_id = await runtime.events.write(
        _envelope(
            session,
            event_type="coordination.revoked",
            payload=payload,
            request_id=body.request_id,
            owner_scope="shared:household",
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {"event_id": event_id, "revoked": True, "provider_effect_count": 0}


def _member_execution_grant(
    runtime: Any,
    session: Session,
    *,
    grant_id: str,
    permission: str,
) -> dict[str, Any]:
    if runtime.projections is None:
        raise HTTPException(503, "CoS execution authority is unavailable")
    grants = list_service_delegations(
        runtime.projections.connection("parties"),
        session,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
    )
    matches = [
        grant
        for grant in grants
        if grant["grant_id"] == grant_id
        and grant.get("active") is True
        and grant.get("context_scope") == "member_private"
        and grant.get("context_member_id") == session.auth_member_id
        and permission in set(grant.get("permissions") or [])
    ]
    if len(matches) != 1:
        raise HTTPException(403, "one exact active CoS execution grant is required")
    return matches[0]


def _validate_fixture_owner_destination(
    session: Session,
    *,
    provider_account_owner_principal_id: str,
    provider_account_id: str,
    destination_id: str,
    representation_kind: RepresentationKind,
) -> None:
    member = session.auth_member_id
    if representation_kind == "cos_operational":
        if (
            provider_account_owner_principal_id != COS_SERVICE_PRINCIPAL_ID
            or provider_account_id != "fixture-account:service:chief-of-staff"
            or not destination_id.startswith("fixture:service:chief-of-staff:")
        ):
            raise HTTPException(403, "CoS operational destination is not service-owned")
        return
    if (
        provider_account_owner_principal_id != member
        or provider_account_id != f"fixture-account:member:{member}"
        or not destination_id.startswith(f"fixture:member:{member}:")
    ):
        raise HTTPException(403, "member fixture destination is not self-owned")


@router.get("/execution-envelopes")
async def execution_envelopes(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "CoS execution projection is unavailable")
    return {
        "contract": "cos-delegated-execution-v1",
        "items": list_execution_envelopes(
            runtime.projections.connection("tasks"), session
        ),
        "openclaw_permission_included": False,
        "provider_execution_scope": "fixture_only",
    }


@router.post("/execution-envelopes", status_code=201)
async def grant_execution_envelope(
    body: ExecutionEnvelopeRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Grant exact standing domain authority; never OpenClaw permission."""

    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "CoS execution authority is unavailable")
    _member_execution_grant(
        runtime,
        session,
        grant_id=body.authority_grant_id,
        permission="execute_fixture_actions",
    )
    spec = ACTION_SPECS[body.action_class]
    if not spec.fixture_executable:
        raise HTTPException(
            422, "standing authority is unavailable for this action class"
        )
    if body.canonical_object_kind not in spec.canonical_object_kinds:
        raise HTTPException(
            422, "canonical object kind does not match the action class"
        )
    if spec.requires_target and not body.target_external_id:
        raise HTTPException(422, "this action class requires an exact provider object")
    _validate_fixture_owner_destination(
        session,
        provider_account_owner_principal_id=body.provider_account_owner_principal_id,
        provider_account_id=body.provider_account_id,
        destination_id=body.destination_id,
        representation_kind=body.representation_kind,
    )
    if sorted(body.audience_ids) != [session.auth_member_id]:
        raise HTTPException(
            403, "member-private standing authority has one exact audience"
        )
    now = datetime.now(timezone.utc)
    if body.expires_at <= now or body.expires_at > now + timedelta(days=31):
        raise HTTPException(422, "standing authority must expire within 31 days")
    desired_digest = action_fingerprint(body.desired_payload)
    payload = {
        "envelope_id": body.envelope_id,
        "authority_grant_id": body.authority_grant_id,
        "granted_by_member_id": session.auth_member_id,
        "represented_service_principal_id": COS_SERVICE_PRINCIPAL_ID,
        "action_class": body.action_class,
        "canonical_object_kind": body.canonical_object_kind,
        "canonical_object_id": body.canonical_object_id,
        "occurrence_id": body.occurrence_id,
        "series_id": body.series_id,
        "recurrence_strategy": body.recurrence_strategy,
        "desired_representation_id": body.desired_representation_id,
        "provider_family": spec.provider_family,
        "provider_account_id": body.provider_account_id,
        "provider_account_owner_principal_id": body.provider_account_owner_principal_id,
        "destination_id": body.destination_id,
        "audience_ids": sorted(body.audience_ids),
        "representation_kind": body.representation_kind,
        "target_external_id": body.target_external_id,
        "expected_provider_revision": body.expected_provider_revision,
        "desired_payload_digest": desired_digest,
        "granted_at": now.isoformat(),
        "expires_at": body.expires_at.isoformat(),
        "provider_execution_scope": "fixture_only",
        "openclaw_permission_included": False,
        "provider_effect_count": 0,
    }
    try:
        event_id = await runtime.events.write(
            _envelope(
                session,
                event_type="cos.execution_envelope.granted",
                payload=payload,
                request_id=body.request_id,
                owner_scope=f"private:{session.auth_member_id}",
            )
        )
    except IdempotencyConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await runtime.wait_for_projection("tasks", event_id)
    item = next(
        (
            entry
            for entry in list_execution_envelopes(
                runtime.projections.connection("tasks"), session
            )
            if entry["envelope_id"] == body.envelope_id
        ),
        None,
    )
    return {"event_id": event_id, "item": item, "provider_effect_count": 0}


@router.post("/execution-envelopes/{envelope_id}/revoke")
async def revoke_execution_envelope(
    envelope_id: str,
    body: ExecutionEnvelopeRevocationRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "CoS execution authority is unavailable")
    item = next(
        (
            entry
            for entry in list_execution_envelopes(
                runtime.projections.connection("tasks"), session
            )
            if entry["envelope_id"] == envelope_id
        ),
        None,
    )
    if item is None or item["granted_by_member_id"] != session.auth_member_id:
        raise HTTPException(404, "execution envelope not found")
    event_id = await runtime.events.write(
        _envelope(
            session,
            event_type="cos.execution_envelope.revoked",
            payload={
                "envelope_id": envelope_id,
                "revoked_by_member_id": session.auth_member_id,
                "revoked_at": EventEnvelope.now_utc_iso(),
                "reason_code": body.reason_code,
            },
            request_id=body.request_id,
            owner_scope=f"private:{session.auth_member_id}",
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {"event_id": event_id, "revoked": True}


@router.get("/delegated-actions")
async def delegated_actions(request: Request, session: SessionDep) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.projections is None:
        raise HTTPException(503, "CoS execution projection is unavailable")
    return {
        "contract": "cos-delegated-execution-v1",
        "items": list_delegated_actions(
            runtime.projections.connection("tasks"), session
        ),
        "provider_effect_count": 0,
    }


@router.post("/delegated-actions/{action_id}/approve")
async def approve_delegated_action(
    action_id: str,
    body: DelegatedActionApprovalRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    """Give one-off domain authority for one immutable desired representation."""

    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "CoS execution authority is unavailable")
    action = get_delegated_action(
        runtime.projections.connection("tasks"), session, action_id=action_id
    )
    if (
        action is None
        or action.get("active_context_member_id") != session.auth_member_id
    ):
        raise HTTPException(404, "delegated action not found")
    if action["state"] != "awaiting_member_approval":
        raise HTTPException(409, "delegated action is not awaiting member approval")
    if action["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "delegated action changed")
    if action["action_fingerprint"] != body.expected_action_fingerprint:
        raise HTTPException(409, "delegated action content changed")
    _member_execution_grant(
        runtime,
        session,
        grant_id=str(action["authority_grant_id"]),
        permission="execute_fixture_actions",
    )
    now = datetime.now(timezone.utc)
    action_expiry = datetime.fromisoformat(
        str(action["expires_at"]).replace("Z", "+00:00")
    )
    approval_expiry = body.expires_at or min(action_expiry, now + timedelta(minutes=10))
    if approval_expiry <= now or approval_expiry > action_expiry:
        raise HTTPException(422, "one-off approval expiry is outside the action window")
    event_id = await runtime.events.write(
        _envelope(
            session,
            event_type="cos.delegated_action.domain_approved",
            payload={
                "action_id": action_id,
                "action_revision": action["action_revision"],
                "action_fingerprint": action["action_fingerprint"],
                "approval_kind": "one_off",
                "approved_by_member_id": session.auth_member_id,
                "approval_event_id": None,
                "execution_envelope_id": None,
                "approved_at": now.isoformat(),
                "expires_at": approval_expiry.isoformat(),
                "openclaw_permission_included": False,
            },
            request_id=body.request_id,
            owner_scope=f"private:{session.auth_member_id}",
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {
        "event_id": event_id,
        "state": "awaiting_openclaw_approval",
        "openclaw_permission_included": False,
    }


@router.post("/delegated-actions/{action_id}/withdraw")
async def withdraw_delegated_action(
    action_id: str,
    body: DelegatedActionWithdrawalRequest,
    request: Request,
    session: SessionDep,
) -> dict[str, Any]:
    _require_self_principal(session)
    runtime = _runtime(request)
    if runtime.events is None or runtime.projections is None:
        raise HTTPException(503, "CoS execution authority is unavailable")
    action = get_delegated_action(
        runtime.projections.connection("tasks"), session, action_id=action_id
    )
    if (
        action is None
        or action.get("active_context_member_id") != session.auth_member_id
    ):
        raise HTTPException(404, "delegated action not found")
    if action["last_event_id"] != body.expected_last_event_id:
        raise HTTPException(409, "delegated action changed")
    if action["state"] in {
        "awaiting_result",
        "awaiting_observation",
        "active",
        "reconciled",
    }:
        raise HTTPException(409, "a dispatched action cannot be withdrawn as intent")
    event_id = await runtime.events.write(
        _envelope(
            session,
            event_type="cos.delegated_action.withdrawn",
            payload={
                "action_id": action_id,
                "withdrawn_by_member_id": session.auth_member_id,
                "withdrawn_at": EventEnvelope.now_utc_iso(),
                "reason_code": body.reason_code,
            },
            request_id=body.request_id,
            owner_scope=f"private:{session.auth_member_id}",
        )
    )
    await runtime.wait_for_projection("tasks", event_id)
    return {"event_id": event_id, "state": "withdrawn"}


__all__ = ["router"]
