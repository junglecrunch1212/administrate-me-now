"""Three authenticated conversation origins use the existing exact Gmail ledger.

Only the provider/model HTTP boundaries are synthetic. Source intake, encrypted
receipts, independent policy receipt consumption, final sender guard and durable
send/result paths are production composition. The unchanged operational activation
gate has its own tests; these cases hold its existing authority constant.
"""

from __future__ import annotations

import base64
from datetime import datetime
from email import message_from_bytes
import json
from pathlib import Path
import time
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.cos_response_assurance import cos_provider_effect_counts
from adminme.cos_self_replies import self_reply_session
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.openclaw_topology import member_agent_id
from adminme.products.openclaw.request_authorization import fingerprint
from adminme.runtime.application import create_application
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from tests.acceptance.test_authenticated_conversation_channels import (
    EMAIL_ACCOUNT,
    MEMBER_A,
    MEMBER_B,
    MEMBER_HEADERS,
    _events,
    gmail_observation,
    portal_instruction,
    provenance_for,
)
from tests.acceptance.test_authenticated_conversation_reply import (
    configured,
    history,
    install_gmail,
)
from tests.acceptance.test_authenticated_request_actions import model_boundary
from tests.acceptance.test_cos_response_execution import FakeGmailService
from tests.acceptance.test_forwarded_channel_intake import ACCOUNT
from tests.acceptance.test_native_apple_reminder import (
    AUTH,
    OPENCLAW_COMMIT,
    OPENCLAW_VERSION,
    SECRET,
    _grant,
    _normal_operation,
)

CONFIRM = "/api/openclaw/v1/tools/confirm-requested-email"
STATUS = "/api/openclaw/v1/tools/requested-email-status"
EXECUTE = "/api/openclaw/v1/tools/cos-response"
TOOL = "administrateme_cos_response"
RULE = "current-human-request.cos-email-to-requester.v1"
SUBJECT = "Travel checklist"
BODY = "Please bring the blue folder."
TEXT = f'Email me from your account with subject "{SUBJECT}" and body "{BODY}"'
RECIPIENT = "member-a@example.invalid"


def source_for(
    client: TestClient,
    channel: str,
    identifier: str,
    *,
    text: str = TEXT,
    other: bool = False,
) -> dict[str, Any]:
    if channel == "portal":
        return portal_instruction(client, identifier, text=text, other=other)
    if channel == "gmail":
        assert not other
        original = gmail_observation(client, identifier, text=text)
        sources = client.portal.call(
            _events, client.app.state.runtime, "cos.conversation.instruction"
        )
        source = next(
            row
            for row in sources
            if row["payload"]["source_message_event_id"] == original
        )
        return {
            "provenance": provenance_for(source),
            "source_message_event_id": source["event_id"],
        }
    member = MEMBER_B if other else MEMBER_A
    actor = "actor-forward-b" if other else "actor-forward-a"
    provenance = {
        "actor_id": actor,
        "agent_id": member_agent_id(member),
        "account_id": ACCOUNT,
        "session_key": self_reply_session(member, ACCOUNT, actor),
        "channel_id": "imessage",
        "message_id": identifier,
        "correlation_id": identifier,
        "call_kind": "bridge",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }
    forwarded = client.post(
        "/api/openclaw/v1/submissions/forward",
        headers=AUTH,
        json={
            "provenance": provenance,
            "request_id": identifier,
            "text": text,
            "consent": True,
            "submission_gesture": "dedicated_intake",
        },
    )
    assert forwarded.status_code == 200, forwarded.text
    return {
        "provenance": provenance,
        "source_message_event_id": forwarded.json()["intent_event_id"],
    }


def prepare(client: TestClient, service: Any, *, other: bool = False) -> None:
    runtime = client.app.state.runtime
    _grant(client)
    if other:
        _grant(client, other=True)
    policy = client.put(
        "/api/member/v1/settings/cos-replies",
        headers=MEMBER_HEADERS,
        json={
            "request_id": "requested-email-policy",
            "expected_revision": 0,
            "entries": [
                {"member_id": MEMBER_A, "channel": "email", "address": RECIPIENT},
                *(
                    [
                        {
                            "member_id": MEMBER_B,
                            "channel": "email",
                            "address": "member-b@example.invalid",
                        }
                    ]
                    if other
                    else []
                ),
            ],
        },
    )
    assert policy.status_code == 202, policy.text
    _normal_operation(client)
    install_sender(runtime, service)


def install_sender(runtime: Any, service: Any) -> None:
    # Actual adapter implementation and normal configuration; the fixture
    # supplies only its provider client after isolated application startup.
    install_gmail(runtime, service)
    runtime.settings.adapter_config["adapters"] = [
        {
            "id": "fixture-conversation-gmail",
            "kind": "messaging-gmail-api",
            "enabled": True,
            "config": {
                "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
                "provider_account_id": EMAIL_ACCOUNT,
                "user_email": EMAIL_ACCOUNT,
                "channel_id": "channel:chief-of-staff:gmail",
                "enable_writes": True,
                "read_only": False,
                "workspace": "service",
                "permission_scope": [
                    "https://www.googleapis.com/auth/gmail.readonly",
                    "https://www.googleapis.com/auth/gmail.send",
                ],
            },
        }
    ]


def body_for(
    source: dict[str, Any], identifier: str = "requested-email-create"
) -> dict[str, Any]:
    return {**source, "request_id": identifier, "subject": SUBJECT, "body": BODY}


def confirm(
    client: TestClient,
    source: dict[str, Any],
    identifier: str = "requested-email-create",
) -> dict[str, Any]:
    response = client.post(CONFIRM, headers=AUTH, json=body_for(source, identifier))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_request_policy"
    return response.json()


def policy_receipt(
    client: TestClient,
    source: dict[str, Any],
    approved: dict[str, Any],
    *,
    suffix: str = "email",
) -> dict[str, Any]:
    ticket = approved["request_authorization"]
    params = {
        "request_id": "execute-requested-email",
        "approval_event_id": approved["approval_event_id"],
        "request_authorization_event_id": ticket["grounding_event_id"],
    }
    issued = int(time.time() * 1000)
    authorization = {
        "source_event_id": ticket["source_event_id"],
        "request_authorization_event_id": ticket["grounding_event_id"],
        "domain_approval_event_id": ticket["domain_approval_event_id"],
        "action_fingerprint": ticket["action_fingerprint"],
        "rule_id": ticket["rule_id"],
    }
    signed = {
        "receipt_id": f"ocreq_fixture-{suffix}",
        "tool_name": TOOL,
        "request_fingerprint": fingerprint(params),
        "issued_at_ms": issued,
        "expires_at_ms": min(
            issued + 120000,
            int(datetime.fromisoformat(ticket["expires_at"]).timestamp() * 1000),
        ),
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
        "authorization": authorization,
    }
    saved = client.post(
        "/api/openclaw/v1/approvals/authenticated-request",
        headers=AUTH,
        json={
            **signed,
            "decision": "authenticated_request",
            "signature": sign_approval_receipt(SECRET, **signed),
        },
    )
    assert saved.status_code == 200, saved.text
    return {
        **params,
        "provenance": source["provenance"],
        "openclaw_approval_receipt_id": signed["receipt_id"],
    }


@pytest.fixture(autouse=True)
def isolated_activation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")

    async def activation(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"authorized": True}

    monkeypatch.setattr(
        "adminme.products.openclaw.cos_responses.require_cos_response_activation",
        activation,
    )


@pytest.mark.parametrize("channel", ["imessage", "portal", "gmail"])
def test_three_origins_send_exact_requested_new_email_and_return_retained_receipt(
    tmp_path: Path, channel: str
) -> None:
    config = configured(tmp_path)
    service = FakeGmailService()
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        runtime = client.app.state.runtime
        prepare(client, service)
        model, model_calls = model_boundary(client)
        try:
            source = source_for(client, channel, f"requested-email-{channel}")
            approved = confirm(client, source)
            ticket = approved["request_authorization"]
            assert ticket["rule_id"] == RULE
            assert ticket["action"]["resource_owner"] == COS_SERVICE_PRINCIPAL_ID
            assert ticket["action"]["recipient_member_id"] == MEMBER_A
            assert service.calls == []
            replay = confirm(client, source)
            assert replay["request_authorization"] == ticket
            assert len(model_calls) == 1
            execution = policy_receipt(client, source, approved)
            result = client.post(EXECUTE, headers=AUTH, json=execution)
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "sent"
            assert result.json()["provider_effect_count"] == 1
            assert len(service.calls) == 1
            sent = service.calls[0]["body"]
            assert "threadId" not in sent
            mime = message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
            assert mime["From"] == EMAIL_ACCOUNT and mime["To"] == RECIPIENT
            assert mime["Subject"] == SUBJECT
            assert mime["In-Reply-To"] is None and mime["References"] is None
            assert mime.get_payload(decode=True).decode().strip() == BODY
            # Tool result carries the same member/origin receipt; it does not send
            # another reply or borrow a recipient from the origin channel.
            check = client.post(
                STATUS,
                headers=AUTH,
                json={**source, "approval_event_id": approved["approval_event_id"]},
            )
            assert check.status_code == 200, check.text
            assert check.json()["status"] == "sent"
            assert (
                check.json()["source_message_event_id"]
                == source["source_message_event_id"]
            )
            assert (
                client.post(EXECUTE, headers=AUTH, json=execution).json()["status"]
                == "sent"
            )
            assert len(service.calls) == 1
            rows = client.portal.call(history, runtime)
            host = [row for row in rows if row["type"] == "openclaw.approval.received"]
            assert len(host) == 1
            assert host[0]["schema_version"] == 2
            assert host[0]["payload"]["approval_method"] == "authenticated_request"
            assert host[0]["payload"]["rule_id"] == RULE
            counts = cos_provider_effect_counts(rows)
            assert counts.get("malformed_request", 0) == 0, counts
            assert counts.get("approval_correlation_missing", 0) == 0, counts
        finally:
            client.portal.call(model.aclose)
    # Preserve encrypted history, rebuild both application and real adapter.
    config.adapter_config["adapters"] = []
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        install_sender(client.app.state.runtime, service)
        result = client.post(
            STATUS,
            headers=AUTH,
            json={**source, "approval_event_id": approved["approval_event_id"]},
        )
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "sent"
        assert (
            client.post(EXECUTE, headers=AUTH, json=execution).json()["status"]
            == "sent"
        )
        assert len(service.calls) == 1


@pytest.mark.parametrize("change", ["recipient_choice", "sender_disabled"])
def test_confirmation_returns_only_current_permitted_choices_without_approval(
    tmp_path: Path, change: str
) -> None:
    service = FakeGmailService()
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        prepare(client, service)
        runtime = client.app.state.runtime
        model, calls = model_boundary(client)
        try:
            source = source_for(client, "portal", "requested-email-choose")
            if change == "recipient_choice":
                response = client.put(
                    "/api/member/v1/settings/cos-replies",
                    headers=MEMBER_HEADERS,
                    json={
                        "request_id": "requested-email-choice-policy",
                        "expected_revision": 1,
                        "entries": [
                            {
                                "member_id": MEMBER_A,
                                "channel": "email",
                                "address": value,
                            }
                            for value in (RECIPIENT, "member-a-second@example.invalid")
                        ],
                    },
                )
                assert response.status_code == 202, response.text
            else:
                runtime.settings.adapter_config["adapters"][0]["config"][
                    "enable_writes"
                ] = False
            result = client.post(CONFIRM, headers=AUTH, json=body_for(source))
            assert result.status_code == 200, result.text
            if change == "recipient_choice":
                assert result.json()["status"] == "email_recipient_choice_required"
                assert set(result.json()["recipients"]) == {
                    RECIPIENT,
                    "member-a-second@example.invalid",
                }
            else:
                assert result.json()["status"] == "email_sender_unavailable"
                assert result.json()["accounts"] == []
            assert "approval_event_id" not in result.json()
            assert result.json()["provider_effect_count"] == 0
            assert calls == [] and service.calls == []
            assert client.portal.call(_events, runtime, "cos.response.approved") == []
        finally:
            client.portal.call(model.aclose)


def test_ambiguous_provider_timeout_is_retained_unknown_and_never_retried(
    tmp_path: Path,
) -> None:
    config = configured(tmp_path)
    service = FakeGmailService(ambiguous=True)
    with TestClient(
        create_application(config),
        base_url="https://central-fixture.ts.net",
        raise_server_exceptions=False,
    ) as client:
        prepare(client, service)
        model, _ = model_boundary(client)
        try:
            source = source_for(client, "portal", "requested-email-timeout")
            approved = confirm(client, source)
            execution = policy_receipt(client, source, approved)
            client.post(EXECUTE, headers=AUTH, json=execution)
            receipt = client.post(
                STATUS,
                headers=AUTH,
                json={**source, "approval_event_id": approved["approval_event_id"]},
            )
            assert receipt.status_code == 200, receipt.text
            assert receipt.json()["status"] == "unknown"
            assert receipt.json()["provider_effect_count"] is None
            assert receipt.json()["automatic_retry_allowed"] is False
            assert (
                client.post(EXECUTE, headers=AUTH, json=execution).json()["status"]
                == "unknown"
            )
            assert len(service.calls) == 1
        finally:
            client.portal.call(model.aclose)
    config.adapter_config["adapters"] = []
    with TestClient(
        create_application(config), base_url="https://central-fixture.ts.net"
    ) as client:
        install_sender(client.app.state.runtime, service)
        assert (
            client.post(EXECUTE, headers=AUTH, json=execution).json()["status"]
            == "unknown"
        )
        assert len(service.calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        "observation",
        "recipient_revoked",
        "write_disabled",
        "recovery",
        "wrong_source",
        "missing_host",
        "different_payload",
    ],
)
def test_current_authority_is_rechecked_before_provider_contact(
    tmp_path: Path, change: str
) -> None:
    config = configured(tmp_path)
    service = FakeGmailService()
    with TestClient(
        create_application(config),
        base_url="https://central-fixture.ts.net",
        raise_server_exceptions=False,
    ) as client:
        runtime = client.app.state.runtime
        prepare(client, service)
        model, _ = model_boundary(client)
        try:
            source = source_for(client, "imessage", "requested-email-final-check")
            approved = confirm(client, source)
            execution = policy_receipt(client, source, approved)
            if change == "observation":
                client.portal.call(
                    runtime.observation.enable, "operator:fixture", "isolated recovery"
                )
            elif change == "recipient_revoked":
                response = client.put(
                    "/api/member/v1/settings/cos-replies",
                    headers=MEMBER_HEADERS,
                    json={
                        "request_id": "requested-email-revoke",
                        "expected_revision": 1,
                        "entries": [],
                    },
                )
                assert response.status_code == 202, response.text
            elif change == "write_disabled":
                runtime.settings.adapter_config["adapters"][0]["config"][
                    "enable_writes"
                ] = False
            elif change == "recovery":
                (
                    runtime.settings.instance.config_dir
                    / "request-actions-control.json"
                ).write_text(
                    json.dumps(
                        {
                            "contract": "request-actions-control-v1",
                            "disabled": True,
                            "revoked_before": None,
                        }
                    )
                )
            elif change == "wrong_source":
                new_source = source_for(
                    client, "imessage", "requested-email-unrelated", text="Hello"
                )
                execution["provenance"] = new_source["provenance"]
            elif change == "missing_host":
                execution.pop("openclaw_approval_receipt_id")
            else:
                changed = body_for(source)
                changed["body"] = "Different email content"
                response = client.post(CONFIRM, headers=AUTH, json=changed)
                assert response.status_code == 409, response.text
                return
            result = client.post(EXECUTE, headers=AUTH, json=execution)
            assert result.status_code in {200, 403, 409}, result.text
            if result.status_code == 200:
                assert result.json()["status"] in {"revoked", "denied", "expired"}
            assert service.calls == []
            assert client.portal.call(_events, runtime, "cos.response.attempted") == []
        finally:
            client.portal.call(model.aclose)


def test_same_request_id_is_member_namespaced_and_receipts_never_cross_members(
    tmp_path: Path,
) -> None:
    service = FakeGmailService()
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        prepare(client, service, other=True)
        model, _ = model_boundary(client)
        try:
            first = source_for(client, "portal", "requested-email-first")
            second = source_for(client, "portal", "requested-email-second", other=True)
            approved_a = confirm(client, first, "shared-caller-request-id")
            approved_b = confirm(client, second, "shared-caller-request-id")
            assert approved_a["approval_event_id"] != approved_b["approval_event_id"]
            assert approved_a["recipient"] == RECIPIENT
            assert approved_b["recipient"] == "member-b@example.invalid"
            denied = client.post(
                STATUS,
                headers=AUTH,
                json={**second, "approval_event_id": approved_a["approval_event_id"]},
            )
            assert denied.status_code == 404, denied.text
            foreign = body_for(first, "requested-email-foreign")
            foreign["recipient"] = "member-b@example.invalid"
            assert client.post(CONFIRM, headers=AUTH, json=foreign).status_code == 403
            assert service.calls == []
        finally:
            client.portal.call(model.aclose)


@pytest.mark.parametrize(
    "match,wrong_span", [("not_requested", False), ("matched", True)]
)
def test_unrelated_or_mismatched_model_evidence_grants_no_email_authority(
    tmp_path: Path, match: str, wrong_span: bool
) -> None:
    service = FakeGmailService()
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        prepare(client, service)
        model, _ = model_boundary(client, match=match, wrong_span=wrong_span)
        try:
            source = source_for(
                client, "portal", "requested-email-unrelated", text="Hello"
            )
            response = client.post(CONFIRM, headers=AUTH, json=body_for(source))
            assert response.status_code in {403, 409}, response.text
            assert (
                client.portal.call(
                    _events, client.app.state.runtime, "cos.response.approved"
                )
                == []
            )
            assert service.calls == []
        finally:
            client.portal.call(model.aclose)


def test_terminal_readback_keeps_original_authority_and_rejects_mismatched_history(
    tmp_path: Path,
) -> None:
    import copy

    service = FakeGmailService()
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
    ) as client:
        runtime = client.app.state.runtime
        prepare(client, service)
        model, _ = model_boundary(client)
        try:
            source = source_for(client, "imessage", "requested-email-audit")
            approved = confirm(client, source)
            execution = policy_receipt(client, source, approved)
            result = client.post(EXECUTE, headers=AUTH, json=execution)
            assert result.status_code == 200, result.text
            assert result.json()["status"] == "sent"
            rows = client.portal.call(history, runtime)
            for mismatch in (
                "source_member",
                "grounding_rule",
                "host_method",
                "action_recipient",
                "outbound_fingerprint",
            ):
                altered = copy.deepcopy(rows)
                if mismatch == "source_member":
                    next(
                        row
                        for row in altered
                        if row["event_id"] == source["source_message_event_id"]
                    )["actor_identity"] = MEMBER_B
                elif mismatch == "grounding_rule":
                    next(
                        row
                        for row in altered
                        if row["type"] == "cos.action.request_grounded"
                    )["payload"][
                        "rule_id"
                    ] = "current-human-request.apple-reminder-create.v1"
                elif mismatch == "host_method":
                    next(
                        row
                        for row in altered
                        if row["type"] == "openclaw.approval.received"
                    )["payload"]["approval_method"] = "allow_once"
                elif mismatch == "action_recipient":
                    next(
                        row for row in altered if row["type"] == "cos.response.approved"
                    )["payload"]["request_action"][
                        "recipient"
                    ] = "member-b@example.invalid"
                else:
                    next(row for row in altered if row["type"] == "external.sent")[
                        "payload"
                    ]["content_fingerprint"] = "0" * 64
                counts = cos_provider_effect_counts(altered)
                assert (
                    counts.get("malformed_request", 0)
                    + counts.get("approval_correlation_missing", 0)
                    > 0
                ), (mismatch, counts)
            revoked = client.put(
                "/api/member/v1/settings/cos-replies",
                headers=MEMBER_HEADERS,
                json={
                    "request_id": "requested-email-after-send-revoke",
                    "expected_revision": 1,
                    "entries": [],
                },
            )
            assert revoked.status_code == 202, revoked.text
            # A new genuine conversation can read this member's existing result.
            # Later revocation prevents another send; it cannot unsend this email.
            later = source_for(
                client,
                "portal",
                "requested-email-audit-status",
                text="Did that email send?",
            )
            read = client.post(
                STATUS,
                headers=AUTH,
                json={**later, "approval_event_id": approved["approval_event_id"]},
            )
            assert read.status_code == 200, read.text
            assert read.json()["status"] == "sent"
            assert (
                read.json()["source_message_event_id"]
                == source["source_message_event_id"]
            )
            assert len(service.calls) == 1
        finally:
            client.portal.call(model.aclose)


def test_crash_after_durable_attempt_reconciles_unknown_without_provider_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adminme.products.openclaw import cos_responses

    service = FakeGmailService()
    with TestClient(
        create_application(configured(tmp_path)),
        base_url="https://central-fixture.ts.net",
        raise_server_exceptions=False,
    ) as client:
        runtime = client.app.state.runtime
        prepare(client, service)
        model, _ = model_boundary(client)
        try:
            source = source_for(client, "portal", "requested-email-crash")
            approved = confirm(client, source)
            execution = policy_receipt(client, source, approved)
            original = cos_responses._receipt

            async def crash_after_attempt(*args: Any, **kwargs: Any) -> str:
                identifier = await original(*args, **kwargs)
                if kwargs["event_type"] == "cos.response.attempted":
                    raise RuntimeError("isolated crash after durable attempt")
                return identifier

            monkeypatch.setattr(cos_responses, "_receipt", crash_after_attempt)
            assert client.post(EXECUTE, headers=AUTH, json=execution).status_code == 500
            monkeypatch.setattr(cos_responses, "_receipt", original)
            assert (
                len(client.portal.call(_events, runtime, "cos.response.attempted")) == 1
            )
            assert client.portal.call(_events, runtime, "cos.response.result") == []
            read = client.post(
                STATUS,
                headers=AUTH,
                json={**source, "approval_event_id": approved["approval_event_id"]},
            )
            assert read.json()["status"] == "unknown"
            assert client.portal.call(_events, runtime, "cos.response.result") == [], (
                "Status must remain read-only"
            )
            recovered = client.post(EXECUTE, headers=AUTH, json=execution)
            assert recovered.status_code == 200, recovered.text
            assert recovered.json()["status"] == "unknown"
            assert recovered.json()["automatic_retry_allowed"] is False
            assert len(client.portal.call(_events, runtime, "cos.response.result")) == 1
            assert (
                client.post(EXECUTE, headers=AUTH, json=execution).json()["status"]
                == "unknown"
            )
            assert service.calls == []
        finally:
            client.portal.call(model.aclose)
