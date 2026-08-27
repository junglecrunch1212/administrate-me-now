"""Bridge-runtime composition tests for Apple Contacts."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme_bridge.adapters.base import (
    AdapterStatus,
    BridgeContext,
    StateMirrorBridgeAdapter,
)
from adminme_bridge.adapters.sinks import BridgeEventSink, EmitResult
from adminme_bridge.connector_packs.contacts_apple_contacts_bridge import (
    AppleContactsBridgeAdapter,
)
from adminme_bridge.connector_packs.contacts_apple_contacts_bridge import (
    wrapper as wrapper_mod,
)
from adminme_bridge.ingest_client import IngestClient


NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
ACCOUNT = "provider-account:" + "a" * 32


class _Sink:
    async def emit(self, _envelope: Any) -> EmitResult:
        return EmitResult(ok=True, event_id="event-contacts", error=None)


class _Internal:
    def __init__(self, *, config: dict[str, Any]) -> None:
        self.config = dict(config)
        self.context: AdapterContext | None = None
        self.stopped = False
        self.operations: list[Any] = []

    async def start(self, *, ctx: AdapterContext) -> None:
        self.context = ctx

    async def stop(self) -> None:
        self.stopped = True

    async def apply_write(self, operation: Any) -> Any:
        self.operations.append(operation)
        return type(
            "WriteResultFixture",
            (),
            {
                "applied": True,
                "external_id": operation.target_external_id,
                "suppressed": False,
            },
        )()

    def status(self) -> AdapterStatus:
        return AdapterStatus(state="running", started_at=NOW)


def _client() -> IngestClient:
    import httpx

    return IngestClient(
        central_endpoint_url="http://127.0.0.1:0",
        tailscale_identity_header="contacts-bridge.fixture.ts.net",
        enrollment_id="contacts-enrollment",
        client=httpx.AsyncClient(timeout=httpx.Timeout(0.1)),
    )


def _context(*, sink: BridgeEventSink | None = None) -> BridgeContext:
    return BridgeContext(
        member_id="member-a",
        ingest_client=_client(),
        enrollment_id="contacts-enrollment",
        configuration_sha256="b" * 64,
        correlation_id_factory=lambda: "correlation-contacts",
        now_factory=lambda: NOW,
        sink=sink,
    )


def _config() -> dict[str, Any]:
    return {
        "provider_account_id": ACCOUNT,
        "container_external_id": "all-containers",
    }


def test_contacts_wrapper_requires_distinct_provider_coordinate() -> None:
    with pytest.raises(ValueError, match="provider account"):
        AppleContactsBridgeAdapter(
            internal_config={"container_external_id": "all-containers"}
        )


def test_contacts_wrapper_is_bridge_only_and_forwards_sparse_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[_Internal] = []

    def factory(*, config: dict[str, Any]) -> _Internal:
        instance = _Internal(config=config)
        captured.append(instance)
        return instance

    monkeypatch.setattr(wrapper_mod, "_load_central_adapter_class", lambda: factory)
    sink = _Sink()
    adapter = AppleContactsBridgeAdapter(internal_config=_config())
    assert isinstance(adapter, StateMirrorBridgeAdapter)

    asyncio.run(adapter.start(ctx=_context(sink=sink)))
    internal = captured[0]
    assert internal.config == {
        **_config(),
        "member_id": "member-a",
        "tenant_id": "tenant-default",
        "deployment_target": "bridge",
        "credentials_ref": "keychain://device-permissions/contacts",
        "scopes": ["contacts.read", "contacts.write"],
        "_bridge_enrollment_id": "contacts-enrollment",
        "_bridge_configuration_sha256": "b" * 64,
    }
    assert internal.context is not None
    assert internal.context.config["deployment_target"] == "bridge"
    assert internal.context.event_sink.tenant_id == "tenant-default"

    result = asyncio.run(
        adapter.apply_write(
            "update_party_identifier",
            {
                "container_external_id": "all-containers",
                "identifier_kind": "email",
                "prior_value_normalized": "old@example.invalid",
                "value": "new@example.invalid",
                "expected_provider_revision": "revision-1",
            },
            "contact-external-1",
        )
    )
    assert result == {
        "applied": True,
        "external_id": "contact-external-1",
        "suppressed": False,
    }
    assert internal.operations[0].kind == "update_party_identifier"
    assert adapter.status().state == "running"
    asyncio.run(adapter.stop())
    assert internal.stopped is True
