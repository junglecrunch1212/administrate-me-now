"""Comms product router: operational communications, not generic chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/comms", tags=["comms"])


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("comms")


__all__ = ["router"]
