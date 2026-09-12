"""Deterministic, privacy-bounded cross-source reconciliation.

The evaluator reads immutable events and emits immutable admission receipts.  It
does not write a projection, call a model, or perform provider work.  Exact and
strong typed relationships may consolidate review work; weak text similarity is
recorded only as a suggestion and has no automatic effect.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

from adminme.context_fabric import normalized_exact_text, stable_digest
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.schemas.reconciliation import (
    AdmissionDecision,
    EvidenceRelationshipType,
    ReconciliationEvidenceRelationshipV1,
    ReconciliationPolicySnapshotV1,
    TriageReconciliationDecidedV1,
)
from adminme.events.writer import EventSink
from adminme.message_context import authored_message_text
from adminme.obligation_assessment import assessment_for_proposal
from adminme.settings_control import SettingsControlStore
from adminme.source_policy import (
    SignalAssessment,
    SourcePolicyV2,
    assess_source_signals,
    route_for_policy,
    source_coordinate_for_event,
)


_REFERENCE_PATTERN = re.compile(
    r"\b(payment|account|appointment|mortgage|claim|invoice|confirmation|reference|ref)"
    r"(?:\s+(?:number|no|id|account))?\s*(?:#|:)?\s*"
    r"((?=[A-Z0-9-]{0,39}[0-9])[A-Z0-9][A-Z0-9-]{2,39})\b",
    re.IGNORECASE,
)
_REQUEST_PATTERN = re.compile(
    r"\b(?:can|could|would)\s+you\b|\bplease\b|\bneed\s+you\s+to\b|"
    r"\bwill\s+you\b",
    re.IGNORECASE,
)
_PROMISE_PATTERN = re.compile(
    r"\b(?:i|we)\s+(?:will|can|promise\s+to|plan\s+to|am\s+going\s+to)\b",
    re.IGNORECASE,
)
_SECURITY_PATTERN = re.compile(
    r"\b(?:fraud|breach|password|security|unrecognized\s+login|new\s+sign[ -]?in|"
    r"verification\s+code|unauthorized\s+access|new\s+device)\b",
    re.IGNORECASE,
)
_RECEIPT_PATTERN = re.compile(
    r"\b(?:receipt|order\s+confirmation|payment\s+received|thanks\s+for\s+your\s+payment)\b",
    re.IGNORECASE,
)
_SATISFIED_PATTERN = re.compile(
    r"\b(?:payment\s+(?:received|posted|complete)|paid\s+in\s+full|already\s+paid|"
    r"(?:task|appointment|order|request|obligation)\s+(?:is\s+|was\s+)?completed|"
    r"has\s+been\s+completed)\b",
    re.IGNORECASE,
)
_CANCELED_PATTERN = re.compile(
    r"\b(?:cancelled|canceled|superseded|withdrawn|no\s+longer\s+required)\b",
    re.IGNORECASE,
)
_AUTOMATED_SENDER_PATTERN = re.compile(
    r"(?:^|[._+-])(?:no-?reply|notifications?|alerts?|updates?|mailer|automated)(?:@|[._+-]|$)",
    re.IGNORECASE,
)
_LOW_SIGNAL_LABELS = {
    "SPAM",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_UPDATES",
    "CATEGORY_SOCIAL",
    "CATEGORY_FORUMS",
}
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "in",
    "of",
    "on",
    "the",
    "to",
    "your",
}
_IDENTITY_RELATIONSHIPS = {
    "exact_duplicate",
    "same_provider_object",
    "exact_external_identifier",
    "exact_canonical_object_link",
    "exact_payment_reference",
    "exact_appointment_reference",
    "strong_deterministic_correlation",
}
_TERMINAL_SATISFIED = {"satisfied", "completed"}
_TERMINAL_SUPERSEDED = {"canceled", "cancelled", "superseded", "deleted"}


ReferenceKind = Literal[
    "exact_duplicate",
    "same_provider_object",
    "same_conversation",
    "exact_external_identifier",
    "exact_canonical_object_link",
    "exact_payment_reference",
    "exact_account_reference",
    "exact_appointment_reference",
    "strong_deterministic_correlation",
]
RelationshipAuthority = Literal[
    "provider_exact",
    "member_confirmed",
    "explicitly_shared_typed_fact",
    "deterministic_derived",
    "review_suggestion",
]
PriorityBasis = Literal[
    "explicit_member_instruction",
    "exact_member_commitment",
    "verified_deadline",
    "known_human_actionable_request",
    "current_state_and_consequence",
    "household_policy",
    "standard_actionable_candidate",
]


@dataclass(frozen=True)
class EvidenceProfile:
    source_event_id: str
    member_id: str | None
    target_kind: str | None
    title: str
    references: dict[ReferenceKind, frozenset[str]]
    weak_tokens: frozenset[str]
    comparable_fields: dict[str, str]
    current_state: str
    source_freshness: Literal["current", "stale", "unknown"]
    explicitly_shared: bool = False
    actionability: str | None = None
    ownership_unclear: bool = False
    occurrence_at: str | None = None
    obligation_purpose: str | None = None


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    value = event.get("payload")
    return value if isinstance(value, Mapping) else {}


def _member_id(event: Mapping[str, Any]) -> str | None:
    payload = _payload(event)
    value = payload.get("owner_member_id") or payload.get("member_id")
    if isinstance(value, str) and value:
        return value
    scope = str(event.get("owner_scope") or "")
    return scope.removeprefix("private:") if scope.startswith("private:") else None


def _source_text(event: Mapping[str, Any], proposal: Mapping[str, Any] | None) -> str:
    payload = _payload(event)
    proposal_payload = _payload(proposal or {})
    values = [
        proposal_payload.get("title"),
        payload.get("subject"),
        payload.get("title"),
        payload.get("body_text"),
        payload.get("text"),
        payload.get("description"),
        payload.get("notes"),
        payload.get("merchant_name"),
        payload.get("original_description"),
    ]
    return "\n".join(str(value) for value in values if isinstance(value, str) and value)


def reference_digest(tenant_id: str, kind: str, value: object) -> str:
    """Hash one normalized exact reference without retaining its raw value."""

    normalized = normalized_exact_text(value)
    encoded = json.dumps(
        [tenant_id, kind, normalized], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _add_reference(
    values: dict[ReferenceKind, set[str]],
    tenant_id: str,
    kind: ReferenceKind,
    value: object,
) -> None:
    normalized = normalized_exact_text(value)
    if normalized:
        values.setdefault(kind, set()).add(
            reference_digest(tenant_id, kind, normalized)
        )


def _current_state(event: Mapping[str, Any], text: str) -> str:
    payload = _payload(event)
    event_type = str(event.get("type") or "")
    if event_type in {"task.completed", "list_item.completed"}:
        return "completed"
    if event_type in {
        "task.cancelled",
        "task.deleted",
        "calendar.event_deleted",
        "list_item.deleted",
    }:
        return "canceled"
    if event_type == "money_flow.provider_observed" and payload.get("kind") == "paid":
        return "satisfied"
    for field in (
        "obligation_state",
        "current_state",
        "lifecycle_state",
        "status",
        "state",
    ):
        state = normalized_exact_text(payload.get(field))
        if state in _TERMINAL_SATISFIED | _TERMINAL_SUPERSEDED | {"current", "open"}:
            return state
    # Communication prose describes multiple objects and may quote historical
    # payments or cancellations. It cannot terminalize the whole message.
    if event_type in {"messaging.received", "messaging.sent", "telephony.sms_received"}:
        return "current"
    if _CANCELED_PATTERN.search(text):
        return "canceled"
    if _SATISFIED_PATTERN.search(text):
        return "satisfied"
    return "current"


def _candidate_payload(proposal: Mapping[str, Any] | None) -> Mapping[str, Any]:
    proposal_payload = _payload(proposal or {})
    value = proposal_payload.get("candidate")
    return value if isinstance(value, Mapping) else {}


def issuer_reference(event: Mapping[str, Any], value: object) -> list[object]:
    """An issuer's printed reference is not a globally unique object ID.

    Exact native counterparty identity is deliberately conservative: different
    addresses need an explicit canonical link before automatic reconciliation.
    The source account is the fallback when no counterparty is known.
    """

    payload = _payload(event)
    counterparty = payload.get("from_identifier")
    if event.get("type") == "messaging.sent":
        counterparty = payload.get("to_identifier")
    issuer: object = (
        ["counterparty", normalized_exact_text(counterparty)]
        if counterparty
        else [
            "provider_account",
            payload.get("provider") or event.get("source_adapter"),
            payload.get("provider_account_id") or event.get("source_account_id"),
        ]
    )
    return [issuer, value]


def evidence_profile(
    event: Mapping[str, Any],
    proposal: Mapping[str, Any] | None = None,
) -> EvidenceProfile:
    """Derive content-free exact coordinates and bounded weak tokens."""

    tenant_id = str(event.get("tenant_id") or "unknown")
    payload = _payload(event)
    proposal_payload = _payload(proposal or {})
    candidate = _candidate_payload(proposal)
    source_event_id = str(event.get("event_id") or "unknown")
    target_kind = str(proposal_payload.get("target_kind") or "") or None
    title = str(
        proposal_payload.get("title")
        or payload.get("title")
        or payload.get("subject")
        or ""
    )
    text = _source_text(event, proposal)
    references: dict[ReferenceKind, set[str]] = {}

    if event.get("type") == "context.reconciliation.typed_fact_shared":
        kind_map: dict[str, ReferenceKind] = {
            "provider_object": "same_provider_object",
            "external_identifier": "exact_external_identifier",
            "canonical_object": "exact_canonical_object_link",
            "payment_reference": "exact_payment_reference",
            "account_reference": "exact_account_reference",
            "appointment_reference": "exact_appointment_reference",
            "strong_correlation": "strong_deterministic_correlation",
        }
        for item in payload.get("references") or []:
            if not isinstance(item, Mapping):
                continue
            mapped = kind_map.get(str(item.get("kind") or ""))
            digest = str(item.get("digest") or "")
            if mapped and re.fullmatch(r"[0-9a-f]{64}", digest):
                references.setdefault(mapped, set()).add(digest)
    else:
        provider = str(payload.get("provider") or event.get("source_adapter") or "")
        account = str(
            payload.get("provider_account_id") or event.get("source_account_id") or ""
        )
        workspace = str(payload.get("workspace") or "")
        for key in (
            "native_message_id",
            "transaction_id",
            "external_event_id",
            "item_external_id",
            "provider_object_id",
            "provider_native_id",
        ):
            if payload.get(key):
                _add_reference(
                    references,
                    tenant_id,
                    "same_provider_object",
                    [provider, account, workspace, key, payload[key]],
                )
        if payload.get("thread_id"):
            _add_reference(
                references,
                tenant_id,
                "same_conversation",
                [provider, account, workspace, payload["thread_id"]],
            )

        combined: dict[str, Any] = {**payload, **candidate}
        excluded_ids = {
            "member_id",
            "owner_member_id",
            "provider_account_id",
            "source_event_id",
            "extraction_event_id",
            "parent_intent_id",
            "intent_id",
        }
        for key, value in combined.items():
            if key in excluded_ids or value in (None, "", [], {}):
                continue
            lowered = key.casefold()
            if lowered in {
                "payment_reference",
                "payment_id",
                "transaction_reference",
            }:
                _add_reference(
                    references,
                    tenant_id,
                    "exact_payment_reference",
                    issuer_reference(event, value),
                )
            elif lowered in {"account_reference", "account_number", "account_last4"}:
                _add_reference(
                    references,
                    tenant_id,
                    "exact_account_reference",
                    issuer_reference(event, value),
                )
            elif lowered in {
                "appointment_reference",
                "appointment_id",
                "visit_id",
            }:
                _add_reference(
                    references,
                    tenant_id,
                    "exact_appointment_reference",
                    issuer_reference(event, value),
                )
            elif lowered in {
                "task_id",
                "commitment_id",
                "calendar_event_id",
                "reminder_id",
                "recurrence_id",
                "canonical_object_id",
            }:
                _add_reference(
                    references,
                    tenant_id,
                    "exact_canonical_object_link",
                    [lowered, value],
                )
            elif lowered in {
                "external_identifier",
                "external_reference",
                "confirmation_number",
                "invoice_id",
                "claim_id",
                "reference_id",
            }:
                _add_reference(
                    references,
                    tenant_id,
                    "exact_external_identifier",
                    issuer_reference(event, [lowered, value]),
                )
        if candidate.get("linked_kind") and candidate.get("linked_id"):
            _add_reference(
                references,
                tenant_id,
                "exact_canonical_object_link",
                [candidate["linked_kind"], candidate["linked_id"]],
            )

        for match in _REFERENCE_PATTERN.finditer(text):
            prefix = match.group(1).casefold()
            value = match.group(2)
            if not any(character.isdigit() for character in value):
                continue
            special: ReferenceKind = (
                "exact_payment_reference"
                if prefix in {"payment", "invoice"}
                else "exact_appointment_reference"
                if prefix == "appointment"
                else "exact_account_reference"
                if prefix in {"account", "mortgage"}
                else "exact_external_identifier"
            )
            _add_reference(
                references, tenant_id, special, issuer_reference(event, value)
            )
            if special != "exact_account_reference":
                _add_reference(
                    references,
                    tenant_id,
                    "exact_external_identifier",
                    issuer_reference(event, [prefix, value]),
                )

        comparable = {
            key: combined[key]
            for key in (
                "due",
                "due_at",
                "due_date",
                "deadline",
                "deadline_at",
                "start",
                "start_at",
                "end",
                "end_at",
                "amount_minor",
                "currency",
            )
            if combined.get(key) not in (None, "")
        }
        exact_candidate = {key: comparable[key] for key in sorted(comparable)} | {
            key: candidate[key]
            for key in ("kind", "rrule", "value")
            if candidate.get(key) not in (None, "")
        }
        normalized_title = normalized_exact_text(title)
        if target_kind and normalized_title:
            _add_reference(
                references,
                tenant_id,
                "exact_duplicate",
                [
                    provider,
                    account,
                    workspace,
                    issuer_reference(event, ""),
                    target_kind,
                    normalized_title,
                    exact_candidate,
                    None
                    if exact_candidate
                    else payload.get("native_message_id") or source_event_id,
                ],
            )

    comparable_fields = {
        key: normalized_exact_text(value)
        for key, value in {**payload, **candidate}.items()
        if key
        in {
            "due",
            "due_at",
            "due_date",
            "deadline",
            "deadline_at",
            "start",
            "start_at",
            "end",
            "end_at",
            "amount_minor",
            "currency",
        }
        and value not in (None, "")
    }
    normalized_title = normalized_exact_text(title)
    weak_tokens = frozenset(
        word
        for word in normalized_title.split()
        if len(word) > 2 and word not in _STOPWORDS
    )
    raw_freshness = str(payload.get("freshness") or "unknown")
    freshness = cast(
        Literal["current", "stale", "unknown"],
        raw_freshness
        if raw_freshness in {"current", "stale", "unknown"}
        else "unknown",
    )
    assessment = assessment_for_proposal(proposal)
    interpretation = proposal_payload.get("interpretation") or {}
    assessed_contract = (
        isinstance(interpretation, Mapping)
        and interpretation.get("contract") == "triage-context-interpretation-v3"
    )
    return EvidenceProfile(
        source_event_id=source_event_id,
        member_id=_member_id(proposal or event),
        target_kind=target_kind,
        title=title,
        references={kind: frozenset(values) for kind, values in references.items()},
        weak_tokens=weak_tokens,
        comparable_fields=comparable_fields,
        # An imperative candidate title such as "Complete the form" is proposed
        # work, not evidence that the source obligation is already complete.
        current_state=_current_state(event, _source_text(event, None)),
        source_freshness=freshness,
        explicitly_shared=event.get("type")
        == "context.reconciliation.typed_fact_shared",
        actionability=assessment.actionability
        if assessment
        else "unknown"
        if assessed_contract
        else None,
        ownership_unclear=assessed_contract
        and interpretation.get("action_owner") == "unknown",
        occurrence_at=str(candidate.get("start"))
        if assessment
        and target_kind == "calendar_event"
        and assessment.date_role == "occurrence"
        and "start" in assessment.verified_date_fields
        else None,
        obligation_purpose=assessment.purpose if assessment else None,
    )


def _relationship(
    current: EvidenceProfile,
    related: EvidenceProfile,
    relationship_type: EvidenceRelationshipType,
    common_digest: str,
    *,
    authority: RelationshipAuthority,
    automatic_effect: bool,
    reasons: list[str],
) -> ReconciliationEvidenceRelationshipV1:
    relationship_id = "rel_" + stable_digest(
        *sorted((current.source_event_id, related.source_event_id)),
        relationship_type,
        common_digest,
    )
    return ReconciliationEvidenceRelationshipV1(
        relationship_id=relationship_id,
        relationship_type=relationship_type,
        related_source_event_id=related.source_event_id,
        authority=authority,
        reason_codes=reasons,
        automatic_effect=automatic_effect,
    )


def evidence_relationships(
    current: EvidenceProfile, related: EvidenceProfile
) -> list[ReconciliationEvidenceRelationshipV1]:
    """Compare two scoped profiles without assigning identity from similarity."""

    relationships: list[ReconciliationEvidenceRelationshipV1] = []
    for relationship_type in (
        "exact_duplicate",
        "same_provider_object",
        "same_conversation",
        "exact_external_identifier",
        "exact_canonical_object_link",
        "exact_payment_reference",
        "exact_account_reference",
        "exact_appointment_reference",
        "strong_deterministic_correlation",
    ):
        kind = cast(ReferenceKind, relationship_type)
        common = current.references.get(kind, frozenset()) & related.references.get(
            kind, frozenset()
        )
        for digest in sorted(common):
            automatic = relationship_type not in {
                "same_conversation",
                "exact_account_reference",
            }
            if (
                relationship_type == "same_provider_object"
                and current.target_kind
                and related.target_kind
            ):
                # A source message may contain several independent actions.
                automatic = (
                    current.target_kind == related.target_kind
                    and normalized_exact_text(current.title)
                    == normalized_exact_text(related.title)
                )
            if (
                current.target_kind
                and related.target_kind
                and current.target_kind != related.target_kind
            ):
                automatic = False
            if (
                current.target_kind
                and related.target_kind
                and relationship_type != "exact_canonical_object_link"
            ):
                if (
                    current.obligation_purpose
                    and related.obligation_purpose
                    and current.obligation_purpose != related.obligation_purpose
                ):
                    automatic = False
                if (
                    current.obligation_purpose or related.obligation_purpose
                ) and normalized_exact_text(current.title) != normalized_exact_text(
                    related.title
                ):
                    # Two actions can concern one invoice or appointment.
                    # An explicit canonical link can still establish identity.
                    automatic = False
            authority = cast(
                RelationshipAuthority,
                "explicitly_shared_typed_fact"
                if current.explicitly_shared or related.explicitly_shared
                else "provider_exact"
                if relationship_type
                in {
                    "same_provider_object",
                    "same_conversation",
                    "exact_external_identifier",
                }
                else "deterministic_derived",
            )
            relationships.append(
                _relationship(
                    current,
                    related,
                    cast(EvidenceRelationshipType, relationship_type),
                    digest,
                    authority=authority,
                    automatic_effect=automatic,
                    reasons=[relationship_type, "scope_preserved"],
                )
            )
    identity_present = any(
        item.relationship_type in _IDENTITY_RELATIONSHIPS and item.automatic_effect
        for item in relationships
    )
    if identity_present:
        conflicts = sorted(
            key
            for key in current.comparable_fields.keys()
            & related.comparable_fields.keys()
            if current.comparable_fields[key] != related.comparable_fields[key]
        )
        if conflicts:
            relationships.append(
                _relationship(
                    current,
                    related,
                    "contradiction",
                    reference_digest("relationship", "contradiction", conflicts),
                    authority="deterministic_derived",
                    automatic_effect=True,
                    reasons=[
                        "exact_identity_with_conflicting_typed_fields",
                        *conflicts[:8],
                    ],
                )
            )
        if current.current_state in _TERMINAL_SATISFIED:
            relationships.append(
                _relationship(
                    current,
                    related,
                    "satisfaction_or_completion",
                    current.current_state,
                    authority=(
                        "explicitly_shared_typed_fact"
                        if current.explicitly_shared
                        else "deterministic_derived"
                    ),
                    automatic_effect=True,
                    reasons=[f"typed_current_state:{current.current_state}"],
                )
            )
        elif current.current_state in _TERMINAL_SUPERSEDED:
            relationships.append(
                _relationship(
                    current,
                    related,
                    "cancellation_or_supersession",
                    current.current_state,
                    authority=(
                        "explicitly_shared_typed_fact"
                        if current.explicitly_shared
                        else "deterministic_derived"
                    ),
                    automatic_effect=True,
                    reasons=[f"typed_current_state:{current.current_state}"],
                )
            )
    if (
        not identity_present
        and len(current.weak_tokens) >= 2
        and len(related.weak_tokens) >= 2
    ):
        common_tokens = current.weak_tokens & related.weak_tokens
        union = current.weak_tokens | related.weak_tokens
        if len(common_tokens) >= 2 and len(common_tokens) / len(union) >= 0.6:
            relationships.append(
                _relationship(
                    current,
                    related,
                    "weak_review_suggestion",
                    reference_digest(
                        "relationship", "weak_tokens", sorted(common_tokens)
                    ),
                    authority="review_suggestion",
                    automatic_effect=False,
                    reasons=["bounded_token_overlap", "no_automatic_effect"],
                )
            )
    return relationships


def _message_direction(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("type") or "")
    if event_type in {"messaging.sent"} or "SENT" in {
        str(value).upper() for value in _payload(event).get("provider_labels") or []
    }:
        return "outbound"
    if event_type in {"messaging.received", "telephony.sms_received"}:
        return "inbound"
    return "unknown"


def _candidate_action_text(
    event: Mapping[str, Any], proposal: Mapping[str, Any]
) -> str:
    """Ground legacy action evidence in this candidate's primary source quote.

    Current contextual proposals must use their qualified assessment. A missing
    or invalid assessment cannot be repaired by scanning the message. Older
    producers retain only their own exact quote, never other context references
    or a sibling action elsewhere in the source.
    """

    payload = _payload(proposal)
    interpretation = payload.get("interpretation")
    if proposal.get("type") != "triage.item.proposed" or payload.get(
        "source_event_id"
    ) != event.get("event_id"):
        return ""
    quote = payload.get("title")
    if isinstance(interpretation, Mapping):
        if interpretation.get("contract") == "triage-context-interpretation-v3":
            return ""
        references = interpretation.get("evidence_refs")
        if references:
            if not isinstance(references, list):
                return ""
            primary = references[0]
            if not isinstance(primary, Mapping) or primary.get(
                "source_event_id"
            ) != event.get("event_id"):
                return ""
            quote = primary.get("quote")
    if not isinstance(quote, str) or not quote.strip():
        return ""
    quote = quote.replace("\r\n", "\n").replace("\r", "\n").strip()
    authored = authored_message_text(_payload(event))
    return quote if authored.reliable and quote in authored.text else ""


def assessed_signals(
    event: Mapping[str, Any],
    proposal: Mapping[str, Any] | None = None,
    *,
    prior_member_correspondence: bool = False,
) -> SignalAssessment:
    """Add deterministic message evidence without promoting vocabulary to authority."""

    base = assess_source_signals(event)
    assessment = assessment_for_proposal(proposal)
    interpretation = _payload(proposal or {}).get("interpretation")
    if (
        assessment
        and isinstance(interpretation, Mapping)
        and interpretation.get("assessment_state") != "verified"
    ):
        assessment = None
    payload = _payload(event)
    text = _source_text(event, proposal)
    direction = _message_direction(event)
    sender = str(payload.get("from_identifier") or payload.get("from_number") or "")
    automated = bool(_AUTOMATED_SENDER_PATTERN.search(sender))
    labels = {str(value).upper() for value in payload.get("provider_labels") or []}
    action_text = ""
    if assessment is None:
        action_text = (
            _candidate_action_text(event, proposal) if proposal is not None else text
        )
    request = direction == "inbound" and bool(_REQUEST_PATTERN.search(action_text))
    known_request = request and prior_member_correspondence and not automated
    promise = direction == "outbound" and bool(_PROMISE_PATTERN.search(action_text))
    security = automated and bool(_SECURITY_PATTERN.search(text))
    spam = "SPAM" in labels
    promotion = "CATEGORY_PROMOTIONS" in labels
    bulk = automated or bool(
        labels & (_LOW_SIGNAL_LABELS - {"SPAM", "CATEGORY_PROMOTIONS"})
    )
    receipt = bool(_RECEIPT_PATTERN.search(text))
    actionability = list(base.deterministic_actionability_evidence)
    if (
        assessment
        and assessment.date_role == "deadline"
        and "due" in assessment.verified_date_fields
    ):
        actionability.append("verified_typed_deadline")
    if assessment:
        promise = (
            assessment.independent_obligation
            and assessment.purpose == "member_promise"
            and direction == "outbound"
        )
        known_request = (
            assessment.independent_obligation
            and assessment.purpose == "personal_request"
            and direction == "inbound"
            and prior_member_correspondence
            and not automated
        )
        actionability.append("source_grounded_obligation_assessment")
    if known_request:
        actionability.extend(
            ("known_correspondent_prior_outbound", "exact_request_phrase")
        )
    if promise:
        actionability.append("exact_outbound_member_promise")
    return SignalAssessment.model_validate(
        {
            **base.model_dump(mode="json"),
            "verified_obligation": bool(
                assessment and assessment.independent_obligation
            ),
            "candidate_kind": str(_payload(proposal or {}).get("target_kind") or "")
            or None,
            "obligation_actionability": assessment.actionability
            if assessment
            else "unknown",
            "message_purpose": assessment.purpose if assessment else "unknown",
            "purpose_evidence_verified": assessment is not None,
            "known_human_incoming_request": known_request,
            "member_promise": promise or (proposal is None and base.member_promise),
            "security_alert": (assessment.purpose == "account_alert")
            if assessment
            else base.security_alert or security,
            "provider_update": base.provider_update
            or (bulk and not promotion and not spam),
            "receipt": base.receipt or receipt,
            "promotion": base.promotion or promotion,
            "bulk_notification": base.bulk_notification or bulk,
            "spam": base.spam or spam,
            "relationship_valued_communication": (
                base.relationship_valued_communication
                or (prior_member_correspondence and not automated)
            ),
            "sender_class": (
                "known_human"
                if prior_member_correspondence and not automated
                else "automated"
                if automated
                else base.sender_class
            ),
            "known_correspondent_evidence": (
                [*base.known_correspondent_evidence, "prior_outbound_exact_endpoint"]
                if prior_member_correspondence
                else base.known_correspondent_evidence
            ),
            "prior_interaction_evidence": (
                [*base.prior_interaction_evidence, "prior_member_correspondence"]
                if prior_member_correspondence
                else base.prior_interaction_evidence
            ),
            "deterministic_actionability_evidence": list(dict.fromkeys(actionability)),
        }
    )


def priority_for(signals: SignalAssessment) -> tuple[int, list[PriorityBasis]]:
    """Return code-owned priority; sender/provider urgency never participates."""

    if signals.authenticated_member_instruction:
        return 0, ["explicit_member_instruction"]
    if signals.member_promise:
        return 10, ["exact_member_commitment"]
    if signals.known_human_incoming_request:
        return 20, ["known_human_actionable_request"]
    if (
        signals.transactional_deadline
        or "verified_typed_deadline" in signals.deterministic_actionability_evidence
    ):
        return 30, ["verified_deadline"]
    if signals.already_satisfied_or_canceled_obligation:
        return 40, ["current_state_and_consequence"]
    if signals.member_priority:
        return 40, ["household_policy"]
    return 60, ["standard_actionable_candidate"]


def choose_decision(
    *,
    coordinate_exact: bool,
    signals: SignalAssessment,
    policy: ReconciliationPolicySnapshotV1,
    profile: EvidenceProfile,
    relationships: Sequence[ReconciliationEvidenceRelationshipV1],
    target_kind: str | None,
    as_of: datetime | None = None,
) -> AdmissionDecision:
    """Apply current state, exact evidence, relevance, and source policy in order."""

    if profile.current_state in _TERMINAL_SATISFIED:
        return "already_satisfied"
    if profile.current_state in _TERMINAL_SUPERSEDED:
        return "superseded"
    if profile.occurrence_at and as_of and not signals.authenticated_member_instruction:
        occurrence = datetime.fromisoformat(
            profile.occurrence_at.replace("Z", "+00:00")
        )
        if occurrence.date() < as_of.date():
            # Historical attendance differs from an overdue unpaid bill.
            # Date-only evidence never marks an event earlier today as done.
            return "superseded"
    relationship_types = {
        item.relationship_type for item in relationships if item.automatic_effect
    }
    automatic_identity = bool(relationship_types & _IDENTITY_RELATIONSHIPS)
    if automatic_identity and "contradiction" in relationship_types:
        return "conflict_review"
    if signals.security_alert and not any(
        (
            signals.authenticated_member_instruction,
            signals.verified_obligation,
            signals.known_human_incoming_request,
            signals.member_promise,
            signals.transactional_deadline,
        )
    ):
        return "alert_only" if policy.alert_route != "none" else "context_only"
    if automatic_identity:
        if "exact_duplicate" in relationship_types:
            return "duplicate"
        if any(
            item.relationship_type
            in {"satisfaction_or_completion", "cancellation_or_supersession"}
            for item in relationships
        ):
            return (
                "already_satisfied"
                if "satisfaction_or_completion" in relationship_types
                else "superseded"
            )
        return (
            "update_existing_matter" if profile.comparable_fields else "attach_evidence"
        )
    if target_kind in {
        "party_candidate",
        "party_identifier",
        "party_relationship",
        "relationship_fact",
    }:
        return (
            "context_only"
            if policy.context_route != "none"
            else "source_policy_suppression"
        )
    if (
        any(
            (
                signals.spam,
                signals.promotion,
                signals.bulk_notification,
                signals.receipt,
                signals.provider_update,
            )
        )
        and not signals.relationship_valued_communication
        and not signals.verified_obligation
        and not signals.authenticated_member_instruction
        and "purpose_route:review" not in policy.reason_codes
        and not (
            signals.purpose_evidence_verified
            and signals.obligation_actionability == "conditional"
            and policy.triage_route == "review"
        )
    ):
        if policy.alert_route != "none":
            return "alert_only"
        return (
            "context_only"
            if policy.context_route != "none"
            else "source_policy_suppression"
        )
    if policy.triage_route == "none":
        if policy.alert_route != "none":
            return "alert_only"
        if policy.context_route != "none":
            return "context_only"
        return "source_policy_suppression"
    if not coordinate_exact:
        return "unresolved"
    if profile.actionability in {"conditional", "unknown"} or profile.ownership_unclear:
        return "unresolved"
    if profile.actionability in {"none", "informational"}:
        return "context_only"
    return "admit"


class ReconciliationAdmissionEvaluator:
    """Durable bus consumer that emits decision revisions from authorized state."""

    # Revisit retained decisions that previously inherited promise/request
    # priority from another action in the same message. The immutable namespace
    # preserves prior receipts while emitting candidate-grounded revisions.
    CONSUMER_VERSION = "2.0.2"

    def __init__(
        self,
        event_sink: EventSink,
        event_log: EventLog,
        settings: SettingsControlStore,
        *,
        provider_bindings: tuple[Any, ...] = (),
        admission_active: bool = False,
    ) -> None:
        self._events = event_sink
        self._log = event_log
        self._settings = settings
        self._provider_bindings = provider_bindings
        self._admission_active = admission_active
        self._history: dict[tuple[str, str | None], list[dict[str, Any]]] = {}
        self._history_cursors: dict[tuple[str, str | None], str] = {}
        self._source_cache: dict[str, dict[str, Any]] = {}
        self._decision_index: dict[tuple[str | None, str], dict[str, Any]] = {}
        self._profile_cache: dict[tuple[str, str], EvidenceProfile] = {}

    async def _events_of_type(
        self, event_type: str, *, owner_scope: str | None = None
    ) -> list[dict[str, Any]]:
        key = (event_type, owner_scope)
        values = self._history.setdefault(key, [])
        async for event in self._log.read_since(
            self._history_cursors.get(key), types=[event_type], owner_scope=owner_scope
        ):
            values.append(event)
            self._history_cursors[key] = str(event["event_id"])
            if event_type == "triage.reconciliation.decided":
                self._decision_index[
                    (owner_scope, str(_payload(event).get("proposal_id") or ""))
                ] = event
        return list(values)

    def _profile(
        self, source: Mapping[str, Any], proposal: Mapping[str, Any]
    ) -> EvidenceProfile:
        key = (str(source["event_id"]), str(proposal["event_id"]))
        if key not in self._profile_cache:
            self._profile_cache[key] = evidence_profile(source, proposal)
        return self._profile_cache[key]

    async def _source(self, event_id: str) -> dict[str, Any] | None:
        if event_id not in self._source_cache:
            value = await self._log.get(event_id)
            if value is not None:
                self._source_cache[event_id] = value
        return self._source_cache.get(event_id)

    async def _prior_correspondence(self, source: Mapping[str, Any]) -> str | None:
        if source.get("type") != "messaging.received":
            return None
        payload = _payload(source)
        sender = normalized_exact_text(payload.get("from_identifier"))
        if not sender:
            return None
        matched_event_id: str | None = None
        for event in await self._events_of_type(
            "messaging.sent", owner_scope=str(source.get("owner_scope") or "")
        ):
            if event.get("tenant_id") != source.get("tenant_id"):
                continue
            prior = _payload(event)
            exact_source_keys = (
                "provider",
                "provider_account_id",
                "workspace",
                "bridge_enrollment_id",
                "protected_source_id",
            )
            if any(
                payload.get(key) != prior.get(key)
                for key in exact_source_keys
                if payload.get(key) is not None
            ):
                continue
            recipients = {
                normalized_exact_text(value)
                for value in [
                    prior.get("to_identifier"),
                    *(prior.get("to_identifiers") or []),
                ]
                if value
            }
            if sender in recipients:
                matched_event_id = str(event["event_id"])
        return matched_event_id

    async def _latest_decision(
        self, proposal_id: str, owner_scope: str
    ) -> dict[str, Any] | None:
        if ("triage.reconciliation.decided", owner_scope) not in self._history:
            await self._events_of_type(
                "triage.reconciliation.decided", owner_scope=owner_scope
            )
        return self._decision_index.get((owner_scope, proposal_id))

    async def _policy_snapshot(
        self, source: Mapping[str, Any], signals: SignalAssessment
    ) -> tuple[Any, ReconciliationPolicySnapshotV1]:
        coordinate = source_coordinate_for_event(
            source, provider_bindings=self._provider_bindings
        )
        effective = self._settings.effective_source_policy_v2(
            coordinate.owner_principal_id, coordinate
        )
        source_policy = SourcePolicyV2.model_validate(effective["policy"])
        route = route_for_policy(coordinate, source_policy, signals)
        snapshot = ReconciliationPolicySnapshotV1(
            policy_key=coordinate.key,
            policy_revision=int(effective["revision"]),
            policy_origin=effective["origin"],
            triage_route=route.triage,
            alert_route=route.alert,
            context_route=route.context,
            reason_codes=route.reason_codes,
        )
        return coordinate, snapshot

    async def _emit_decision(
        self,
        *,
        proposal: Mapping[str, Any],
        source: Mapping[str, Any],
        trigger: Mapping[str, Any],
        profile: EvidenceProfile,
        relationships: list[ReconciliationEvidenceRelationshipV1],
        decision: AdmissionDecision,
        primary_proposal_id: str | None,
        signals: SignalAssessment,
        coordinate: Any,
        policy: ReconciliationPolicySnapshotV1,
        extra_reasons: Sequence[str] = (),
        extra_evidence_source_event_ids: Sequence[str] = (),
    ) -> str:
        proposal_payload = _payload(proposal)
        proposal_id = str(proposal_payload["proposal_id"])
        owner_member_id = str(proposal_payload["owner_member_id"])
        owner_scope = f"private:{owner_member_id}"
        trigger_id = str(trigger["event_id"])
        identity = hashlib.sha256(
            f"{self.CONSUMER_VERSION}|{proposal_id}|{trigger_id}".encode("utf-8")
        ).hexdigest()
        idempotency_key = f"triage-reconciliation:v1:{identity}"
        existing = await self._log.get_by_idempotency(
            str(proposal["tenant_id"]), idempotency_key
        )
        if existing is not None:
            return str(existing["event_id"])
        await self._events_of_type(
            "triage.reconciliation.decided", owner_scope=owner_scope
        )
        prior = await self._latest_decision(proposal_id, owner_scope)
        revision = int(_payload(prior or {}).get("decision_revision") or 0) + 1
        root_proposal_id = primary_proposal_id or proposal_id
        matter_id = "ctx_reconciled_" + stable_digest(
            proposal["tenant_id"], owner_member_id, root_proposal_id
        )
        priority_rank, priority_basis = priority_for(signals)
        relationship_reasons = [
            reason for item in relationships for reason in item.reason_codes
        ]
        evidence_ids = list(
            dict.fromkeys(
                [
                    str(proposal_payload["source_event_id"]),
                    trigger_id,
                    *(item.related_source_event_id for item in relationships),
                    *extra_evidence_source_event_ids,
                ]
            )
        )
        authored_text_digest = (
            hashlib.sha256(
                normalized_exact_text(_source_text(source, None)).encode("utf-8")
            ).hexdigest()
            if signals.member_promise and _message_direction(source) == "outbound"
            else None
        )
        decided_at_text = str(
            trigger.get("recorded_at")
            or trigger.get("occurred_at")
            or "1970-01-01T00:00:00Z"
        )
        decided_at = datetime.fromisoformat(decided_at_text.replace("Z", "+00:00"))
        if decided_at.tzinfo is None:
            decided_at = decided_at.replace(tzinfo=timezone.utc)
        payload = TriageReconciliationDecidedV1(
            reconciliation_id="recon_" + identity[:32],
            proposal_id=proposal_id,
            primary_proposal_id=primary_proposal_id,
            matter_id=matter_id,
            owner_member_id=owner_member_id,
            source_event_id=str(proposal_payload["source_event_id"]),
            evidence_source_event_ids=evidence_ids,
            member_authored_text_digest=authored_text_digest,
            relationships=relationships,
            decision=decision,
            decision_revision=revision,
            supersedes_reconciliation_event_id=(
                str(prior["event_id"]) if prior is not None else None
            ),
            policy=policy,
            source_coordinate=coordinate,
            freshness=profile.source_freshness,
            priority_rank=priority_rank,
            priority_basis=priority_basis,
            reason_codes=list(
                dict.fromkeys(
                    [
                        f"admission_decision:{decision}",
                        "authorized_current_state_only",
                        "weak_similarity_has_no_automatic_effect",
                        "provider_urgency_and_model_confidence_excluded_from_priority",
                        *policy.reason_codes,
                        *relationship_reasons,
                        *extra_reasons,
                        "zero_provider_effects",
                    ]
                )
            )[:60],
            decided_at=decided_at,
            admission_active=self._admission_active,
        )
        envelope = EventEnvelope(
            event_at_ms=int(trigger["event_at_ms"]),
            tenant_id=str(proposal["tenant_id"]),
            type="triage.reconciliation.decided",
            schema_version=1,
            occurred_at=decided_at.isoformat(),
            source_adapter="cross-source-reconciliation",
            source_account_id="internal",
            owner_scope=owner_scope,
            visibility_scope=owner_scope,
            sensitivity=cast(
                Literal["normal", "sensitive", "privileged"],
                proposal.get("sensitivity") or "normal",
            ),
            actor_identity="cross-source-reconciliation",
            idempotency_key=idempotency_key,
            payload=payload.model_dump(mode="json"),
        )
        return await self._events.write(
            envelope,
            correlation_id=trigger.get("correlation_id"),
            causation_id=trigger_id,
        )

    async def _handle_proposal(
        self, proposal: Mapping[str, Any], *, trigger: Mapping[str, Any] | None = None
    ) -> None:
        proposal_payload = _payload(proposal)
        await self._events_of_type(
            "triage.reconciliation.decided",
            owner_scope=str(proposal.get("owner_scope") or ""),
        )
        source = await self._source(str(proposal_payload.get("source_event_id") or ""))
        if source is None:
            # A proposal cannot substitute for its immutable source fact. Keep
            # the pre-G03 queue behavior until source lineage is available.
            return
        profile = self._profile(source, proposal)
        prior_correspondence_event_id = await self._prior_correspondence(source)
        signals = assessed_signals(
            source,
            proposal,
            prior_member_correspondence=prior_correspondence_event_id is not None,
        )
        coordinate, policy = await self._policy_snapshot(source, signals)
        owner_scope = str(proposal.get("owner_scope") or "")
        primary_id: str | None = None
        relationships: list[ReconciliationEvidenceRelationshipV1] = []
        primary_profile: EvidenceProfile | None = None
        prior_proposals = await self._events_of_type(
            "triage.item.proposed", owner_scope=owner_scope
        )
        retired_proposal_ids = {
            str(_payload(event).get("proposal_id") or "")
            for event in await self._events_of_type(
                "triage.item.superseded", owner_scope=owner_scope
            )
            if event.get("tenant_id") == proposal.get("tenant_id")
        }
        for prior_proposal in prior_proposals:
            prior_payload = _payload(prior_proposal)
            if prior_payload.get("proposal_id") == proposal_payload.get("proposal_id"):
                continue
            if prior_payload.get("proposal_id") in retired_proposal_ids:
                # A retired interpretation cannot consume its replacement as
                # duplicate evidence. A racing member edit is preserved by
                # projection compare-and-apply; uncertainty here never closes
                # a different candidate through the old interpretation.
                continue
            if prior_proposal.get("tenant_id") != proposal.get("tenant_id"):
                continue
            if str(prior_proposal["event_id"]) >= str(proposal["event_id"]):
                # Append-order roots remain stable during retained-event replay;
                # a later proposal cannot become the first proposal's parent.
                continue
            latest = await self._latest_decision(
                str(prior_payload.get("proposal_id") or ""), owner_scope
            )
            if latest is not None and _payload(latest).get("decision") not in {
                "admit",
                "conflict_review",
                "unresolved",
            }:
                continue
            prior_source = await self._source(
                str(prior_payload.get("source_event_id") or "")
            )
            if prior_source is None:
                continue
            candidate_profile = self._profile(prior_source, prior_proposal)
            candidate_relationships = evidence_relationships(profile, candidate_profile)
            if not candidate_relationships:
                continue
            relationships.extend(candidate_relationships)
            if primary_id is None and any(
                item.automatic_effect
                and item.relationship_type in _IDENTITY_RELATIONSHIPS
                for item in candidate_relationships
            ):
                primary_id = str(prior_payload["proposal_id"])
                primary_profile = candidate_profile

        if primary_id is not None:
            relationships = [
                item
                for item in relationships
                if item.related_source_event_id
                == cast(EvidenceProfile, primary_profile).source_event_id
            ]
        decision = choose_decision(
            coordinate_exact=coordinate.exact,
            signals=signals,
            policy=policy,
            profile=profile,
            relationships=relationships,
            target_kind=str(proposal_payload.get("target_kind") or "") or None,
            as_of=datetime.fromisoformat(
                str(
                    (trigger or proposal).get("recorded_at")
                    or (trigger or proposal).get("occurred_at")
                    or "1970-01-01T00:00:00Z"
                ).replace("Z", "+00:00")
            ),
        )
        await self._emit_decision(
            proposal=proposal,
            source=source,
            trigger=trigger or proposal,
            profile=profile,
            relationships=relationships,
            decision=decision,
            primary_proposal_id=primary_id,
            signals=signals,
            coordinate=coordinate,
            policy=policy,
            extra_reasons=(
                ["known_correspondent_prior_interaction_verified"]
                if prior_correspondence_event_id
                else []
            ),
            extra_evidence_source_event_ids=(
                [prior_correspondence_event_id]
                if prior_correspondence_event_id is not None
                else []
            ),
        )

    async def _handle_member_intent(self, intent: Mapping[str, Any]) -> None:
        """Admit the primary cue without interpreting it as canonical work.

        A member intent already has a Triage row.  This synthetic proposal view
        exists only so the established source-policy and reconciliation seam
        can issue its immutable admission receipt.  The source remains the
        authenticated intent event itself; no target kind or provider authority
        is invented here.
        """

        if intent.get("source_adapter") != "direct-intake":
            return
        payload = _payload(intent)
        intent_id = str(payload.get("intent_id") or "")
        member_id = str(payload.get("member_id") or "")
        text = str(payload.get("text") or "")
        if not intent_id or not member_id or not text:
            return
        owner_scope = f"private:{member_id}"
        if (
            intent.get("owner_scope") != owner_scope
            or intent.get("visibility_scope") != owner_scope
        ):
            return
        proposal = {
            **intent,
            "payload": {
                "proposal_id": intent_id,
                "owner_member_id": member_id,
                "target_kind": payload.get("kind_hint"),
                "title": text,
                "candidate": {
                    "source_surface": payload.get("source_surface"),
                    "exact_text": text,
                },
                "route": {"member_id": member_id},
                "source_event_id": str(intent["event_id"]),
                "confidence": None,
                "reasons": ["authenticated_member_intent"],
            },
        }
        await self._handle_proposal(proposal)

    async def _handle_state_evidence(self, trigger: Mapping[str, Any]) -> None:
        trigger_profile = evidence_profile(trigger)
        if (
            trigger_profile.current_state
            not in _TERMINAL_SATISFIED | _TERMINAL_SUPERSEDED
        ):
            return
        targets: list[str]
        trigger_payload = _payload(trigger)
        if trigger.get("type") == "context.reconciliation.typed_fact_shared":
            if (
                trigger.get("owner_scope") != "shared:household"
                or trigger.get("visibility_scope") != "shared:household"
                or trigger.get("actor_identity")
                != trigger_payload.get("source_member_id")
            ):
                return
            targets = [
                str(value)
                for value in trigger_payload.get("shared_with_member_ids") or []
            ]
        else:
            member = _member_id(trigger)
            targets = [member] if member else []
        for member_id in targets:
            owner_scope = f"private:{member_id}"
            proposals = await self._events_of_type(
                "triage.item.proposed", owner_scope=owner_scope
            )
            for proposal in proposals:
                if proposal.get("tenant_id") != trigger.get("tenant_id"):
                    continue
                proposal_payload = _payload(proposal)
                source = await self._source(
                    str(proposal_payload.get("source_event_id") or "")
                )
                if source is None:
                    continue
                proposal_profile = self._profile(source, proposal)
                relationships = evidence_relationships(
                    trigger_profile, proposal_profile
                )
                if not any(
                    item.automatic_effect
                    and item.relationship_type in _IDENTITY_RELATIONSHIPS
                    for item in relationships
                ):
                    continue
                prior_correspondence_event_id = await self._prior_correspondence(source)
                signals = assessed_signals(
                    source,
                    proposal,
                    prior_member_correspondence=prior_correspondence_event_id
                    is not None,
                )
                coordinate, policy = await self._policy_snapshot(source, signals)
                decision: AdmissionDecision = (
                    "already_satisfied"
                    if trigger_profile.current_state in _TERMINAL_SATISFIED
                    else "superseded"
                )
                await self._emit_decision(
                    proposal=proposal,
                    source=source,
                    trigger=trigger,
                    profile=trigger_profile,
                    relationships=relationships,
                    decision=decision,
                    primary_proposal_id=None,
                    signals=signals,
                    coordinate=coordinate,
                    policy=policy,
                    extra_reasons=[
                        "explicitly_shared_typed_fact_only"
                        if trigger_profile.explicitly_shared
                        else "same_member_authorized_state"
                    ],
                    extra_evidence_source_event_ids=(
                        [prior_correspondence_event_id]
                        if prior_correspondence_event_id is not None
                        else []
                    ),
                )

    async def handle(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "triage.item.proposed":
            await self._handle_proposal(event)
        elif event_type == "triage.intent.submitted":
            await self._handle_member_intent(event)
        elif event_type == "context.reconciliation.typed_fact_shared":
            await self._handle_state_evidence(event)
        elif event_type == "settings.source_policy_v2_changed":
            owner_scope = str(event.get("owner_scope") or "")
            if (
                not owner_scope.startswith("private:")
                or event.get("visibility_scope") != owner_scope
            ):
                return
            for proposal in await self._events_of_type(
                "triage.item.proposed", owner_scope=owner_scope
            ):
                if proposal.get("tenant_id") == event.get("tenant_id"):
                    await self._handle_proposal(proposal, trigger=event)
        elif event_type != "triage.reconciliation.decided":
            await self._handle_state_evidence(event)


__all__ = [
    "EvidenceProfile",
    "ReconciliationAdmissionEvaluator",
    "assessed_signals",
    "choose_decision",
    "evidence_profile",
    "evidence_relationships",
    "priority_for",
    "reference_digest",
]
