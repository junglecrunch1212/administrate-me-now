"""
Unit tests for adminme.lib.session.

Covers the DIAGRAMS.md §4 view-as blocking matrix end-to-end plus the three
constructors (node-console / openclaw / internal) per BUILD.md L3-continued
and CONSOLE_PATTERNS.md §§1-2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from adminme.lib.session import (
    AuthError,
    Session,
    build_internal_session,
    build_session_from_node,
    build_session_from_openclaw,
)


@dataclass
class _FakeReq:
    identity: dict[str, Any] | None = None
    view_as: dict[str, Any] | None = None
    correlation_id: str | None = None


@dataclass
class _FakeOpenclawReq:
    invoking_member_id: str | None = None
    invoking_role: str | None = None
    kind: str | None = None
    correlation_id: str | None = None


@dataclass
class _FakeConfig:
    tenant_id: str = "tenant-a"


# ---------------------------------------------------------------------------
# build_session_from_node — DIAGRAMS.md §4 view-as matrix
# ---------------------------------------------------------------------------


def test_principal_views_self_fast_path() -> None:
    """Principal viewing their own surface — viewMember == authMember."""
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
    )
    s = build_session_from_node(req, _FakeConfig())
    assert s.auth_member_id == "p_a"
    assert s.view_member_id == "p_a"
    assert not s.is_view_as
    assert s.auth_role == "principal"
    assert s.source == "node_console"


def test_principal_views_other_principal_allowed() -> None:
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "p_b", "role": "principal"},
    )
    s = build_session_from_node(req, _FakeConfig())
    assert s.is_view_as
    assert s.auth_member_id == "p_a"
    assert s.view_member_id == "p_b"
    assert s.view_role == "principal"


def test_principal_views_child_allowed() -> None:
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "k_c", "role": "child"},
    )
    s = build_session_from_node(req, _FakeConfig())
    assert s.view_member_id == "k_c"
    assert s.view_role == "child"
    assert s.auth_role == "principal"


def test_principal_views_ambient_rejected() -> None:
    """Ambient entities have no surface to view [DIAGRAMS.md §4]."""
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "amb_x", "role": "ambient"},
    )
    with pytest.raises(AuthError, match="view_target_has_no_surface"):
        build_session_from_node(req, _FakeConfig())


def test_child_view_as_anyone_rejected() -> None:
    """Children cannot view-as regardless of target [§6.2, DIAGRAMS.md §4]."""
    req = _FakeReq(
        identity={"member_id": "k_c", "role": "child", "tailscale_login": "c@x"},
        view_as={"member_id": "p_a", "role": "principal"},
    )
    with pytest.raises(AuthError, match="only_principals_can_view_as"):
        build_session_from_node(req, _FakeConfig())


def test_ambient_construction_rejected_via_node() -> None:
    """Ambient sessions have no console surface — node bridge accepts the
    construction (the nav middleware refuses the request)."""
    req = _FakeReq(
        identity={"member_id": "amb_x", "role": "ambient", "tailscale_login": "amb"},
    )
    s = build_session_from_node(req, _FakeConfig())
    # Construction succeeds but allowed_scopes is empty — defense-in-depth.
    assert s.auth_role == "ambient"
    assert s.allowed_scopes == frozenset()


def test_coach_session_cannot_view_as() -> None:
    """Coach sessions are LLM/external context builders; view-as is a UI
    concept that does not apply [DIAGRAMS.md §5]."""
    req = _FakeReq(
        identity={"member_id": "coach_a", "role": "coach_session", "tailscale_login": "n/a"},
        view_as={"member_id": "p_b", "role": "principal"},
    )
    with pytest.raises(AuthError, match="only_principals_can_view_as"):
        build_session_from_node(req, _FakeConfig())


def test_missing_identity_raises_auth_error() -> None:
    req = _FakeReq(identity=None)
    with pytest.raises(AuthError, match="missing_identity"):
        build_session_from_node(req, _FakeConfig())


def test_malformed_identity_missing_role_raises() -> None:
    req = _FakeReq(identity={"member_id": "p_a"})
    with pytest.raises(AuthError, match="malformed_identity"):
        build_session_from_node(req, _FakeConfig())


def test_invalid_role_raises() -> None:
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "wizard", "tailscale_login": "a@x"},
    )
    with pytest.raises(AuthError, match="invalid_auth_role"):
        build_session_from_node(req, _FakeConfig())


def test_missing_tenant_id_raises() -> None:
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
    )

    @dataclass
    class _NoTenant:
        tenant_id: str = ""

    with pytest.raises(AuthError, match="missing_tenant_id"):
        build_session_from_node(req, _NoTenant())


def test_correlation_id_propagates_through_view_as() -> None:
    """Correlation id is attached at construction and carried through view-as
    [§6, D8]."""
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "p_b", "role": "principal"},
        correlation_id="cor_xyz",
    )
    s = build_session_from_node(req, _FakeConfig())
    assert s.correlation_id == "cor_xyz"
    assert s.is_view_as


# ---------------------------------------------------------------------------
# Session.allowed_scopes
# ---------------------------------------------------------------------------


def test_allowed_scopes_principal_self_view() -> None:
    s = build_internal_session("p_a", "principal", "tenant-a")
    assert "shared:household" in s.allowed_scopes
    assert "private:p_a" in s.allowed_scopes
    assert "org:*" in s.allowed_scopes


def test_allowed_scopes_principal_view_as_other_carries_view_member() -> None:
    """view-as means we read the viewMember's private scope; the privileged
    check still consults the auth member [CONSOLE_PATTERNS.md §2]."""
    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "p_b", "role": "principal"},
    )
    s = build_session_from_node(req, _FakeConfig())
    assert "private:p_b" in s.allowed_scopes
    assert "shared:household" in s.allowed_scopes


def test_allowed_scopes_child() -> None:
    s = build_internal_session("k_c", "child", "tenant-a")
    assert "shared:household" in s.allowed_scopes
    assert "private:k_c" in s.allowed_scopes
    # Children do not get org:* [DIAGRAMS.md §5].
    assert "org:*" not in s.allowed_scopes


def test_allowed_scopes_ambient_empty() -> None:
    """Ambient: no surface at all [§6.2, DIAGRAMS.md §5]."""
    # build_internal_session refuses ambient; build directly.
    s = Session(
        tenant_id="tenant-a",
        auth_member_id="amb_x",
        auth_role="ambient",
        view_member_id="amb_x",
        view_role="ambient",
        dm_scope="shared",
        source="product_api_internal",
    )
    assert s.allowed_scopes == frozenset()


def test_allowed_scopes_device_household_only() -> None:
    """Device (e.g. scoreboard TV): household-shared only [DIAGRAMS.md §5]."""
    s = build_internal_session("dev_tv", "device", "tenant-a")
    assert "shared:household" in s.allowed_scopes
    # Device gets a private:dev_tv slot but no org:* [DIAGRAMS.md §5].
    assert "org:*" not in s.allowed_scopes


def test_is_view_as_property() -> None:
    self_s = build_internal_session("p_a", "principal", "tenant-a")
    assert not self_s.is_view_as

    req = _FakeReq(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "p_b", "role": "principal"},
    )
    other_s = build_session_from_node(req, _FakeConfig())
    assert other_s.is_view_as


def test_session_is_frozen() -> None:
    s = build_internal_session("p_a", "principal", "tenant-a")
    with pytest.raises((AttributeError, TypeError)):
        s.auth_member_id = "p_b"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# build_session_from_openclaw
# ---------------------------------------------------------------------------


def test_openclaw_slash_command_session() -> None:
    req = _FakeOpenclawReq(
        invoking_member_id="p_a",
        invoking_role="principal",
        kind="slash_command",
        correlation_id="cor_oc",
    )
    s = build_session_from_openclaw(req, _FakeConfig())
    assert s.source == "openclaw_slash_command"
    assert s.auth_member_id == "p_a"
    assert s.correlation_id == "cor_oc"


def test_openclaw_standing_order_session() -> None:
    req = _FakeOpenclawReq(
        invoking_member_id="standing",
        invoking_role="principal",
        kind="standing_order",
    )
    s = build_session_from_openclaw(req, _FakeConfig())
    assert s.source == "openclaw_standing_order"


def test_openclaw_invalid_kind_raises() -> None:
    req = _FakeOpenclawReq(
        invoking_member_id="p_a",
        invoking_role="principal",
        kind="cron",
    )
    with pytest.raises(AuthError, match="invalid_openclaw_kind"):
        build_session_from_openclaw(req, _FakeConfig())


def test_openclaw_malformed_request_raises() -> None:
    req = _FakeOpenclawReq()
    with pytest.raises(AuthError, match="malformed_openclaw_request"):
        build_session_from_openclaw(req, _FakeConfig())


# ---------------------------------------------------------------------------
# build_internal_session
# ---------------------------------------------------------------------------


def test_internal_session_xlsx_workbooks_source() -> None:
    """Forward xlsx builders construct via build_internal_session per the
    08a carry-forward [§10]."""
    s = build_internal_session("xlsx_workbooks", "device", "tenant-a")
    assert s.source == "xlsx_workbooks"
    assert s.auth_member_id == "xlsx_workbooks"
    assert s.auth_role == "device"
    assert s.tenant_id == "tenant-a"


def test_internal_session_xlsx_reverse_source() -> None:
    """Reverse daemon source-name path; UT-7 closure (08b) wires it in."""
    s = build_internal_session("xlsx_reverse", "device", "tenant-a")
    assert s.source == "xlsx_reverse_daemon"


def test_internal_session_bootstrap_source() -> None:
    s = build_internal_session("bootstrap", "device", "tenant-a")
    assert s.source == "bootstrap_wizard"


def test_internal_session_default_product_api_internal() -> None:
    s = build_internal_session("skill_runner", "device", "tenant-a")
    assert s.source == "product_api_internal"


def test_internal_session_ambient_rejected() -> None:
    with pytest.raises(AuthError, match="ambient_cannot_act_internally"):
        build_internal_session("amb_x", "ambient", "tenant-a")


def test_internal_session_invalid_role() -> None:
    with pytest.raises(AuthError, match="invalid_role"):
        build_internal_session("x", "wizard", "tenant-a")


def test_internal_session_correlation_id_optional() -> None:
    s = build_internal_session("a", "device", "tenant-a", correlation_id="cor_1")
    assert s.correlation_id == "cor_1"
    s2 = build_internal_session("a", "device", "tenant-a")
    assert s2.correlation_id is None
