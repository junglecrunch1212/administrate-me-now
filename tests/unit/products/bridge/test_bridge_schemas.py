"""Unit tests for the ``bridge.enrolled@v1`` schema.

Pins the [D7] additive-v1 shape + ``extra="forbid"`` rule + registry
binding for the schema c1 introduced. Memo 04 §3.2 + parent draft §1
item 3.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from adminme.events.registry import registry
from adminme.events.schemas.bridge import BridgeEnrolledV1


def _valid_payload() -> dict:
    return {
        "member_id": "m1",
        "bridge_node_id": "bridge-node-001",
        "tailscale_identity": "m1@example.ts.net",
        "adapters_active": ["apple-notes", "voice-notes"],
        "enrolled_at": datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc),
    }


def test_registry_returns_bridge_enrolled_v1() -> None:
    """[D7]: ``registry.get("bridge.enrolled", 1)`` resolves to
    :class:`BridgeEnrolledV1`. Other Cohort D consumers depend on
    this binding (e.g. 11d's ``member_knowledge`` projection)."""
    assert registry.get("bridge.enrolled", 1) is BridgeEnrolledV1


def test_valid_payload_round_trips() -> None:
    """Field set per parent draft §1 item 3 is accepted unchanged."""
    payload = _valid_payload()
    model = BridgeEnrolledV1.model_validate(payload)
    assert model.member_id == "m1"
    assert model.bridge_node_id == "bridge-node-001"
    assert model.tailscale_identity == "m1@example.ts.net"
    assert model.adapters_active == ["apple-notes", "voice-notes"]
    assert model.enrolled_at == payload["enrolled_at"]


def test_empty_adapters_active_accepted() -> None:
    """An empty bridge (no adapters reporting yet) is a valid enrollment
    snapshot — the wizard emits ``bridge.enrolled`` once pairing
    completes even if every adapter happens to be in the ``starting``
    state at that millisecond."""
    payload = _valid_payload() | {"adapters_active": []}
    model = BridgeEnrolledV1.model_validate(payload)
    assert model.adapters_active == []


def test_extra_field_rejected() -> None:
    """[D7]: ``extra="forbid"`` keeps the v1 shape closed; v2 would be a
    new registration, never a silent field addition."""
    payload = _valid_payload() | {"unexpected_field": "nope"}
    with pytest.raises(ValidationError) as exc_info:
        BridgeEnrolledV1.model_validate(payload)
    assert "unexpected_field" in str(exc_info.value)


@pytest.mark.parametrize(
    "missing_field",
    [
        "member_id",
        "bridge_node_id",
        "tailscale_identity",
        "adapters_active",
        "enrolled_at",
    ],
)
def test_required_field_missing_rejected(missing_field: str) -> None:
    """All five fields are required per parent draft §1 item 3."""
    payload = _valid_payload()
    del payload[missing_field]
    with pytest.raises(ValidationError):
        BridgeEnrolledV1.model_validate(payload)


@pytest.mark.parametrize(
    "blank_field",
    ["member_id", "bridge_node_id", "tailscale_identity"],
)
def test_blank_string_fields_rejected(blank_field: str) -> None:
    """``min_length=1`` on identifier fields blocks the ``""``-smuggle
    attack at the schema layer (defense in depth alongside the
    Tailscale-header non-empty check in the ingest router)."""
    payload = _valid_payload() | {blank_field: ""}
    with pytest.raises(ValidationError):
        BridgeEnrolledV1.model_validate(payload)
