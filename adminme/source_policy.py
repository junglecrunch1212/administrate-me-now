"""Exact per-principal, per-source policy and shadow routing.

This is the V2 policy contract.  It deliberately does not replace the live
``candidate_policy_v1`` admission path.  Source facts are mapped to an exact,
owner-scoped coordinate, an immutable policy decision is computed, and a
shadow receipt is emitted.  Provider execution is never a policy plane.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from adminme.candidate_policy import CANDIDATE_KINDS, SOURCE_BY_ID, source_id_for_event


OwnerKind = Literal["member", "service_principal"]
AdapterCategory = Literal[
    "communication",
    "external_state_mirror",
    "inbound_data",
    "outbound_action",
    "personal_knowledge",
    "member_surface",
    "unknown",
]
SourceClass = Literal[
    "direct_authenticated_intent",
    "passive_communication",
    "passive_state_mirror",
    "personal_knowledge",
    "financial_observation",
    "context_observation",
    "unknown",
]
PolicyPreset = Literal["focused", "balanced", "review_everything", "custom"]
AcquisitionState = Literal["active", "paused"]
TriageMode = Literal[
    "direct_work_order_proposal",
    "review",
    "alert_only",
    "context_only",
    "ignore",
    "manual_only",
]
AlertMode = Literal[
    "none",
    "security_only",
    "financial_and_security",
    "actionable_and_critical",
]
ContextMode = Literal[
    "none",
    "metadata_only",
    "typed_facts",
    "typed_facts_safe_snippet",
    "content_on_explicit_authorized_demand",
]

V2_CANDIDATE_KINDS = tuple(
    dict.fromkeys(
        [
            *CANDIDATE_KINDS,
            "coordination",
        ]
    )
)


class SourceCoordinate(BaseModel):
    """One exact source boundary; no adapter-wide aliases are accepted."""

    model_config = {"extra": "forbid", "frozen": True}

    tenant_id: str = Field(min_length=1, max_length=240)
    owner_principal_id: str = Field(min_length=1, max_length=500)
    owner_kind: OwnerKind
    context_member_id: str | None = Field(default=None, min_length=1, max_length=240)
    adapter_category: AdapterCategory
    adapter_kind: str = Field(min_length=1, max_length=240)
    adapter_instance_id: str = Field(min_length=1, max_length=500)
    provider: str = Field(min_length=1, max_length=120)
    provider_account_id: str = Field(min_length=1, max_length=500)
    workspace: str = Field(min_length=1, max_length=240)
    source_kind: str = Field(min_length=1, max_length=240)
    source_class: SourceClass
    trusted_route: str = Field(min_length=1, max_length=500)
    native_container_kind: Literal["folder", "list", "label", "calendar"] | None = (
        None
    )
    native_container_id: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_owner_boundary(self) -> SourceCoordinate:
        if self.owner_kind == "member":
            if self.context_member_id != self.owner_principal_id:
                raise ValueError("member source context must match its owner principal")
            if self.trusted_route.startswith("member_private:") and self.trusted_route != (
                f"member_private:{self.owner_principal_id}"
            ):
                raise ValueError("member-private route must match its owner principal")
        elif self.context_member_id is not None and not self.trusted_route.startswith(
            ("member_private:", "external_contact:")
        ):
            raise ValueError("service source member context requires an exact trusted route")
        elif (
            self.context_member_id is not None
            and self.trusted_route.startswith("member_private:")
            and self.trusted_route != f"member_private:{self.context_member_id}"
        ):
            raise ValueError("service source member route must match its context member")
        if (self.native_container_kind is None) != (self.native_container_id is None):
            raise ValueError("native container kind and id must be supplied together")
        return self

    @property
    def key(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return f"spv2_{hashlib.sha256(canonical).hexdigest()}"

    @property
    def exact(self) -> bool:
        unknown = {"unknown", "unresolved", "unattributed"}
        return not any(
            value.casefold() in unknown
            for value in (
                self.adapter_kind,
                self.adapter_instance_id,
                self.provider,
                self.provider_account_id,
                self.workspace,
                self.trusted_route,
            )
        )


class SourcePolicyV2(BaseModel):
    """Bounded settings that cannot manufacture access or provider authority."""

    model_config = {"extra": "forbid", "frozen": True}

    preset: PolicyPreset = "balanced"
    acquisition_state: AcquisitionState = "active"
    triage_mode: TriageMode = "review"
    alert_mode: AlertMode = "financial_and_security"
    context_mode: ContextMode = "typed_facts"
    candidate_kinds: list[str] = Field(min_length=1, max_length=20)
    scope_widening: Literal[False] = False
    credential_creation: Literal[False] = False
    provider_write_authority: Literal[False] = False
    admission_active: Literal[False] = False

    @model_validator(mode="after")
    def validate_candidate_kinds(self) -> SourcePolicyV2:
        selected = list(dict.fromkeys(self.candidate_kinds))
        if selected != self.candidate_kinds:
            raise ValueError("candidate kinds must be unique")
        if set(selected) - set(V2_CANDIDATE_KINDS):
            raise ValueError("candidate kind is outside the bounded V2 catalog")
        return self


class AcquisitionEvidence(BaseModel):
    """Observed acquisition state; it cannot widen scopes or create credentials."""

    model_config = {"extra": "forbid", "frozen": True}

    state: AcquisitionState
    coverage: Literal["complete", "partial", "unknown"] = "unknown"
    freshness: Literal["current", "stale", "unknown"] = "unknown"
    provider_capability: Literal["available", "unavailable", "unknown"] = "unknown"
    scope_widening: Literal[False] = False
    credential_creation: Literal[False] = False


class SignalAssessment(BaseModel):
    """Typed evidence only; none of these fields grants identity or authority."""

    model_config = {"extra": "forbid", "frozen": True}

    authenticated_member_instruction: bool = False
    known_human_incoming_request: bool = False
    member_promise: bool = False
    human_reply_requiring_follow_up: bool = False
    transactional_deadline: bool = False
    already_satisfied_or_canceled_obligation: bool = False
    financial_notice: bool = False
    security_alert: bool = False
    provider_update: bool = False
    receipt: bool = False
    promotion: bool = False
    bulk_notification: bool = False
    spam: bool = False
    relationship_valued_communication: bool = False
    incidental_entity_extraction: bool = False
    sender_class: str = Field(default="unknown", min_length=1, max_length=120)
    known_correspondent_evidence: list[str] = Field(default_factory=list, max_length=20)
    prior_interaction_evidence: list[str] = Field(default_factory=list, max_length=20)
    direction: Literal["inbound", "outbound", "member_authored", "state", "unknown"]
    continuity: list[str] = Field(default_factory=list, max_length=20)
    provider_labels_non_authoritative: list[str] = Field(
        default_factory=list, max_length=50
    )
    deterministic_actionability_evidence: list[str] = Field(
        default_factory=list, max_length=30
    )
    llm_extracted_claims_non_authoritative: list[str] = Field(
        default_factory=list, max_length=20
    )
    member_priority: str | None = Field(default=None, max_length=120)
    provider_or_sender_urgency_non_authoritative: str | None = Field(
        default=None, max_length=120
    )
    freshness: Literal["current", "stale", "unknown"] = "unknown"


class RouteDecision(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}

    triage: Literal["direct_work_order_proposal", "review", "none"]
    alert: Literal["security", "financial", "actionable", "none"]
    context: ContextMode
    candidate_kinds: list[str] = Field(default_factory=list, max_length=20)
    reason_codes: list[str] = Field(min_length=1, max_length=40)


PRESETS: dict[str, SourcePolicyV2] = {
    "focused": SourcePolicyV2(
        preset="focused",
        triage_mode="review",
        alert_mode="financial_and_security",
        context_mode="metadata_only",
        candidate_kinds=[
            "task",
            "commitment",
            "calendar_event",
            "reminder",
            "coordination",
        ],
    ),
    "balanced": SourcePolicyV2(
        preset="balanced",
        triage_mode="review",
        alert_mode="financial_and_security",
        context_mode="typed_facts",
        candidate_kinds=list(V2_CANDIDATE_KINDS),
    ),
    "review_everything": SourcePolicyV2(
        preset="review_everything",
        triage_mode="review",
        alert_mode="actionable_and_critical",
        context_mode="typed_facts_safe_snippet",
        candidate_kinds=list(V2_CANDIDATE_KINDS),
    ),
}


def policy_for_preset(preset: str) -> SourcePolicyV2:
    try:
        return PRESETS[preset].model_copy(deep=True)
    except KeyError as exc:
        raise ValueError("unknown source-policy preset") from exc


def validate_custom_policy(value: Mapping[str, Any]) -> SourcePolicyV2:
    policy = SourcePolicyV2.model_validate({**value, "preset": "custom"})
    return policy


def _text_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item)[:500] for item in value if str(item).strip()][:limit]


def _provider_for_event(event: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    provider = str(payload.get("provider") or "").strip().casefold()
    if provider:
        return provider
    identity = " ".join(
        (
            str(event.get("source_adapter") or ""),
            str(payload.get("source_channel") or ""),
        )
    ).casefold()
    for token, normalized in (
        ("gmail", "gmail"),
        ("google-calendar", "google_calendar"),
        ("calendar", "calendar"),
        ("eventkit", "apple"),
        ("apple", "apple"),
        ("microsoft", "microsoft_graph"),
        ("graph", "microsoft_graph"),
        ("bluebubbles", "bluebubbles"),
        ("twilio", "twilio"),
        ("plaid", "plaid"),
        ("home-assistant", "home_assistant"),
    ):
        if token in identity:
            return normalized
    return "unknown"


def _event_shape(event_type: str) -> tuple[AdapterCategory, SourceClass]:
    if event_type == "triage.intent.submitted":
        return "member_surface", "direct_authenticated_intent"
    if event_type.startswith(("messaging.", "telephony.")):
        return "communication", "passive_communication"
    if event_type.startswith(("calendar.", "list.", "list_item.", "contact.")):
        return "external_state_mirror", "passive_state_mirror"
    if event_type.startswith(("knowledge.", "member_knowledge.")):
        return "personal_knowledge", "personal_knowledge"
    if event_type.startswith(("money_flow.", "financial.")):
        return "inbound_data", "financial_observation"
    if event_type.startswith("context."):
        return "inbound_data", "context_observation"
    return "unknown", "unknown"


def _trusted_route(
    event: Mapping[str, Any], payload: Mapping[str, Any], owner_id: str, owner_kind: str
) -> str:
    routing_scope = str(payload.get("routing_scope") or "")
    routing_target = str(payload.get("routing_target_id") or "")
    if routing_scope in {"member_private", "external_contact"} and routing_target:
        return f"{routing_scope}:{routing_target}"
    if routing_scope == "household_shared":
        return "household_shared"
    owner_scope = str(event.get("owner_scope") or "")
    if owner_scope.startswith("private:"):
        return f"member_private:{owner_scope.removeprefix('private:')}"
    if owner_scope.startswith("service-private:"):
        return f"service_private:{owner_scope.removeprefix('service-private:')}"
    if owner_scope == "shared:household":
        return "household_shared"
    if owner_kind == "service_principal" and owner_id != "unattributed":
        return f"service_private:{owner_id}"
    return "unresolved"


def source_coordinate_for_event(
    event: Mapping[str, Any], *, provider_bindings: Sequence[Any] = ()
) -> SourceCoordinate:
    """Map a source fact without inspecting free text or provider credentials."""

    payload_value = event.get("payload")
    payload = payload_value if isinstance(payload_value, Mapping) else {}
    event_type = str(event.get("type") or "unknown")
    adapter_category, source_class = _event_shape(event_type)
    owner_scope = str(event.get("owner_scope") or "")
    member_id = str(payload.get("member_id") or "").strip()
    service_id = str(payload.get("service_principal_id") or "").strip()
    if (
        member_id
        and owner_scope.startswith("private:")
        and owner_scope.removeprefix("private:") != member_id
    ):
        raise ValueError("source member contradicts its private envelope owner")
    if (
        service_id
        and owner_scope.startswith("service-private:")
        and owner_scope.removeprefix("service-private:") != service_id
    ):
        raise ValueError("source service principal contradicts its private envelope owner")
    if service_id:
        owner_kind: OwnerKind = "service_principal"
        owner_id = service_id
    elif member_id:
        owner_kind = "member"
        owner_id = member_id
    elif owner_scope.startswith("private:"):
        owner_kind = "member"
        owner_id = owner_scope.removeprefix("private:")
        member_id = owner_id
    elif owner_scope.startswith("service-private:"):
        owner_kind = "service_principal"
        owner_id = owner_scope.removeprefix("service-private:")
    else:
        # Historical shared facts remain explicitly unattributed.  They do not
        # receive a fake member or an adapter-wide policy.
        owner_kind = "service_principal"
        owner_id = "unattributed"

    source_kind = source_id_for_event(event) or event_type
    catalog_source = SOURCE_BY_ID.get(source_kind)
    adapter_kind = str(
        payload.get("adapter_kind")
        or (catalog_source.get("adapter_kind") if catalog_source else "")
        or event.get("source_adapter")
        or "unknown"
    )
    adapter_instance = str(
        payload.get("adapter_instance_id")
        or event.get("source_adapter")
        or "unknown"
    )
    account_id = str(
        payload.get("provider_account_id")
        or event.get("source_account_id")
        or "unattributed"
    )
    workspace = str(
        payload.get("workspace")
        or ("service" if owner_kind == "service_principal" else "unknown")
    )
    if source_class == "direct_authenticated_intent":
        adapter_kind = "member-surface"
        adapter_instance = source_kind
        account_id = owner_id
        workspace = "private_portal"
    container_kind: Literal["folder", "list", "label", "calendar"] | None = None
    container_id: str | None = None
    for kind, key in (
        ("calendar", "calendar_external_id"),
        ("calendar", "calendar_id"),
        ("list", "list_external_id"),
        ("list", "list_id"),
        ("label", "label_id"),
        ("label", "label"),
        ("folder", "folder_id"),
        ("folder", "folder"),
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            container_kind = kind  # type: ignore[assignment]
            container_id = value.strip()
            break

    coordinate = SourceCoordinate(
        tenant_id=str(event.get("tenant_id") or "unknown"),
        owner_principal_id=owner_id,
        owner_kind=owner_kind,
        context_member_id=member_id or None,
        adapter_category=adapter_category,
        adapter_kind=adapter_kind,
        adapter_instance_id=adapter_instance,
        provider=(
            "administrateme"
            if source_class == "direct_authenticated_intent"
            else _provider_for_event(event, payload)
        ),
        provider_account_id=account_id,
        workspace=workspace,
        source_kind=source_kind,
        source_class=source_class,
        trusted_route=_trusted_route(event, payload, owner_id, owner_kind),
        native_container_kind=container_kind,
        native_container_id=container_id,
    )
    matching_bindings = [
        binding
        for binding in provider_bindings
        if str(getattr(binding, "owner_kind", "")) == coordinate.owner_kind
        and str(getattr(binding, "owner_id", "")) == coordinate.owner_principal_id
        and str(getattr(binding, "provider_account_id", "")).casefold()
        == coordinate.provider_account_id.casefold()
        and (
            coordinate.provider == "unknown"
            or str(getattr(binding, "provider_family", "")).casefold()
            == coordinate.provider.casefold()
            or str(getattr(binding, "kind", "")).casefold().find(
                coordinate.provider.casefold()
            )
            >= 0
        )
        and (
            coordinate.adapter_instance_id == "unknown"
            or str(getattr(binding, "adapter_id", ""))
            == coordinate.adapter_instance_id
        )
        and (
            coordinate.workspace == "unknown"
            or str(getattr(binding, "workspace", "")) == coordinate.workspace
        )
    ]
    if len(matching_bindings) == 1:
        binding = matching_bindings[0]
        coordinate = coordinate.model_copy(
            update={
                "adapter_kind": str(getattr(binding, "kind")),
                "adapter_instance_id": str(getattr(binding, "adapter_id")),
                "workspace": str(getattr(binding, "workspace")),
                "provider": coordinate.provider
                if coordinate.provider != "unknown"
                else str(getattr(binding, "provider_family", "unknown")),
            }
        )
    return coordinate


def assess_source_signals(event: Mapping[str, Any]) -> SignalAssessment:
    """Preserve typed evidence without letting labels or LLM claims decide."""

    payload_value = event.get("payload")
    payload = payload_value if isinstance(payload_value, Mapping) else {}
    event_type = str(event.get("type") or "")
    labels = _text_list(payload.get("provider_labels"), limit=50)
    direction: Literal["inbound", "outbound", "member_authored", "state", "unknown"]
    if event_type == "triage.intent.submitted":
        direction = "member_authored"
    elif event_type in {"messaging.received", "telephony.sms_received"}:
        direction = "inbound"
    elif event_type == "messaging.sent":
        direction = "outbound"
    elif event_type.startswith(
        ("calendar.", "list.", "list_item.", "contact.", "money_flow.", "context.")
    ):
        direction = "state"
    else:
        direction = "unknown"

    actionability: list[str] = []
    if direction == "member_authored":
        actionability.append("authenticated_member_intent")
    if payload.get("due_at") or payload.get("deadline_at"):
        actionability.append("typed_deadline_field")
    if payload.get("request_kind"):
        actionability.append("typed_request_kind")
    if payload.get("routing_scope"):
        actionability.append("reviewed_route")

    continuity: list[str] = []
    for key in ("thread_id", "in_reply_to", "rfc_message_id"):
        if payload.get(key):
            continuity.append(key)

    raw_freshness = str(payload.get("freshness") or "unknown")
    freshness = cast(
        Literal["current", "stale", "unknown"],
        raw_freshness
        if raw_freshness in {"current", "stale", "unknown"}
        else "unknown",
    )

    sender_class = str(payload.get("sender_class") or "unknown")[:120]
    known_evidence = _text_list(payload.get("known_correspondent_evidence"), limit=20)
    prior_evidence = _text_list(payload.get("prior_interaction_evidence"), limit=20)
    trusted_known_human = bool(
        direction == "inbound"
        and sender_class == "known_human"
        and known_evidence
    )
    provider_urgency = payload.get("provider_urgency")
    if provider_urgency is None and (payload.get("important") or payload.get("starred")):
        provider_urgency = "provider_marked"

    typed_flags = {
        name: bool(payload.get(name, False))
        for name in (
            "member_promise",
            "human_reply_requiring_follow_up",
            "transactional_deadline",
            "already_satisfied_or_canceled_obligation",
            "financial_notice",
            "security_alert",
            "provider_update",
            "receipt",
            "promotion",
            "bulk_notification",
            "spam",
            "relationship_valued_communication",
            "incidental_entity_extraction",
        )
    }
    return SignalAssessment(
        authenticated_member_instruction=direction == "member_authored",
        known_human_incoming_request=trusted_known_human
        and bool(payload.get("request_kind")),
        sender_class=sender_class,
        known_correspondent_evidence=known_evidence,
        prior_interaction_evidence=prior_evidence,
        direction=direction,
        continuity=continuity,
        provider_labels_non_authoritative=labels,
        deterministic_actionability_evidence=actionability,
        llm_extracted_claims_non_authoritative=_text_list(
            payload.get("llm_extracted_claims"), limit=20
        ),
        member_priority=(
            str(payload.get("member_priority"))[:120]
            if direction == "member_authored" and payload.get("member_priority")
            else None
        ),
        provider_or_sender_urgency_non_authoritative=(
            str(provider_urgency)[:120] if provider_urgency is not None else None
        ),
        freshness=freshness,
        **typed_flags,
    )


def route_for_policy(
    coordinate: SourceCoordinate,
    policy: SourcePolicyV2,
    signals: SignalAssessment,
) -> RouteDecision:
    reasons = [f"source_policy_v2:{coordinate.key}", f"preset:{policy.preset}"]
    direct = (
        coordinate.source_class == "direct_authenticated_intent"
        and signals.authenticated_member_instruction
    )
    if direct:
        return RouteDecision(
            triage="direct_work_order_proposal",
            alert="none",
            context=(
                "typed_facts"
                if policy.context_mode == "none"
                else policy.context_mode
            ),
            candidate_kinds=list(policy.candidate_kinds),
            reason_codes=[*reasons, "authenticated_member_intent_cannot_be_suppressed"],
        )

    if policy.acquisition_state == "paused":
        return RouteDecision(
            triage="none",
            alert="none",
            context="none",
            reason_codes=[*reasons, "source_acquisition_paused"],
        )
    if policy.triage_mode == "ignore":
        return RouteDecision(
            triage="none",
            alert="none",
            context="none",
            reason_codes=[*reasons, "policy_ignore_preserves_provenance_only"],
        )
    if policy.triage_mode == "manual_only":
        return RouteDecision(
            triage="none",
            alert="none",
            context="none",
            reason_codes=[*reasons, "policy_manual_only"],
        )

    alert: Literal["security", "financial", "actionable", "none"] = "none"
    if signals.security_alert and policy.alert_mode != "none":
        alert = "security"
    elif signals.financial_notice and policy.alert_mode in {
        "financial_and_security",
        "actionable_and_critical",
    }:
        alert = "financial"
    elif (
        policy.alert_mode == "actionable_and_critical"
        and signals.deterministic_actionability_evidence
    ):
        alert = "actionable"

    if policy.triage_mode == "alert_only":
        return RouteDecision(
            triage="none",
            alert=alert,
            context="none",
            reason_codes=[*reasons, "policy_alert_only"],
        )
    if policy.triage_mode == "context_only":
        context = (
            "typed_facts"
            if policy.context_mode == "none"
            else policy.context_mode
        )
        return RouteDecision(
            triage="none",
            alert=alert,
            context=context,
            reason_codes=[*reasons, "policy_context_only_without_triage"],
        )

    if policy.preset == "focused" and not any(
        (
            signals.known_human_incoming_request,
            signals.member_promise,
            signals.human_reply_requiring_follow_up,
            signals.transactional_deadline,
            signals.security_alert,
            signals.financial_notice,
        )
    ):
        triage: Literal["direct_work_order_proposal", "review", "none"] = "none"
        reasons.append("focused_requires_typed_actionability")
    elif policy.preset == "balanced" and any(
        (signals.spam, signals.promotion, signals.bulk_notification, signals.receipt)
    ) and not signals.relationship_valued_communication:
        triage = "none"
        reasons.append("balanced_suppresses_low_actionability_signal")
    else:
        triage = "review"
        reasons.append("policy_review")

    if policy.triage_mode == "direct_work_order_proposal" and not direct:
        reasons.append("passive_source_cannot_directly_create_work")
    return RouteDecision(
        triage=triage,
        alert=alert,
        context=policy.context_mode,
        candidate_kinds=list(policy.candidate_kinds) if triage != "none" else [],
        reason_codes=reasons,
    )


def current_v1_route(event: Mapping[str, Any], policy: Mapping[str, Any]) -> RouteDecision:
    event_type = str(event.get("type") or "")
    if event_type == "triage.intent.submitted":
        triage: Literal["direct_work_order_proposal", "review", "none"] = "review"
        reason = "candidate_policy_v1:direct_authenticated_intent"
    elif policy.get("mode") == "review":
        triage = "review"
        reason = "candidate_policy_v1:review"
    else:
        triage = "none"
        reason = f"candidate_policy_v1:{policy.get('mode', 'review')}"
    return RouteDecision(
        triage=triage,
        alert="none",
        context="typed_facts",
        candidate_kinds=list(policy.get("candidate_kinds") or []),
        reason_codes=[reason, "live_admission_unchanged"],
    )


def source_policy_event_types(repository_root: Any) -> list[str]:
    """Every declared inbound/member source; provider action requests are excluded."""

    from adminme.source_assurance import load_source_obligations

    return sorted(
        {
            obligation.event_type
            for obligation in load_source_obligations(repository_root)
            if obligation.mode
            not in {"action_request_terminal", "terminal_action"}
        }
    )


__all__ = [
    "AcquisitionEvidence",
    "PRESETS",
    "V2_CANDIDATE_KINDS",
    "RouteDecision",
    "SignalAssessment",
    "SourceCoordinate",
    "SourcePolicyV2",
    "assess_source_signals",
    "current_v1_route",
    "policy_for_preset",
    "route_for_policy",
    "source_coordinate_for_event",
    "source_policy_event_types",
    "validate_custom_policy",
]
