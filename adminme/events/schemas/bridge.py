"""
L1-bridge canonical event family — emitted by the bridge daemon's bootstrap
mini-wizard once a member's bridge Mac Mini has paired with the central
service. ``adapters_active`` is retained for wire compatibility but is
non-authorizing telemetry. Current reviewed enrollment emits it empty; a
separate capability contract is required before provider events or commands.

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
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from adminme.bridge_source_contract import validate_member_source_coordinates

from adminme.events.registry import registry


class BridgeEnrolledV1(BaseModel):
    model_config = {"extra": "forbid"}

    member_id: str = Field(min_length=1)
    bridge_node_id: str = Field(min_length=1)
    tailscale_identity: str = Field(min_length=1)
    adapters_active: list[str] = Field(
        description=(
            "Non-authorizing compatibility telemetry. Current transport-only "
            "enrollment emits an empty list and makes no provider claim."
        ),
    )
    enrolled_at: datetime


registry.register("bridge.enrolled", 1, BridgeEnrolledV1)


class BridgeSourceAuthorizedV1(BaseModel):
    """Frozen private source coordinates, not permission for provider writes."""

    model_config = {"extra": "forbid"}

    contract: Literal["cos-member-source-observation-v1"]
    request_id: str = Field(min_length=8, max_length=120)
    enrollment_id: str = Field(min_length=1, max_length=160)
    member_id: str = Field(min_length=1, max_length=240)
    instance_id: str = Field(min_length=1, max_length=240)
    release_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    capability_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tailscale_identity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_id: Literal["apple_calendar", "apple_reminders", "apple_notes"]
    pack: str = Field(min_length=1)
    pack_config: dict[str, Any]
    enrollment_event_id: str = Field(min_length=1)
    authentication_event_id: str | None = None
    progress_idempotency_key: str = Field(min_length=1, max_length=240)
    observation_only: Literal[True]
    provider_writes: Literal["deny"]
    content_exposed: Literal[False]

    @model_validator(mode="after")
    def content_free_coordinates(self) -> BridgeSourceAuthorizedV1:
        validate_member_source_coordinates(self.pack, self.pack_config)
        return self


registry.register("bridge.source_authorized", 1, BridgeSourceAuthorizedV1)


__all__ = ["BridgeEnrolledV1", "BridgeSourceAuthorizedV1"]
