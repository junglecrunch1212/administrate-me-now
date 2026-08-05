"""
L1-bridge canonical event family — emitted by the bridge daemon's bootstrap
mini-wizard once a member's bridge Mac Mini has paired with the central
service. ``adapters_active`` may be empty for the transport-only enrollment
proof; it lists only packs already configured and healthy at that moment.

Per ADMINISTRATEME_BUILD.md §MEMBER BRIDGES, SYSTEM_INVARIANTS.md §1
invariants 3 + 9 (event log append-only, typed registry) + §6.19 (bridge
sovereignty: bridge devices emit canonical events; never write projections
directly), DECISIONS.md §D7 (schemas immutable at v1; this module ADDS a
new v1 schema), §D17 (L1-bridge canonical shape; adapters never write
projections — bridges emit canonical events that downstream projections
consume), memo 04 §3.2 (bridge.enrolled@v1 emission contract from the
bootstrap mini-wizard, post-pairing).

Emission lives downstream in the bootstrap mini-wizard (16-§10); 11c-i
registers the schema only so the central :3330 bridge ingest namespace can
validate the envelope shape end-to-end and so downstream consumers
(11d member_knowledge projection) can subscribe by canonical name.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from adminme.events.registry import registry


class BridgeEnrolledV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    bridge_node_id: str = Field(min_length=1)
    tailscale_identity: str = Field(min_length=1)
    adapters_active: list[str] = Field(
        description=(
            "Bridge packs reporting healthy at enrollment time; empty is a "
            "valid transport-only enrollment and makes no provider claim."
        ),
    )
    enrolled_at: datetime


registry.register("bridge.enrolled", 1, BridgeEnrolledV1)


__all__ = ["BridgeEnrolledV1"]
