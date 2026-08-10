"""Read-time, source-grounded context for one Triage decision.

The context is derived from the authorized immutable source event and the
candidate's fixed eligibility evidence.  It is deliberately not stored as
canonical truth. The member sees provider/channel, counterparty, subject,
exact evidence, extracted fields, and the extraction layers that produced the
proposal. Conversation-aware proposals may add a bounded language
interpretation, always labelled separately from exact source evidence and
re-authorized source-by-source at read time.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adminme.candidate_policy import source_id_for_event
from adminme.lib.scope import allowed_read


_FIELD_LABELS = {
    "due": "Due",
    "due_date": "Due",
    "start": "Starts",
    "end": "Ends",
    "next_occurrence": "Next occurrence",
    "recurrence_text": "Repeats",
    "rrule": "Repeat rule",
    "location": "Location",
    "priority": "Priority",
    "kind": "Kind",
    "value": "Value",
    "display_name": "Name",
}


def _first(payload: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _source_text(payload: Mapping[str, Any]) -> str:
    subject = _first(payload, "subject", "title")
    body = _first(payload, "body_text", "body", "description", "notes", "text")
    if subject and body:
        return f"Subject: {subject}\n\n{body}"
    return subject or body or ""


def _exact_quote(item: Mapping[str, Any], payload: Mapping[str, Any]) -> str | None:
    candidate = item.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    text = _source_text(payload)
    for value in (
        candidate.get("source_message_preview"),
        candidate.get("evidence_quote"),
        candidate.get("description"),
        candidate.get("notes"),
        item.get("title"),
    ):
        if not isinstance(value, str):
            continue
        quote = value.strip()
        if quote and quote in text:
            return quote[:1000]
    return None


def _methods(item: Mapping[str, Any]) -> list[dict[str, str]]:
    rules = [str(value) for value in item.get("eligibility_rule_ids") or []]
    values: list[dict[str, str]] = [
        {
            "id": "raw",
            "label": "Provider facts",
            "detail": "Channel, sender, account, timestamps, and native fields",
        }
    ]
    if any(
        token in rule
        for rule in rules
        for token in ("explicit_", "fallback", "structured_", "reviewed_context")
    ):
        values.append(
            {
                "id": "rules",
                "label": "Deterministic rules",
                "detail": "Versioned code matched an allowlisted signal",
            }
        )
    if any("grounded" in rule or "language" in rule for rule in rules):
        values.append(
            {
                "id": "language",
                "label": "Grounded language",
                "detail": "OpenClaw returned an exact quote and typed fields only",
            }
        )
    return values


def compose_triage_source_context(
    item: Mapping[str, Any],
    source_event: Mapping[str, Any],
    context_events: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = source_event.get("payload")
    payload = payload if isinstance(payload, Mapping) else {}
    event_type = str(source_event.get("type") or "unknown")
    direction = "outbound" if event_type == "messaging.sent" else "inbound"
    labels = {str(value).upper() for value in payload.get("provider_labels") or []}
    if "SENT" in labels:
        direction = "outbound"
    channel = _first(payload, "source_channel", "source_kind")
    if channel is None:
        channel = (
            "finance"
            if event_type.startswith("money_flow.")
            else "context signal"
            if event_type.startswith("context.")
            else "member entry"
            if event_type == "triage.intent.submitted"
            else event_type.split(".", 1)[0]
        )
    sender = _first(
        payload,
        "to_identifier" if direction == "outbound" else "from_identifier",
        "from_number",
        "merchant_name",
        "original_description",
    )
    subject = _first(payload, "subject", "title")
    candidate = item.get("candidate")
    candidate = candidate if isinstance(candidate, Mapping) else {}
    extracted_fields = [
        {"field": key, "label": _FIELD_LABELS[key], "value": value}
        for key, value in candidate.items()
        if key in _FIELD_LABELS and value is not None and value != "" and value != []
    ][:8]
    quote = _exact_quote(item, payload)
    policy_rule = next(
        (
            str(rule)
            for rule in item.get("eligibility_rule_ids") or []
            if str(rule).startswith("candidate_policy_v1:")
        ),
        None,
    )
    interpretation = item.get("interpretation")
    interpretation = interpretation if isinstance(interpretation, Mapping) else None
    context_events = context_events or {}
    evidence_refs: list[dict[str, Any]] = []
    source_chain: list[dict[str, Any]] = []
    if interpretation is not None:
        for ref in interpretation.get("evidence_refs") or []:
            if not isinstance(ref, Mapping):
                continue
            ref_event_id = str(ref.get("source_event_id") or "")
            ref_event = context_events.get(ref_event_id)
            if ref_event is None:
                continue
            ref_payload = ref_event.get("payload")
            ref_payload = ref_payload if isinstance(ref_payload, Mapping) else {}
            ref_quote = str(ref.get("quote") or "").strip()
            if ref_quote and ref_quote in _source_text(ref_payload):
                evidence_refs.append(
                    {"source_event_id": ref_event_id, "quote": ref_quote[:1000]}
                )
        source_contracts = {
            str(value.get("source_event_id") or ""): value
            for value in interpretation.get("context_sources") or []
            if isinstance(value, Mapping)
        }
        for event_id in interpretation.get("context_source_event_ids") or []:
            event_id = str(event_id)
            context_event = context_events.get(event_id)
            if context_event is None:
                continue
            context_payload = context_event.get("payload")
            context_payload = (
                context_payload if isinstance(context_payload, Mapping) else {}
            )
            context_type = str(context_event.get("type") or "")
            source_contract = source_contracts.get(event_id, {})
            context_direction = (
                "outbound" if context_type == "messaging.sent" else "inbound"
            )
            source_chain.append(
                {
                    "source_event_id": event_id,
                    "relation": source_contract.get("relation") or "thread",
                    "relation_reason_codes": source_contract.get("reason_codes") or [],
                    "direction": context_direction,
                    "channel": _first(context_payload, "source_channel") or "message",
                    "sender": _first(
                        context_payload,
                        "to_identifier"
                        if context_direction == "outbound"
                        else "from_identifier",
                        "from_number",
                    ),
                    "subject": _first(context_payload, "subject"),
                    "occurred_at": context_event.get("occurred_at"),
                }
            )
    evidence = {
        "quote": evidence_refs[0]["quote"] if evidence_refs else quote,
        "exactly_grounded": bool(evidence_refs) or quote is not None,
    }
    if interpretation is not None:
        evidence["quotes"] = evidence_refs
    return {
        "contract": "triage-source-context-v2",
        "source_event_id": source_event.get("event_id"),
        "source_event_type": event_type,
        "source_id": source_id_for_event(source_event),
        "adapter": source_event.get("source_adapter"),
        "account": source_event.get("source_account_id"),
        "channel": channel,
        "direction": direction
        if event_type.startswith(("messaging.", "telephony."))
        else None,
        "sender": sender,
        "subject": subject,
        "occurred_at": source_event.get("occurred_at"),
        "meaning": {
            "target_kind": item.get("target_kind"),
            "summary": (
                interpretation.get("compact_summary")
                if interpretation is not None
                else item.get("title")
            ),
            "extracted_fields": extracted_fields,
        },
        "evidence": evidence,
        "interpretation": (
            {
                "label": "Context summary",
                "case_summary": interpretation.get("case_summary"),
                "why_member": interpretation.get("why_member"),
                "next_step": interpretation.get("next_step"),
                "thread_state": interpretation.get("thread_state"),
                "action_owner": interpretation.get("action_owner"),
                "lifecycle_state": interpretation.get("lifecycle_state"),
                "unresolved_questions": interpretation.get("unresolved_questions")
                or [],
                "source_chain": source_chain,
            }
            if interpretation is not None
            else None
        ),
        "methods": _methods(item),
        "policy_rule": policy_rule,
    }


async def add_triage_source_context(
    runtime: Any, session: Any, surface: dict[str, Any]
) -> None:
    """Augment already-scoped Triage rows with authorized source metadata."""

    if runtime.event_log is None:
        return
    cache: dict[str, dict[str, Any] | None] = {}
    items = [*(surface.get("items") or []), *(surface.get("recent_items") or [])]
    requested_ids: set[str] = set()
    for item in items:
        requested_ids.add(str(item.get("source_event_id") or ""))
        interpretation = item.get("interpretation")
        if isinstance(interpretation, Mapping):
            requested_ids.update(
                str(value)
                for value in interpretation.get("context_source_event_ids") or []
            )
    for event_id in requested_ids:
        if event_id:
            cache[event_id] = await runtime.event_log.get(event_id)
    for item in items:
        event_id = str(item.get("source_event_id") or "")
        if not event_id:
            item["source_context"] = {"state": "unavailable"}
            continue
        source = cache[event_id]
        if source is None:
            item["source_context"] = {"state": "unavailable"}
            continue
        visibility = str(source.get("visibility_scope") or "shared:household")
        owner = str(source.get("owner_scope") or "shared:household")
        sensitivity = str(source.get("sensitivity") or "normal")
        if visibility not in session.allowed_scopes or not allowed_read(
            session, sensitivity, owner
        ):
            item["source_context"] = {"state": "withheld"}
            continue
        authorized_context: dict[str, Mapping[str, Any]] = {}
        interpretation = item.get("interpretation")
        if isinstance(interpretation, Mapping):
            for context_id in interpretation.get("context_source_event_ids") or []:
                context_id = str(context_id)
                context_source = cache.get(context_id)
                if context_source is None:
                    continue
                context_visibility = str(
                    context_source.get("visibility_scope") or "shared:household"
                )
                context_owner = str(
                    context_source.get("owner_scope") or "shared:household"
                )
                context_sensitivity = str(context_source.get("sensitivity") or "normal")
                if context_visibility in session.allowed_scopes and allowed_read(
                    session, context_sensitivity, context_owner
                ):
                    authorized_context[context_id] = context_source
        item["source_context"] = {
            "state": "available",
            **compose_triage_source_context(item, source, authorized_context),
        }


__all__ = ["add_triage_source_context", "compose_triage_source_context"]
