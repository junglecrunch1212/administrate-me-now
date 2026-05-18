"""
Bridge ingest auth — Tailscale-identity resolution + member binding.

Per memo 04 §2.2 (Tailscale-User-Login is the only auth seam; identity
binds ``owner_scope`` non-overridably) and CONSOLE_PATTERNS.md §1 (header
name + case-insensitive lookup). [§9.5] no password fallback.
[§6.19] bridge sovereignty enforces identity at the physical layer.

``MemberIdResolver`` is a Protocol so production wires the real
``party_tailscale_binding`` projection while tests inject a fixture
mapping per F-8.
"""

from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException, Request, status

TAILSCALE_IDENTITY_HEADER = "Tailscale-User-Login"


class MemberIdResolver(Protocol):
    """Resolve a Tailscale identity to a household member_id. Returns
    ``None`` for unknown identities; the caller raises HTTP 403."""

    def resolve(self, tailscale_identity: str) -> str | None: ...


class DictMemberIdResolver:
    """In-memory ``MemberIdResolver`` keyed by tailscale identity.

    Used by tests and by the bootstrap mini-wizard before the
    ``party_tailscale_binding`` projection lands on main (F-8)."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = dict(mapping)

    def resolve(self, tailscale_identity: str) -> str | None:
        return self._mapping.get(tailscale_identity)


def resolve_tailscale_identity(request: Request) -> str:
    """Extract ``Tailscale-User-Login`` from the request. Missing or
    empty → HTTP 403 (an empty header is treated as missing to prevent
    ``""`` from smuggling past the resolver)."""
    value = request.headers.get(TAILSCALE_IDENTITY_HEADER)
    if not value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="missing_tailscale_identity",
        )
    return value


def resolve_member_id(
    tailscale_identity: str,
    resolver: MemberIdResolver,
) -> str:
    """Map ``tailscale_identity`` → ``member_id``. Unknown identity →
    HTTP 403 per memo 04 §7(d) (privileged-access violation, not soft
    fallback)."""
    member_id = resolver.resolve(tailscale_identity)
    if not member_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="unknown_tailscale_identity",
        )
    return member_id


__all__ = [
    "DictMemberIdResolver",
    "MemberIdResolver",
    "TAILSCALE_IDENTITY_HEADER",
    "resolve_member_id",
    "resolve_tailscale_identity",
]
