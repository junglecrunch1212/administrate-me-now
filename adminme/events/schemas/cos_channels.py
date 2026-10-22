"""Audited CoS operating-mode transitions, without contact coordinates."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from adminme.cos_channel_operation import CosChannelActivation, CosChannelActivationV2
from adminme.events.registry import registry


class CosChannelDeactivatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    reason: Literal["operation_failed", "operator_requested", "interrupted_operation"]
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    observation_active: Literal[True] = True


class CosChannelDeactivatedV2(CosChannelDeactivatedV1):
    # Preserve the registered v1 contract and its historical reasons unchanged.
    reason: Literal[  # type: ignore[assignment]
        "operation_failed",
        "operator_requested",
        "interrupted_operation",
        "deployment_observation",
    ]


def upcast_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    """Historical deactivations keep their original reason and identity."""
    return dict(payload)


registry.register("cos.channels.activated", 1, CosChannelActivation)
registry.register("cos.channels.activated", 2, CosChannelActivationV2)
registry.register_upcaster(
    "cos.channels.activated", 1, lambda payload: {**payload, "self_reply_bindings": []}
)
registry.register("cos.channels.deactivated", 1, CosChannelDeactivatedV1)
registry.register("cos.channels.deactivated", 2, CosChannelDeactivatedV2)
registry.register_upcaster("cos.channels.deactivated", 1, upcast_v1_to_v2)
