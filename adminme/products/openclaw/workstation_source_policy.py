"""Apply only the operator's exact staged request through existing policy history."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from adminme.events.envelope import EventEnvelope
from adminme.products.openclaw import main as seam
from adminme.products.openclaw import service_work as work
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_inventory import read_private_document
from adminme.workstation_source_policy import BrowserSourceProvision, PROVISION_FILE
from adminme.workstation_sources import coordinate, current_resource, digest

router = APIRouter(
    prefix="/api/openclaw/v1/tools/workstation-browser",
    tags=["workstation-source-policy"],
)


class PolicyStateRequest(work.ServiceOnlyRequest):
    resource_id: str = Field(min_length=1, max_length=300)


class ProvisionRequest(work.ServiceOnlyRequest):
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")


def _source(runtime: Any, resource_id: str) -> tuple[dict[str, Any], Any]:
    try:
        resource = current_resource(runtime, resource_id, for_read=False)
        return resource, coordinate(runtime, resource)
    except (OSError, ValueError, KeyError):
        raise HTTPException(
            403, "configured CoS browser source is unavailable"
        ) from None


@router.post("/source-policy")
async def source_policy(body: PolicyStateRequest, request: Request) -> dict[str, Any]:
    work._session(request, body.provenance)
    runtime = seam._runtime(request)
    resource, source = _source(runtime, body.resource_id)
    return {
        "resource_id": resource["resource_id"],
        "binding_revision": resource["binding_revision"],
        "coordinate": source.model_dump(mode="json"),
        **runtime.settings_control.effective_source_policy_v2(
            COS_SERVICE_PRINCIPAL_ID, source
        ),
    }


@router.post("/provision-source-policy")
async def provision_source_policy(
    body: ProvisionRequest, request: Request
) -> dict[str, Any]:
    session = work._session(request, body.provenance)
    runtime = seam._runtime(request)
    try:
        staged = BrowserSourceProvision.model_validate(
            read_private_document(runtime.settings.instance.config_dir / PROVISION_FILE)
        )
    except (OSError, ValueError):
        raise HTTPException(
            403, "no exact operator-staged source request is available"
        ) from None
    if body.request_id != staged.request_id:
        raise HTTPException(
            403, "source request does not match the operator-staged request"
        )
    resource, source = _source(runtime, staged.resource_id)
    if (
        source.key != staged.policy_key
        or resource["binding_revision"] != staged.expected_binding_revision
    ):
        raise HTTPException(
            409, "configured source scope changed; prepare current exact policy"
        )
    key = "workstation-browser-source-policy:" + staged.request_id
    fingerprint = (
        "operator-authorization-sha256:"
        + staged.owner_authorization_sha256
        + ";request-sha256:"
        + digest(staged.model_dump(mode="json"))
    )
    async with runtime.settings_command_locks.setdefault(
        "settings-control", asyncio.Lock()
    ):
        await runtime.settings_control.replay_source_policy_v2_events(runtime.event_log)
        prior = await runtime.event_log.get_by_idempotency(session.tenant_id, key)
        if prior is not None:
            if prior.get("raw_ref") != fingerprint:
                raise HTTPException(
                    409, "operator request conflicts with retained source policy"
                )
            return {
                "status": "policy_recorded",
                "event_id": prior["event_id"],
                "idempotent": True,
                **runtime.settings_control.effective_source_policy_v2(
                    COS_SERVICE_PRINCIPAL_ID, source
                ),
            }
        if not staged.current(datetime.now(timezone.utc)):
            raise HTTPException(
                409, "operator-staged request expired; prepare a current request"
            )
        effective = runtime.settings_control.effective_source_policy_v2(
            COS_SERVICE_PRINCIPAL_ID, source
        )
        policy = effective["policy"]
        if policy["acquisition_state"] != "active" or (
            effective["origin"] != "recommended_default"
            and policy["context_mode"] != staged.context_mode
        ):
            raise HTTPException(
                409, "an explicit source restriction must be resolved independently"
            )
        try:
            revision = runtime.settings_control.prepare_source_policy_v2_change(
                COS_SERVICE_PRINCIPAL_ID,
                source,
                expected_revision=staged.expected_source_revision,
            )
        except ValueError:
            raise HTTPException(
                409, "source policy revision changed; review current policy"
            ) from None
        occurred_at = EventEnvelope.now_utc_iso()
        payload = {
            "owner_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "owner_kind": "service_principal",
            "policy_key": source.key,
            "coordinate": source.model_dump(mode="json"),
            "action": "set",
            "policy": {**policy, "context_mode": staged.context_mode},
            "revision": revision,
            "changed_by_principal_id": staged.operator_principal_id,
            "changed_at": occurred_at,
            "scope_widening": False,
            "credential_creation": False,
            "provider_write_authority": False,
            "live_admission_changed": False,
        }
        event = EventEnvelope(
            event_at_ms=int(time.time() * 1000),
            tenant_id=session.tenant_id,
            type="settings.source_policy_v2_changed",
            schema_version=1,
            occurred_at=occurred_at,
            source_adapter="workstation-operator-control",
            source_account_id=COS_SERVICE_PRINCIPAL_ID,
            owner_scope=session.private_scope,
            visibility_scope=session.private_scope,
            sensitivity="sensitive",
            actor_identity=staged.operator_principal_id,
            idempotency_key=key,
            raw_ref=fingerprint,
            payload=payload,
        )
        event_id = await runtime.events.write(
            event, correlation_id=body.provenance.correlation_id
        )
        runtime.settings_control.apply_source_policy_v2_event(payload)
        return {
            "status": "policy_recorded",
            "event_id": event_id,
            "idempotent": False,
            **runtime.settings_control.effective_source_policy_v2(
                COS_SERVICE_PRINCIPAL_ID, source
            ),
        }
