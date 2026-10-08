"""Production-root proofs for reviewed Gmail and OpenClaw CoS submissions."""

from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import importlib.util
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.products.openclaw.constants import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
    DEFAULT_SERVICE_IDENTITY_CONFIG,
)

ROOT = Path(__file__).resolve().parents[2]
MEMBER_A = "member-fixture-forward-a"
MEMBER_B = "member-fixture-forward-b"
ACCOUNT = "cos-messages-fixture"
EMAIL_ACCOUNT = "cos-service@example.invalid"
SECRET = "forwarded-channel-credential-free-test-secret"
AUTH = {"Authorization": f"Bearer {SECRET}"}
MEMBER_HEADERS = {"Tailscale-User-Login": "forward-a.ts.net"}
OTHER_HEADERS = {"Tailscale-User-Login": "forward-b.ts.net"}
TEXT = "[fixture:triage-kind=task] Submit the form by 2031-01-02."


def _settings(path: Path, *, intake_mode: str = "member_submission") -> RuntimeSettings:
    services = copy.deepcopy(DEFAULT_SERVICE_IDENTITY_CONFIG)
    services["channels"] = [
        {
            "channel_id": "channel:chief-of-staff:apple-imessage",
            "display_name": "CoS Messages fixture",
            "provider": "apple_imessage",
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "provider_account_reference": "provider-account:"
            + hashlib.sha256(ACCOUNT.encode()).hexdigest(),
            "state": "observation",
            "enrolled": True,
            "observation_active": True,
            "sending_enabled": False,
            "routes": [],
            "intake_mode": intake_mode,
            "openclaw_channel_id": "imessage",
        },
        {
            "channel_id": "channel:chief-of-staff:gmail",
            "display_name": "CoS Gmail fixture",
            "provider": "gmail",
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "provider_account_reference": "provider-account:"
            + hashlib.sha256(EMAIL_ACCOUNT.encode()).hexdigest(),
            "state": "observation",
            "enrolled": True,
            "observation_active": True,
            "sending_enabled": False,
            "routes": [
                {
                    "route_key": "route:" + "1" * 64,
                    "scope": "member_private",
                    "target_id": MEMBER_A,
                }
            ],
        },
    ]
    return RuntimeSettings.credential_free(
        path,
        encryption_key=b"f" * 32,
        repository_root=ROOT,
        member_identities={"forward-a.ts.net": MEMBER_A, "forward-b.ts.net": MEMBER_B},
        member_roles={MEMBER_A: "principal", MEMBER_B: "principal"},
        fixture_member_id=MEMBER_A,
        deterministic_openclaw_fixture=True,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            f"imessage|{ACCOUNT}|actor-forward-a": MEMBER_A,
            **(
                {f"imessage|{ACCOUNT}|actor-forward-b": MEMBER_B}
                if intake_mode == "member_submission"
                else {}
            ),
            "imessage|personal-account|actor-forward-a": MEMBER_A,
        },
        adapter_config={
            "adapter_supervisor_port": 0,
            "service_identities": services,
            "adapters": [],
        },
    )


@pytest.mark.parametrize("transport", ["imessage", "bluebubbles"])
@pytest.mark.parametrize("provider", ["apple_imessage", "imessage"])
def test_shared_cos_intake_keeps_exact_member_actor_bindings(
    tmp_path: Path, transport: str, provider: str
) -> None:
    settings = _settings(tmp_path / "shared-cos-identity")
    config = copy.deepcopy(settings.adapter_config)
    config["service_identities"]["channels"][0]["provider"] = provider
    config["service_identities"]["channels"][0]["openclaw_channel_id"] = transport
    identities = {
        key.replace("imessage|", transport + "|", 1): member
        for key, member in settings.openclaw_actor_identities.items()
    }
    verified = replace(
        settings, adapter_config=config, openclaw_actor_identities=identities
    )
    assert (
        verified.openclaw_actor_identities[f"{transport}|{ACCOUNT}|actor-forward-a"]
        == MEMBER_A
    )
    assert (
        verified.openclaw_actor_identities[f"{transport}|{ACCOUNT}|actor-forward-b"]
        == MEMBER_B
    )


@pytest.mark.parametrize(
    "invalid",
    [
        "no_service",
        "wrong_fingerprint",
        "disabled",
        "revoked",
        "conversation",
        "different_account_case",
        "other_provider",
        "duplicate_service_account",
        "missing_transport",
        "wrong_transport",
    ],
)
def test_shared_endpoint_exception_cannot_be_used_for_member_accounts(
    tmp_path: Path, invalid: str
) -> None:
    settings = _settings(tmp_path / "rejected-shared-account")
    config = copy.deepcopy(settings.adapter_config)
    identities = dict(settings.openclaw_actor_identities)
    channel = config["service_identities"]["channels"][0]
    if invalid == "no_service":
        del config["service_identities"]
    elif invalid == "wrong_fingerprint":
        channel["provider_account_reference"] = "provider-account:" + "0" * 64
    elif invalid in {"disabled", "revoked"}:
        channel["state"] = invalid
    elif invalid == "conversation":
        channel["intake_mode"] = "conversation"
    elif invalid == "different_account_case":
        del identities[f"imessage|{ACCOUNT}|actor-forward-b"]
        identities[f"imessage|{ACCOUNT.upper()}|actor-forward-b"] = MEMBER_B
    elif invalid == "other_provider":
        channel["provider"] = "discord"
    elif invalid == "duplicate_service_account":
        config["service_identities"]["channels"].append(
            {**channel, "channel_id": "channel:duplicate"}
        )
    elif invalid == "missing_transport":
        del channel["openclaw_channel_id"]
    elif invalid == "wrong_transport":
        channel["openclaw_channel_id"] = "bluebubbles"
    with pytest.raises(ValueError, match="OpenClaw channel account cannot be shared"):
        replace(settings, adapter_config=config, openclaw_actor_identities=identities)
    with pytest.raises(ValueError, match="OpenClaw channel account cannot be shared"):
        replace(
            settings,
            openclaw_actor_identities={
                "discord|ordinary-member-account|actor-a": MEMBER_A,
                "discord|ordinary-member-account|actor-b": MEMBER_B,
            },
        )


def _provenance(
    *,
    actor: str = "actor-forward-a",
    account: str = ACCOUNT,
    channel: str = "imessage",
) -> dict[str, Any]:
    return {
        "actor_id": actor,
        "channel_id": channel,
        "account_id": account,
        "message_id": "native-forward-receipt",
        "correlation_id": "forward:fixture",
        "call_kind": "bridge",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _grant(client: TestClient) -> None:
    for headers, key in [
        (MEMBER_HEADERS, "forward-grant-a"),
        (OTHER_HEADERS, "forward-grant-b"),
    ]:
        response = client.post(
            "/api/member/v1/cos/delegations",
            headers=headers,
            json={
                "request_id": key,
                "context_scope": "member_private",
                "permissions": ["read_context", "create_internal_work"],
            },
        )
        assert response.status_code in {200, 201}, response.text


async def _events(runtime: Any, kind: str) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=[kind])]


def _settle(client: TestClient, intent_event_id: str) -> list[dict[str, Any]]:
    runtime = client.app.state.runtime
    client.portal.call(
        runtime.wait_for_subscriber,
        "member-submissions:interpretation",
        intent_event_id,
    )
    proposals = client.portal.call(_events, runtime, "triage.item.proposed")
    if proposals:
        client.portal.call(
            runtime.wait_for_projection, "triage", proposals[-1]["event_id"]
        )
    return proposals


def test_channel_submission_extracts_privately_and_deduplicates_after_restart(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path / "channel-forward")
    body = {
        "provenance": _provenance(),
        "request_id": "forward-request-1",
        "text": TEXT,
        "consent": True,
        "submission_gesture": "dedicated_intake",
    }
    with TestClient(create_application(settings)) as client:
        _grant(client)
        response = client.post(
            "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["provider_effect_permitted"] is False
        proposals = _settle(client, result["intent_event_id"])
        assert any(row["payload"]["target_kind"] == "task" for row in proposals)
        assert all(row["owner_scope"] == f"private:{MEMBER_A}" for row in proposals)
        own = client.get(
            "/api/member/v1/surfaces/triage", headers=MEMBER_HEADERS
        ).json()
        other = client.get(
            "/api/member/v1/surfaces/triage", headers=OTHER_HEADERS
        ).json()
        assert any(item.get("title") == TEXT for item in own["items"])
        assert not any(item.get("title") == TEXT for item in other["items"])
        intent = client.portal.call(
            client.app.state.runtime.event_log.get, result["intent_event_id"]
        )
        assert intent["actor_identity"] == MEMBER_A
        assert intent["payload"]["submission_kind"] == "member_forwarded"
        assert intent["payload"]["intake_channel"] == "apple_imessage"
        assert (
            client.portal.call(
                _events, client.app.state.runtime, "external_sync.write_requested"
            )
            == []
        )
    with TestClient(create_application(settings)) as client:
        body["request_id"] = "different-http-request-same-receipt"
        body["provenance"]["correlation_id"] = "forward:retry"
        retry = client.post(
            "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["duplicate"] is True
        assert retry.json()["intent_event_id"] == result["intent_event_id"]
        assert (
            len(
                client.portal.call(
                    _events, client.app.state.runtime, "triage.intent.submitted"
                )
            )
            == 1
        )
        body["text"] = "Changed content for the same receipt"
        conflict = client.post(
            "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
        )
        assert conflict.status_code == 409
    remapped = replace(
        settings,
        openclaw_actor_identities={
            **settings.openclaw_actor_identities,
            f"imessage|{ACCOUNT}|actor-forward-a": MEMBER_B,
        },
    )
    with TestClient(create_application(remapped)) as client:
        body["text"] = TEXT
        moved = client.post(
            "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
        )
        assert moved.status_code == 403
        assert (
            len(
                client.portal.call(
                    _events, client.app.state.runtime, "triage.intent.submitted"
                )
            )
            == 1
        )
    renamed_config = copy.deepcopy(remapped.adapter_config)
    renamed_config["service_identities"]["channels"][0]["channel_id"] = (
        "channel:renamed-cos-intake"
    )
    renamed = replace(remapped, adapter_config=renamed_config)
    with TestClient(create_application(renamed)) as client:
        moved = client.post(
            "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
        )
        assert moved.status_code == 403
        assert (
            len(
                client.portal.call(
                    _events, client.app.state.runtime, "triage.intent.submitted"
                )
            )
            == 1
        )


@pytest.mark.parametrize("reviewed_transport", ["imessage", "bluebubbles"])
def test_authenticated_actor_on_other_transport_cannot_use_cos_account(
    tmp_path: Path, reviewed_transport: str
) -> None:
    other_transport = "bluebubbles" if reviewed_transport == "imessage" else "imessage"
    settings = _settings(tmp_path / "exact-transport")
    config = copy.deepcopy(settings.adapter_config)
    config["service_identities"]["channels"][0]["openclaw_channel_id"] = (
        reviewed_transport
    )
    identities = {
        key.replace("imessage|", reviewed_transport + "|", 1): member
        for key, member in settings.openclaw_actor_identities.items()
    }
    # This actor has a valid member mapping in the other transport. That cannot
    # grant the unrelated endpoint the reviewed service account's intake purpose.
    identities[f"{other_transport}|{ACCOUNT}|actor-forward-a"] = MEMBER_A
    settings = replace(
        settings, adapter_config=config, openclaw_actor_identities=identities
    )
    with TestClient(create_application(settings)) as client:
        _grant(client)
        for gesture in ("dedicated_intake", "explicit"):
            response = client.post(
                "/api/openclaw/v1/submissions/forward",
                headers=AUTH,
                json={
                    "provenance": _provenance(channel=other_transport),
                    "request_id": "wrong-transport-" + gesture,
                    "text": TEXT,
                    "consent": True,
                    "submission_gesture": gesture,
                },
            )
            assert response.status_code == 403, response.text
        assert (
            client.portal.call(
                _events, client.app.state.runtime, "triage.intent.submitted"
            )
            == []
        )


def test_channel_binding_purpose_and_missing_evidence_fail_closed(
    tmp_path: Path,
) -> None:
    with TestClient(
        create_application(_settings(tmp_path / "closed", intake_mode="conversation"))
    ) as client:
        _grant(client)
        body = {
            "provenance": _provenance(),
            "request_id": "forward-request-2",
            "text": TEXT,
            "consent": True,
            "submission_gesture": "dedicated_intake",
        }
        assert (
            client.post(
                "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
            ).status_code
            == 403
        )
        body["submission_gesture"] = "explicit"
        body["provenance"] = _provenance(account="personal-account")
        assert (
            client.post(
                "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
            ).status_code
            == 403
        )
        body["provenance"] = _provenance(actor="unmapped-actor")
        assert (
            client.post(
                "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
            ).status_code
            == 403
        )
        body["provenance"] = _provenance()
        body["provenance"]["call_kind"] = "tool"
        assert (
            client.post(
                "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
            ).status_code
            == 403
        )
        body["provenance"] = _provenance()
        for sensitivity in ("privileged", "unknown"):
            rejected = client.post(
                "/api/openclaw/v1/submissions/forward",
                headers=AUTH,
                json={**body, "sensitivity": sensitivity},
            )
            assert rejected.status_code == 422
        assert (
            client.portal.call(
                _events, client.app.state.runtime, "triage.intent.submitted"
            )
            == []
        )
        body["unsupported_attachment_count"] = 1
        response = client.post(
            "/api/openclaw/v1/submissions/forward", headers=AUTH, json=body
        )
        assert response.status_code == 200, response.text
        assert _settle(client, response.json()["intent_event_id"]) == []
        intent = client.portal.call(
            client.app.state.runtime.event_log.get, response.json()["intent_event_id"]
        )
        assert intent["payload"]["evidence_complete"] is False
        assert intent["payload"]["evidence_issues"] == [
            "unsupported_channel_attachment"
        ]


def test_real_gmail_producer_route_reaches_durable_member_interpreter(
    tmp_path: Path,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "gmail_composed_intake", ROOT / "packs/adapters/messaging/gmail/adapter.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = module.GmailAdapter(
        config={
            "user_email": EMAIL_ACCOUNT,
            "provider_account_id": EMAIL_ACCOUNT,
            "service_principal_id": COS_SERVICE_PRINCIPAL_ID,
            "workspace": "service",
            "channel_id": "channel:chief-of-staff:gmail",
            "trusted_sender_routes": [
                {
                    "exact_from_identifier": "member-a@example.invalid",
                    "route_key": "route:" + "1" * 64,
                    "authentication_policy": "receiver_aligned_sender",
                }
            ],
        }
    )
    message = {
        "id": "gmail-native-forward",
        "internalDate": "1925035200000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {
                    "name": "Received",
                    "value": "from sender.invalid by mx.google.com with ESMTPS id fixture",
                },
                {
                    "name": "Authentication-Results",
                    "value": "mx.google.com; dkim=pass header.i=@example.invalid; dmarc=pass header.from=example.invalid",
                },
                {"name": "From", "value": "member-a@example.invalid"},
                {"name": "To", "value": EMAIL_ACCOUNT},
            ],
            "body": {"data": base64.urlsafe_b64encode(TEXT.encode()).decode()},
        },
    }
    payload, version, occurred_at = adapter._received_observation(message)
    with TestClient(create_application(_settings(tmp_path / "gmail"))) as client:
        runtime = client.app.state.runtime
        source_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1925035200000,
                tenant_id=runtime.settings.instance.tenant_id,
                type="messaging.received",
                schema_version=version,
                occurred_at=occurred_at,
                source_adapter="messaging-gmail-api:fixture-cos",
                source_account_id=EMAIL_ACCOUNT,
                owner_scope=f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
                visibility_scope=f"service-private:{COS_SERVICE_PRINCIPAL_ID}",
                payload=payload,
            ),
        )
        client.portal.call(
            runtime.wait_for_subscriber, "direct-intake:routing", source_id
        )
        intents = client.portal.call(_events, runtime, "triage.intent.submitted")
        assert len(intents) == 1
        assert intents[0]["payload"]["source_event_id"] == source_id
        assert intents[0]["payload"]["text"] == TEXT
        proposals = _settle(client, intents[0]["event_id"])
        assert any(row["payload"]["title"] == TEXT for row in proposals)
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
