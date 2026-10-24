"""Read current workstation routes through actual member or service authority."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from adminme.products.openclaw import main as seam
from adminme.products.openclaw import service_work
from adminme.products.openclaw.service_context import active_contexts
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_inventory import compose_inventory

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["workstation-inventory"])


@router.post("/workstation-inventory")
async def inventory(
    body: service_work.ServiceOnlyRequest, request: Request
) -> dict[str, Any]:
    runtime = seam._runtime(request)
    allowed = {COS_SERVICE_PRINCIPAL_ID}
    if body.provenance.channel_id == "administrateme-service":
        service = service_work._session(request, body.provenance)
        allowed.update(
            context.context_member_id
            for context in active_contexts(runtime, service)
            if context.context_member_id is not None
        )
        result = compose_inventory(
            runtime, allowed_owners=allowed, agent_id=body.provenance.agent_id or ""
        )
        await service_work._audit(
            runtime, service, body.provenance, "workstation.inventory", []
        )
    else:
        member = seam._session(request, body.provenance)
        seam._require_adult(member)
        allowed.add(member.auth_member_id)
        result = compose_inventory(
            runtime, allowed_owners=allowed, agent_id=body.provenance.agent_id or ""
        )
        await seam._record_tool_call(
            request, member, body.provenance, operation="workstation.inventory"
        )
    return result
