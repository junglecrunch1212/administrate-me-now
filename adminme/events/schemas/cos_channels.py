"""Audited CoS operating-mode transitions, without contact coordinates."""

from typing import Literal

from pydantic import BaseModel, Field

from adminme.cos_channel_operation import CosChannelActivation
from adminme.events.registry import registry


class CosChannelDeactivatedV1(BaseModel):
    model_config = {"extra": "forbid"}
    activation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    reason: Literal["operation_failed", "operator_requested", "interrupted_operation"]
    operator_principal_id: str = Field(pattern=r"^operator:.+", max_length=240)
    observation_active: Literal[True] = True


registry.register("cos.channels.activated", 1, CosChannelActivation)
registry.register("cos.channels.deactivated", 1, CosChannelDeactivatedV1)
