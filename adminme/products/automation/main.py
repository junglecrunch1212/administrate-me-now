"""Automation product router: governed finance, sensor, and action surfaces."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("automation")


__all__ = ["router"]
