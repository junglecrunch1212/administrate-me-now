"""Verified email origin plus native host decision uses the existing send ledger."""

from __future__ import annotations

import base64
import copy
from dataclasses import replace
from email import message_from_bytes
import hashlib
import hmac
import importlib.util
from pathlib import Path
import time
from typing import Any

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from adminme.cos_response_assurance import cos_provider_effect_counts
from adminme.daemons.adapter_supervisor import _ManagedAdapter
from adminme.products.openclaw.request_authorization import canonical
from adminme.products.openclaw.conversation_reply import _reply_sender
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from tests.acceptance.test_authenticated_conversation_channels import (
    EMAIL_ACCOUNT,
    MEMBER_A,
    MEMBER_HEADERS,
    ROOT,
    _events,
    gmail_observation,
    provenance_for,
    settings,
)
from tests.acceptance.test_cos_response_execution import FakeGmailService
from tests.acceptance.test_native_apple_reminder import (
    AUTH,
    SECRET,
    _grant,
    _normal_operation,
)

REPLY = "/api/openclaw/v1/conversations/reply"


def configured(path: Path) -> Any:
    value = settings(path)
    config = copy.deepcopy(value.adapter_config)
    for channel in config["service_identities"]["channels"]:
        if channel["provider"] == "gmail":
            channel.update(
                state="healthy", observation_active=False, sending_enabled=True
            )
    # Current reviewed source route, without starting a real provider adapter.
    # The credential-free send fixture is installed separately below.
    config["adapters"].append(
        {
            "kind": "messaging-gmail-api",
            "enabled": False,
            "config": {
                "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "provider_account_id": EMAIL_ACCOUNT,
                "user_email": EMAIL_ACCOUNT,
                "channel_id": "channel:chief-of-staff:gmail",
                "trusted_sender_routes": [
                    {
                        "exact_from_identifier": "member-a@example.invalid",
                        "route_key": "route:" + "1" * 64,
                        "authentication_policy": "receiver_aligned_sender",
                    }
                ],
            },
        }
    )
    return replace(value, adapter_config=config)


def install_gmail(runtime: Any, service: Any, *, disabled: bool = False) -> None:
    spec = importlib.util.spec_from_file_location(
        "conversation_send_fixture", ROOT / "packs/adapters/messaging/gmail/adapter.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = module.GmailAdapter(
        config={
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "provider_account_id": EMAIL_ACCOUNT,
            "user_email": EMAIL_ACCOUNT,
            "enable_writes": not disabled,
            "read_only": disabled,
            "workspace": "service",
            "permission_scope": [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send",
            ],
            "channel_id": "channel:chief-of-staff:gmail",
        },
        observation_session=runtime.adapters._ctx_factory().observation_session,
        observation_manager=runtime.observation,
        observation_event_log=runtime.events,
    )
    adapter._service = service
    runtime.adapters._managed["fixture-conversation-gmail"] = _ManagedAdapter(
        adapter=adapter
    )


def reply_body(
    source: dict[str, Any],
    *,
    body: str = "Your reminder is recorded. Its provider result is still pending.",
) -> dict[str, Any]:
    p = source["payload"]
    now = int(time.time() * 1000)
    host = {
        "contract": "authenticated-conversation-reply-v1",
        "source_event_id": source["event_id"],
        "channel": "gmail",
        **{
            key: p[key]
            for key in (
                "account_id",
                "actor_id",
                "session_key",
                "agent_id",
                "message_id",
                "thread_id",
            )
        },
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "rule_id": "same_conversation_reply_v1",
        "issued_at_ms": now,
        "expires_at_ms": now + 120000,
    }
    signature = hmac.new(
        SECRET.encode(),
        (host["contract"] + "|" + canonical(host)).encode(),
        hashlib.sha256,
    ).hexdigest()
    return {
        "provenance": provenance_for(source),
        "source_message_event_id": source["event_id"],
        "body": body,
        "request_id": "conversation-final-reply",
        "host_decision": {**host, "signature": signature},
    }


@pytest.mark.parametrize("display_name", [False, True])
@pytest.mark.parametrize(
    "ambiguous,disabled", [(False, False), (True, False), (False, True)]
)
def test_actual_gmail_thread_reply_and_restart_never_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ambiguous: bool,
    disabled: bool,
    display_name: bool,
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")

    async def activation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"authorized": True}

    # Operational activation is unchanged and independently exercised by its lane.
    monkeypatch.setattr(
        "adminme.products.openclaw.cos_responses.require_cos_response_activation",
        activation,
    )
    config = configured(tmp_path)
    service = FakeGmailService(ambiguous=ambiguous)
    with TestClient(
        create_application(config),
        base_url="https://central-fixture.ts.net",
        raise_server_exceptions=False,
    ) as client:
        runtime = client.app.state.runtime
        _grant(client)
        allowed = client.put(
            "/api/member/v1/settings/cos-replies",
            headers=MEMBER_HEADERS,
            json={
                "request_id": "conversation-reply-policy",
                "expected_revision": 0,
                "entries": [
                    {
                        "member_id": MEMBER_A,
                        "channel": "email",
                        "address": "member-a@example.invalid",
                    }
                ],
            },
        )
        assert allowed.status_code == 202, allowed.text
        _normal_operation(client)
        install_gmail(runtime, service, disabled=disabled)
        original = gmail_observation(
            client,
            "direct-reply-input",
            from_header='"Fixture Member, A" <member-a@example.invalid>'
            if display_name
            else "member-a@example.invalid",
        )
        source = next(
            row
            for row in client.portal.call(
                _events, runtime, "cos.conversation.instruction"
            )
            if row["payload"]["source_message_event_id"] == original
        )
        body = reply_body(source)
        forged = copy.deepcopy(body)
        forged["body"] = "Different output"
        assert client.post(REPLY, headers=AUTH, json=forged).status_code == 403
        assert service.calls == []
        first = client.post(REPLY, headers=AUTH, json=body)
        assert first.status_code == (500 if ambiguous or disabled else 200), first.text
        second = client.post(REPLY, headers=AUTH, json=body)
        assert second.status_code == 200, second.text
        assert second.json()["status"] == (
            "unknown" if ambiguous or disabled else "sent"
        )
        assert second.json()["automatic_retry_allowed"] is False
        assert len(service.calls) == (0 if disabled else 1)
        if not disabled:
            sent = service.calls[0]["body"]
            assert sent["threadId"] == "fixture-real-email-thread"
            mime = message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
            assert (
                mime["From"] == EMAIL_ACCOUNT
                and mime["To"] == "member-a@example.invalid"
            )
            assert mime["In-Reply-To"] == "<fixture-current@example.invalid>"
            assert mime["References"] == "<fixture-current@example.invalid>"
        all_events = client.portal.call(history, runtime)
        counts = cos_provider_effect_counts(all_events)
        assert counts.get("malformed_request", 0) == 0, counts
        assert counts.get("approval_correlation_missing", 0) == 0, counts
    # Existing durable terminal wins; no new provider contact or response identity.
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        again = client.post(REPLY, headers=AUTH, json=body)
        assert again.status_code == 200, again.text
        assert again.json()["status"] == (
            "unknown" if ambiguous or disabled else "sent"
        )
        assert len(service.calls) == (0 if disabled else 1)


async def history(runtime: Any) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since()]


def test_reply_sender_requires_current_exact_enrolled_route(tmp_path: Path) -> None:
    with TestClient(create_application(configured(tmp_path))) as client:
        _grant(client)
        _normal_operation(client)
        runtime = client.app.state.runtime
        original_id = gmail_observation(
            client,
            "strict-sender-input",
            from_header='"Fixture Member, A" <member-a@example.invalid>',
        )
        original = client.portal.call(runtime.event_log.get, original_id)
        assert _reply_sender(runtime, original, MEMBER_A) == "member-a@example.invalid"
        # Header syntax never replaces the provider-authenticated source binding.
        invalid = [
            {"from_identifier": "not-a-mailbox"},
            {"from_identifier": "<member-a@example.invalid"},
            {"from_identifier": "member-a@example.invalid, other@example.invalid"},
            {"from_identifier": "Group: member-a@example.invalid;"},
            {
                "from_identifier": "Name <member-a@example.invalid>\r\nBcc: outsider@example.invalid"
            },
            {"from_identifier": "Wrong <other@example.invalid>"},
            {"from_identifier": "Case <Member-a@example.invalid>"},
            {"authenticated_member_route": False},
            {"trusted_route_key": "route:" + "2" * 64},
            {"route_assurance": "unverified"},
            {"reply_to_identifier": "other@example.invalid"},
            {"reply_to_identifier": "member-a@example.invalid, other@example.invalid"},
        ]
        for changes in invalid:
            forged = copy.deepcopy(original)
            forged["payload"].update(changes)
            if "from_identifier" in changes:
                forged["payload"]["reply_to_identifier"] = changes["from_identifier"]
            with pytest.raises(HTTPException) as error:
                _reply_sender(runtime, forged, MEMBER_A)
            assert error.value.status_code == 403, changes
        source = next(
            row
            for row in client.portal.call(
                _events, runtime, "cos.conversation.instruction"
            )
            if row["payload"]["source_message_event_id"] == original_id
        )
        adapter_config = runtime.settings.adapter_config["adapters"][0]["config"]
        route = adapter_config["trusted_sender_routes"][0]
        for field, changed in [
            ("exact_from_identifier", "other@example.invalid"),
            ("authentication_policy", "exact_dkim_identity"),
            ("route_key", "route:" + "2" * 64),
        ]:
            before = route[field]
            route[field] = changed
            response = client.post(REPLY, headers=AUTH, json=reply_body(source))
            assert response.status_code == 403, response.text
            route[field] = before
        adapter_config["trusted_sender_routes"].append(dict(route))
        assert (
            client.post(REPLY, headers=AUTH, json=reply_body(source)).status_code == 403
        )
        assert client.portal.call(_events, runtime, "cos.response.approved") == []
        assert client.portal.call(_events, runtime, "cos.response.attempted") == []
