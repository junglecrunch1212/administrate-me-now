"""Evidence-grounded personal-action interpretation, without domain authority.

The language seam supplies meaning. This boundary verifies exact citations and
keeps uncertainty, date roles and conditionality separate from source routing.
It does not read providers, assign a household identity, or materialize work.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError


ASSESSMENT_CONTRACT = "personal-obligation-assessment-v1"
MessagePurpose = Literal[
    "personal_request",
    "member_promise",
    "appointment",
    "preparation",
    "payment",
    "account_alert",
    "optional_opportunity",
    "status_update",
    "receipt",
    "survey",
    "reference",
    "recurrence",
    "unknown",
]
Actionability = Literal["required", "conditional", "informational", "none", "unknown"]


class ObligationEvidenceV1(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}
    source_event_id: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=1, max_length=1000)
    role: Literal[
        "action",
        "condition",
        "negation",
        "completion",
        "date",
        "ownership",
        "recurrence",
    ]


class PersonalObligationAssessmentV1(BaseModel):
    model_config = {"extra": "forbid", "frozen": True}
    contract: Literal["personal-obligation-assessment-v1"] = (
        "personal-obligation-assessment-v1"
    )
    purpose: MessagePurpose = "unknown"
    actionability: Actionability = "unknown"
    condition: str | None = Field(default=None, max_length=500)
    condition_state: Literal["met", "unmet", "unknown", "not_applicable"] = "unknown"
    beneficiary: str | None = Field(default=None, max_length=160)
    date_role: Literal[
        "deadline", "occurrence", "expiry", "reporting", "none", "unknown"
    ] = "unknown"
    evidence: list[ObligationEvidenceV1] = Field(min_length=1, max_length=9)
    independent_obligation: bool = False
    verified_date_fields: list[str] = Field(default_factory=list, max_length=5)
    recurrence_grounded: bool = False
    evidence_verified: Literal[True] = True


def date_is_supported(value: Any, quotes: list[str]) -> bool:
    """Verify a normalized calendar day against explicit date evidence.

    Relative phrases remain reviewable but are not promoted to a verified
    deadline. No wall-clock date, guessed year, or model confidence is used.
    """
    if not isinstance(value, str) or len(value) < 10:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    variants = {
        parsed.date().isoformat(),
        f"{parsed.month}/{parsed.day}/{parsed.year}",
        f"{parsed.month:02}/{parsed.day:02}/{parsed.year}",
        f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}",
        f"{parsed.strftime('%B')} {parsed.day} {parsed.year}",
        f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}",
        f"{parsed.day} {parsed.strftime('%B')} {parsed.year}",
    }
    return any(
        variant.casefold() in " ".join(quote.split()).casefold()
        for variant in variants
        for quote in quotes
    )


def ground_obligation_assessment(
    raw: Any,
    *,
    source_texts: Mapping[str, tuple[str, str]],
    primary_alias: str,
    primary_quote: str,
    action_owner: str,
    fields: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Resolve bounded model aliases to exact authorized source citations.

    ``source_texts`` must be built by the caller's scoped context builder. Raw
    model event IDs and model-supplied verification booleans are never accepted.
    """
    if not isinstance(raw, Mapping) or primary_alias not in source_texts:
        return None
    primary_id, primary_text = source_texts[primary_alias]
    if not primary_quote or primary_quote not in primary_text:
        return None
    evidence = [
        ObligationEvidenceV1(
            source_event_id=primary_id, quote=primary_quote, role="action"
        )
    ]
    qualifiers = raw.get("qualifying_evidence", [])
    if not isinstance(qualifiers, list) or len(qualifiers) > 8:
        return None
    try:
        for item in qualifiers:
            if not isinstance(item, Mapping):
                return None
            source = source_texts.get(str(item.get("source_id") or ""))
            quote = item.get("quote")
            if (
                source is None
                or not isinstance(quote, str)
                or not quote
                or quote not in source[1]
            ):
                return None
            evidence.append(
                ObligationEvidenceV1.model_validate(
                    {
                        "source_event_id": source[0],
                        "quote": quote,
                        "role": item.get("role"),
                    }
                )
            )
        assessment = PersonalObligationAssessmentV1.model_validate(
            {
                "purpose": raw.get("purpose", "unknown"),
                "actionability": raw.get("actionability", "unknown"),
                "condition": raw.get("condition"),
                "condition_state": raw.get("condition_state", "unknown"),
                "beneficiary": raw.get("beneficiary"),
                "date_role": raw.get("date_role", "unknown"),
                "evidence": evidence,
            }
        )
    except (ValidationError, TypeError, ValueError):
        return None
    roles = {item.role for item in evidence}
    actionability = assessment.actionability
    if assessment.condition and "condition" not in roles:
        return None
    if assessment.condition_state in {"unmet", "unknown"} and assessment.condition:
        actionability = "conditional"
    elif actionability == "required" and assessment.condition_state == "unknown":
        actionability = "unknown"
    if roles.intersection({"negation", "completion"}):
        # These roles explicitly qualify this action. Another action in the
        # same receipt or conversation receives its own assessment.
        actionability = "none"
    independent = (
        actionability == "required"
        and action_owner in {"member", "group_including_member"}
        and assessment.condition_state in {"met", "not_applicable"}
        and assessment.purpose
        not in {
            "status_update",
            "receipt",
            "survey",
            "reference",
            "optional_opportunity",
            "unknown",
        }
        and not roles.intersection({"negation", "completion"})
    )
    date_quotes = [item.quote for item in evidence if item.role == "date"]
    verified_dates = [
        key
        for key in ("due", "start", "end", "next_occurrence")
        if date_is_supported(fields.get(key), date_quotes)
    ]
    return assessment.model_copy(
        update={
            "actionability": actionability,
            "independent_obligation": independent,
            "verified_date_fields": verified_dates,
            "recurrence_grounded": "recurrence" in roles and independent,
        }
    ).model_dump(mode="json")


def assessment_from_interpretation(
    interpretation: Any,
) -> PersonalObligationAssessmentV1 | None:
    """Read a persisted validated assessment, not an arbitrary source flag."""
    if (
        not isinstance(interpretation, Mapping)
        or interpretation.get("contract") != "triage-context-interpretation-v3"
    ):
        return None
    try:
        return PersonalObligationAssessmentV1.model_validate(
            interpretation.get("obligation_assessment")
        )
    except (ValidationError, TypeError, ValueError):
        return None


def assessment_for_proposal(
    proposal: Mapping[str, Any] | None,
) -> PersonalObligationAssessmentV1 | None:
    if not isinstance(proposal, Mapping):
        return None
    # Only the code-owned proposal producer may attach validated assessments.
    # Provider payloads with identically named fields carry no such authority.
    if (
        proposal.get("type") != "triage.item.proposed"
        or proposal.get("source_adapter") != "deterministic_candidate_eligibility"
    ):
        return None
    payload = proposal.get("payload")
    if not isinstance(payload, Mapping):
        return None
    return assessment_from_interpretation(payload.get("interpretation"))
