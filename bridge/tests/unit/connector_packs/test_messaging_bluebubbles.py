"""Read-only, fixture-only tests for the pinned BlueBubbles bridge pack."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx
import pytest

from adminme.adapters.cursor_store import AdapterCursorStore
from adminme.events.envelope import EventEnvelope
from adminme.events.registry import ensure_autoloaded, registry
from adminme.lib.pack_registry.validator import validate_adapter_pack
from adminme_bridge.adapters.base import (
    BridgeContext,
    CommunicationSourceBridgeAdapter,
)
from adminme_bridge.adapters.sinks import EmitResult
from adminme_bridge.connector_packs.messaging_bluebubbles import (
    SELECTED_TRANSPORT_COMMIT,
    SELECTED_TRANSPORT_VERSION,
    BlueBubblesAuthenticationError,
    BlueBubblesMessagesAdapter,
    BlueBubblesRestTransport,
    BlueBubblesTransportError,
    MessagePage,
)
from adminme_bridge.packs import COMMUNICATION_SOURCE_SUBKIND, discover_packs


ACCOUNT = "provider-account:" + "a" * 32
SOURCE = "protected-source:" + hashlib.sha256(b"fixture-computer").hexdigest()
DEVICE = "c" * 64
CREDENTIAL_REF = "keychain://adminme-bluebubbles/member-bridge"


@dataclass
class _RecordingSink:
    events: list[EventEnvelope] = field(default_factory=list)

    async def emit(self, envelope: EventEnvelope) -> EmitResult:
        ensure_autoloaded()
        registry.validate(envelope.type, envelope.schema_version, envelope.payload)
        self.events.append(envelope)
        return EmitResult(True, f"event-{len(self.events)}", None)


@dataclass
class _Resolver:
    secret: str
    references: list[str] = field(default_factory=list)

    async def resolve(self, reference: str) -> str:
        self.references.append(reference)
        return self.secret


@dataclass
class _FixtureTransport:
    pages: list[MessagePage]
    chats: dict[str, Mapping[str, Any]]
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def server_info(self) -> Mapping[str, Any]:
        self.calls.append(("server_info", {}))
        return {
            "server_version": SELECTED_TRANSPORT_VERSION,
            "computer_id": "fixture-computer",
            "private_api": False,
        }

    async def query_messages(self, **kwargs: Any) -> MessagePage:
        self.calls.append(("query_messages", dict(kwargs)))
        assert self.pages, "unexpected fixture query"
        return self.pages.pop(0)

    async def chat(self, native_chat_id: str) -> Mapping[str, Any]:
        self.calls.append(("chat", {"native_chat_id": native_chat_id}))
        return self.chats[native_chat_id]

    async def aclose(self) -> None:
        self.calls.append(("aclose", {}))


class _NoopIngestClient:
    pass


def _config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider_account_id": ACCOUNT,
        "protected_source_id": SOURCE,
        "device_identity_sha256": DEVICE,
        "member_id": "member-fixture",
        "bridge_enrollment_id": "bridge-enrollment-fixture",
        "credential_ref": CREDENTIAL_REF,
        "transport_version": SELECTED_TRANSPORT_VERSION,
        "transport_commit": SELECTED_TRANSPORT_COMMIT,
        "history_limit": 10,
        "page_size": 10,
        "reconcile_limit": 10,
        "poll_interval_seconds": 2,
    }
    base.update(overrides)
    return base


def _context(sink: _RecordingSink) -> BridgeContext:
    counter = iter(range(1, 100))
    return BridgeContext(
        member_id="member-fixture",
        ingest_client=_NoopIngestClient(),  # type: ignore[arg-type]
        correlation_id_factory=lambda: f"correlation-{next(counter)}",
        now_factory=lambda: datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
        sink=sink,
        tenant_id="tenant-fixture",
        enrollment_id="bridge-enrollment-fixture",
        configuration_sha256="d" * 64,
        device_identity_sha256=DEVICE,
    )


def _message(
    guid: str,
    date_ms: int,
    *,
    chat_id: str = "iMessage;-;chat-fixture",
    outgoing: bool = False,
    text: str | None = "fixture body",
    **overrides: Any,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "guid": guid,
        "dateCreated": date_ms,
        "text": text,
        "subject": None,
        "isFromMe": outgoing,
        "isDelivered": outgoing,
        "dateDelivered": date_ms if outgoing else None,
        "dateRead": None,
        "dateEdited": None,
        "dateRetracted": None,
        "isArchived": False,
        "itemType": 0,
        "isSystemMessage": False,
        "associatedMessageGuid": None,
        "associatedMessageType": None,
        "replyToGuid": None,
        "handle": {
            "address": "participant-alpha",
            "service": "iMessage",
        },
        "chats": [{"guid": chat_id}],
        "attachments": [],
        # Deliberately present private serializer data: the adapter must ignore
        # it and never carry it into the typed event.
        "attributedBody": "fixture-private-serialized-body",
    }
    base.update(overrides)
    return base


def _chat(
    chat_id: str = "iMessage;-;chat-fixture", *, style: int = 45
) -> dict[str, Any]:
    return {
        "guid": chat_id,
        "style": style,
        "participants": [{"address": "participant-alpha"}],
    }


def test_manifest_and_protocol_are_explicitly_read_only() -> None:
    root = Path(__file__).resolve().parents[3] / "adminme_bridge" / "connector_packs"
    handles = {handle.name: handle for handle in discover_packs(root)}
    handle = handles["messaging-bluebubbles"]
    assert handle.subkind == COMMUNICATION_SOURCE_SUBKIND
    cursor_store = AdapterCursorStore(Path(":memory:"))
    adapter = BlueBubblesMessagesAdapter(
        config=_config(),
        transport=_FixtureTransport([], {}),
        cursor_store=cursor_store,
    )
    assert isinstance(adapter, CommunicationSourceBridgeAdapter)
    assert not hasattr(adapter, "send")
    assert not hasattr(adapter, "apply_write")
    assert SELECTED_TRANSPORT_COMMIT == ("ba31cd1cf6c03e154c18ffa2cd6da47a934c3be1")
    assert adapter.status().extra["write_capabilities"] == []
    ensure_autoloaded()
    validation = validate_adapter_pack(
        handle.pack_dir,
        registry=registry,
        projections_dir=Path(__file__).resolve().parents[4] / "adminme" / "projections",
    )
    assert validation.errors() == []
    cursor_store.close()


async def test_rest_transport_is_loopback_only_and_uses_only_read_routes() -> None:
    resolver = _Resolver("fixture-password-never-emitted")
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/server/info"):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "server_version": "1.9.9",
                        "computer_id": "fixture-computer",
                    }
                },
            )
        if request.url.path.endswith("/message/query"):
            body = json.loads(request.content)
            assert body["with"] == ["chats", "attachments"]
            assert body["convertAttachments"] is False
            assert "where" not in body
            return httpx.Response(
                200,
                json={
                    "data": [],
                    "metadata": {"offset": 0, "limit": 5, "total": 0, "count": 0},
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "guid": "iMessage;-;chat-fixture",
                    "style": 45,
                    "participants": [],
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    transport = BlueBubblesRestTransport(
        base_url="http://127.0.0.1:1234",
        credential_ref=CREDENTIAL_REF,
        credential_resolver=resolver,
        client=client,
    )
    await transport.server_info()
    await transport.query_messages(sort="ASC", limit=5, after_ms=0)
    await transport.chat("iMessage;-;chat-fixture")
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert [request.url.path for request in requests] == [
        "/api/v1/server/info",
        "/api/v1/message/query",
        "/api/v1/chat/iMessage;-;chat-fixture",
    ]
    assert all(request.url.host == "127.0.0.1" for request in requests)
    assert resolver.references == [CREDENTIAL_REF] * 3
    assert all(
        b"fixture-password-never-emitted" not in request.content for request in requests
    )
    await client.aclose()

    for unsafe in (
        "https://bridge.example.invalid:1234",
        "http://127.0.0.1:1234/extra",
        "http://name:credential@127.0.0.1:1234",
    ):
        with pytest.raises(ValueError, match="loopback"):
            BlueBubblesRestTransport(
                base_url=unsafe,
                credential_ref=CREDENTIAL_REF,
                credential_resolver=resolver,
            )


async def test_rest_transport_auth_and_network_failures_never_expose_secret() -> None:
    secret = "fixture-password-must-stay-redacted"
    resolver = _Resolver(secret)

    unauthorized_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(401, request=request, json={"error": "no"})
        )
    )
    unauthorized = BlueBubblesRestTransport(
        base_url="http://127.0.0.1:1234",
        credential_ref=CREDENTIAL_REF,
        credential_resolver=resolver,
        client=unauthorized_client,
    )
    with pytest.raises(BlueBubblesAuthenticationError) as auth_failure:
        await unauthorized.server_info()
    assert secret not in str(auth_failure.value)
    await unauthorized_client.aclose()

    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("fixture offline", request=request)

    unavailable_client = httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    transport = BlueBubblesRestTransport(
        base_url="http://127.0.0.1:1234",
        credential_ref=CREDENTIAL_REF,
        credential_resolver=resolver,
        client=unavailable_client,
    )
    with pytest.raises(BlueBubblesTransportError) as network_failure:
        await transport.server_info()
    assert secret not in str(network_failure.value)
    assert network_failure.value.__cause__ is None
    await unavailable_client.aclose()


async def test_history_boundary_precedes_history_and_emits_typed_private_facts(
    tmp_path: Path,
) -> None:
    first = _message("message-1", 1_000)
    second = _message(
        "message-2",
        2_000,
        outgoing=True,
        replyToGuid="message-1",
        attachments=[
            {
                "guid": "attachment-1",
                "mimeType": "image/png",
                "uti": "public.png",
                "transferName": "fixture.png",
                "totalBytes": 2048,
                "transferState": 5,
                "isOutgoing": True,
                "isSticker": False,
            }
        ],
    )
    transport = _FixtureTransport(
        pages=[
            MessagePage((second,), 0, 1, 2),
            MessagePage((second, first), 0, 10, 2),
        ],
        chats={"iMessage;-;chat-fixture": _chat()},
    )
    store = AdapterCursorStore(tmp_path / "messages-cursor.sqlite3")
    adapter = BlueBubblesMessagesAdapter(
        config=_config(), transport=transport, cursor_store=store
    )
    sink = _RecordingSink()
    event_ids = await adapter.observe_once(_context(sink))

    query_calls = [call for call in transport.calls if call[0] == "query_messages"]
    assert query_calls[0][1] == {"sort": "DESC", "limit": 1}
    assert query_calls[1][1]["before_ms"] == 2_000
    assert [event.type for event in sink.events] == [
        "messaging.received",
        "messaging.sent",
        "messaging.source_observed",
    ]
    assert len(event_ids) == 3
    received, sent, coverage = sink.events
    assert received.owner_scope == "private:member-fixture"
    assert received.visibility_scope == "private:member-fixture"
    assert received.sensitivity == "sensitive"
    assert received.payload["direction"] == "incoming"
    assert sent.payload["direction"] == "outgoing"
    assert sent.payload["reply_to_native_message_id"] == "message-1"
    assert sent.payload["in_reply_to"] == "message-1"
    assert sent.payload["attachments"] == [
        {
            "attachment_id": "attachment-1",
            "mime_type": "image/png",
            "uti": "public.png",
            "byte_size": 2048,
            "filename": "fixture.png",
            "transfer_state": "5",
            "is_outgoing": True,
            "is_sticker": False,
            "protected_ref": sent.payload["attachments"][0]["protected_ref"],
        }
    ]
    assert sent.payload["attachments"][0]["protected_ref"].startswith(
        "protected-attachment:"
    )
    assert "attributedBody" not in received.payload
    assert coverage.payload["accepted"] == 2
    assert coverage.payload["requested"] == 2
    assert coverage.payload["coverage"] == "partial"
    cursor = store.get(adapter._adapter_id)  # noqa: SLF001 - persistence assertion
    assert json.loads(cursor.provider_watermark or "{}")["date_ms"] == 2_000
    assert adapter.status().state == "degraded"
    assert adapter.status().extra["network_containment"] == "unverified"
    store.close()


async def test_observed_transport_identity_must_match_protected_source(
    tmp_path: Path,
) -> None:
    store = AdapterCursorStore(tmp_path / "source-identity-cursor.sqlite3")
    adapter = BlueBubblesMessagesAdapter(
        config=_config(protected_source_id="protected-source:" + "f" * 64),
        transport=_FixtureTransport([], {}),
        cursor_store=store,
    )
    with pytest.raises(BlueBubblesTransportError, match="source identity"):
        await adapter.observe_once(_context(_RecordingSink()))
    store.close()


async def test_cursor_and_dedupe_survive_restart_without_replaying_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "restart-cursor.sqlite3"
    message = _message("message-1", 1_000)
    first_store = AdapterCursorStore(path)
    first = BlueBubblesMessagesAdapter(
        config=_config(),
        transport=_FixtureTransport(
            [MessagePage((message,), 0, 1, 1), MessagePage((message,), 0, 10, 1)],
            {"iMessage;-;chat-fixture": _chat()},
        ),
        cursor_store=first_store,
    )
    await first.observe_once(_context(_RecordingSink()))
    first_store.close()

    second_store = AdapterCursorStore(path)
    restarted_transport = _FixtureTransport(
        [
            MessagePage((message,), 0, 10, 1),
            MessagePage((message,), 0, 10, 1),
        ],
        {"iMessage;-;chat-fixture": _chat()},
    )
    restarted = BlueBubblesMessagesAdapter(
        config=_config(), transport=restarted_transport, cursor_store=second_store
    )
    sink = _RecordingSink()
    await restarted.observe_once(_context(sink))
    query_calls = [
        kwargs for name, kwargs in restarted_transport.calls if name == "query_messages"
    ]
    assert query_calls[0]["sort"] == "ASC"
    assert query_calls[0]["after_ms"] == 1_000
    assert all(
        not (call["sort"] == "DESC" and call["limit"] == 1) for call in query_calls
    )
    assert [event.type for event in sink.events] == [
        "messaging.source_observed",
        "messaging.source_observed",
    ]
    assert sum(event.payload["duplicates"] for event in sink.events) == 2
    second_store.close()


async def test_unsafe_records_are_quarantined_without_private_bodies(
    tmp_path: Path,
) -> None:
    reaction = _message(
        "reaction-1",
        1_000,
        associatedMessageGuid="message-0",
        associatedMessageType=2000,
    )
    mutation = _message("mutation-1", 2_000, dateEdited=2_500)
    oversized = _message("oversized-1", 3_000, text="x" * 50)
    transport = _FixtureTransport(
        [
            MessagePage((oversized,), 0, 1, 3),
            MessagePage((oversized, mutation, reaction), 0, 10, 3),
        ],
        {"iMessage;-;chat-fixture": _chat()},
    )
    store = AdapterCursorStore(tmp_path / "quarantine.sqlite3")
    adapter = BlueBubblesMessagesAdapter(
        config=_config(max_body_chars=10),
        transport=transport,
        cursor_store=store,
    )
    sink = _RecordingSink()
    await adapter.observe_once(_context(sink))
    assert [event.type for event in sink.events] == [
        "adapter.record.quarantined",
        "adapter.record.quarantined",
        "adapter.record.quarantined",
        "messaging.source_observed",
    ]
    for event in sink.events[:-1]:
        serialized = json.dumps(event.model_dump())
        assert "fixture body" not in serialized
        assert "xxxxxxxxxx" not in serialized
        assert event.payload["external_id"].startswith("native-message:")
    assert sink.events[-1].payload["quarantined"] == 3
    store.close()


async def test_out_of_order_native_revision_cannot_roll_back_observed_state(
    tmp_path: Path,
) -> None:
    store = AdapterCursorStore(tmp_path / "revision-guard.sqlite3")
    current = _message("message-1", 1_000, outgoing=True, dateDelivered=3_000)
    first = BlueBubblesMessagesAdapter(
        config=_config(),
        transport=_FixtureTransport(
            [MessagePage((current,), 0, 1, 1), MessagePage((current,), 0, 10, 1)],
            {"iMessage;-;chat-fixture": _chat()},
        ),
        cursor_store=store,
    )
    await first.observe_once(_context(_RecordingSink()))

    stale = _message("message-1", 1_000, outgoing=True, dateDelivered=2_000)
    restarted = BlueBubblesMessagesAdapter(
        config=_config(),
        transport=_FixtureTransport(
            [MessagePage((stale,), 0, 10, 1), MessagePage((stale,), 0, 10, 1)],
            {"iMessage;-;chat-fixture": _chat()},
        ),
        cursor_store=store,
    )
    sink = _RecordingSink()
    await restarted.observe_once(_context(sink))
    assert "messaging.sent" not in [event.type for event in sink.events]
    quarantines = [
        event for event in sink.events if event.type == "adapter.record.quarantined"
    ]
    assert len(quarantines) == 1
    assert quarantines[0].payload["error_message"] == "out_of_order_native_revision"
    assert (
        sum(
            event.payload["duplicates"]
            for event in sink.events
            if event.type == "messaging.source_observed"
        )
        == 1
    )
    store.close()


def test_config_rejects_embedded_secrets_and_non_keychain_references() -> None:
    transport = _FixtureTransport([], {})
    with pytest.raises(ValueError, match="credential values"):
        BlueBubblesMessagesAdapter(
            config=_config(password="must-not-be-here"), transport=transport
        )
    with pytest.raises(ValueError, match="Keychain"):
        BlueBubblesMessagesAdapter(
            config=_config(credential_ref="plaintext://fixture"), transport=transport
        )
