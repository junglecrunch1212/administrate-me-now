from __future__ import annotations

from pathlib import Path

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.historical_attribution import audit_historical_attribution
from adminme.provider_bindings import ProviderBinding
from adminme.service_identities import COS_SERVICE_PRINCIPAL_ID
from tests.unit.test_cos_response_assurance import MEMBER, _chain


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "service_actor",
        "attempt_actor",
        "approval_actor",
        "payload_member",
        "owner_scope",
        "visibility_scope",
        "account",
        "adapter",
        "approval_account",
        "approval_tenant",
        "attempt_tenant",
        "missing_approval",
        "future_approval",
        "policy",
        "fingerprint",
    ],
)
async def test_internal_cos_attribution_requires_exact_private_approval_context(
    tmp_path: Path, defect: str
) -> None:
    events = _chain()
    approved, attempt = events[1], events[3]
    for event in (approved, attempt):
        event.update(source_adapter="openclaw-plugin", source_account_id="cos-response")
    if defect == "service_actor":
        attempt["actor_identity"] = COS_SERVICE_PRINCIPAL_ID
    elif defect in {"attempt_actor", "approval_actor"}:
        (attempt if defect == "attempt_actor" else approved)["actor_identity"] = (
            "member-other-fixture"
        )
    elif defect == "payload_member":
        attempt["payload"]["member_id"] = "member-other-fixture"
    elif defect in {"owner_scope", "visibility_scope"}:
        attempt[defect] = "private:member-other-fixture"
    elif defect == "account":
        attempt["source_account_id"] = "spoofed-provider@example.invalid"
    elif defect == "adapter":
        attempt["source_adapter"] = "unrelated-adapter"
    elif defect == "approval_account":
        approved["source_account_id"] = "unrelated-command"
    elif defect in {"approval_tenant", "attempt_tenant"}:
        (approved if defect == "approval_tenant" else attempt)["tenant_id"] = (
            "tenant-other-fixture"
        )
    elif defect == "missing_approval":
        attempt["payload"]["approval_event_id"] = "absent-approval"
    elif defect == "future_approval":
        approved["event_id"] = "cos-audit-9999"
        attempt["payload"]["approval_event_id"] = approved["event_id"]
    elif defect == "policy":
        attempt["payload"]["policy_event_id"] = "unrelated-policy"
    elif defect == "fingerprint":
        attempt["payload"]["content_fingerprint"] = "f" * 64
    log = EventLog(tmp_path / "cos-events.db", b"c" * 32)
    try:
        for event in events:
            await log.append(EventEnvelope(**event))
        proof = await audit_historical_attribution(
            log,
            repository_root=REPO_ROOT,
            # Even a matching provider binding cannot rescue invalid internal
            # command coordinates; this is not an external source account.
            provider_bindings=(
                ProviderBinding(
                    adapter_id="fixture-spoofed-provider",
                    kind="messaging-gmail-api",
                    provider_family="google",
                    owner_kind="member",
                    owner_id=MEMBER,
                    member_id=MEMBER,
                    service_principal_id=None,
                    provider_account_id="spoofed-provider@example.invalid",
                    workspace="personal",
                    permission_scope=("gmail.read",),
                    binding_provenance="oauth-subject",
                ),
            ),
            hash_key=b"cos-attribution-fixture" * 2,
        )
    finally:
        await log.close()
    valid = defect in {"none", "service_actor"}
    assert proof["source_event_count"] == 1
    assert proof["state"] == ("healthy" if valid else "degraded")
    assert proof["attributed_event_count"] == int(valid)
    assert proof["unattributed_event_count"] == int(not valid)
    assert proof["groups"][0]["binding_state"] == (
        "internal_cos_member_source_reconciled" if valid else "unattributed"
    )
    assert proof["content_exposed"] is proof["raw_identifiers_exposed"] is False
    rendered = str(proof)
    assert MEMBER not in rendered
    assert "Synthetic private response" not in rendered
    assert "member-audit@example.invalid" not in rendered


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
async def test_historical_attribution_matches_service_owned_provider_scope(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "service-events.db", b"v" * 32)
    try:
        await log.append(
            EventEnvelope(
                event_at_ms=1,
                tenant_id="tenant-fixture",
                type="calendar.event_added",
                schema_version=1,
                occurred_at="2026-09-05T12:00:00Z",
                source_adapter="calendaring-google-calendar",
                source_account_id="service-account@invalid.example",
                owner_scope="service-private:service-principal:chief-of-staff",
                visibility_scope="service-private:service-principal:chief-of-staff",
                payload={
                    "source": "calendar:google",
                    "external_event_id": "fixture-event",
                    "calendar_id": "fixture-calendar",
                    "summary": "Fixture",
                    "start": "2026-09-05T12:00:00Z",
                    "end": "2026-09-05T13:00:00Z",
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
                    adapter_id="cos-calendar-read",
                    kind="calendaring-google-calendar",
                    provider_family="google",
                    owner_kind="service_principal",
                    owner_id="service-principal:chief-of-staff",
                    member_id=None,
                    service_principal_id="service-principal:chief-of-staff",
                    provider_account_id="service-account@invalid.example",
                    workspace="service",
                    permission_scope=("calendar.read",),
                    binding_provenance="service-account-delegation",
                ),
            ),
            hash_key=b"service-audit-key" * 2,
        )
    finally:
        await log.close()

    assert proof["state"] == "healthy"
    assert proof["attributed_event_count"] == 1
    assert proof["groups"][0]["binding_state"] == "matched"
    assert proof["groups"][0]["workspace"] == "service"


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
