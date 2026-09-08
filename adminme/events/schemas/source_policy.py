"""Event contracts for exact source policy and shadow-only routing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from adminme.events.registry import registry
from adminme.source_policy import (
    AcquisitionEvidence,
    RouteDecision,
    SignalAssessment,
    SourceCoordinate,
    SourcePolicyV2,
)


class SettingsSourcePolicyV2ChangedV1(BaseModel):
    model_config = {"extra": "forbid"}

    owner_principal_id: str = Field(min_length=1, max_length=500)
    owner_kind: Literal["member", "service_principal"]
    policy_key: str = Field(pattern=r"^spv2_[0-9a-f]{64}$")
    coordinate: SourceCoordinate
    action: Literal["set", "reset"]
    policy: SourcePolicyV2 | None = None
    revision: int = Field(ge=1)
    changed_by_principal_id: str = Field(min_length=1, max_length=500)
    changed_at: str = Field(min_length=1)
    scope_widening: Literal[False] = False
    credential_creation: Literal[False] = False
    provider_write_authority: Literal[False] = False
    live_admission_changed: Literal[False] = False

    @model_validator(mode="after")
    def validate_change(self) -> SettingsSourcePolicyV2ChangedV1:
        if self.coordinate.key != self.policy_key:
            raise ValueError("policy key does not match the exact source coordinate")
        if self.coordinate.owner_principal_id != self.owner_principal_id:
            raise ValueError("policy owner does not match source owner")
        if self.coordinate.owner_kind != self.owner_kind:
            raise ValueError("policy owner kind does not match source owner kind")
        if (self.action == "set") != (self.policy is not None):
            raise ValueError("set requires policy and reset forbids policy")
        return self


class SourcePolicyV2ShadowEvaluatedV1(BaseModel):
    model_config = {"extra": "forbid"}

    source_event_id: str = Field(min_length=1, max_length=240)
    policy_version: Literal["source-policy-v2"]
    policy_key: str = Field(pattern=r"^spv2_[0-9a-f]{64}$")
    coordinate: SourceCoordinate
    coordinate_state: Literal["exact", "ambiguous"]
    policy_revision: int = Field(ge=0)
    policy: SourcePolicyV2
    policy_origin: Literal["exact_override", "recommended_default", "migration_needed"]
    migration_state: Literal["not_required", "migration_needed", "resolved"]
    acquisition: AcquisitionEvidence
    signals: SignalAssessment
    current_route: RouteDecision
    shadow_route: RouteDecision
    reason_codes: list[str] = Field(min_length=1, max_length=40)
    evaluated_at: str = Field(min_length=1)
    live_admission_changed: Literal[False] = False
    provider_effects: Literal[False] = False
    llm_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_coordinate(self) -> SourcePolicyV2ShadowEvaluatedV1:
        if self.coordinate.key != self.policy_key:
            raise ValueError("shadow policy key does not match source coordinate")
        return self


class SettingsSourcePolicyV2ChangedV2(SettingsSourcePolicyV2ChangedV1):
    """An exact attention preference may affect local reconciliation only."""

    live_admission_changed: bool = False  # type: ignore[assignment]


registry.register(
    "settings.source_policy_v2_changed", 1, SettingsSourcePolicyV2ChangedV1
)
registry.register(
    "settings.source_policy_v2_changed", 2, SettingsSourcePolicyV2ChangedV2
)
registry.register(
    "source.policy_v2.shadow_evaluated", 1, SourcePolicyV2ShadowEvaluatedV1
)


__all__: list[str] = []
