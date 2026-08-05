"""Trusted-ingress request identity for product APIs."""

from __future__ import annotations

from fastapi import HTTPException, Request

from adminme.lib.session import Session


def session_from_request(request: Request) -> Session:
    settings = request.app.state.runtime.settings
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(403, "identity header accepted only from trusted ingress")
    login = request.headers.get("Tailscale-User-Login")
    member_id = settings.member_identities.get(login or "")
    if not member_id:
        raise HTTPException(401, "missing or unknown Tailscale identity")
    role = settings.member_roles.get(member_id, "principal")
    view_member = request.headers.get("X-AdminMe-View-Member") or member_id
    view_role = settings.member_roles.get(view_member, "principal")
    if view_member != member_id and role != "principal":
        raise HTTPException(403, "only principals may view as another member")
    return Session(
        tenant_id=settings.instance.tenant_id,
        auth_member_id=member_id,
        auth_role=role,
        view_member_id=view_member,
        view_role=view_role,
        dm_scope="per_channel_peer",
        source="member_portal",
        correlation_id=request.headers.get("X-Correlation-Id"),
    )


def require_principal(session: Session) -> None:
    if session.auth_role != "principal":
        raise HTTPException(403, "principal authority required")


__all__ = ["require_principal", "session_from_request"]
