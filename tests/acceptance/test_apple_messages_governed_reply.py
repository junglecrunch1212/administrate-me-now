"""G11 process proof for governed Apple Messages replies (fixtures only)."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.messages_reply import REPLY_CAPABILITY_VERSION, REPLY_TRANSPORT
from adminme.openclaw_approvals import sign_approval_receipt
from adminme.openclaw_contract import OPENCLAW_COMMIT, OPENCLAW_VERSION
from adminme.runtime.application import RuntimeSettings, create_application
from adminme_bridge.adapters.base import BridgeContext
from adminme_bridge.config import BridgeConfig
from adminme_bridge.connector_packs.messaging_bluebubbles import (
    BlueBubblesMessagesAdapter,
    MessagePage,
)
from adminme_bridge.main import BridgeSupervisor, _CommandLedger
from bootstrap.backup_restore import BackupManager
from bootstrap.contracts import DeploymentPaths
from bootstrap.secrets import secret_store
from bootstrap.services import ServiceManager
from bootstrap.wizard import BootstrapWizard
from tests.acceptance.test_apple_messages_bridge_ingestion import (
    ACCOUNT_A,
    DEVICE_A,
    ENROLLMENT_A,
    HEADERS_A,
    LOGIN_A,
    LOGIN_B,
    MEMBER_A,
    MEMBER_B,
    PACK,
    SOURCE_A,
    _bridge_record,
    _chat,
    _message,
    _PostingSink,
    _settings as g10_settings,
)


SECRET = "g11-loopback-secret-at-least-32-bytes"
REPO_ROOT = Path(__file__).resolve().parents[2]
OPENCLAW_HEADERS = {"Authorization": f"Bearer {SECRET}"}
REPLY_PERMISSION_EVIDENCE = hashlib.sha256(b"g11-local-reply-fixture").hexdigest()


def _settings(instance_dir: Path) -> Any:
    base = g10_settings(instance_dir)
    bridge_enrollments = copy.deepcopy(base.bridge_enrollments)
    for record in bridge_enrollments.values():
        config = record["pack_config"][PACK]
        config.update(
            {
                "reply_transport": REPLY_TRANSPORT,
                "reply_capability_version": REPLY_CAPABILITY_VERSION,
                "reply_permission_verified": True,
                "reply_permission_evidence_sha256": REPLY_PERMISSION_EVIDENCE,
            }
        )
    return replace(
        base,
        bridge_enrollments=bridge_enrollments,
        openclaw_enabled=True,
        openclaw_shared_secret=SECRET,
        openclaw_actor_identities={
            "fixture|g11-account-a|g11-actor-a": MEMBER_A,
            "fixture|g11-account-b|g11-actor-b": MEMBER_B,
        },
    )


def _provenance(suffix: str = "a") -> dict[str, Any]:
    return {
        "actor_id": f"g11-actor-{suffix}",
        "agent_id": f"g11-agent-{suffix}",
        "session_key": f"g11-session-key-{suffix}",
        "session_id": f"g11-session-{suffix}",
        "channel_id": "fixture",
        "account_id": f"g11-account-{suffix}",
        "message_id": f"g11-message-{suffix}",
        "thread_id": f"g11-thread-{suffix}",
        "correlation_id": f"g11-correlation-{suffix}-{time.time_ns()}",
        "call_kind": "tool",
        "openclaw_version": OPENCLAW_VERSION,
        "openclaw_commit": OPENCLAW_COMMIT,
    }


def _allow_once(client: TestClient, domain_approval_event_id: str, suffix: str) -> str:
    params = {"domain_approval_event_id": domain_approval_event_id}
    fingerprint = hashlib.sha256(
        json.dumps(params, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    issued_at_ms = int(time.time() * 1000)
    expires_at_ms = issued_at_ms + 120_000
    receipt_id = f"g11-openclaw-receipt-{suffix}"
    signature = sign_approval_receipt(
        SECRET,
        receipt_id=receipt_id,
        tool_name="administrateme_execute_pending_provider_action",
        request_fingerprint=fingerprint,
        issued_at_ms=issued_at_ms,
        expires_at_ms=expires_at_ms,
        openclaw_version=OPENCLAW_VERSION,
        openclaw_commit=OPENCLAW_COMMIT,
    )
    response = client.post(
        "/api/openclaw/v1/approvals/resolve",
        headers=OPENCLAW_HEADERS,
        json={
            "receipt_id": receipt_id,
            "tool_name": "administrateme_execute_pending_provider_action",
            "request_fingerprint": fingerprint,
            "issued_at_ms": issued_at_ms,
            "expires_at_ms": expires_at_ms,
            "decision": "allow-once",
            "openclaw_version": OPENCLAW_VERSION,
            "openclaw_commit": OPENCLAW_COMMIT,
            "signature": signature,
        },
    )
    assert response.status_code == 200, response.text
    return receipt_id


def _execute(
    client: TestClient, domain_approval_event_id: str, suffix: str, *, receipt: bool
) -> Any:
    body: dict[str, Any] = {
        "provenance": _provenance(),
        "domain_approval_event_id": domain_approval_event_id,
    }
    if receipt:
        body["openclaw_approval_receipt_id"] = _allow_once(
            client, domain_approval_event_id, suffix
        )
    return client.post(
        "/api/openclaw/v1/tools/execute-pending-provider-action",
        headers=OPENCLAW_HEADERS,
        json=body,
    )


@dataclass
class _WriteTransport:
    pages: list[MessagePage]
    chats: dict[str, Mapping[str, Any]]
    messages: dict[str, Mapping[str, Any]]
    effects: list[dict[str, str]] = field(default_factory=list)
    send_error: Exception | None = None

    async def server_info(self) -> Mapping[str, Any]:
        return {
            "server_version": "1.9.9",
            "computer_id": "fixture-computer-g10",
            "private_api": False,
        }

    async def query_messages(self, **_kwargs: Any) -> MessagePage:
        assert self.pages
        return self.pages.pop(0)

    async def chat(self, native_chat_id: str) -> Mapping[str, Any]:
        return self.chats[native_chat_id]

    async def message(self, native_message_id: str) -> Mapping[str, Any]:
        return self.messages[native_message_id]

    async def send_text_reply(
        self, *, native_chat_id: str, body: str, temp_guid: str
    ) -> Mapping[str, Any]:
        if self.send_error is not None:
            raise self.send_error
        self.effects.append(
            {"native_chat_id": native_chat_id, "body": body, "temp_guid": temp_guid}
        )
        return {
            "outcome": "sent",
            "external_id": "message-g11-sent",
            "provider_revision": "fixture-revision-1",
            "temp_guid": temp_guid,
        }

    async def aclose(self) -> None:
        return None


def _thread(client: TestClient) -> dict[str, Any]:
    response = client.get(
        "/api/member/v1/surfaces/comms",
        headers={"Tailscale-User-Login": LOGIN_A},
    )
    assert response.status_code == 200, response.text
    return next(
        item
        for item in response.json()["cockpit"]["threads"]
        if item["provider"] == "bluebubbles"
    )


def _approve_member(
    client: TestClient, thread: dict[str, Any], *, command_id: str, body: str
) -> Any:
    return client.post(
        f"/api/member/v1/comms/{thread['last_interaction_id']}/commands",
        headers={"Tailscale-User-Login": LOGIN_A},
        json={
            "operation": "reply",
            "expected_last_event_id": thread["last_event_id"],
            "request_id": command_id,
            "body": body,
        },
    )


async def _reply_lifecycle_events(runtime: Any) -> list[dict[str, Any]]:
    return [
        event
        async for event in runtime.event_log.read_since(
            types=["messaging.reply.observed", "messaging.reply.reconciled"]
        )
    ]


def test_g11_owner_approvals_bridge_effect_and_g10_reconciliation(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    chat_id = "iMessage;-;chat-g11-exact"
    incoming = _message(
        "message-g11-incoming",
        1,
        chat_id=chat_id,
        sender="endpoint-g11",
        text="Can you reply exactly?",
    )
    transport = _WriteTransport(
        pages=[
            MessagePage((incoming,), 0, 1, 1),
            MessagePage((incoming,), 0, 500, 1),
        ],
        chats={chat_id: _chat(chat_id, "endpoint-g11")},
        messages={"message-g11-incoming": incoming},
    )
    with TestClient(app) as client:
        runtime = app.state.runtime
        delegation = client.post(
            "/api/member/v1/cos/delegations",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "request_id": "g11-cos-delegation",
                "context_scope": "member_private",
                "permissions": ["read_context", "prepare_external_actions"],
            },
        )
        assert delegation.status_code == 201, delegation.text
        config = settings.bridge_enrollments[LOGIN_A]["pack_config"][PACK]
        cursor_store = AdapterCursorStore(tmp_path / "g11-cursor.sqlite3")
        adapter = BlueBubblesMessagesAdapter(
            config=config, transport=transport, cursor_store=cursor_store
        )
        sink = _PostingSink(client, HEADERS_A)
        ctx = BridgeContext(
            member_id=MEMBER_A,
            ingest_client=object(),  # type: ignore[arg-type]
            correlation_id_factory=lambda: f"g11-{time.time_ns()}",
            now_factory=lambda: datetime.now(timezone.utc),
            sink=sink,
            tenant_id=runtime.settings.instance.tenant_id,
            enrollment_id=ENROLLMENT_A,
            configuration_sha256="1" * 64,
            device_identity_sha256=DEVICE_A,
        )
        unsettled = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": LOGIN_A},
        ).json()["control"]["apple_messages_replies"]
        assert unsettled["state"] == "read_observation_not_settled"
        initial_ids = asyncio.run(adapter.observe_once(ctx))
        client.portal.call(runtime.wait_for_projection, "interactions", initial_ids[0])
        client.portal.call(
            runtime.wait_for_subscriber, "source-policy-v2:shadow", initial_ids[0]
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            initial_ids[0],
        )

        settings_surface = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": LOGIN_A},
        ).json()["control"]["apple_messages_replies"]
        assert settings_surface["state"] == "available_but_disabled"
        assert settings_surface["provider_effects"] is False
        assert _thread(client)["capabilities"]["reply"] is False

        view_as = client.post(
            "/api/member/v1/settings/apple-messages-replies",
            headers={
                "Tailscale-User-Login": LOGIN_A,
                "X-AdminMe-View-Member": MEMBER_B,
            },
            json={
                "expected_revision": 0,
                "request_id": "g11-view-as-enable",
                "action": "enable",
            },
        )
        assert view_as.status_code == 403
        assert not client.get(
            "/api/member/v1/surfaces/comms",
            headers={"Tailscale-User-Login": LOGIN_B},
        ).json()["cockpit"]["threads"]

        enabled = client.post(
            "/api/member/v1/settings/apple-messages-replies",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 0,
                "request_id": "g11-owner-enable",
                "action": "enable",
            },
        )
        assert enabled.status_code == 202, enabled.text
        assert enabled.json()["provider_effects"] == 0
        thread = _thread(client)
        assert thread["capabilities"]["reply"] is True
        assert thread["reply_eligibility"]["new_conversation_allowed"] is False
        asset = client.get("/member-assets/member.js")
        assert asset.status_code == 200
        for browser_contract in (
            "Approve this exact reply",
            "external provider effect",
            "Group conversation",
            "participant set",
            "reconciled",
            "ambiguous",
            "canceled",
        ):
            assert browser_contract in asset.text
        view_as_send = client.post(
            f"/api/member/v1/comms/{thread['last_interaction_id']}/commands",
            headers={
                "Tailscale-User-Login": LOGIN_A,
                "X-AdminMe-View-Member": MEMBER_B,
            },
            json={
                "operation": "reply",
                "expected_last_event_id": thread["last_event_id"],
                "request_id": "g11-view-as-send",
                "body": "must not send",
            },
        )
        assert view_as_send.status_code == 403
        oversized = _approve_member(
            client,
            thread,
            command_id="g11-oversized-command",
            body="x" * 20_001,
        )
        assert oversized.status_code == 422

        exact_suppressed_body = "  Unicode reply: café 🧭\nsecond line  "
        approved = _approve_member(
            client,
            thread,
            command_id="g11-suppressed-command",
            body=exact_suppressed_body,
        )
        assert approved.status_code == 200, approved.text
        duplicate = _approve_member(
            client,
            thread,
            command_id="g11-suppressed-command",
            body=exact_suppressed_body,
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["event_id"] == approved.json()["event_id"]
        modified = _approve_member(
            client,
            thread,
            command_id="g11-suppressed-command",
            body=exact_suppressed_body + "changed",
        )
        assert modified.status_code == 409
        missing_openclaw = _execute(
            client,
            approved.json()["domain_approval_event_id"],
            "missing",
            receipt=False,
        )
        assert missing_openclaw.status_code == 403
        missing_domain = _execute(
            client,
            "event-g11-domain-approval-does-not-exist",
            "missing-domain",
            receipt=False,
        )
        assert missing_domain.status_code in {403, 404}
        dispatched = _execute(
            client,
            approved.json()["domain_approval_event_id"],
            "suppressed",
            receipt=True,
        )
        assert dispatched.status_code == 200, dispatched.text
        suppressed = client.get(
            "/api/bridge/commands?providers=bluebubbles",
            headers=HEADERS_A,
        )
        assert suppressed.status_code == 200, suppressed.text
        assert suppressed.json()["commands"] == []
        assert suppressed.json()["observation_active"] is True
        assert transport.effects == []

        client.portal.call(
            runtime.observation.disable,
            "operator:fixture:g11",
            "credential-free G11 process acceptance",
        )
        pause_thread = _thread(client)
        pause_pending = _approve_member(
            client,
            pause_thread,
            command_id="g11-pause-during-pending",
            body="This must be stopped before provider contact",
        )
        assert pause_pending.status_code == 200, pause_pending.text
        pause_dispatched = _execute(
            client,
            pause_pending.json()["domain_approval_event_id"],
            "pause-pending",
            receipt=True,
        )
        assert pause_dispatched.status_code == 200, pause_dispatched.text
        paused = client.post(
            "/api/member/v1/settings/apple-messages-replies",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 1,
                "request_id": "g11-owner-pause",
                "action": "pause",
            },
        )
        assert paused.status_code == 202, paused.text
        denied_after_pause = client.get(
            "/api/bridge/commands?providers=bluebubbles", headers=HEADERS_A
        )
        assert denied_after_pause.status_code == 200, denied_after_pause.text
        assert denied_after_pause.json()["commands"] == []
        paused_terminal = client.portal.call(
            runtime.event_log.get_by_idempotency,
            runtime.settings.instance.tenant_id,
            "external-sync-result:g11-pause-during-pending",
        )
        assert paused_terminal["type"] == "external_sync.write_failed"
        assert paused_terminal["payload"]["provider_called"] is False
        resumed = client.post(
            "/api/member/v1/settings/apple-messages-replies",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 2,
                "request_id": "g11-owner-resume",
                "action": "enable",
            },
        )
        assert resumed.status_code == 202, resumed.text

        cancel_thread = _thread(client)
        cancel_pending = _approve_member(
            client,
            cancel_thread,
            command_id="g11-cancel-pending",
            body="Canceled before OpenClaw approval",
        )
        assert cancel_pending.status_code == 200, cancel_pending.text
        canceled = client.post(
            "/api/member/v1/comms/replies/g11-cancel-pending/cancel",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={"request_id": "g11-cancel-request"},
        )
        assert canceled.status_code == 202, canceled.text
        canceled_execute = _execute(
            client,
            cancel_pending.json()["domain_approval_event_id"],
            "canceled",
            receipt=True,
        )
        assert canceled_execute.status_code == 409

        thread = _thread(client)
        exact_body = " Keep leading and trailing whitespace 🧭 "
        approved_send = _approve_member(
            client, thread, command_id="g11-send-command", body=exact_body
        )
        assert approved_send.status_code == 200, approved_send.text
        dispatched_send = _execute(
            client,
            approved_send.json()["domain_approval_event_id"],
            "send",
            receipt=True,
        )
        assert dispatched_send.status_code == 200, dispatched_send.text
        pending = client.get(
            "/api/bridge/commands?providers=bluebubbles", headers=HEADERS_A
        )
        assert pending.status_code == 200, pending.text
        assert pending.json()["count"] == 1
        command = pending.json()["commands"][0]
        assert command["payload"]["body"] == exact_body
        assert command["payload"]["native_chat_id"] == chat_id

        bridge_config = BridgeConfig(
            member_id=MEMBER_A,
            enrollment_id=ENROLLMENT_A,
            tenant_id=runtime.settings.instance.tenant_id,
            central_endpoint="https://fixture.invalid",
            tailscale_identity=LOGIN_A,
            cursor_db_path=str(tmp_path / "g11-command-ledger.sqlite3"),
            configured_packs=[PACK],
            enabled_packs=[PACK],
            pack_config={PACK: config},
        )
        supervisor = BridgeSupervisor(
            config=bridge_config,
            ingest_client=object(),  # type: ignore[arg-type]
            adapters=[adapter],
        )

        def assert_denied_without_effect(candidate: dict[str, Any]) -> None:
            before = list(transport.effects)
            with pytest.raises(Exception):
                candidate_adapter = supervisor._authorized_command_adapter(  # noqa: SLF001
                    candidate, {"bluebubbles": adapter}
                )
                asyncio.run(
                    candidate_adapter.apply_write(
                        candidate["operation"],
                        candidate["payload"],
                        candidate["target_external_id"],
                    )
                )
            assert transport.effects == before

        forged_cases: list[dict[str, Any]] = []
        for path, value in (
            (("event_id",), ""),
            (("domain_approval_event_id",), ""),
            (("member_id",), MEMBER_B),
            (("provider_account_id",), "provider-account:" + "f" * 32),
            (("payload", "approval_basis"), "forged"),
            (("payload", "approval_expires_at"), "2000-01-01T00:00:00+00:00"),
            (("payload", "body"), exact_body + "changed"),
            (("payload", "native_chat_id"), "iMessage;-;different-chat"),
            (("payload", "participant_ids"), ["forged", "participants"]),
            (("payload", "bridge_enrollment_id"), "wrong-enrollment"),
            (("payload", "member_owner_id"), MEMBER_B),
            (("payload", "capability_version"), "changed-capability"),
            (("payload", "policy_version"), "changed-policy"),
            (("payload", "expected_native_revision"), "changed-revision"),
            (("payload", "is_group"), True),
            (("payload", "observation_mode_disposition"), "dispatch_unchecked"),
            (("payload", "idempotency_key"), "forged-idempotency"),
        ):
            forged = copy.deepcopy(command)
            target = forged
            for segment in path[:-1]:
                target = target[segment]
            target[path[-1]] = value
            forged_cases.append(forged)
        for forged in forged_cases:
            assert_denied_without_effect(forged)

        permission_missing_config = copy.deepcopy(config)
        permission_missing_config["reply_permission_verified"] = False
        permission_missing_config["reply_permission_evidence_sha256"] = None
        permission_missing_bridge = BridgeConfig(
            member_id=MEMBER_A,
            enrollment_id=ENROLLMENT_A,
            tenant_id=runtime.settings.instance.tenant_id,
            central_endpoint="https://fixture.invalid",
            tailscale_identity=LOGIN_A,
            cursor_db_path=str(tmp_path / "g11-permission-missing.sqlite3"),
            configured_packs=[PACK],
            enabled_packs=[PACK],
            pack_config={PACK: permission_missing_config},
        )
        permission_missing_supervisor = BridgeSupervisor(
            config=permission_missing_bridge,
            ingest_client=object(),  # type: ignore[arg-type]
            adapters=[adapter],
        )
        with pytest.raises(ValueError, match="local_reply_capability"):
            permission_missing_supervisor._authorized_command_adapter(  # noqa: SLF001
                command, {"bluebubbles": adapter}
            )
        permission_missing_supervisor._command_ledger.close()  # noqa: SLF001

        authorized_adapter = supervisor._authorized_command_adapter(  # noqa: SLF001
            command, {"bluebubbles": adapter}
        )
        result = asyncio.run(
            authorized_adapter.apply_write(
                command["operation"], command["payload"], command["target_external_id"]
            )
        )
        assert result["outcome"] == "sent"
        assert transport.effects == [
            {
                "native_chat_id": chat_id,
                "body": exact_body,
                "temp_guid": transport.effects[0]["temp_guid"],
            }
        ]
        result_envelope = supervisor._result_envelope(  # noqa: SLF001
            command, result=result, provider_called=True
        )
        uploaded = client.post(
            "/api/bridge/ingest", headers=HEADERS_A, json=result_envelope
        )
        assert uploaded.status_code == 202, uploaded.text
        duplicate_result = client.post(
            "/api/bridge/ingest", headers=HEADERS_A, json=result_envelope
        )
        assert duplicate_result.status_code == 202, duplicate_result.text
        assert len(transport.effects) == 1

        outgoing = _message(
            "message-g11-sent",
            2,
            chat_id=chat_id,
            sender="endpoint-g11",
            text=exact_body,
            outgoing=True,
        )
        transport.messages["message-g11-sent"] = outgoing
        transport.pages.extend(
            [
                MessagePage((outgoing,), 0, 250, 1),
                MessagePage((incoming,), 0, 250, 1),
            ]
        )
        later_ids = asyncio.run(adapter.observe_once(ctx))
        outgoing_event_id = next(
            event_id
            for envelope, event_id in sink.accepted
            if envelope.type == "messaging.sent"
            and envelope.payload["native_message_id"] == "message-g11-sent"
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "apple-messages-reply:reconciliation",
            outgoing_event_id,
        )
        reconciled = client.portal.call(
            runtime.event_log.get_by_idempotency,
            runtime.settings.instance.tenant_id,
            "messages-reply-reconciled:g11-send-command",
        )
        assert reconciled is not None
        assert reconciled["payload"]["observation_event_id"] == outgoing_event_id
        assert reconciled["payload"]["native_message_id"] == "message-g11-sent"
        client.portal.call(
            runtime.wait_for_projection, "interactions", reconciled["event_id"]
        )
        client.portal.call(
            runtime.wait_for_subscriber, "source-policy-v2:shadow", outgoing_event_id
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            outgoing_event_id,
        )
        assert _thread(client)["command_state"] == "reconciled"
        assert later_ids

        before_rebuild = (
            runtime.projections.connection("interactions")
            .execute(
                "SELECT interaction_id, command_state, last_command_id FROM interactions "
                "WHERE tenant_id=? AND last_command_id=?",
                (runtime.settings.instance.tenant_id, "g11-send-command"),
            )
            .fetchall()
        )
        client.portal.call(runtime.projections.rebuild, "interactions")
        after_rebuild = (
            runtime.projections.connection("interactions")
            .execute(
                "SELECT interaction_id, command_state, last_command_id FROM interactions "
                "WHERE tenant_id=? AND last_command_id=?",
                (runtime.settings.instance.tenant_id, "g11-send-command"),
            )
            .fetchall()
        )
        assert [tuple(row) for row in after_rebuild] == [
            tuple(row) for row in before_rebuild
        ]

        revoke_thread = _thread(client)
        revoke_pending = _approve_member(
            client,
            revoke_thread,
            command_id="g11-revoke-during-pending",
            body="This approval must be revoked before provider contact",
        )
        assert revoke_pending.status_code == 200, revoke_pending.text
        revoke_dispatched = _execute(
            client,
            revoke_pending.json()["domain_approval_event_id"],
            "revoke-pending",
            receipt=True,
        )
        assert revoke_dispatched.status_code == 200, revoke_dispatched.text
        revoked = client.post(
            "/api/member/v1/settings/apple-messages-replies",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 3,
                "request_id": "g11-owner-revoke",
                "action": "revoke",
            },
        )
        assert revoked.status_code == 202, revoked.text
        denied_after_revoke = client.get(
            "/api/bridge/commands?providers=bluebubbles", headers=HEADERS_A
        )
        assert denied_after_revoke.status_code == 200, denied_after_revoke.text
        assert denied_after_revoke.json()["commands"] == []
        revoked_terminal = client.portal.call(
            runtime.event_log.get_by_idempotency,
            runtime.settings.instance.tenant_id,
            "external-sync-result:g11-revoke-during-pending",
        )
        assert revoked_terminal["type"] == "external_sync.write_failed"
        assert revoked_terminal["payload"]["provider_called"] is False
        assert len(transport.effects) == 1

        direct = _message(
            "message-g11-direct-external",
            3,
            chat_id=chat_id,
            sender="endpoint-g11",
            text="Direct member action outside AdministrateMe",
            outgoing=True,
        )
        transport.messages["message-g11-direct-external"] = direct
        transport.pages.extend(
            [
                MessagePage((direct,), 0, 250, 1),
                MessagePage((incoming,), 0, 250, 1),
            ]
        )
        asyncio.run(adapter.observe_once(ctx))
        direct_event_id = next(
            event_id
            for envelope, event_id in sink.accepted
            if envelope.type == "messaging.sent"
            and envelope.payload["native_message_id"] == "message-g11-direct-external"
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "apple-messages-reply:reconciliation",
            direct_event_id,
        )
        lifecycle = client.portal.call(_reply_lifecycle_events, runtime)
        assert all(
            event["payload"].get("native_message_id") != "message-g11-direct-external"
            for event in lifecycle
        )
        supervisor._command_ledger.close()  # noqa: SLF001
        cursor_store.close()

    with TestClient(create_application(settings)) as restarted_client:
        restarted_thread = _thread(restarted_client)
        assert restarted_thread["capabilities"]["reply"] is False
        restarted_control = restarted_client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": LOGIN_A},
        ).json()["control"]["apple_messages_replies"]
        assert restarted_control["state"] == "revoked"
        assert restarted_control["revision"] == 4


def test_g11_bridge_ledger_and_edge_contract_fail_closed(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.sqlite3"
    first = _CommandLedger(ledger_path)
    assert first.begin("command-1", "a" * 64) == "new"
    cached = {"payload": {"request_fingerprint": "c" * 64}}
    first.put("command-result", cached)
    assert first.get("command-result", "c" * 64) == cached
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        first.get("command-result", "d" * 64)
    first.close()
    restarted = _CommandLedger(ledger_path)
    assert restarted.begin("command-1", "a" * 64) == "ambiguous"
    assert restarted.begin("command-1", "b" * 64) == "conflict"
    restarted.close()


def test_g11_encrypted_backup_restore_preserves_reply_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_TEST_MODE", "1")
    paths = DeploymentPaths.from_root(tmp_path / "deployment")
    BootstrapWizard(
        paths,
        root=REPO_ROOT,
        fixture="two-adult-one-child",
        test_mode=True,
        dry_run=True,
        preflight_overrides={
            "system": "Darwin",
            "machine": "arm64",
            "macos_version": "26.0",
        },
    ).run()
    references = json.loads(
        (paths.instance_dir / "config" / "secret-references.json").read_text("utf-8")
    )["references"]
    event_key = secret_store(paths, test_mode=True).get(references["event_log"])
    mapping = json.loads(
        (paths.instance_dir / "config" / "member-mapping.json").read_text("utf-8")
    )
    member_id = next(
        str(member["member_id"])
        for member in mapping["members"]
        if member["role"] == "principal"
    )
    login = "g11-backup.ts.net"
    enrollment = "bridge-enrollment-g11-backup"
    record = _bridge_record(
        member_id=member_id,
        enrollment_id=enrollment,
        account=ACCOUNT_A,
        source=SOURCE_A,
        device=DEVICE_A,
    )
    record["pack_config"][PACK].update(
        {
            "reply_transport": REPLY_TRANSPORT,
            "reply_capability_version": REPLY_CAPABILITY_VERSION,
            "reply_permission_verified": True,
            "reply_permission_evidence_sha256": REPLY_PERMISSION_EVIDENCE,
        }
    )
    # The bootstrap already applies the production intake policy. Reply controls
    # and their backup/restore authority must survive without passive extraction.
    settings = RuntimeSettings.credential_free(
        paths.instance_dir,
        encryption_key=event_key,
        repository_root=REPO_ROOT,
        member_identities={login: member_id},
        member_roles={
            str(member["member_id"]): str(member["role"])
            for member in mapping["members"]
        },
        household_mapping=mapping,
        bridge_enrollments={login: record},
    )
    event_id: str
    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        chat_id = "iMessage;-;chat-g11-backup"
        incoming = _message(
            "message-g11-backup-observed",
            1,
            chat_id=chat_id,
            sender="endpoint-g11-backup",
            text="Backup fixture read settlement",
        )
        transport = _WriteTransport(
            pages=[
                MessagePage((incoming,), 0, 1, 1),
                MessagePage((incoming,), 0, 500, 1),
            ],
            chats={chat_id: _chat(chat_id, "endpoint-g11-backup")},
            messages={"message-g11-backup-observed": incoming},
        )
        cursor_store = AdapterCursorStore(tmp_path / "g11-backup-cursor.sqlite3")
        adapter = BlueBubblesMessagesAdapter(
            config=record["pack_config"][PACK],
            transport=transport,
            cursor_store=cursor_store,
        )
        sink = _PostingSink(
            client,
            {
                "Tailscale-User-Login": login,
                "X-AdministrateMe-Bridge-Enrollment": enrollment,
            },
        )
        observed_ids = asyncio.run(
            adapter.observe_once(
                BridgeContext(
                    member_id=member_id,
                    ingest_client=object(),  # type: ignore[arg-type]
                    correlation_id_factory=lambda: f"g11-backup-{time.time_ns()}",
                    now_factory=lambda: datetime.now(timezone.utc),
                    sink=sink,
                    tenant_id=runtime.settings.instance.tenant_id,
                    enrollment_id=enrollment,
                    configuration_sha256="2" * 64,
                    device_identity_sha256=DEVICE_A,
                )
            )
        )
        client.portal.call(runtime.wait_for_projection, "interactions", observed_ids[0])
        client.portal.call(
            runtime.wait_for_subscriber,
            "source-policy-v2:shadow",
            observed_ids[0],
        )
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            observed_ids[0],
        )
        enabled = client.post(
            "/api/member/v1/settings/apple-messages-replies",
            headers={"Tailscale-User-Login": login},
            json={
                "expected_revision": 0,
                "request_id": "g11-backup-enable",
                "action": "enable",
            },
        )
        assert enabled.status_code == 202, enabled.text
        event_id = enabled.json()["event_id"]
        assert enabled.json()["provider_effects"] == 0
        cursor_store.close()

    ServiceManager(paths, test_mode=True).start()
    manager = BackupManager(paths, root=REPO_ROOT, test_mode=True)
    backup = manager.create()
    restored = manager.restore(Path(backup["path"]))
    assert restored["projections_rebuilt"] == 15

    with TestClient(create_application(settings)) as client:
        runtime = client.app.state.runtime
        control = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": login},
        ).json()["control"]["apple_messages_replies"]
        assert control["state"] == "enabled_ask_every_time"
        assert control["revision"] == 1
        event = client.portal.call(runtime.event_log.get, event_id)
        assert event["type"] == "settings.apple_messages_reply_controlled"
        assert event["payload"]["coordinates"] == control["coordinates"]
        assert event["payload"]["provider_effects"] is False
