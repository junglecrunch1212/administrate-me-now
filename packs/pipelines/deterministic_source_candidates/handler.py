"""Human-gated candidates from typed financial and context source facts.

This pack contains no language call and no routing discretion.  It has two
closed rules:

* three exact-payee settled charges with weekly/monthly spacing (or two with
  annual spacing) may propose one recurring-payment review item; and
* a member-bound Home Assistant context signal may propose only the exact
  Task, Reminder, or Recurrence declared by a reviewed adapter rule snapshot.

Both paths emit a terminal assessment for every source fact.  A match creates
only a private Triage proposal; it never creates canonical work or performs a
provider write.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

from dateutil.relativedelta import relativedelta

from adminme.candidate_policy import default_policy, source_id_for_event
from adminme.events.envelope import EventEnvelope
from adminme.events.schemas.triage import TriageTargetKind
from adminme.pipelines.base import PipelineContext, Triggers
from adminme.triage_intent import CANDIDATE_FIELD_ALLOWLISTS


_FINANCIAL_EVENTS = {
    "money_flow.provider_observed",
    "money_flow.provider_removed",
    "financial.account_observed",
}
_RULE_TARGETS = {"task", "reminder", "recurrence"}


class StructuredCandidate:
    __slots__ = ("key", "target_kind", "title", "fields", "rule_id", "reason")

    def __init__(
        self,
        *,
        key: str,
        target_kind: TriageTargetKind,
        title: str,
        fields: dict[str, Any],
        rule_id: str,
        reason: str,
    ) -> None:
        self.key = key
        self.target_kind = target_kind
        self.title = title
        self.fields = fields
        self.rule_id = rule_id
        self.reason = reason

    def feature_payload(self) -> dict[str, Any]:
        return {
            "candidate_key": self.key,
            "target_kind": self.target_kind,
            "title": self.title,
            "fields": self.fields,
            "evidence_spans": [],
            "confidence": 1.0,
            "reasons": [self.reason],
        }


def _recorded_at(event: dict[str, Any]) -> str:
    return str(
        event.get("recorded_at")
        or event.get("occurred_at")
        or datetime.fromtimestamp(
            int(event.get("event_at_ms") or 0) / 1000, timezone.utc
        ).isoformat()
    )


def _private_member(event: dict[str, Any]) -> str | None:
    owner_scope = str(event.get("owner_scope") or "")
    if not owner_scope.startswith("private:"):
        return None
    member_id = owner_scope.removeprefix("private:")
    payload_member = (event.get("payload") or {}).get("member_id")
    if not member_id or (payload_member is not None and payload_member != member_id):
        return None
    return member_id


def _payee(payload: dict[str, Any]) -> tuple[str, str] | None:
    display = str(
        payload.get("merchant_name") or payload.get("original_description") or ""
    ).strip()
    normalized = re.sub(r"\s+", " ", display).casefold()
    if len(normalized) < 3:
        return None
    return display[:200], normalized


def _flow_date(payload: dict[str, Any]) -> date | None:
    try:
        return datetime.fromisoformat(
            str(payload.get("occurred_at") or "").replace("Z", "+00:00")
        ).date()
    except ValueError:
        try:
            return date.fromisoformat(str(payload.get("occurred_at") or "")[:10])
        except ValueError:
            return None


def _cadence(dates: list[date]) -> tuple[str, str, date] | None:
    unique = sorted(set(dates))
    if len(unique) >= 3:
        last = unique[-3:]
        gaps = [(right - left).days for left, right in zip(last, last[1:])]
        if all(5 <= gap <= 9 for gap in gaps):
            return "weekly", "FREQ=WEEKLY", unique[-1] + timedelta(days=7)
        if all(20 <= gap <= 40 for gap in gaps):
            return "monthly", "FREQ=MONTHLY", unique[-1] + relativedelta(months=1)
    if len(unique) >= 2 and 330 <= (unique[-1] - unique[-2]).days <= 400:
        return "annual", "FREQ=YEARLY", unique[-1] + relativedelta(years=1)
    return None


def _rule_matches(rule: dict[str, Any], payload: dict[str, Any]) -> bool:
    operator = str(rule.get("operator") or "")
    state = str(payload.get("state") or "")
    if operator == "present":
        return True
    if operator == "equals":
        return state == str(rule.get("expected_state") or "")
    if operator == "not_equals":
        return state != str(rule.get("expected_state") or "")
    if operator in {"entered", "left"}:
        return state == operator
    value = payload.get("numeric_value")
    expected = rule.get("expected_number")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return False
    return (
        value > expected
        if operator == "above"
        else value < expected
        if operator == "below"
        else False
    )


class DeterministicSourceCandidatesPipeline:
    pack_id = "pipeline:deterministic_source_candidates"
    version = "2.0.0"
    triggers: Triggers = {"events": [*_FINANCIAL_EVENTS, "context.signal.observed"]}

    async def _recurring_payment_candidate(
        self, event: dict[str, Any], ctx: PipelineContext, member_id: str
    ) -> StructuredCandidate | None:
        payload = event.get("payload") or {}
        if (
            event.get("type") != "money_flow.provider_observed"
            or payload.get("kind") != "paid"
            or bool(payload.get("pending"))
        ):
            return None
        current_payee = _payee(payload)
        if current_payee is None:
            return None
        display_payee, normalized_payee = current_payee

        observed: dict[str, dict[str, Any]] = {}
        removed: set[str] = set()
        async for candidate_event in ctx.event_log.read_since(
            types=["money_flow.provider_observed", "money_flow.provider_removed"]
        ):
            if candidate_event.get("tenant_id") != event.get(
                "tenant_id"
            ) or candidate_event.get("owner_scope") != event.get("owner_scope"):
                continue
            candidate_payload = candidate_event.get("payload") or {}
            if candidate_payload.get("member_id") != member_id:
                continue
            transaction_id = str(candidate_payload.get("transaction_id") or "")
            if not transaction_id:
                continue
            if candidate_event.get("type") == "money_flow.provider_removed":
                removed.add(transaction_id)
                observed.pop(transaction_id, None)
                continue
            if transaction_id not in removed:
                observed[transaction_id] = candidate_payload

        matches = [
            row
            for row in observed.values()
            if row.get("kind") == "paid"
            and not bool(row.get("pending"))
            and (_payee(row) or ("", ""))[1] == normalized_payee
        ]
        matched_dates = [value for row in matches if (value := _flow_date(row))]
        cadence = _cadence(matched_dates)
        if cadence is None:
            return None
        cadence_label, rrule, next_occurrence = cadence
        amounts = [int(row["amount_minor"]) for row in matches]
        candidate_seed = f"{member_id}|{normalized_payee}|{cadence_label}"
        candidate_key = (
            "recurring-payment:"
            + hashlib.sha256(candidate_seed.encode()).hexdigest()[:24]
        )
        notes = (
            f"Deterministic review: {len(matches)} settled payments with exact "
            f"payee identity and {cadence_label} spacing. Observed amount range "
            f"{min(amounts)}–{max(amounts)} minor currency units. Confirm timing, "
            "autopay state, and whether occurrences should require completion."
        )
        return StructuredCandidate(
            key=candidate_key,
            target_kind="recurrence",
            title=f"Recurring payment: {display_payee}",
            fields={
                "recurrence_text": cadence_label,
                "rrule": rrule,
                "next_occurrence": datetime.combine(
                    next_occurrence, datetime.min.time(), tzinfo=timezone.utc
                ).isoformat(),
                "kind": "recurring_bill",
                "lead_time_days": 7,
                "trackable": False,
                "linked_kind": "account",
                "linked_id": str(payload.get("account_id") or member_id),
                "notes": notes,
            },
            rule_id="structured_finance.exact_payee_cadence.v1",
            reason="Exact settled-payee observations match a reviewed cadence window",
        )

    def _context_candidates(self, event: dict[str, Any]) -> list[StructuredCandidate]:
        payload = event.get("payload") or {}
        attributes = payload.get("attributes") or {}
        raw_rules = (
            attributes.get("_administrateme_candidate_rules")
            if isinstance(attributes, dict)
            else None
        )
        if not isinstance(raw_rules, list):
            return []
        candidates: list[StructuredCandidate] = []
        seen: set[str] = set()
        for raw_rule in raw_rules[:10]:
            if not isinstance(raw_rule, dict):
                continue
            rule_id = str(raw_rule.get("rule_id") or "")
            target_kind = str(raw_rule.get("target_kind") or "")
            title = str(raw_rule.get("title") or "").strip()
            if (
                not rule_id
                or rule_id in seen
                or target_kind not in _RULE_TARGETS
                or not title
                or len(title) > 1000
                or not _rule_matches(raw_rule, payload)
            ):
                continue
            seen.add(rule_id)
            raw_fields = raw_rule.get("candidate")
            allowlist = CANDIDATE_FIELD_ALLOWLISTS[target_kind]
            fields = {
                str(key): value
                for key, value in (
                    raw_fields.items() if isinstance(raw_fields, dict) else []
                )
                if str(key) in allowlist
            }
            candidates.append(
                StructuredCandidate(
                    key=f"context-rule:{rule_id}",
                    target_kind=cast(TriageTargetKind, target_kind),
                    title=title,
                    fields=fields,
                    rule_id=f"reviewed_context_rule:{rule_id}",
                    reason="Reviewed typed context rule matched the observed signal",
                )
            )
        return candidates

    async def _emit_assessment(
        self,
        event: dict[str, Any],
        ctx: PipelineContext,
        candidates: list[StructuredCandidate],
        *,
        reason_code: str,
    ) -> str:
        source_event_id = str(event["event_id"])
        assessed_at = _recorded_at(event)
        envelope = EventEnvelope(
            event_at_ms=int(event["event_at_ms"]),
            tenant_id=str(event["tenant_id"]),
            type="triage.intent.features_extracted",
            schema_version=3,
            occurred_at=assessed_at,
            source_adapter="deterministic_source_candidates",
            source_account_id="pipeline",
            owner_scope=str(event["owner_scope"]),
            visibility_scope=str(event["visibility_scope"]),
            sensitivity=event.get("sensitivity", "normal"),
            idempotency_key=f"triage:structured-source:v2:{source_event_id}",
            payload={
                "intent_id": f"source:{source_event_id}",
                "source_event_id": source_event_id,
                "skill_call_event_id": None,
                "extractor_id": "deterministic-structured-source-rules-v1",
                "language": None,
                "candidates": [candidate.feature_payload() for candidate in candidates],
                "extraction_state": "complete",
                "extracted_at": assessed_at,
                "assessment_outcome": "eligible_candidates"
                if candidates
                else "no_candidate",
                "reason_codes": [reason_code],
            },
        )
        return await ctx.event_sink.write(
            envelope,
            correlation_id=ctx.correlation_id,
            causation_id=source_event_id,
        )

    async def handle(self, event: dict[str, Any], ctx: PipelineContext) -> None:
        event_type = str(event.get("type") or "")
        if event_type not in _FINANCIAL_EVENTS | {"context.signal.observed"}:
            return
        member_id = _private_member(event)
        if member_id is None:
            await self._emit_assessment(
                event, ctx, [], reason_code="source_member_scope_mismatch"
            )
            return
        policy_source_id = source_id_for_event(event) or (
            "adapter:home_assistant"
            if event_type == "context.signal.observed"
            else "adapter:plaid"
        )
        policy = (
            ctx.candidate_policy_fn(member_id, policy_source_id)
            if ctx.candidate_policy_fn is not None
            else default_policy(policy_source_id)
        )

        candidates: list[StructuredCandidate]
        if event_type == "money_flow.provider_observed":
            recurring = await self._recurring_payment_candidate(event, ctx, member_id)
            candidates = [recurring] if recurring is not None else []
            reason = (
                "exact_payee_cadence_matched"
                if candidates
                else "financial_fact_has_no_reviewed_future_obligation_pattern"
            )
        elif event_type == "context.signal.observed":
            candidates = self._context_candidates(event)
            reason = (
                "reviewed_context_rule_matched"
                if candidates
                else "no_reviewed_context_rule_matched"
            )
        else:
            candidates = []
            reason = "canonical_financial_fact_requires_no_candidate"

        if policy.get("mode") != "review":
            candidates = []
            reason = (
                "candidate_policy_manual_only"
                if policy.get("mode") == "manual"
                else "candidate_policy_quiet"
            )
        else:
            allowed_kinds = set(policy.get("candidate_kinds") or [])
            candidates = [
                candidate
                for candidate in candidates
                if candidate.target_kind in allowed_kinds
            ]

        extraction_event_id = await self._emit_assessment(
            event, ctx, candidates, reason_code=reason
        )
        for candidate in candidates:
            stable_seed = f"{member_id}|{candidate.key}|{candidate.target_kind}"
            stable_hash = hashlib.sha256(stable_seed.encode()).hexdigest()[:28]
            proposal_id = f"triage:structured:{stable_hash}"
            envelope = EventEnvelope(
                event_at_ms=int(event["event_at_ms"]),
                tenant_id=str(event["tenant_id"]),
                type="triage.item.proposed",
                schema_version=2,
                occurred_at=_recorded_at(event),
                source_adapter="deterministic_source_candidates",
                source_account_id="pipeline",
                owner_scope=str(event["owner_scope"]),
                visibility_scope=str(event["visibility_scope"]),
                sensitivity=event.get("sensitivity", "normal"),
                idempotency_key=f"triage:structured-candidate:v2:{stable_hash}",
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": candidate.target_kind,
                    "owner_member_id": member_id,
                    "title": candidate.title,
                    "candidate": candidate.fields,
                    "route": {"member_id": member_id},
                    "source_event_id": str(event["event_id"]),
                    "confidence": 1.0,
                    "reasons": [
                        candidate.reason,
                        f"candidate_policy_v1:{policy_source_id}:review",
                    ],
                    "parent_intent_id": None,
                    "candidate_key": candidate.key,
                    "extraction_event_id": extraction_event_id,
                    "eligibility_rule_ids": [
                        candidate.rule_id,
                        f"candidate_policy_v1:{policy_source_id}:review",
                    ],
                },
            )
            await ctx.event_sink.write(
                envelope,
                correlation_id=ctx.correlation_id,
                causation_id=extraction_event_id,
            )


__all__ = ["DeterministicSourceCandidatesPipeline"]
