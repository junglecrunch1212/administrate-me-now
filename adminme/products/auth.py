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
    if (
        not login
        and settings.synthetic_member_mode
        and host in {"127.0.0.1", "::1", "testclient"}
    ):
        # This fallback exists only in an explicitly constructed synthetic
        # runtime. Production settings cannot enable it from environment
        # variables, so the trusted Tailscale ingress contract is unchanged.
        login = settings.synthetic_member_identity
    normalized_login = login.strip().casefold() if isinstance(login, str) else ""
    matches = [
        member
        for identity, member in settings.member_identities.items()
        if identity.casefold() == normalized_login
    ]
    if len(matches) != 1:
        raise HTTPException(401, "missing or unknown Tailscale identity")
    member_id = matches[0]
    role = settings.member_roles.get(member_id)
    if role not in {"principal", "child", "ambient", "device"}:
        raise HTTPException(403, "portal identity has no interactive member role")
    view_member = request.headers.get("X-AdminMe-View-Member") or member_id
    view_role = settings.member_roles.get(view_member)
    if view_role not in {"principal", "child", "ambient", "device"}:
        raise HTTPException(403, "view target is not an interactive household member")
    if view_member != member_id and role != "principal":
        raise HTTPException(403, "only principals may view as another member")
    return Session(
        tenant_id=settings.instance.tenant_id,
        auth_member_id=member_id,
        auth_role=role,
        view_member_id=view_member,
        view_role=view_role,
        dm_scope="per_account_channel_peer",
        source="member_portal",
        correlation_id=request.headers.get("X-Correlation-Id"),
    )


def require_principal(session: Session) -> None:
    if session.auth_role != "principal":
        raise HTTPException(403, "principal authority required")


__all__ = ["require_principal", "session_from_request"]
