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
        output = {
            "classification": "personal",
            "confidence": 0.99,
            "reasons": ["fixture household message"],
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
    )


__all__ = ["build_fixture_runtime"]
