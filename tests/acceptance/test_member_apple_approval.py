"""Authenticated member review of genuine native Apple action approval bindings.

The protocol socket is synthetic here; the separate pinned Gateway probe uses
its real plugin approval store. Neither fixture calls an Apple provider.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.lib.session import Session
from adminme.member_chat import MemberChatError, OpenClawGatewayChatTransport
from adminme.openclaw_topology import member_agent_id
from adminme.products.member.main import _append_provider_action_confirmation
from adminme.runtime.application import create_application
from tests.acceptance.test_apple_service_permissions import (
    PATH,
    grant_services,
    prepare_apple_sources,
)
from tests.acceptance.test_cos_member_bridge_acquisition import (
    MEMBER_A,
    MEMBER_HEADERS,
    OTHER_HEADERS,
    _configuration_settings,
)
from tests.acceptance.test_forwarded_channel_intake import _events
from tests.acceptance.test_member_cos_approval import (
    APPROVAL,
    ENDPOINT,
    SECRET,
    GatewaySocket,
)


def _domain(client: TestClient, provider: str) -> str:
    runtime = client.app.state.runtime
    item = next(
        item
        for item in client.get(PATH, headers=MEMBER_HEADERS).json()["items"]
        if item["provider"] == provider
    )
    scope = item["scopes"][0]
    if provider == "apple_notes":
        result = client.post(
            "/api/member/v1/notes/commands",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "native-apple-notes",
                "operation": "create_note",
                "provider_account_id": scope["provider_account_id"],
                "account_external_id": scope["account_id"],
                "folder_external_id": scope["container_id"],
                "title": "Review this note",
                "body": "Exact private note text",
            },
        )
        assert result.status_code == 202, result.text
        return result.json()["domain_approval_event_id"]
    if provider == "apple_reminders":
        result = client.post(
            "/api/member/v1/reminders/commands",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "native-apple-reminder",
                "provider": provider,
                "provider_account_id": scope["provider_account_id"],
                "list_external_id": scope["container_id"],
                "operation": "create_reminder",
                "payload": {
                    "title": "Review this reminder",
                    "notes": "Exact private reminder text",
                },
            },
        )
        assert result.status_code == 202, result.text
        return result.json()["domain_approval_event_id"]
    # Calendar and Triage share this real confirmation composition; the selected
    # empty calendar intentionally has no fabricated existing event.
    session = Session(
        tenant_id=runtime.settings.instance.tenant_id,
        auth_member_id=MEMBER_A,
        auth_role="principal",
        view_member_id=MEMBER_A,
        view_role="principal",
        dm_scope="per_account_channel_peer",
        source="member_portal",
    )
    event_id, _ = client.portal.call(
        partial(
            _append_provider_action_confirmation,
            runtime,
            session,
            command_payload={
                "member_id": MEMBER_A,
                "provider": provider,
                "provider_account_id": scope["provider_account_id"],
                "operation": "create_event",
                "payload": {
                    "calendar_id": scope["container_id"],
                    "summary": "Review this event",
                    "start": {"dateTime": "2031-01-01T12:00:00+00:00"},
                    "end": {"dateTime": "2031-01-01T13:00:00+00:00"},
                },
            },
            request_id="native-apple-calendar",
            request_fingerprint="c" * 64,
            source_adapter="member-triage",
            sensitivity="normal",
            causation_id=None,
        )
    )
    return event_id


@pytest.fixture
def apple_native(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    base = _configuration_settings(tmp_path)
    settings = replace(
        base,
        openclaw_shared_secret=SECRET,
        member_chat_enabled_members=frozenset(base.member_roles),
        deterministic_member_chat_fixture=True,
        openclaw_model_configured=True,
    )
    with TestClient(
        create_application(settings), base_url="https://central-fixture.ts.net"
    ) as client:
        runtime = client.app.state.runtime
        prepare_apple_sources(client, tmp_path)
        delegation = client.post(
            "/api/member/v1/cos/delegations",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "native-apple-delegation",
                "context_scope": "member_private",
                "permissions": ["read_context", "prepare_external_actions"],
            },
        )
        assert delegation.status_code == 201, delegation.text
        grant_services(client)
        client.portal.call(
            runtime.observation.disable, "operator:fixture", "Native approval fixture"
        )
        _, session_key = runtime.member_chat_binding(MEMBER_A)
        pending: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        transport = OpenClawGatewayChatTransport(
            url="ws://127.0.0.1:18789", token="fixture-token"
        )

        async def connect() -> GatewaySocket:
            return GatewaySocket(pending, decisions)

        monkeypatch.setattr(transport, "_connect", connect)
        runtime.member_chat = transport

        def remember(provider: str) -> dict[str, Any]:
            domain_id = _domain(client, provider)
            row = {
                "kind": "provider_action",
                "tool_name": "administrateme_execute_pending_provider_action",
                "approval_id": APPROVAL,
                "revision": "a" * 64,
                "expires_at_ms": int(time.time() * 1000) + 60_000,
                "domain_approval_id": domain_id,
                "request_id": "",
                "request_fingerprint": hashlib.sha256(
                    json.dumps(
                        {"domain_approval_event_id": domain_id},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "session_key": session_key,
                "agent_id": member_agent_id(MEMBER_A),
            }
            pending[:] = [row]
            return row

        yield client, remember, pending, decisions
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
        assert (
            client.portal.call(_events, runtime, "external_sync.write_completed") == []
        )


@pytest.mark.parametrize(
    "provider", ["apple_calendar", "apple_reminders", "apple_notes"]
)
@pytest.mark.parametrize("decision", ["allow-once", "deny"])
def test_owner_reviews_and_resolves_one_native_apple_action(
    apple_native, provider: str, decision: str
):
    client, remember, _, decisions = apple_native
    remember(provider)
    response = client.get(ENDPOINT, headers=MEMBER_HEADERS)
    assert response.status_code == 200, response.text
    [row] = response.json()["items"]
    assert row["kind"] == "provider_action" and row["provider"] == provider
    assert row["can_allow"] and row["destination"] and row["review"]
    assert "Review this" in response.text
    for hidden in (
        "provider-account:",
        "fixture-notes-folder",
        "apple_service_permission",
        "domain_approval_id",
        "session_key",
    ):
        assert hidden not in response.text
    assert client.get(ENDPOINT, headers=OTHER_HEADERS).json() == {"items": []}
    request = {"approval_id": APPROVAL, "revision": "a" * 64, "decision": decision}
    assert client.post(ENDPOINT, headers=OTHER_HEADERS, json=request).status_code == 409
    assert (
        client.post(
            ENDPOINT, headers=MEMBER_HEADERS, json={**request, "revision": "b" * 64}
        ).status_code
        == 409
    )
    assert decisions == []
    resolved = client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request)
    assert resolved.status_code == 200, resolved.text
    assert resolved.json() == {"resolved": True, "sent": False}
    assert len(decisions) == 1 and decisions[0]["decision"] == decision
    assert (
        client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request).status_code == 409
    )
    assert len(decisions) == 1


def test_apple_approval_scope_observation_and_durable_revocation_fail_closed(
    apple_native, monkeypatch
):
    client, remember, pending, decisions = apple_native
    runtime = client.app.state.runtime
    original = copy.deepcopy(remember("apple_notes"))
    request = {"approval_id": APPROVAL, "revision": "a" * 64, "decision": "allow-once"}
    for key, value in (
        ("kind", "cos_response"),
        ("tool_name", "administrateme_cos_response"),
        ("session_key", "another-member-session"),
        ("agent_id", "another-member-agent"),
        ("request_fingerprint", "b" * 64),
        ("expires_at_ms", 1),
    ):
        pending[:] = [{**original, key: value}]
        assert client.get(ENDPOINT, headers=MEMBER_HEADERS).json()["items"] == []
        assert (
            client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request).status_code
            == 409
        )
    pending[:] = [original]
    client.portal.call(
        runtime.observation.enable, "operator:fixture", "Native approval refusal"
    )
    assert (
        client.get(ENDPOINT, headers=MEMBER_HEADERS).json()["items"][0]["can_allow"]
        is False
    )
    assert (
        client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request).status_code == 409
    )
    client.portal.call(
        runtime.observation.disable, "operator:fixture", "Native approval restored"
    )

    def interrupted(*_args, **_kwargs):
        raise RuntimeError("fixture interrupted cache")

    monkeypatch.setattr(
        runtime.settings_control, "apply_apple_service_permission_event", interrupted
    )
    with pytest.raises(RuntimeError, match="fixture interrupted cache"):
        client.post(
            f"{PATH}/apple_notes",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "native-apple-revoke",
                "expected_revision": 1,
                "enabled": False,
                "scope_ids": [],
            },
        )
    assert (
        client.get(ENDPOINT, headers=MEMBER_HEADERS).json()["items"][0]["can_allow"]
        is False
    )
    assert (
        client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request).status_code == 409
    )
    assert decisions == []
    assert (
        client.post(
            ENDPOINT, headers=MEMBER_HEADERS, json={**request, "decision": "deny"}
        ).status_code
        == 200
    )
    assert len(decisions) == 1 and decisions[0]["decision"] == "deny"


def test_unknown_native_resolution_is_not_retried(apple_native, monkeypatch):
    client, remember, pending, decisions = apple_native
    remember("apple_notes")

    class UnknownSocket(GatewaySocket):
        async def recv(self) -> str:
            if self.sent[-1]["method"].endswith(".resolve"):
                raise MemberChatError(
                    "approval_unavailable", "Fixture lost resolution acknowledgement"
                )
            return await super().recv()

    async def connect() -> GatewaySocket:
        return UnknownSocket(pending, decisions)

    monkeypatch.setattr(client.app.state.runtime.member_chat, "_connect", connect)
    request = {"approval_id": APPROVAL, "revision": "a" * 64, "decision": "allow-once"}
    unknown = client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request)
    assert unknown.status_code == 503, unknown.text
    assert len(decisions) == 1
    assert client.get(ENDPOINT, headers=MEMBER_HEADERS).json()["items"] == []
    assert (
        client.post(ENDPOINT, headers=MEMBER_HEADERS, json=request).status_code == 409
    )
    assert len(decisions) == 1
