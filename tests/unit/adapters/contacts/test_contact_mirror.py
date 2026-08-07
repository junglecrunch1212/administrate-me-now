"""Contact mirror contract: private facts, loss-aware mapping, guarded writes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

import pytest

from adminme.adapters.base import AdapterContext
from adminme.events.envelope import EventEnvelope
from adminme.lib.adapters import ProviderConflictError, WriteOperation
from adminme.lib.adapters.contacts_base import ContactMirrorAdapter


class _Sink:
    tenant_id = "tenant-test"

    def __init__(self) -> None:
        self.envelopes: list[EventEnvelope] = []

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        del correlation_id, causation_id
        self.envelopes.append(envelope)
        return f"event-{len(self.envelopes)}"


class _Client:
    def __init__(self) -> None:
        self.writes: list[tuple[str, str | None, dict[str, Any]]] = []

    async def changes(self, cursor: str | None) -> dict[str, Any]:
        assert cursor is None
        return {
            "records": [
                {
                    "external_id": "provider-person-1",
                    "revision": "etag-1",
                    "display_name": "Example Person",
                    "given_name": "Example",
                    "family_name": "Person",
                    "identifiers": [
                        {
                            "kind": "email",
                            "value": "Example@Example.test ",
                            "primary": True,
                        }
                    ],
                    "fields": {"birthday": "2000-01-02"},
                }
            ],
            "groups": [
                {"external_id": "group-1", "revision": "g-1", "name": "Friends"}
            ],
            "deletions": [
                {"external_id": "provider-person-2", "revision": "etag-deleted"}
            ],
            "next_cursor": "cursor-2",
        }

    async def write(
        self, kind: str, target_external_id: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.writes.append((kind, target_external_id, payload))
        return {"external_id": target_external_id or "provider-new"}


class _Adapter(ContactMirrorAdapter):
    name: ClassVar[str] = "contacts-fixture"
    provider: ClassVar[str] = "fixture"


def _context(sink: _Sink) -> AdapterContext:
    return AdapterContext(
        event_sink=sink,  # type: ignore[arg-type]
        config={},
        correlation_id_factory=lambda: "correlation-test",
        now_factory=lambda: datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )


async def test_contact_changes_emit_private_loss_aware_facts() -> None:
    sink = _Sink()
    client = _Client()
    adapter = _Adapter(
        config={
            "member_id": "member-a",
            "provider_account_id": "account-a",
            "container_external_id": "contacts-a",
        },
        client_factory=lambda config: client,
        max_poll_cycles=1,
    )
    await adapter.start(ctx=_context(sink))

    assert [value.type for value in sink.envelopes] == [
        "adapter.authenticated",
        "contact.group.observed",
        "contact.record.observed",
        "contact.record.deleted",
    ]
    record = sink.envelopes[2]
    assert record.owner_scope == "private:member-a"
    assert record.visibility_scope == "private:member-a"
    assert record.payload["provider_revision"] == "etag-1"
    assert record.payload["identifiers"][0]["value_normalized"] == (
        "example@example.test"
    )


async def test_contact_updates_require_provider_revision() -> None:
    sink = _Sink()
    client = _Client()
    adapter = _Adapter(
        config={"member_id": "member-a", "provider_account_id": "account-a"},
        client_factory=lambda config: client,
        max_poll_cycles=1,
    )
    await adapter.start(ctx=_context(sink))
    with pytest.raises(ProviderConflictError):
        await adapter.apply_write(
            WriteOperation(
                kind="update_contact",
                target_external_id="provider-person-1",
                payload={"display_name": "Changed"},
            )
        )
    result = await adapter.apply_write(
        WriteOperation(
            kind="update_contact",
            target_external_id="provider-person-1",
            payload={"display_name": "Changed", "expected_provider_revision": "etag-1"},
        )
    )
    assert result.applied is True
    assert client.writes[-1][0] == "update_contact"
