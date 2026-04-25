"""
Unit tests for adminme.lib.scope.

Walks the DIAGRAMS.md §5 multi-dimensional matrix (auth_role × sensitivity ×
owner_scope) plus the privacy-filter, coach-strip, and child-tag-filter
predicates per CONSOLE_PATTERNS.md §§6-7.
"""

from __future__ import annotations

import pytest

from adminme.lib.scope import (
    CHILD_FORBIDDEN_TAGS,
    ScopeViolation,
    allowed_read,
    child_hidden_tag_filter,
    coach_column_strip,
    privacy_filter,
)
from adminme.lib.session import Session, build_internal_session


def _principal(member_id: str = "p_a") -> Session:
    return build_internal_session(member_id, "principal", "tenant-a")


def _child(member_id: str = "k_c") -> Session:
    return build_internal_session(member_id, "child", "tenant-a")


def _device() -> Session:
    return build_internal_session("dev_tv", "device", "tenant-a")


def _coach() -> Session:
    return build_internal_session("coach_a", "coach_session", "tenant-a")


def _ambient() -> Session:
    # build_internal_session refuses ambient — construct directly.
    return Session(
        tenant_id="tenant-a",
        auth_member_id="amb_x",
        auth_role="ambient",
        view_member_id="amb_x",
        view_role="ambient",
        dm_scope="shared",
        source="product_api_internal",
    )


# ---------------------------------------------------------------------------
# allowed_read — DIAGRAMS.md §5 matrix
# ---------------------------------------------------------------------------


def test_principal_normal_household_allowed() -> None:
    assert allowed_read(_principal(), "normal", "shared:household") is True


def test_principal_normal_self_allowed() -> None:
    assert allowed_read(_principal("p_a"), "normal", "private:p_a") is True


def test_principal_normal_other_principal_denied() -> None:
    assert allowed_read(_principal("p_a"), "normal", "private:p_b") is False


def test_principal_sensitive_household_allowed() -> None:
    assert allowed_read(_principal(), "sensitive", "shared:household") is True


def test_principal_sensitive_self_allowed() -> None:
    assert allowed_read(_principal("p_a"), "sensitive", "private:p_a") is True


def test_principal_sensitive_other_principal_denied() -> None:
    assert allowed_read(_principal("p_a"), "sensitive", "private:p_b") is False


def test_principal_privileged_household_denied() -> None:
    """Privileged content owned at household scope is not actually a
    legal combination, but the predicate must refuse it [§6.9]."""
    assert allowed_read(_principal(), "privileged", "shared:household") is False


def test_principal_privileged_self_allowed() -> None:
    """Owner of privileged content always reads [§6.9]."""
    assert allowed_read(_principal("p_a"), "privileged", "private:p_a") is True


def test_principal_privileged_other_denied() -> None:
    """Other principal's privileged content is refused at allowed_read.
    The calendar busy-block path goes through privacy_filter separately."""
    assert allowed_read(_principal("p_a"), "privileged", "private:p_b") is False


# Child rows ---------------------------------------------------------------


def test_child_normal_household_allowed() -> None:
    """Tag-based filtering happens separately in child_hidden_tag_filter."""
    assert allowed_read(_child(), "normal", "shared:household") is True


def test_child_normal_self_allowed() -> None:
    assert allowed_read(_child("k_c"), "normal", "private:k_c") is True


def test_child_normal_other_denied() -> None:
    assert allowed_read(_child("k_c"), "normal", "private:p_a") is False


def test_child_sensitive_anything_denied() -> None:
    for owner in ("shared:household", "private:k_c", "private:p_a"):
        assert allowed_read(_child("k_c"), "sensitive", owner) is False


def test_child_privileged_anything_denied() -> None:
    for owner in ("shared:household", "private:k_c", "private:p_a"):
        assert allowed_read(_child("k_c"), "privileged", owner) is False


# Ambient rows -------------------------------------------------------------


def test_ambient_anything_denied() -> None:
    sess = _ambient()
    for sens in ("normal", "sensitive", "privileged"):
        for owner in ("shared:household", "private:amb_x", "private:p_a"):
            assert allowed_read(sess, sens, owner) is False


# Device rows --------------------------------------------------------------


def test_device_normal_household_allowed() -> None:
    assert allowed_read(_device(), "normal", "shared:household") is True


def test_device_normal_private_denied() -> None:
    """Device sees household-shared only — never private rows."""
    assert allowed_read(_device(), "normal", "private:dev_tv") is False
    assert allowed_read(_device(), "normal", "private:p_a") is False


def test_device_sensitive_or_higher_denied() -> None:
    for sens in ("sensitive", "privileged"):
        assert allowed_read(_device(), sens, "shared:household") is False


# Coach session ------------------------------------------------------------


def test_coach_normal_household_allowed() -> None:
    """Coach has the same visibility-scope axis as principal at normal /
    sensitive levels; column-strip happens at coach_column_strip."""
    assert allowed_read(_coach(), "normal", "shared:household") is True


def test_coach_privileged_self_allowed_but_stripped_downstream() -> None:
    """allowed_read returns True for coach-as-owner because coach is the
    auth_member; the privileged-content carve-out for coach happens at
    [§6.9]'s "never appear in coach sessions" rule, enforced in the
    pipeline that builds the coach context — not in this predicate."""
    s = build_internal_session("coach_a", "coach_session", "tenant-a")
    assert allowed_read(s, "privileged", "private:coach_a") is True


# ---------------------------------------------------------------------------
# privacy_filter — CONSOLE_PATTERNS.md §6
# ---------------------------------------------------------------------------


def _calendar_row(
    *,
    sensitivity: str = "privileged",
    owner: str = "p_b",
    summary: str = "Client X re: settlement",
    location: str = "Conference Room 5",
    description: str = "Counsel meeting; privileged",
) -> dict:
    return {
        "calendar_event_id": "ce_1",
        "tenant_id": "tenant-a",
        "owner_party": owner,
        "owner_scope": f"private:{owner}",
        "visibility_scope": f"private:{owner}",
        "sensitivity": sensitivity,
        "summary": summary,
        "description": description,
        "location": location,
        "start_at": "2026-04-25T15:00:00Z",
        "end_at": "2026-04-25T16:00:00Z",
        "all_day": 0,
        "attendees_json": "[]",
        "calendar_source": "google",
        "last_event_id": "ev_1",
    }


def test_privacy_filter_passthrough_normal() -> None:
    sess = _principal("p_a")
    row = _calendar_row(sensitivity="normal", owner="p_a")
    assert privacy_filter(sess, row) is row


def test_privacy_filter_passthrough_sensitive() -> None:
    sess = _principal("p_a")
    row = _calendar_row(sensitivity="sensitive", owner="p_b")
    # Sensitive is not collapsed by privacy_filter — sensitive is the
    # allowed_read gate's job. privacy_filter only redacts privileged.
    assert privacy_filter(sess, row) is row


def test_privacy_filter_owner_sees_full_content() -> None:
    sess = _principal("p_b")
    row = _calendar_row(sensitivity="privileged", owner="p_b")
    out = privacy_filter(sess, row)
    assert out["summary"] == "Client X re: settlement"
    assert out["description"] == "Counsel meeting; privileged"


def test_privacy_filter_non_owner_redacts_to_busy_block() -> None:
    sess = _principal("p_a")
    row = _calendar_row(sensitivity="privileged", owner="p_b")
    out = privacy_filter(sess, row)
    assert out["title"] == "[busy]"
    assert out["kind"] == "busy_block"
    assert out["display_hint"] == "privileged"
    assert out["start_at"] == "2026-04-25T15:00:00Z"
    assert out["end_at"] == "2026-04-25T16:00:00Z"
    # Owner hint preserved per CONSOLE_PATTERNS §6 default.
    assert out["owner_hint"] == "p_b"


def test_privacy_filter_redaction_drops_content_fields() -> None:
    """The allowlist shape must NOT leak description, location, or
    summary [CONSOLE_PATTERNS.md §6 'allowlist not blocklist']."""
    sess = _principal("p_a")
    row = _calendar_row(
        sensitivity="privileged",
        owner="p_b",
        summary="leaky title",
        location="leaky location",
        description="leaky body",
    )
    out = privacy_filter(sess, row)
    assert "description" not in out
    assert "location" not in out
    assert "summary" not in out
    # Spot-check that even attendees_json is dropped.
    assert "attendees_json" not in out


def test_privacy_filter_view_as_uses_auth_member_for_privileged_check() -> None:
    """[CONSOLE_PATTERNS.md §2] When principal A views principal B's
    surface, privileged content owned by B still redacts because the
    'who is reading' check uses authMember (A), not viewMember (B)."""
    from dataclasses import dataclass
    from adminme.lib.session import build_session_from_node

    @dataclass
    class _Req:
        identity: dict
        view_as: dict | None
        correlation_id: str | None = None

    @dataclass
    class _Cfg:
        tenant_id: str = "tenant-a"

    req = _Req(
        identity={"member_id": "p_a", "role": "principal", "tailscale_login": "a@x"},
        view_as={"member_id": "p_b", "role": "principal"},
    )
    sess = build_session_from_node(req, _Cfg())
    row = _calendar_row(sensitivity="privileged", owner="p_b")
    out = privacy_filter(sess, row)
    assert out["title"] == "[busy]"
    assert "summary" not in out


# ---------------------------------------------------------------------------
# coach_column_strip — DIAGRAMS.md §5 / [§13]
# ---------------------------------------------------------------------------


def test_coach_column_strip_drops_financial_columns() -> None:
    row = {
        "id": "x",
        "title": "rent",
        "financial_amount_minor": 200000,
        "financial_account_last4": "1234",
    }
    out = coach_column_strip(row)
    assert "financial_amount_minor" not in out
    assert "financial_account_last4" not in out
    assert out["title"] == "rent"


def test_coach_column_strip_drops_health_columns() -> None:
    row = {
        "id": "x",
        "title": "checkup",
        "health_provider": "Dr. Y",
        "health_diagnosis_code": "Z00.00",
    }
    out = coach_column_strip(row)
    assert "health_provider" not in out
    assert "health_diagnosis_code" not in out
    assert out["title"] == "checkup"


def test_coach_column_strip_passes_through_neutral_columns() -> None:
    row = {"id": "x", "title": "t", "owner_scope": "shared:household"}
    out = coach_column_strip(row)
    assert out == row


# ---------------------------------------------------------------------------
# child_hidden_tag_filter — CONSOLE_PATTERNS.md §6 / §7
# ---------------------------------------------------------------------------


def test_child_hidden_tag_filter_drops_finance_tag() -> None:
    sess = _child()
    row = {"id": "t1", "tags": "finance,errands"}
    assert child_hidden_tag_filter(sess, row) is False


def test_child_hidden_tag_filter_drops_health_tag() -> None:
    sess = _child()
    row = {"id": "t1", "tags_json": '["health", "x"]'}
    assert child_hidden_tag_filter(sess, row) is False


def test_child_hidden_tag_filter_drops_legal_tag() -> None:
    sess = _child()
    row = {"id": "t1", "tags": "legal"}
    assert child_hidden_tag_filter(sess, row) is False


def test_child_hidden_tag_filter_drops_adult_only_tag() -> None:
    sess = _child()
    row = {"id": "t1", "tags": ["adult_only"]}
    assert child_hidden_tag_filter(sess, row) is False


def test_child_hidden_tag_filter_keeps_neutral_tags() -> None:
    sess = _child()
    row = {"id": "t1", "tags": "errands,school"}
    assert child_hidden_tag_filter(sess, row) is True


def test_child_hidden_tag_filter_keeps_no_tags() -> None:
    sess = _child()
    row = {"id": "t1"}
    assert child_hidden_tag_filter(sess, row) is True


def test_child_hidden_tag_filter_principal_passes_through() -> None:
    sess = _principal()
    row = {"id": "t1", "tags": "finance"}
    assert child_hidden_tag_filter(sess, row) is True


def test_child_forbidden_tags_const_immutable_set() -> None:
    """The constant 14a's middleware will also consume must be a frozenset
    so neither side can mutate the other [CONSOLE_PATTERNS.md §7]."""
    assert isinstance(CHILD_FORBIDDEN_TAGS, frozenset)
    expected = {"finance", "health", "legal", "adult_only"}
    assert CHILD_FORBIDDEN_TAGS == expected


# ---------------------------------------------------------------------------
# ScopeViolation canary
# ---------------------------------------------------------------------------


def test_scope_violation_is_exception() -> None:
    assert issubclass(ScopeViolation, Exception)


def test_scope_violation_can_be_raised_and_caught() -> None:
    with pytest.raises(ScopeViolation, match="canary"):
        raise ScopeViolation("canary")
