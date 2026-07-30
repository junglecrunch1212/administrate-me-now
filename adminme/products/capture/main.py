"""Capture/Knowledge router.

Quick-prefix capture, direct voice ingestion, and triage are intentionally not
routes in v2.  Member-owned knowledge arrives through the Cat-D bridge.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/capture", tags=["capture-knowledge"])


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("capture")


__all__ = ["router"]
