from __future__ import annotations

from pathlib import Path

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import ProviderBinding


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_historical_attribution_is_content_free_and_binding_aware(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", b"h" * 32)
    body = "neutral source content must never appear in attribution evidence"
    try:
        await log.append(
            EventEnvelope(
                event_at_ms=1,
                tenant_id="tenant-fixture",
                type="messaging.received",
                schema_version=3,
                occurred_at="2026-08-26T12:00:00Z",
                source_adapter="gmail",
                source_account_id="account-a@example.invalid",
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                payload={
                    "provider": "gmail",
                    "provider_account_id": "account-a@example.invalid",
                    "member_id": "member-a",
                    "source_channel": "gmail",
                    "native_message_id": "message-a",
                    "thread_id": "thread-a",
                    "from_identifier": "sender@example.invalid",
                    "to_identifier": "account-a@example.invalid",
                    "workspace": "personal",
                    "subject": "Neutral fixture",
                    "body_text": body,
                    "received_at": "2026-08-26T12:00:00Z",
                    "provider_labels": [],
                    "attachments": [],
                },
            )
        )
        proof = await audit_historical_attribution(
            log,
            repository_root=REPO_ROOT,
            provider_bindings=(
                ProviderBinding(
                    adapter_id="gmail-a",
                    kind="messaging-gmail-api",
                    provider_family="google",
                    owner_kind="member",
                    owner_id="member-a",
                    member_id="member-a",
                    service_principal_id=None,
                    provider_account_id="account-a@example.invalid",
                    workspace="personal",
                    permission_scope=("gmail.read",),
                    binding_provenance="oauth-subject",
                ),
                ProviderBinding(
                    adapter_id="calendar-a",
                    kind="calendaring-google-calendar",
                    provider_family="google",
                    owner_kind="member",
                    owner_id="member-a",
                    member_id="member-a",
                    service_principal_id=None,
                    provider_account_id="account-a@example.invalid",
                    workspace="personal",
                    permission_scope=("calendar.read",),
                    binding_provenance="oauth-subject",
                ),
            ),
            hash_key=b"audit-key" * 4,
        )
    finally:
        await log.close()
    assert proof["state"] == "healthy"
    assert proof["source_event_count"] == proof["attributed_event_count"] == 1
    assert proof["content_exposed"] is False
    assert proof["raw_identifiers_exposed"] is False
    rendered = str(proof)
    assert body not in rendered
    assert "account-a@example.invalid" not in rendered
    assert "member-a" not in rendered
    assert proof["groups"][0]["workspace"] == "personal"


@pytest.mark.asyncio
async def test_historical_attribution_reconciles_exact_legacy_member_coordinates(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "legacy-events.db", b"l" * 32)
    try:
        calendar_id = await log.append(
            EventEnvelope(
                event_at_ms=1,
                tenant_id="tenant-fixture",
                type="calendar.event_added",
                schema_version=1,
                occurred_at="2026-08-26T12:00:00Z",
                source_adapter="calendaring-google-calendar",
                source_account_id="member-a",
                owner_scope="private:member-a",
                visibility_scope="private:member-a",
                payload={
                    "source": "calendar:google",
                    "external_event_id": "fixture-event",
                    "calendar_id": "fixture-calendar",
                    "summary": "Fixture",
                    "start": "2026-08-26T12:00:00Z",
                    "end": "2026-08-26T13:00:00Z",
                    "location": None,
                    "attendees": [],
                    "body": None,
                },
            )
        )
        proof = await audit_historical_attribution(
            log,
            repository_root=REPO_ROOT,
            provider_bindings=(
                ProviderBinding(
                    adapter_id="calendar-a",
                    kind="calendaring-google-calendar",
                    provider_family="google",
                    owner_kind="member",
                    owner_id="member-a",
                    member_id="member-a",
                    service_principal_id=None,
                    provider_account_id="account-a@example.invalid",
                    workspace="personal",
                    permission_scope=("calendar.read",),
                    binding_provenance="oauth-subject",
                ),
            ),
            hash_key=b"legacy-audit-key" * 2,
            through_event_id=calendar_id,
        )
    finally:
        await log.close()
    assert proof["state"] == "healthy"
    assert proof["attributed_event_count"] == 1
    assert proof["groups"][0]["binding_state"] == (
        "legacy_member_coordinate_reconciled"
    )


@pytest.mark.asyncio
async def test_historical_attribution_reconciles_exact_legacy_shared_gmail_fact(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "legacy-shared-events.db", b"s" * 32)
    try:
        await log.append(
            EventEnvelope(
                event_at_ms=1,
                tenant_id="tenant-fixture",
                type="messaging.received",
                schema_version=3,
                occurred_at="2026-08-26T12:00:00Z",
                source_adapter="messaging-gmail-api",
                source_account_id="account-a@example.invalid",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                payload={
                    "provider": "gmail",
                    "provider_account_id": "account-a@example.invalid",
                    "member_id": "member-a",
                    "source_channel": "gmail",
                    "native_message_id": "message-a",
                    "thread_id": "thread-a",
                    "from_identifier": "sender@example.invalid",
                    "to_identifier": "account-a@example.invalid",
                    "workspace": "personal",
                    "subject": "Neutral fixture",
                    "body_text": "Legacy shared visibility fixture",
                    "received_at": "2026-08-26T12:00:00Z",
                    "provider_labels": [],
                    "attachments": [],
                },
            )
        )
        proof = await audit_historical_attribution(
            log,
            repository_root=REPO_ROOT,
            provider_bindings=(
                ProviderBinding(
                    adapter_id="gmail-a",
                    kind="messaging-gmail-api",
                    provider_family="google",
                    owner_kind="member",
                    owner_id="member-a",
                    member_id="member-a",
                    service_principal_id=None,
                    provider_account_id="account-a@example.invalid",
                    workspace="personal",
                    permission_scope=("gmail.read",),
                    binding_provenance="oauth-subject",
                ),
                ProviderBinding(
                    adapter_id="calendar-a",
                    kind="calendaring-google-calendar",
                    provider_family="google",
                    owner_kind="member",
                    owner_id="member-a",
                    member_id="member-a",
                    service_principal_id=None,
                    provider_account_id="account-a@example.invalid",
                    workspace="personal",
                    permission_scope=("calendar.read",),
                    binding_provenance="oauth-subject",
                ),
            ),
            hash_key=b"legacy-shared-key" * 2,
        )
    finally:
        await log.close()
    assert proof["state"] == "healthy"
    assert proof["attributed_event_count"] == 1
    assert proof["groups"][0]["binding_state"] == (
        "legacy_shared_visibility_reconciled"
    )


@pytest.mark.asyncio
async def test_legacy_shared_visibility_requires_one_exact_gmail_binding(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "ambiguous-shared-events.db", b"a" * 32)
    try:
        await log.append(
            EventEnvelope(
                event_at_ms=1,
                tenant_id="tenant-fixture",
                type="messaging.received",
                schema_version=3,
                occurred_at="2026-08-26T12:00:00Z",
                source_adapter="messaging-gmail-api",
                source_account_id="account-a@example.invalid",
                owner_scope="shared:household",
                visibility_scope="shared:household",
                payload={
                    "provider": "gmail",
                    "provider_account_id": "account-a@example.invalid",
                    "member_id": "member-a",
                    "source_channel": "gmail",
                    "native_message_id": "message-a",
                    "thread_id": "thread-a",
                    "from_identifier": "sender@example.invalid",
                    "to_identifier": "account-a@example.invalid",
                    "workspace": "personal",
                    "subject": "Neutral fixture",
                    "body_text": "Ambiguous legacy fixture",
                    "received_at": "2026-08-26T12:00:00Z",
                    "provider_labels": [],
                    "attachments": [],
                },
            )
        )
        bindings = tuple(
            ProviderBinding(
                adapter_id=f"gmail-{member}",
                kind="messaging-gmail-api",
                provider_family="google",
                owner_kind="member",
                owner_id=member,
                member_id=member,
                service_principal_id=None,
                provider_account_id="account-a@example.invalid",
                workspace="personal",
                permission_scope=("gmail.read",),
                binding_provenance="oauth-subject",
            )
            for member in ("member-a", "member-b")
        )
        proof = await audit_historical_attribution(
            log,
            repository_root=REPO_ROOT,
            provider_bindings=bindings,
            hash_key=b"ambiguous-shared-key" * 2,
        )
    finally:
        await log.close()
    assert proof["state"] == "degraded"
    assert proof["unattributed_event_count"] == 1
    assert proof["groups"][0]["binding_state"] == "ambiguous_or_owner_mismatch"
