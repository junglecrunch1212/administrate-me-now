"""Authenticated, read-only distribution of the reviewed Mac setup helper."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from adminme.bridge_enrollment import BridgeEnrollmentError, reviewed_release
from adminme.bridge_helper_distribution import ARCHIVE, read_distribution
from adminme.lib.session import Session
from adminme.products.auth import session_from_request


router = APIRouter()
SessionDep = Annotated[Session, Depends(session_from_request)]
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _download(request: Request, session: Session) -> tuple[dict[str, Any], bytes]:
    from adminme.products.member.main import _require_self_settings

    _require_self_settings(session)
    runtime = request.app.state.runtime
    coordinator = runtime.bridge_enrollment
    try:
        commit = reviewed_release(
            coordinator.repository_root,
            deployment_release_commit=coordinator.deployment_release_commit,
            require_deployment_release=coordinator.require_deployment_release,
        )
        return read_distribution(coordinator.instance_dir, commit)
    except (BridgeEnrollmentError, OSError, ValueError) as exc:
        raise HTTPException(
            503,
            "The verified helper for this release is not available yet. "
            "No pairing is needed until the helper is ready.",
            headers=HEADERS,
        ) from exc


@router.get("/api/member/v1/settings/bridges/helper")
async def helper_metadata(
    request: Request, response: Response, session: SessionDep
) -> dict[str, Any]:
    manifest, _ = await run_in_threadpool(_download, request, session)
    response.headers.update(HEADERS)
    return {
        **manifest,
        "download_url": "/api/member/v1/settings/bridges/helper/download",
    }


@router.get("/api/member/v1/settings/bridges/helper/download")
async def helper_download(request: Request, session: SessionDep) -> Response:
    manifest, payload = await run_in_threadpool(_download, request, session)
    return Response(
        payload,
        media_type="application/zip",
        headers={
            **HEADERS,
            "Content-Disposition": f'attachment; filename="{ARCHIVE}"',
            "X-AdministrateMe-SHA256": manifest["sha256"],
        },
    )
