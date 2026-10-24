"""Read current workstation routes through actual member or service authority."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, model_validator

from adminme.products.openclaw import main as seam
from adminme.products.openclaw import service_work
from adminme.products.openclaw.service_context import active_contexts
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from adminme.workstation_inventory import (
    InventoryChanged,
    compose_inventory,
    inventory_page,
)

router = APIRouter(prefix="/api/openclaw/v1/tools", tags=["workstation-inventory"])


class InventoryRequest(service_work.ServiceOnlyRequest):
    view: Literal["overview", "details"] = "overview"
    section: Literal["identities", "providers", "bridges", "tools"] | None = None
    binding_id: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=100000)
    limit: int = Field(default=40, ge=1, le=40)
    snapshot: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def detail_section(self) -> InventoryRequest:
        if (
            self.view == "details" or self.binding_id is not None
        ) and self.section is None:
            raise ValueError("select a section for detail or exact binding reads")
        if self.offset and self.snapshot is None:
            raise ValueError("page continuation requires the returned snapshot")
        return self


@router.post("/workstation-inventory")
async def inventory(body: InventoryRequest, request: Request) -> dict[str, Any]:
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
    try:
        return inventory_page(result, **body.model_dump(exclude={"provenance"}))
    except InventoryChanged as exc:
        raise HTTPException(409, "inventory changed; restart at offset zero") from exc
    except ValueError as exc:
        raise HTTPException(404, "authorized inventory page is unavailable") from exc
