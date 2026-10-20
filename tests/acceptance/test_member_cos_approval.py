"""Real authenticated portal routes and pinned Gateway RPC adapter composition."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.member_chat import OpenClawGatewayChatTransport
from adminme.openclaw_topology import member_agent_id
from adminme.runtime.application import create_application
from tests.acceptance.test_cos_response_execution import (
    A,
    AUTH,
    B,
    EXECUTE,
    HEADERS,
    POLICY,
    SECRET,
    _approve,
    _execution,
    _prepare,
    _settings,
)

ENDPOINT = "/api/member/v1/chat/reply-approvals"
APPROVAL = "plugin:aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class GatewaySocket:
    """Pinned protocol boundary; HMAC is checked independently of the route."""

    def __init__(
        self, pending: list[dict[str, Any]], decisions: list[dict[str, Any]]
    ) -> None:
        self.pending, self.decisions = pending, decisions
        self.sent: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        params = request["params"]
        operation = request["method"].rsplit(".", 1)[-1]
        fields = [
            "session_key",
            "agent_id",
            "member_id",
            "approval_id",
            "revision",
            "decision",
            "nonce",
        ]
        material = "|".join(
            [
                "cos-member-approval-v1",
                operation,
                *(params[key] for key in fields),
                str(params["expires_at_ms"]),
            ]
        )
        assert hmac.compare_digest(
            params["signature"],
            hmac.new(SECRET.encode(), material.encode(), hashlib.sha256).hexdigest(),
        )
        assert int(time.time() * 1000) < params["expires_at_ms"]
        self.sent.append(request)
        if operation == "resolve":
            self.decisions.append(params)
            self.pending.clear()

    async def recv(self) -> str:
        request = self.sent[-1]
        payload = (
            {"items": self.pending}
            if request["method"].endswith(".list")
            else {"resolved": True}
        )
        return json.dumps(
            {"type": "res", "id": request["id"], "ok": True, "payload": payload}
        )

    async def close(self) -> None:
        pass


@pytest.mark.parametrize("decision", ["allow-once", "deny"])
def test_member_native_approval_is_explicit_private_exact_and_single_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = replace(
        _settings(instance),
        member_chat_enabled_members=frozenset({A, B}),
        deterministic_member_chat_fixture=True,
    )
    with TestClient(create_application(settings)) as client:
        _prepare(client)
        domain_id = _approve(client)
        runtime = client.app.state.runtime
        _, session_key = runtime.member_chat_binding(A)
        params = {"request_id": "approved-response-run", "approval_event_id": domain_id}
        pending = [
            {
                "approval_id": APPROVAL,
                "revision": "a" * 64,
                "expires_at_ms": int(time.time() * 1000) + 60_000,
                "domain_approval_id": domain_id,
                "request_id": params["request_id"],
                "request_fingerprint": hashlib.sha256(
                    json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "session_key": session_key,
                "agent_id": member_agent_id(A),
            }
        ]
        decisions: list[dict[str, Any]] = []
        transport = OpenClawGatewayChatTransport(
            url="ws://127.0.0.1:18789", token="fixture-token"
        )

        async def connect() -> GatewaySocket:
            return GatewaySocket(pending, decisions)

        monkeypatch.setattr(transport, "_connect", connect)
        runtime.member_chat = transport
        response = client.get(ENDPOINT, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert (
            response.json()["items"][0]["body"]
            == "Your selected obligation is ready for review."
        )
        assert response.json()["items"][0]["can_allow"] is True
        assert "session_key" not in response.text
        assert "request_fingerprint" not in response.text
        assert decisions == []
        wrong_member = {"Tailscale-User-Login": "response-b.example.test"}
        assert client.get(ENDPOINT, headers=wrong_member).json() == {"items": []}
        assert client.get(
            ENDPOINT, headers={**HEADERS, "X-AdminMe-View-Member": B}
        ).status_code in {403, 404}
        body = {"approval_id": APPROVAL, "revision": "a" * 64, "decision": decision}
        assert client.post(ENDPOINT, headers=wrong_member, json=body).status_code == 409
        assert (
            client.post(
                ENDPOINT, headers=HEADERS, json={**body, "revision": "b" * 64}
            ).status_code
            == 409
        )
        assert (
            client.post(
                ENDPOINT, headers=HEADERS, json={**body, "decision": "allow-always"}
            ).status_code
            == 422
        )
        assert decisions == []
        resolved = client.post(ENDPOINT, headers=HEADERS, json=body)
        assert resolved.status_code == 200, resolved.text
        assert resolved.json() == {"resolved": True, "sent": False}
        assert len(decisions) == 1
        assert decisions[0]["member_id"] == A
        assert decisions[0]["session_key"] == session_key
        assert decisions[0]["decision"] == decision
        assert client.post(ENDPOINT, headers=HEADERS, json=body).status_code == 409


@pytest.mark.parametrize(
    "tamper",
    ["session", "agent", "fingerprint", "expired", "revoked", "attempted", "terminal"],
)
def test_native_approval_rejects_changed_scope_expiry_and_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = replace(
        _settings(instance),
        member_chat_enabled_members=frozenset({A}),
        deterministic_member_chat_fixture=True,
    )
    with TestClient(create_application(settings)) as client:
        _prepare(client)
        domain_id = _approve(client)
        runtime = client.app.state.runtime
        _, session_key = runtime.member_chat_binding(A)
        params = {"request_id": "request-fixture", "approval_event_id": domain_id}
        row = {
            "approval_id": APPROVAL,
            "revision": "a" * 64,
            "expires_at_ms": int(time.time() * 1000) + 60_000,
            "domain_approval_id": domain_id,
            "request_id": params["request_id"],
            "request_fingerprint": hashlib.sha256(
                json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "session_key": session_key,
            "agent_id": member_agent_id(A),
        }
        if tamper == "session":
            row["session_key"] = "another-member-session"
        elif tamper == "agent":
            row["agent_id"] = "another-member-agent"
        elif tamper == "fingerprint":
            row["request_fingerprint"] = "b" * 64
        elif tamper == "expired":
            row["expires_at_ms"] = 1
        elif tamper == "revoked":
            assert (
                client.put(
                    POLICY,
                    headers=HEADERS,
                    json={
                        "request_id": "revoke-reply-policy",
                        "expected_revision": 1,
                        "entries": [],
                    },
                ).status_code
                == 202
            )
        elif tamper == "terminal":
            # A durable refusal is terminal too; it cannot become a later send.
            refused = client.post(
                EXECUTE, headers=AUTH, json=_execution(client, domain_id, allow=False)
            )
            assert refused.status_code == 403, refused.text
        else:
            # Crash boundary: intent is durable but delivery is unconfirmed.
            approved = client.portal.call(runtime.event_log.get, domain_id)
            assert approved is not None
            payload = approved["payload"]
            client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=int(time.time() * 1000),
                    tenant_id=settings.tenant_id,
                    type="cos.response.attempted",
                    schema_version=1,
                    occurred_at=EventEnvelope.now_utc_iso(),
                    source_adapter="openclaw-plugin",
                    source_account_id="cos-response",
                    owner_scope=f"private:{A}",
                    visibility_scope=f"private:{A}",
                    actor_identity=A,
                    idempotency_key=f"openclaw:{A}:cos.response.attempted:{domain_id}",
                    payload={
                        "contract": payload["contract"],
                        "member_id": A,
                        "approval_event_id": domain_id,
                        "openclaw_approval_event_id": "fixture-independent-receipt",
                        "content_fingerprint": payload["content_fingerprint"],
                        "policy_event_id": payload["policy_event_id"],
                        "attempted_at": EventEnvelope.now_utc_iso(),
                        "automatic_retry_allowed": False,
                    },
                ),
            )
        decisions: list[dict[str, Any]] = []
        transport = OpenClawGatewayChatTransport(
            url="ws://127.0.0.1:18789", token="fixture-token"
        )

        async def connect() -> GatewaySocket:
            return GatewaySocket([row], decisions)

        monkeypatch.setattr(transport, "_connect", connect)
        runtime.member_chat = transport
        response = client.post(
            ENDPOINT,
            headers=HEADERS,
            json={
                "approval_id": APPROVAL,
                "revision": "a" * 64,
                "decision": "allow-once",
            },
        )
        assert response.status_code == 409, response.text
        assert decisions == []
