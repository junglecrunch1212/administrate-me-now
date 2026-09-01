"""Provider-neutral direct-to-CoS routing decisions.

The source observation remains service-private.  This event records only the
trusted route decision and whether a minimum-content member Triage cue was
created.  It is never provider authority or an external command.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry


class DirectIntakeRoutedV1(BaseModel):
    model_config = {"extra": "forbid"}

    source_event_id: str = Field(min_length=1, max_length=240)
    channel_id: str = Field(min_length=1, max_length=240)
    service_principal_id: str = Field(min_length=1, max_length=240)
    route_source: Literal["exact_authenticated_metadata"]
    route_state: Literal["resolved", "unresolved"]
    routing_scope: Literal[
        "member_private", "household_shared", "external_contact", "unresolved"
    ]
    routing_target_id: str | None = Field(default=None, min_length=1, max_length=240)
    cue_kind: Literal["primary_triage_cue", "none"]
    cue_event_id: str | None = Field(default=None, min_length=1, max_length=240)
    authority: Literal["proposal_only", "external_contact_not_instruction", "none"]
    provider_effect_permitted: Literal[False] = False
    source_content_exposed: Literal[False] = False

    @model_validator(mode="after")
    def _validate_route_result(self) -> "DirectIntakeRoutedV1":
        requires_target = self.routing_scope in {
            "member_private",
            "external_contact",
        }
        if requires_target != (self.routing_target_id is not None):
            raise ValueError("direct intake target contradicts routing scope")
        if self.routing_scope == "member_private":
            if self.cue_kind != "primary_triage_cue" or self.cue_event_id is None:
                raise ValueError("member route requires exactly one primary cue")
            if self.authority != "proposal_only" or self.route_state != "resolved":
                raise ValueError("member route must remain a resolved proposal")
        elif self.cue_kind != "none" or self.cue_event_id is not None:
            raise ValueError("non-member route cannot create a member cue")
        if self.routing_scope == "external_contact" and self.authority != (
            "external_contact_not_instruction"
        ):
            raise ValueError("external contacts are never member instructions")
        if self.routing_scope == "unresolved" and self.route_state != "unresolved":
            raise ValueError("unresolved route must fail closed")
        return self


registry.register("direct_intake.routed", 1, DirectIntakeRoutedV1)
