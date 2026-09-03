"""G10 process proof for read-only Apple Messages bridge ingestion."""

from __future__ import annotations

import asyncio
import copy
import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.envelope import EventEnvelope
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_assurance import audit_source_assurance
from adminme_bridge.adapters.base import BridgeContext
from adminme_bridge.adapters.sinks import EmitResult
from adminme_bridge.connector_packs.messaging_bluebubbles import (
    SELECTED_TRANSPORT_COMMIT,
    SELECTED_TRANSPORT_VERSION,
    BlueBubblesMessagesAdapter,
    MessagePage,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER_A = "member-g10-a"  # fixture:tenant_data:ok
MEMBER_B = "member-g10-b"  # fixture:tenant_data:ok
LOGIN_A = "g10-a.ts.net"
LOGIN_B = "g10-b.ts.net"
ENROLLMENT_A = "bridge-enrollment-g10-a"
ENROLLMENT_B = "bridge-enrollment-g10-b"
PACK = "messaging-bluebubbles"
ACCOUNT_A = "provider-account:" + "a" * 32
ACCOUNT_B = "provider-account:" + "b" * 32
SOURCE_A = "protected-source:" + hashlib.sha256(b"fixture-computer-g10").hexdigest()
SOURCE_B = "protected-source:" + hashlib.sha256(b"fixture-computer-g10-b").hexdigest()
DEVICE_A = "e" * 64
DEVICE_B = "f" * 64
HEADERS_A = {
    "Tailscale-User-Login": LOGIN_A,
    "X-AdministrateMe-Bridge-Enrollment": ENROLLMENT_A,
}
HEADERS_B = {
    "Tailscale-User-Login": LOGIN_B,
    "X-AdministrateMe-Bridge-Enrollment": ENROLLMENT_B,
}


def _pack_config(
    *, member_id: str, enrollment_id: str, account: str, source: str, device: str
) -> dict[str, Any]:
    return {
        "member_id": member_id,
        "provider_account_id": account,
        "deployment_target": "bridge",
        "workspace": "personal",
        "permission_scope": [
            "bluebubbles.messages.read",
            "macos.full_disk_access",
        ],
        "binding_provenance": "member-bridge-enrollment",
        "ownership_attested": True,
        "protected_source_id": source,
        "allowed_container_ids": [source],
        "shared_container_ids": [],
        "base_url": "http://127.0.0.1:1234",
        "credential_ref": "keychain://adminme-bluebubbles/member-bridge",
        "transport_version": SELECTED_TRANSPORT_VERSION,
        "transport_commit": SELECTED_TRANSPORT_COMMIT,
        "history_limit": 20,
        "poll_interval_seconds": 2,
        "network_containment_verified": True,
        "remote_services_verified_disabled": True,
        "device_identity_sha256": device,
        "bridge_enrollment_id": enrollment_id,
    }


def _bridge_record(
    *, member_id: str, enrollment_id: str, account: str, source: str, device: str
) -> dict[str, Any]:
    return {
        "member_id": member_id,
        "enrollment_id": enrollment_id,
        "status": "active",
        "configured_packs": [PACK],
        "enabled_packs": [PACK],
        "authorized_packs": [PACK],
        "adapters_active": [PACK],
        "pack_config": {
            PACK: _pack_config(
                member_id=member_id,
                enrollment_id=enrollment_id,
                account=account,
                source=source,
                device=device,
            )
        },
        "pack_capability_states": {PACK: "attested"},
        "provider_capability_state": "attested",
        "capability_attestations": {PACK: {"event_id": f"attested:{enrollment_id}"}},
    }


def _settings(
    instance_dir: Path, *, member_b_account: str = ACCOUNT_B
) -> RuntimeSettings:
    return RuntimeSettings.credential_free(
        instance_dir,
        encryption_key=b"m" * 32,
        repository_root=REPO_ROOT,
        member_identities={LOGIN_A: MEMBER_A, LOGIN_B: MEMBER_B},
        member_roles={MEMBER_A: "principal", MEMBER_B: "principal"},
        household_mapping={
            "schema_version": 2,
            "household_label": "G10 fixture household",  # fixture:tenant_data:ok
            "members": [
                {
                    "member_id": MEMBER_A,
                    "label": "Fixture member A",
                    "role": "principal",
                },
                {
                    "member_id": MEMBER_B,
                    "label": "Fixture member B",
                    "role": "principal",
                },
            ],
            "memberships": [
                {
                    "membership_id": "membership-g10-a",
                    "party_id": MEMBER_A,
                    "role": "principal",
                },
                {
                    "membership_id": "membership-g10-b",
                    "party_id": MEMBER_B,
                    "role": "principal",
                },
            ],
            "relationships": [],
        },
        bridge_enrollments={
            LOGIN_A: _bridge_record(
                member_id=MEMBER_A,
                enrollment_id=ENROLLMENT_A,
                account=ACCOUNT_A,
                source=SOURCE_A,
                device=DEVICE_A,
            ),
            LOGIN_B: _bridge_record(
                member_id=MEMBER_B,
                enrollment_id=ENROLLMENT_B,
                account=member_b_account,
                source=SOURCE_B,
                device=DEVICE_B,
            ),
        },
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER_A,
        reconciliation_admission_enabled=True,
    )


@dataclass
class _FixtureTransport:
    pages: list[MessagePage]
    chats: dict[str, Mapping[str, Any]]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def server_info(self) -> Mapping[str, Any]:
        self.calls.append(("server_info", {}))
        return {
            "server_version": SELECTED_TRANSPORT_VERSION,
            "computer_id": "fixture-computer-g10",
            "private_api": False,
        }

    async def query_messages(self, **kwargs: Any) -> MessagePage:
        self.calls.append(("query_messages", dict(kwargs)))
        assert self.pages
        return self.pages.pop(0)

    async def chat(self, native_chat_id: str) -> Mapping[str, Any]:
        self.calls.append(("chat", {"native_chat_id": native_chat_id}))
        return self.chats[native_chat_id]

    async def aclose(self) -> None:
        self.calls.append(("aclose", {}))


@dataclass
class _PostingSink:
    client: TestClient
    headers: dict[str, str]
    accepted: list[tuple[EventEnvelope, str]] = field(default_factory=list)

    async def emit(self, envelope: EventEnvelope) -> EmitResult:
        response = self.client.post(
            "/api/bridge/ingest",
            headers=self.headers,
            json=envelope.model_dump(mode="json"),
        )
        if response.status_code != 202:
            return EmitResult(False, None, response.text)
        event_id = str(response.json()["event_id"])
        self.accepted.append((envelope, event_id))
        return EmitResult(True, event_id, None)


def _at_ms(offset_seconds: int) -> int:
    base = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    return int(base.timestamp() * 1000) + offset_seconds * 1000


def _message(
    native_id: str,
    offset_seconds: int,
    *,
    chat_id: str,
    sender: str,
    text: str,
    outgoing: bool = False,
) -> dict[str, Any]:
    timestamp = _at_ms(offset_seconds)
    return {
        "guid": native_id,
        "dateCreated": timestamp,
        "text": text,
        "subject": None,
        "isFromMe": outgoing,
        "isDelivered": outgoing,
        "dateDelivered": timestamp if outgoing else None,
        "dateRead": None,
        "dateEdited": None,
        "dateRetracted": None,
        "isArchived": False,
        "itemType": 0,
        "isSystemMessage": False,
        "associatedMessageGuid": None,
        "associatedMessageType": None,
        "replyToGuid": None,
        "handle": {"address": sender, "service": "iMessage"},
        "chats": [{"guid": chat_id}],
        "attachments": [],
    }


def _chat(chat_id: str, *participants: str, group: bool = False) -> dict[str, Any]:
    return {
        "guid": chat_id,
        "style": 43 if group else 45,
        "participants": [{"address": value} for value in participants],
    }


async def _provider_effect_count(runtime: Any) -> int:
    types = [
        "external_sync.write_requested",
        "external_sync.write_sent",
        "external_sync.write_completed",
        "provider.command.requested",
    ]
    return sum([1 async for _event in runtime.event_log.read_since(types=types)])


async def _source_assurance(runtime: Any) -> dict[str, Any]:
    return await audit_source_assurance(
        runtime.event_log,
        repository_root=REPO_ROOT,
        capabilities=runtime.capabilities,
    )


def test_g10_connector_to_production_triage_comms_policy_and_replay(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    app = create_application(_settings(instance))
    with TestClient(app) as client:
        runtime = app.state.runtime
        promise_chat = "iMessage;-;chat-g10-promise"
        direct_chat = "iMessage;-;chat-g10-direct"
        group_chat = "iMessage;+;chat-g10-group"
        messages = (
            _message(
                "message-g10-promise",
                1,
                chat_id=promise_chat,
                sender="endpoint-alpha",
                text="I will submit the fixture form tomorrow.",
                outgoing=True,
            ),
            _message(
                "message-g10-code",
                2,
                chat_id=direct_chat,
                sender="endpoint-alpha",
                text="Your verification code is 123456.",
            ),
            _message(
                "message-g10-request",
                3,
                chat_id=direct_chat,
                sender="endpoint-alpha",
                text="Please submit the fixture form tomorrow.",
            ),
            _message(
                "message-g10-chatter",
                4,
                chat_id=group_chat,
                sender="endpoint-beta",
                text="Nice, thanks everyone!",
            ),
        )
        transport = _FixtureTransport(
            pages=[
                MessagePage((messages[-1],), 0, 1, len(messages)),
                MessagePage(messages, 0, 20, len(messages)),
            ],
            chats={
                promise_chat: _chat(promise_chat, "endpoint-alpha"),
                direct_chat: _chat(direct_chat, "endpoint-alpha"),
                group_chat: _chat(
                    group_chat, "endpoint-alpha", "endpoint-beta", group=True
                ),
            },
        )
        cursor_store = AdapterCursorStore(tmp_path / "messages-cursor.sqlite3")
        local_config = {
            **_pack_config(
                member_id=MEMBER_A,
                enrollment_id=ENROLLMENT_A,
                account=ACCOUNT_A,
                source=SOURCE_A,
                device=DEVICE_A,
            ),
            "page_size": 20,
            "reconcile_limit": 20,
            "network_containment_verified": True,
            "remote_services_verified_disabled": True,
        }
        sink = _PostingSink(client, HEADERS_A)
        adapter = BlueBubblesMessagesAdapter(
            config=local_config,
            transport=transport,
            cursor_store=cursor_store,
        )
        counter = iter(range(1, 100))
        ctx = BridgeContext(
            member_id=MEMBER_A,
            ingest_client=object(),  # type: ignore[arg-type]
            correlation_id_factory=lambda: f"g10-correlation-{next(counter)}",
            now_factory=lambda: datetime(2026, 9, 3, 12, 5, tzinfo=timezone.utc),
            sink=sink,
            tenant_id=runtime.settings.instance.tenant_id,
            enrollment_id=ENROLLMENT_A,
            configuration_sha256="1" * 64,
            device_identity_sha256=DEVICE_A,
        )
        event_ids = asyncio.run(adapter.observe_once(ctx))
        assert len(event_ids) == 5
        assert adapter.status().state == "running"
        assert [event.type for event, _event_id in sink.accepted] == [
            "messaging.sent",
            "messaging.received",
            "messaging.received",
            "messaging.received",
            "messaging.source_observed",
        ]
        settings_surface = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": LOGIN_A},
        ).json()
        wizard_capabilities = {
            item["capability_id"]: item
            for item in settings_surface["control"]["bridge_wizard"]["capabilities"]
        }
        assert wizard_capabilities["apple_messages_read"]["implementation"] == (
            "available"
        )
        assert wizard_capabilities["apple_messages_reply"]["implementation"] == (
            "planned_g11"
        )
        asset = client.get("/member-assets/member.js")
        assert "Read-only Apple Messages observation is available" in asset.text

        message_events = sink.accepted[:-1]
        for _envelope, event_id in message_events:
            client.portal.call(
                runtime.wait_for_subscriber,
                "source-policy-v2:shadow",
                event_id,
            )
            client.portal.call(
                runtime.wait_for_subscriber,
                "pipeline:pipeline:noise_filtering",
                event_id,
            )
            related = client.portal.call(runtime.event_log.get_by_causation, event_id)
            policy = next(
                item
                for item in related
                if item["type"] == "source.policy_v2.shadow_evaluated"
            )["payload"]
            coordinate = policy["coordinate"]
            assert coordinate["owner_principal_id"] == MEMBER_A
            assert coordinate["bridge_enrollment_id"] == ENROLLMENT_A
            assert coordinate["device_identity_sha256"] == DEVICE_A
            assert coordinate["provider_account_id"] == ACCOUNT_A
            assert coordinate["protected_source_id"] == SOURCE_A
            assert coordinate["connector_id"] == PACK
            assert policy["coordinate_state"] == "exact"
            assert policy["shadow_route"]["context"] == "typed_facts"
            assert policy["shadow_route"]["triage"] in {"review", "none"}
            assert policy["live_admission_changed"] is False
            assessment = next(
                item
                for item in related
                if item["type"] == "triage.intent.features_extracted"
            )
            client.portal.call(
                runtime.wait_for_projection, "interactions", assessment["event_id"]
            )
            proposals = [
                item
                for item in client.portal.call(
                    runtime.event_log.get_by_causation, assessment["event_id"]
                )
                if item["type"] == "triage.item.proposed"
            ]
            for proposal in proposals:
                client.portal.call(
                    runtime.wait_for_subscriber,
                    "triage-reconciliation:admission",
                    proposal["event_id"],
                )

        assurance = client.portal.call(_source_assurance, runtime)
        assert assurance["counts"]["pending"] == 0
        assert assurance["counts"]["missing"] == 0
        assert assurance["counts"]["failed"] == 0
        assert assurance["counts"]["accounted"] == assurance["counts"]["obligations"]
        ingestion = assurance["messaging_ingestion"]
        assert ingestion["counts"]["accepted"] == 4
        assert ingestion["accounting_reconciles"] is True
        assert ingestion["global_history_complete"] is False

        settings_surface = client.get(
            "/api/member/v1/surfaces/settings",
            headers={"Tailscale-User-Login": LOGIN_A},
        ).json()
        source_policy = settings_surface["control"]["source_policy_v2"]
        assert {item["id"] for item in source_policy["presets"]} == {
            "focused",
            "balanced",
            "review_everything",
        }
        messages_source = next(
            item
            for item in source_policy["sources"]
            if item["coordinate"].get("connector_id") == PACK
        )
        assert messages_source["coordinate_state"] == "exact"
        preview = client.post(
            "/api/member/v1/settings/source-policies-v2/preview",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={"coordinate": messages_source["coordinate"], "preset": "focused"},
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["provider_effects"] is False
        why = client.get(
            f"/api/member/v1/settings/source-policies-v2/why/{message_events[0][1]}",
            headers={"Tailscale-User-Login": LOGIN_A},
        )
        assert why.status_code == 200, why.text
        assert why.json()["provider_effects"] is False
        set_policy = client.post(
            f"/api/member/v1/settings/source-policies-v2/{messages_source['policy_key']}",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 0,
                "request_id": "g10-messages-focused",
                "coordinate": messages_source["coordinate"],
                "preset": "focused",
            },
        )
        assert set_policy.status_code == 202, set_policy.text
        reset_policy = client.post(
            f"/api/member/v1/settings/source-policies-v2/{messages_source['policy_key']}",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 1,
                "request_id": "g10-messages-reset-policy",
                "coordinate": messages_source["coordinate"],
                "action": "reset",
            },
        )
        assert reset_policy.status_code == 202, reset_policy.text
        assert reset_policy.json()["origin"] == "recommended_default"

        cockpit = client.get(
            "/api/member/v1/surfaces/comms", headers={"Tailscale-User-Login": LOGIN_A}
        ).json()["cockpit"]
        account = next(
            item for item in cockpit["accounts"] if item["provider"] == "bluebubbles"
        )
        assert account["provider_account_id"] == ACCOUNT_A
        assert account["capabilities"]["read"] is True
        for operation in (
            "reply",
            "send",
            "mark_read",
            "archive",
            "reaction",
            "edit",
            "unsend",
            "chat_create",
        ):
            assert account["capabilities"][operation] is False
        threads = {
            thread["native_chat_id"]: thread
            for thread in cockpit["threads"]
            if thread["provider"] == "bluebubbles"
        }
        assert set(threads) == {promise_chat, direct_chat, group_chat}
        assert threads[direct_chat]["read_only"] is True
        assert threads[direct_chat]["transport"] == "imessage"
        assert threads[group_chat]["is_group"] is True
        group_message = threads[group_chat]["messages"][0]
        assert set(group_message["from"] + group_message["to"]) == {
            "endpoint-alpha",
            "endpoint-beta",
            f"self:{ACCOUNT_A}",
        }

        direct_statuses = {
            message["native_message_id"]: message["triage_status"]
            for message in threads[direct_chat]["messages"]
        }
        assert direct_statuses["message-g10-request"] == "eligible_candidates"
        assert direct_statuses["message-g10-code"] == "no_candidate"
        assert threads[promise_chat]["messages"][0]["triage_status"] == (
            "eligible_candidates"
        )
        assert threads[group_chat]["messages"][0]["triage_status"] == "no_candidate"

        other = client.get(
            "/api/member/v1/surfaces/comms", headers={"Tailscale-User-Login": LOGIN_B}
        ).json()["cockpit"]
        assert not any(
            thread["provider"] == "bluebubbles" for thread in other["threads"]
        )

        source_event, _source_id = sink.accepted[1]
        forged = source_event.model_dump(mode="json")
        forged["payload"]["member_id"] = MEMBER_B
        cross_member = client.post("/api/bridge/ingest", headers=HEADERS_B, json=forged)
        assert cross_member.status_code == 403
        wrong_device = copy.deepcopy(source_event.model_dump(mode="json"))
        wrong_device["payload"]["device_identity_sha256"] = DEVICE_B
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=wrong_device
            ).status_code
            == 403
        )
        wrong_account = copy.deepcopy(source_event.model_dump(mode="json"))
        wrong_account["payload"]["provider_account_id"] = ACCOUNT_B
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=wrong_account
            ).status_code
            == 403
        )
        wrong_source = copy.deepcopy(source_event.model_dump(mode="json"))
        wrong_source["payload"]["protected_source_id"] = SOURCE_B
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=wrong_source
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/bridge/ingest",
                headers={
                    **HEADERS_A,
                    "X-AdministrateMe-Bridge-Enrollment": ENROLLMENT_B,
                },
                json=source_event.model_dump(mode="json"),
            ).status_code
            == 403
        )
        wrong_chat = copy.deepcopy(source_event.model_dump(mode="json"))
        wrong_chat["payload"]["native_chat_id"] = wrong_chat["payload"][
            "native_message_id"
        ]
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=wrong_chat
            ).status_code
            == 422
        )
        spoofed = copy.deepcopy(source_event.model_dump(mode="json"))
        spoofed["payload"]["sender_id"] = "endpoint-spoofed"
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=spoofed
            ).status_code
            == 422
        )
        spoofed_self = copy.deepcopy(source_event.model_dump(mode="json"))
        spoofed_self["payload"]["participants"][-1]["participant_id"] = (
            f"self:{ACCOUNT_B}"
        )
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=spoofed_self
            ).status_code
            == 422
        )
        oversized = copy.deepcopy(source_event.model_dump(mode="json"))
        oversized["payload"]["body_text"] = "x" * 50_001
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=oversized
            ).status_code
            == 422
        )

        replay_columns = (
            "interaction_id, direction, occurred_at, body_text, thread_id, "
            "native_message_id, native_chat_id, native_revision, "
            "native_revision_ms, transport_class, is_group, message_state, "
            "bridge_enrollment_id, device_identity_sha256, connector_id, "
            "protected_source_id, provider, provider_account_id, workspace, "
            "assessment_outcome, assessment_reason_codes, raw_event_ids"
        )
        before_replay = [
            dict(row)
            for row in runtime.projections.connection("interactions")
            .execute(
                f"SELECT {replay_columns} FROM interactions "  # noqa: S608
                "WHERE tenant_id=? AND member_id=? AND provider='bluebubbles' "
                "ORDER BY native_message_id",
                (runtime.settings.instance.tenant_id, MEMBER_A),
            )
            .fetchall()
        ]
        assert len(before_replay) == 4
        client.portal.call(runtime.projections.rebuild, "interactions")
        after_replay = [
            dict(row)
            for row in runtime.projections.connection("interactions")
            .execute(
                f"SELECT {replay_columns} FROM interactions "  # noqa: S608
                "WHERE tenant_id=? AND member_id=? AND provider='bluebubbles' "
                "ORDER BY native_message_id",
                (runtime.settings.instance.tenant_id, MEMBER_A),
            )
            .fetchall()
        ]
        assert after_replay == before_replay
        assert client.portal.call(_provider_effect_count, runtime) == 0
        cursor_store.close()


def test_g10_duplicate_revocation_and_malformed_attachment_fail_closed(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    with TestClient(app) as client:
        runtime = app.state.runtime
        chat_id = "iMessage;-;chat-g10-security"
        message = _message(
            "message-g10-security",
            1,
            chat_id=chat_id,
            sender="endpoint-security",
            text="Please review the fixture document tomorrow.",
        )
        message["attachments"] = [
            {
                "guid": "attachment-g10",
                "mimeType": "application/pdf",
                "uti": "com.adobe.pdf",
                "transferName": "fixture.pdf",
                "totalBytes": 512,
                "transferState": 5,
                "isOutgoing": False,
                "isSticker": False,
            }
        ]
        transport = _FixtureTransport(
            pages=[MessagePage((message,), 0, 1, 1), MessagePage((message,), 0, 20, 1)],
            chats={chat_id: _chat(chat_id, "endpoint-security")},
        )
        store = AdapterCursorStore(tmp_path / "security-cursor.sqlite3")
        sink = _PostingSink(client, HEADERS_A)
        adapter = BlueBubblesMessagesAdapter(
            config={
                **_pack_config(
                    member_id=MEMBER_A,
                    enrollment_id=ENROLLMENT_A,
                    account=ACCOUNT_A,
                    source=SOURCE_A,
                    device=DEVICE_A,
                ),
                "page_size": 20,
                "reconcile_limit": 20,
            },
            transport=transport,
            cursor_store=store,
        )
        ctx = BridgeContext(
            member_id=MEMBER_A,
            ingest_client=object(),  # type: ignore[arg-type]
            correlation_id_factory=lambda: "g10-security-correlation",
            now_factory=lambda: datetime(2026, 9, 3, 12, 5, tzinfo=timezone.utc),
            sink=sink,
            tenant_id=runtime.settings.instance.tenant_id,
            enrollment_id=ENROLLMENT_A,
            configuration_sha256="2" * 64,
            device_identity_sha256=DEVICE_A,
        )
        asyncio.run(adapter.observe_once(ctx))
        observed, observed_id = sink.accepted[0]
        attachment = observed.payload["attachments"][0]
        assert attachment["filename"] == "fixture.pdf"
        assert attachment["protected_ref"].startswith("protected-attachment:")
        assert "path" not in attachment

        duplicate = client.post(
            "/api/bridge/ingest",
            headers=HEADERS_A,
            json=observed.model_dump(mode="json"),
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["event_id"] == observed_id

        malformed = copy.deepcopy(observed.model_dump(mode="json"))
        malformed["payload"]["attachments"][0]["filename"] = "private/fixture.pdf"
        assert (
            client.post(
                "/api/bridge/ingest", headers=HEADERS_A, json=malformed
            ).status_code
            == 422
        )

        settings.bridge_enrollments[LOGIN_A]["status"] = "revoked"
        revoked = client.post(
            "/api/bridge/ingest",
            headers=HEADERS_A,
            json=observed.model_dump(mode="json"),
        )
        assert revoked.status_code == 403
        assert client.portal.call(_provider_effect_count, runtime) == 0
        store.close()


def test_g10_messages_selection_cross_account_reuse_and_central_restart(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="contradictory member ownership"):
        _settings(tmp_path / "invalid-instance", member_b_account=ACCOUNT_A)

    wizard_instance = tmp_path / "wizard-instance"
    wizard_instance.mkdir()
    wizard_settings = replace(_settings(wizard_instance), bridge_enrollments={})
    with TestClient(create_application(wizard_settings)) as client:
        prepared = client.post(
            "/api/member/v1/settings/bridges",
            headers={"Tailscale-User-Login": LOGIN_A},
            json={
                "expected_revision": 0,
                "request_id": "g10-messages-wizard-selection",
                "member_id": MEMBER_A,
                "device_class": "another_mac",
                "capabilities": ["apple_messages_read"],
            },
        )
        assert prepared.status_code == 202, prepared.text
        assert prepared.json()["state"] == "package_ready"

    instance = tmp_path / "restart-instance"
    instance.mkdir()
    settings = _settings(instance)
    app = create_application(settings)
    with TestClient(app) as client:
        observed = EventEnvelope(
            event_at_ms=_at_ms(1),
            tenant_id=app.state.runtime.settings.instance.tenant_id,
            type="messaging.received",
            schema_version=7,
            occurred_at="2026-09-03T12:00:01+00:00",
            source_adapter=PACK,
            source_account_id=ACCOUNT_A,
            owner_scope=f"private:{MEMBER_A}",
            visibility_scope=f"private:{MEMBER_A}",
            sensitivity="sensitive",
            idempotency_key="g10-central-restart-message",
            payload={
                "member_id": MEMBER_A,
                "service_principal_id": None,
                "provider": "bluebubbles",
                "provider_account_id": ACCOUNT_A,
                "workspace": "personal",
                "routing_scope": "member_private",
                "routing_target_id": MEMBER_A,
                "source_channel": "imessage",
                "from_identifier": "endpoint-restart",
                "to_identifier": f"self:{ACCOUNT_A}",
                "thread_id": "iMessage;-;chat-g10-restart",
                "subject": None,
                "body_text": "Fixture restart observation.",
                "body_html": None,
                "received_at": "2026-09-03T12:00:01+00:00",
                "attachments": [],
                "native_message_id": "message-g10-restart",
                "to_identifiers": [f"self:{ACCOUNT_A}"],
                "cc_identifiers": [],
                "bcc_identifiers": [],
                "reply_to_identifier": None,
                "rfc_message_id": None,
                "references": [],
                "provider_labels": [],
                "unread": True,
                "archived": False,
                "starred": False,
                "important": False,
                "in_reply_to": None,
                "bridge_enrollment_id": ENROLLMENT_A,
                "device_identity_sha256": DEVICE_A,
                "connector_id": PACK,
                "protected_source_id": SOURCE_A,
                "native_chat_id": "iMessage;-;chat-g10-restart",
                "native_revision": "1:fixture",
                "native_revision_ms": 1,
                "direction": "incoming",
                "participants": [
                    {
                        "participant_id": "endpoint-restart",
                        "role": "sender",
                        "is_self": False,
                    },
                    {
                        "participant_id": f"self:{ACCOUNT_A}",
                        "role": "self",
                        "is_self": True,
                    },
                ],
                "sender_id": "endpoint-restart",
                "observed_at": "2026-09-03T12:00:01+00:00",
                "reply_to_native_message_id": None,
                "transport_class": "imessage",
                "is_group": False,
                "message_state": "active",
                "transport_version": SELECTED_TRANSPORT_VERSION,
                "transport_commit": SELECTED_TRANSPORT_COMMIT,
                "correlation_id": "g10-central-restart",
            },
        )
        response = client.post(
            "/api/bridge/ingest",
            headers=HEADERS_A,
            json=observed.model_dump(mode="json"),
        )
        assert response.status_code == 202, response.text
        client.portal.call(
            app.state.runtime.wait_for_projection,
            "interactions",
            response.json()["event_id"],
        )

    restarted = create_application(_settings(instance))
    with TestClient(restarted) as client:
        cockpit = client.get(
            "/api/member/v1/surfaces/comms",
            headers={"Tailscale-User-Login": LOGIN_A},
        ).json()["cockpit"]
        assert any(
            message["native_message_id"] == "message-g10-restart"
            for thread in cockpit["threads"]
            for message in thread["messages"]
        )
        assert client.portal.call(_provider_effect_count, restarted.state.runtime) == 0
