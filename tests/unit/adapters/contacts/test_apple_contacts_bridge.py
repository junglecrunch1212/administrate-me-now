from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.envelope import EventEnvelope
from adminme.lib.adapters import WriteOperation
from adminme.lib.adapters.external_state_mirror_base import WriteCapabilityError


REPO_ROOT = Path(__file__).resolve().parents[4]
ADAPTER_PATH = (
    REPO_ROOT / "packs" / "adapters" / "contacts" / "apple-contacts" / "adapter.py"
)
SPEC = importlib.util.spec_from_file_location(
    "_test_apple_contacts_adapter", ADAPTER_PATH
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
AppleContactsAdapter = MODULE.AppleContactsAdapter


class _Sink:
    tenant_id = "tenant-test"

    def __init__(self) -> None:
        self.envelopes: list[EventEnvelope] = []

    async def write(self, envelope: EventEnvelope, **_kwargs: Any) -> str:
        self.envelopes.append(envelope)
        return f"ev-{len(self.envelopes)}"


class _Client:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str | None, dict[str, Any]]] = []

    async def changes(self, cursor: str | None) -> dict[str, Any]:
        assert cursor is None
        return {
            "records": [
                {
                    "external_id": "apple-contact-1",
                    "revision": "revision-1",
                    "display_name": "Contact Fixture",
                    "identifiers": [
                        {"kind": "email", "value": "fixture@example.invalid"}
                    ],
                }
            ],
            "groups": [],
            "deletions": [],
            "next_cursor": "cursor-1",
        }

    async def write(
        self, kind: str, target_external_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.writes.append((kind, target_external_id, dict(payload)))
        return {"external_id": target_external_id}


def _config() -> dict[str, Any]:
    return {
        "member_id": "member-a",
        "provider_account_id": "provider-account:" + "a" * 32,
        "container_external_id": "all-containers",
        "deployment_target": "bridge",
        "credentials_ref": "keychain://device-permissions/contacts",
        "scopes": ["contacts.read", "contacts.write"],
        "_bridge_enrollment_id": "enrollment-a",
        "_bridge_configuration_sha256": "b" * 64,
    }


async def test_apple_contacts_emits_bridge_bound_private_capability_and_fact() -> None:
    sink = _Sink()
    client = _Client()
    adapter = AppleContactsAdapter(
        config=_config(), client_factory=lambda _config: client, max_poll_cycles=1
    )
    await adapter.start(
        ctx=AdapterContext(
            event_sink=sink,  # type: ignore[arg-type]
            config={},
            correlation_id_factory=lambda: "correlation-test",
            now_factory=lambda: datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
        )
    )

    assert [event.type for event in sink.envelopes] == [
        "adapter.authenticated",
        "contact.record.observed",
    ]
    capability, fact = sink.envelopes
    assert capability.schema_version == 2
    assert capability.payload["bridge_enrollment_id"] == "enrollment-a"
    assert capability.payload["configuration_sha256"] == "b" * 64
    assert fact.owner_scope == "private:member-a"
    assert fact.visibility_scope == "private:member-a"
    assert fact.source_account_id == "provider-account:" + "a" * 32
    assert fact.payload["container_external_id"] == "all-containers"


async def test_apple_contacts_allows_only_sparse_revision_guarded_update() -> None:
    client = _Client()
    adapter = AppleContactsAdapter(
        config=_config(), client_factory=lambda _config: client, max_poll_cycles=0
    )
    adapter._client = client  # noqa: SLF001 - pins the pack's write boundary

    for operation in ("create_contact", "update_contact", "delete_contact"):
        with pytest.raises(WriteCapabilityError, match="not declared"):
            await adapter.apply_write(WriteOperation(operation, "apple-contact-1", {}))
    result = await adapter.apply_write(
        WriteOperation(
            "update_party_identifier",
            "apple-contact-1",
            {
                "identifier_kind": "email",
                "prior_value_normalized": "fixture@example.invalid",
                "value": "updated@example.invalid",
                "expected_provider_revision": "revision-1",
            },
        )
    )
    assert result.applied is True
    assert client.writes == [
        (
            "update_party_identifier",
            "apple-contact-1",
            {
                "identifier_kind": "email",
                "prior_value_normalized": "fixture@example.invalid",
                "value": "updated@example.invalid",
                "expected_provider_revision": "revision-1",
            },
        )
    ]
