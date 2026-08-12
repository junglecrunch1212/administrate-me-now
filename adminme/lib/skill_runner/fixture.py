"""Deterministic, credential-free OpenClaw llm-task fixture transport."""

from __future__ import annotations

import json
from typing import Any

import httpx

from adminme.events.log import EventLog
from adminme.events.writer import EventSink
from adminme.lib.skill_runner.wrapper import _Runtime


def _response(request: httpx.Request) -> httpx.Response:
    body: dict[str, Any] = json.loads(request.content)
    args = body["args"]
    title = str(args["schema"].get("title", ""))
    inputs = args["input"]
    message_text = str(inputs.get("message_text", ""))
    triage_kinds = (
        "task",
        "commitment",
        "calendar_event",
        "reminder",
        "list",
        "list_item",
        "recurrence",
        "party_identifier",
        "party_candidate",
        "party_relationship",
        "relationship_fact",
    )
    if "[fixture:malformed]" in message_text:
        return httpx.Response(200, json={"ok": True, "result": {"details": {}}})
    if "[fixture:unreachable]" in message_text:
        return httpx.Response(503, json={"error": "fixture unavailable"})
    if "[fixture:timeout]" in message_text:
        raise httpx.ReadTimeout("fixture timeout", request=request)
    if title.startswith("classify_commitment_candidate"):
        output = {
            "is_candidate": True,
            "confidence": 0.96,
            "reasons": ["explicit request for a reply"],
        }
    elif title.startswith("extract_commitment_fields"):
        output = {
            "kind": "reply",
            "owed_by_member_id": inputs["receiving_member_id"],
            "owed_to_party_id": inputs["sender_party_id"],
            "text_summary": "Reply to the household message",
            "suggested_due": "2026-08-01T17:00:00Z",
            "urgency": "today",
            "confidence": 0.96,
        }
    elif title.startswith("classify_message_nature"):
        body_text = str(inputs.get("body_text", ""))
        candidates = []
        context_pack = inputs.get("context_pack")
        context_pack = context_pack if isinstance(context_pack, dict) else {}
        context_messages = [
            message
            for message in context_pack.get("messages") or []
            if isinstance(message, dict)
        ]

        def contextual_candidate(
            quote: str,
            *,
            target_kind: str,
            compact_summary: str,
            action_owner: str = "member",
        ) -> dict[str, Any]:
            [source] = [
                message
                for message in context_messages
                if quote in str(message.get("body_text") or "")
            ]
            return {
                "target_kind": target_kind,
                "title": compact_summary,
                "evidence_source_id": source["source_id"],
                "evidence_quote": quote,
                "candidate": {},
                "confidence": 0.98,
                "reasons": ["fixture exact conversation evidence"],
                "action_owner": action_owner,
                "lifecycle_state": "open",
                "compact_summary": compact_summary,
                "case_summary": (
                    "A legal authorization matter includes document preparation, "
                    "compliance records, a fee transfer, and an execution decision."
                ),
                "why_member": (
                    "The member is directly addressed or made the cited promise."
                ),
                "next_step": "Review this distinct ask and assign its final destination.",
                "thread_state": "in_progress",
                "unresolved_questions": [
                    "Is a notarized original required?",
                    "Who will collect the original?",
                ],
                "related_source_ids": [
                    message["source_id"]
                    for message in context_messages
                    if message.get("relation") == "related"
                ][:10],
            }

        if inputs.get("candidate_scope") == "primary_thread" and any(
            "[fixture:contextual-legal]" in str(message.get("body_text") or "")
            for message in context_messages
            if message.get("relation") == "thread"
        ):
            candidates = [
                contextual_candidate(
                    "Please prepare two authorization forms for the two proceedings.",
                    target_kind="task",
                    compact_summary="Prepare two authorization forms",
                    action_owner="group_including_member",
                ),
                contextual_candidate(
                    "Please return the compliance form and identification copy.",
                    target_kind="task",
                    compact_summary="Return compliance form and ID copy",
                ),
                contextual_candidate(
                    "I will arrange the legal-fee transfer this afternoon.",
                    target_kind="commitment",
                    compact_summary="Arrange the legal-fee transfer",
                ),
                contextual_candidate(
                    "Can the signed PDF be used, or is the notarized original required?",
                    target_kind="task",
                    compact_summary="Resolve the notarization requirement",
                    action_owner="unknown",
                ),
            ]
        elif inputs.get("candidate_scope") == "primary_thread" and any(
            "[fixture:context-correctness-replay]"
            in str(message.get("body_text") or "")
            for message in context_messages
            if message.get("relation") == "thread"
        ):
            candidates = [
                contextual_candidate(
                    "Please complete the fixture authorization.",
                    target_kind="task",
                    compact_summary="Complete the fixture authorization",
                )
            ]
        fixture_kind = next(
            (
                kind
                for kind in triage_kinds
                if f"[fixture:triage-kind={kind}]" in body_text
            ),
            None,
        )
        if candidates:
            pass
        elif fixture_kind is not None:
            candidates = [
                {
                    "target_kind": fixture_kind,
                    "title": body_text,
                    "evidence_quote": body_text,
                    "candidate": {},
                    "confidence": 0.99,
                    "reasons": ["fixture exact-evidence candidate"],
                }
            ]
        elif "[fixture:recurring-bill]" in body_text:
            candidates = [
                {
                    "target_kind": "task",
                    "title": "Pay the recurring fixture bill",
                    "evidence_quote": body_text,
                    "candidate": {
                        "due": "2026-08-15T17:00:00Z",
                        "description": body_text,
                    },
                    "confidence": 0.97,
                    "reasons": ["explicit payment due action"],
                },
                {
                    "target_kind": "recurrence",
                    "title": "Track the monthly fixture bill",
                    "evidence_quote": body_text,
                    "candidate": {
                        "recurrence_text": "monthly",
                        "rrule": "FREQ=MONTHLY",
                        "next_occurrence": "2026-08-15T17:00:00Z",
                        "kind": "bill_payment",
                        "trackable": True,
                    },
                    "confidence": 0.96,
                    "reasons": ["explicit monthly cadence"],
                },
            ]
        output = {
            "classification": "transactional" if candidates else "personal",
            "confidence": 0.99,
            "reasons": ["fixture household message"],
            "reply_signal": "not_required" if candidates else "unknown",
            "automated": bool(candidates),
            "transactional_due": bool(candidates),
            "security_code": False,
            "person_to_person": not candidates,
            "deadline": "2026-08-15T17:00:00Z" if candidates else None,
            "candidates": candidates,
        }
    elif title.startswith("extract_note_semantics"):
        body_text = str(inputs.get("body_text", ""))
        candidates = []
        fixture_kind = next(
            (
                kind
                for kind in triage_kinds
                if f"[fixture:triage-kind={kind}]" in body_text
            ),
            None,
        )
        if fixture_kind is not None:
            candidates = [
                {
                    "target_kind": fixture_kind,
                    "title": body_text,
                    "candidate": {},
                    "evidence_quote": body_text,
                    "confidence": 0.99,
                    "reasons": ["fixture exact-evidence candidate"],
                }
            ]
        elif "[fixture:member-intent]" in body_text:
            quote = "The school needs an answer by Friday"
            candidates = [
                {
                    "target_kind": "task",
                    "title": "Answer the school",
                    "candidate": {"due": "2026-08-14"},
                    "evidence_quote": quote,
                    "confidence": 0.98,
                    "reasons": ["explicit requested decision"],
                }
            ]
        output = {
            "candidates": candidates,
            "reasons": ["fixture member knowledge extraction"],
        }
    elif title.startswith("classify_thank_you_candidate"):
        output = {"is_candidate": False, "confidence": 0.02, "reasons": ["request"]}
    else:
        return httpx.Response(
            422, json={"error": f"unsupported fixture schema: {title}"}
        )
    return httpx.Response(
        200,
        json={
            "ok": True,
            "result": {
                "details": {"json": output},
                "invocation_id": f"fixture:{body['sessionKey']}:{title}",
                "provider": "fixture",
                "model": "deterministic-v1",
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
            },
        },
    )


def build_fixture_runtime(
    *, event_sink: EventSink, event_log: EventLog, raw_events_dir: Any
) -> _Runtime:
    class _FixtureClient:
        async def post(
            self, url: str, *, json: dict[str, Any], headers: dict[str, str]
        ) -> httpx.Response:
            request = httpx.Request("POST", url, json=json, headers=headers)
            return _response(request)

    client = _FixtureClient()
    return _Runtime(
        event_sink=event_sink,
        event_log=event_log,
        raw_events_dir=raw_events_dir,
        httpx_client_factory=lambda: client,
        gateway_url="http://fixture.invalid/tools/invoke",
        gateway_token="fixture-gateway-token",
    )


__all__ = ["build_fixture_runtime"]
