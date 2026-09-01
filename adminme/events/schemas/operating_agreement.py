"""Structured, event-sourced operating agreements for the household CoS.

Source policies remain a separate intake-policy family.  These schemas model
only household operating authority.  G06 deliberately fixes provider
execution to ``False``; later execution work must pass the independent
provider-governance loop rather than widening an agreement in place.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from adminme.events.registry import registry


AgreementLayer = Literal[
    "shared_household",
    "member_private",
    "exception",
]
AgreementAuthorityScope = Literal["household_shared", "member_private"]
AgreementQuorum = Literal["member_owner", "all_adult_principals"]
TrustStage = Literal[
    "observe",
    "suggest",
    "draft",
    "prepare_or_hold",
    "execute_with_exact_approval",
    "supervised_trial",
    "standing_bounded_authority",
    "permanent_ceiling_or_refusal",
]


class AgreementCeilingsV1(BaseModel):
    model_config = {"extra": "forbid"}

    financial_micros: int | None = Field(default=None, ge=0)
    maximum_duration_minutes: int | None = Field(default=None, ge=0)
    maximum_frequency_count: int | None = Field(default=None, ge=0)
    frequency_period: Literal["occurrence", "day", "week", "month"] | None = None
    maximum_quantity: int | None = Field(default=None, ge=0)
    permitted_destination_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_frequency(self) -> AgreementCeilingsV1:
        if (self.maximum_frequency_count is None) != (self.frequency_period is None):
            raise ValueError("frequency count and period must be supplied together")
        return self


class AgreementTrustScopeV1(BaseModel):
    model_config = {"extra": "forbid"}

    granting_member_ids: list[str] = Field(min_length=1, max_length=20)
    capability_ids: list[str] = Field(min_length=1, max_length=100)
    domains: list[str] = Field(min_length=1, max_length=100)
    providers: list[str] = Field(default_factory=list, max_length=100)
    provider_account_references: list[str] = Field(default_factory=list, max_length=100)
    action_verbs: list[str] = Field(min_length=1, max_length=100)
    destination_ids: list[str] = Field(default_factory=list, max_length=100)
    sensitivity_levels: list[Literal["normal", "sensitive", "privileged"]] = Field(
        min_length=1, max_length=3
    )
    stage: TrustStage
    effective_from: AwareDatetime | None = None
    effective_until: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_time_window(self) -> AgreementTrustScopeV1:
        if (
            self.effective_from is not None
            and self.effective_until is not None
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("trust scope must end after it begins")
        return self


class OperatingRuleV1(BaseModel):
    """One immutable structured revision; Markdown is only a rendering."""

    model_config = {"extra": "forbid"}

    rule_id: str = Field(min_length=1, max_length=160)
    acting_principal_ids: list[str] = Field(min_length=1, max_length=20)
    represented_service_principal_id: str = Field(min_length=1, max_length=200)
    active_member_ids: list[str] = Field(default_factory=list, max_length=20)
    on_behalf_of_member_ids: list[str] = Field(default_factory=list, max_length=20)
    resource_owner_principal_ids: list[str] = Field(
        default_factory=list, max_length=100
    )
    provider_account_owner_principal_ids: list[str] = Field(
        default_factory=list, max_length=100
    )
    responsible_party_ids: list[str] = Field(default_factory=list, max_length=100)
    beneficiary_party_ids: list[str] = Field(default_factory=list, max_length=100)
    purpose: str = Field(min_length=1, max_length=300)
    domains: list[str] = Field(min_length=1, max_length=100)
    trigger: str = Field(min_length=1, max_length=500)
    object_kinds: list[str] = Field(default_factory=list, max_length=100)
    provider_ids: list[str] = Field(default_factory=list, max_length=100)
    provider_account_references: list[str] = Field(default_factory=list, max_length=100)
    workspace_references: list[str] = Field(default_factory=list, max_length=100)
    resource_references: list[str] = Field(default_factory=list, max_length=100)
    destination_ids: list[str] = Field(default_factory=list, max_length=100)
    audience_ids: list[str] = Field(default_factory=list, max_length=100)
    required_fact_types: list[str] = Field(default_factory=list, max_length=100)
    maximum_fact_age_seconds: int | None = Field(default=None, ge=0)
    required_fact_coverage: Literal["exact", "complete", "best_available"] = "exact"
    permitted_observations: list[str] = Field(default_factory=list, max_length=100)
    permitted_internal_processing: list[str] = Field(
        default_factory=list, max_length=100
    )
    permitted_disclosures: list[str] = Field(default_factory=list, max_length=100)
    permitted_proposal_preparation: list[Literal["propose", "prepare", "hold"]] = Field(
        default_factory=list, max_length=3
    )
    prohibitions: list[str] = Field(default_factory=list, max_length=100)
    independent_openclaw_approval_required: bool = False
    ceilings: AgreementCeilingsV1 = Field(default_factory=AgreementCeilingsV1)
    verification_requirements: list[str] = Field(default_factory=list, max_length=100)
    reconciliation_requirements: list[str] = Field(default_factory=list, max_length=100)
    trust_scope: AgreementTrustScopeV1
    provider_execution_permitted: Literal[False] = False
    source_policy_embedded: Literal[False] = False

    @model_validator(mode="after")
    def validate_rule_boundaries(self) -> OperatingRuleV1:
        collections = (
            self.acting_principal_ids,
            self.active_member_ids,
            self.on_behalf_of_member_ids,
            self.resource_owner_principal_ids,
            self.provider_account_owner_principal_ids,
            self.responsible_party_ids,
            self.beneficiary_party_ids,
            self.domains,
            self.object_kinds,
            self.provider_ids,
            self.provider_account_references,
            self.workspace_references,
            self.resource_references,
            self.destination_ids,
            self.audience_ids,
            self.required_fact_types,
            self.permitted_observations,
            self.permitted_internal_processing,
            self.permitted_disclosures,
            self.prohibitions,
            self.verification_requirements,
            self.reconciliation_requirements,
        )
        if any(len(values) != len(set(values)) for values in collections):
            raise ValueError("operating agreement coordinates must be unique")
        forbidden_domains = {"source_policy", "source-policy", "intake_policy"}
        if forbidden_domains & {value.casefold() for value in self.domains}:
            raise ValueError(
                "source policies cannot be embedded in operating agreements"
            )
        broad_tokens = {"*", "all", "anything", "unlimited"}
        scoped_values = (
            self.domains
            + self.provider_ids
            + self.destination_ids
            + self.trust_scope.capability_ids
            + self.trust_scope.action_verbs
        )
        if broad_tokens & {value.strip().casefold() for value in scoped_values}:
            raise ValueError(
                "operating agreement coordinates must be exact and bounded"
            )
        return self


class ConstitutionalAgreementRegisteredV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    rule: OperatingRuleV1
    registered_at: AwareDatetime
    release_commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_effect_count: Literal[0] = 0


class OperatingAgreementProposedV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    supersedes_revision: int | None = Field(default=None, ge=1)
    layer: AgreementLayer
    authority_scope: AgreementAuthorityScope
    owner_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    proposed_by_member_id: str = Field(min_length=1, max_length=200)
    proposed_at: AwareDatetime
    effective_from: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    one_time_use_limit: Literal[1] | None = None
    required_approving_member_ids: list[str] = Field(min_length=1, max_length=20)
    approval_quorum: AgreementQuorum
    principal_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    rule: OperatingRuleV1
    provenance_event_ids: list[str] = Field(default_factory=list, max_length=100)
    provenance_digests: list[str] = Field(default_factory=list, max_length=100)
    self_activates: Literal[False] = False
    provider_effect_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_proposal_scope(self) -> OperatingAgreementProposedV1:
        required = self.required_approving_member_ids
        if len(required) != len(set(required)):
            raise ValueError("required approving members must be unique")
        if self.authority_scope == "member_private":
            if (
                self.layer not in {"member_private", "exception"}
                or not self.owner_member_id
                or required != [self.owner_member_id]
                or self.approval_quorum != "member_owner"
            ):
                raise ValueError("private agreement requires exact owner approval")
        elif (
            self.layer not in {"shared_household", "exception"}
            or self.owner_member_id is not None
            or len(required) < 2
            or self.approval_quorum != "all_adult_principals"
        ):
            raise ValueError("shared agreement requires the full adult quorum")
        if self.layer == "exception" and not (
            self.expires_at is not None or self.one_time_use_limit == 1
        ):
            raise ValueError("an exception must expire or be one-time")
        if self.layer != "exception" and self.one_time_use_limit is not None:
            raise ValueError("only an exception may be one-time")
        start = self.effective_from or self.proposed_at
        if self.expires_at is not None and self.expires_at <= start:
            raise ValueError("agreement expiry must follow its effective time")
        return self


class OperatingAgreementApprovedV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    authority_scope: AgreementAuthorityScope
    owner_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    approved_by_member_id: str = Field(min_length=1, max_length=200)
    approved_at: AwareDatetime
    proposal_event_id: str = Field(min_length=1, max_length=240)
    principal_set_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class OperatingAgreementRejectedV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    authority_scope: AgreementAuthorityScope
    owner_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    rejected_by_member_id: str = Field(min_length=1, max_length=200)
    rejected_at: AwareDatetime
    reason_code: Literal["scope", "privacy", "burden", "timing", "other"]


class OperatingAgreementSuspendedV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    authority_scope: AgreementAuthorityScope
    owner_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    suspended_by_member_id: str = Field(min_length=1, max_length=200)
    suspended_at: AwareDatetime
    reason_code: Literal[
        "privacy",
        "scope_violation",
        "verification_failure",
        "reconciliation_failure",
        "runtime_degradation",
        "member_request",
    ]


class OperatingAgreementRevokedV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    authority_scope: AgreementAuthorityScope
    owner_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    revoked_by_member_id: str = Field(min_length=1, max_length=200)
    revoked_at: AwareDatetime
    reason_code: Literal[
        "member_request",
        "scope_violation",
        "superseded",
        "no_longer_needed",
        "other",
    ]


class OperatingAgreementExceptionConsumedV1(BaseModel):
    model_config = {"extra": "forbid"}

    agreement_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    authority_scope: AgreementAuthorityScope
    owner_member_id: str | None = Field(default=None, min_length=1, max_length=200)
    consumed_at: AwareDatetime
    consumed_by_principal_id: str = Field(min_length=1, max_length=200)
    outcome_event_id: str = Field(min_length=1, max_length=240)


registry.register(
    "operating_agreement.constitution_registered",
    1,
    ConstitutionalAgreementRegisteredV1,
)
registry.register("operating_agreement.proposed", 1, OperatingAgreementProposedV1)
registry.register("operating_agreement.approved", 1, OperatingAgreementApprovedV1)
registry.register("operating_agreement.rejected", 1, OperatingAgreementRejectedV1)
registry.register("operating_agreement.suspended", 1, OperatingAgreementSuspendedV1)
registry.register("operating_agreement.revoked", 1, OperatingAgreementRevokedV1)
registry.register(
    "operating_agreement.exception_consumed",
    1,
    OperatingAgreementExceptionConsumedV1,
)


__all__ = [
    "AgreementAuthorityScope",
    "AgreementCeilingsV1",
    "AgreementLayer",
    "AgreementQuorum",
    "AgreementTrustScopeV1",
    "ConstitutionalAgreementRegisteredV1",
    "OperatingAgreementApprovedV1",
    "OperatingAgreementExceptionConsumedV1",
    "OperatingAgreementProposedV1",
    "OperatingAgreementRejectedV1",
    "OperatingAgreementRevokedV1",
    "OperatingAgreementSuspendedV1",
    "OperatingRuleV1",
    "TrustStage",
]
