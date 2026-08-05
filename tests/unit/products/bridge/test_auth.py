"""Unit tests for ``adminme.products.bridge.auth``.

Pins memo 04 §2.2 (Tailscale-User-Login is THE auth seam; identity
non-overridable) + F-3 (HARD) — every authentication failure surfaces
as a 403, never as a silent fallback. CONSOLE_PATTERNS.md §1 governs
header-name lookup (case-insensitive per HTTP spec)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException, Request

from adminme.products.bridge.auth import (
    DictMemberIdResolver,
    TAILSCALE_IDENTITY_HEADER,
    resolve_member_id,
    resolve_tailscale_identity,
)


def _request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ASGI ``Request`` whose ``.headers`` reflect the
    test's input. We hand-build the scope so the tests don't depend on
    FastAPI's full ``TestClient`` lifecycle."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/bridge/ingest",
        "headers": raw_headers,
        "query_string": b"",
        "raw_path": b"/api/bridge/ingest",
    }
    return Request(scope)


def test_resolve_tailscale_identity_returns_header_value() -> None:
    request = _request({TAILSCALE_IDENTITY_HEADER: "m1@example.ts.net"})
    assert resolve_tailscale_identity(request) == "m1@example.ts.net"


def test_resolve_tailscale_identity_missing_header_raises_403() -> None:
    """Memo 04 §2.2: the bridge service has NO password fallback —
    missing identity is a hard 403."""
    with pytest.raises(HTTPException) as exc_info:
        resolve_tailscale_identity(_request({}))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "missing_tailscale_identity"


def test_resolve_tailscale_identity_empty_header_raises_403() -> None:
    """An empty header is treated as missing — defense against ``""``
    smuggling past the resolver."""
    with pytest.raises(HTTPException) as exc_info:
        resolve_tailscale_identity(_request({TAILSCALE_IDENTITY_HEADER: ""}))
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "missing_tailscale_identity"


def test_resolve_tailscale_identity_header_lookup_is_case_insensitive() -> None:
    """HTTP header names are case-insensitive per RFC 7230 §3.2;
    Starlette/FastAPI exposes this via ``Headers.get`` lookups. The
    bridge auth code relies on that — pinning here so a future refactor
    cannot silently switch to a case-sensitive lookup that would 403
    legitimate clients."""
    request = _request({TAILSCALE_IDENTITY_HEADER.lower(): "m1@example.ts.net"})
    assert resolve_tailscale_identity(request) == "m1@example.ts.net"


def test_resolve_member_id_known_identity_returns_member_id() -> None:
    resolver = DictMemberIdResolver({"m1@example.ts.net": "m1"})
    assert resolve_member_id("m1@example.ts.net", resolver) == "m1"


def test_resolve_member_id_unknown_identity_raises_403() -> None:
    """Memo 04 §7(d): a paired-then-unbound identity (or an identity
    that never paired) is a privileged-access violation — 403 only,
    never a silent fallback to a default member."""
    resolver = DictMemberIdResolver({"m1@example.ts.net": "m1"})
    with pytest.raises(HTTPException) as exc_info:
        resolve_member_id("intruder@example.ts.net", resolver)
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "unknown_tailscale_identity"


def test_resolve_member_id_blank_member_id_raises_403() -> None:
    """A resolver that returns ``""`` for some reason (e.g. stale
    projection row) is still a privileged-access violation — never
    silently let an empty member_id stamp owner_scope."""

    class BadResolver:
        def resolve(self, tailscale_identity: str) -> str:
            return ""

    with pytest.raises(HTTPException) as exc_info:
        resolve_member_id("anything@example.ts.net", BadResolver())
    assert exc_info.value.status_code == 403
