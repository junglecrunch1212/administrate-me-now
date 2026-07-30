"""Core product router: Chief-of-Staff and lifecycle ownership namespace."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request


router = APIRouter(prefix="/api/core", tags=["core"])


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    return request.app.state.runtime.product_status("core")


__all__ = ["router"]
