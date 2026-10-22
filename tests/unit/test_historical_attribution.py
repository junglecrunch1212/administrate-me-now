from __future__ import annotations

from pathlib import Path
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError, EventLog
from adminme.events.writer import EventWriter
from adminme.direct_intake import DirectIntakeProcessor
from adminme.historical_attribution import (
    _openclaw_submission_owner_matches,
    audit_historical_attribution,
)
from adminme.provider_bindings import ProviderBinding
from adminme.service_identities import (
    COS_SERVICE_PRINCIPAL_ID,
    ChannelRoute,
    ServiceChannelAccount,
)
from tests.acceptance.test_service_owned_cos_sources_direct_intake import (
    CHANNEL_ID,
    ROUTE_A,
    _service_message,
)
from tests.unit.test_cos_response_assurance import MEMBER, _chain


REPO_ROOT = Path(__file__).resolve().parents[2]


def forwarded_service_binding() -> ProviderBinding:
    return ProviderBinding(
        adapter_id="cos-gmail-read",
        kind="messaging-gmail-api",
        provider_family="google",
        owner_kind="service_principal",
        owner_id=COS_SERVICE_PRINCIPAL_ID,
        member_id=None,
        service_principal_id=COS_SERVICE_PRINCIPAL_ID,
        provider_account_id="cos-service@example.invalid",
        workspace="service",
        permission_scope=("gmail.readonly",),
        binding_provenance="service-account-delegation",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "member_surface",
        "cue_member",
        "cue_actor",
        "cue_tenant",
        "cue_account",
        "cue_adapter",
        "cue_visibility",
        "cue_native_receipt",
        "parent_scope",
        "parent_actor",
        "parent_tenant",
        "parent_account",
        "parent_native",
        "parent_authentication",
        "route_member",
        "route_actor",
        "route_tenant",
        "route_cue",
        "route_key",
        "missing_route",
        "route_after_watermark",
        "missing_service_binding",
    ],
)
async def test_forwarded_attribution_uses_actual_intake_receipts_and_refuses_mutations(
    tmp_path: Path, defect: str
) -> None:
    class NoopBus:
        async def notify(self, event_id: str) -> None:
            pass

    generated = EventLog(tmp_path / "generated.db", b"g" * 32)
    try:
        writer = EventWriter(generated, NoopBus())  # type: ignore[arg-type]
        channel = ServiceChannelAccount(
            channel_id=CHANNEL_ID,
            display_name="Fixture intake",
            provider="gmail",
            service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            state="observation",
            provider_account_reference="provider-account:"
            + hashlib.sha256(b"cos-service@example.invalid").hexdigest(),
            endpoint_reference=None,
            enrolled=True,
            observation_active=True,
            sending_enabled=False,
            required_for_activation=False,
            routes=(ChannelRoute(ROUTE_A, "member_private", MEMBER),),
            intake_mode="member_submission",
        )
        runtime = SimpleNamespace(
            settings=SimpleNamespace(
                instance=SimpleNamespace(tenant_id="tenant-forwarded-attribution")
            )
        )
        parent_id = await writer.write(
            _service_message(
                runtime,
                key="attribution",
                trusted_route_key=ROUTE_A,
                from_identifier="sender@example.invalid",
                subject="Selected forward",
                body="Synthetic forwarded content remains private.",
                event_at_ms=1,
            )
        )
        parent = await generated.get(parent_id)
        assert parent is not None
        await DirectIntakeProcessor(
            event_sink=writer, event_log=generated, channels=(channel,)
        ).handle(parent)
        events = [event async for event in generated.read_since()]
    finally:
        await generated.close()
    parent, cue, route = events
    assert [event["type"] for event in events] == [
        "messaging.received",
        "triage.intent.submitted",
        "direct_intake.routed",
    ]
    if defect == "member_surface":
        # An account binding cannot stand in for the native authenticated
        # submission receipt, even when its claimed member matches.
        cue["source_adapter"] = "openclaw-submission"
        cue["source_account_id"] = "spoofed-provider@example.invalid"
        cue["actor_identity"] = MEMBER
        cue["idempotency_key"] = "member-forward-fixture"
        cue["payload"]["intent_id"] = "member-forward-fixture"
    elif defect == "cue_member":
        cue["payload"]["member_id"] = "other-member"
    elif defect in {"cue_actor", "route_actor", "parent_actor"}:
        {"cue_actor": cue, "route_actor": route, "parent_actor": parent}[defect][
            "actor_identity"
        ] = "other-member"
    elif defect in {"cue_tenant", "route_tenant", "parent_tenant"}:
        {"cue_tenant": cue, "route_tenant": route, "parent_tenant": parent}[defect][
            "tenant_id"
        ] = "other-tenant"
    elif defect == "cue_account":
        cue["source_account_id"] = "spoofed-provider@example.invalid"
    elif defect == "cue_adapter":
        cue["source_adapter"] = "messaging-gmail-api"
    elif defect == "cue_visibility":
        cue["visibility_scope"] = "private:other-member"
    elif defect == "cue_native_receipt":
        cue["idempotency_key"] = "direct-intake-receipt:" + "f" * 64
    elif defect == "parent_scope":
        parent["owner_scope"] = "private:other-member"
    elif defect == "parent_account":
        parent["source_account_id"] = "different-service@example.invalid"
    elif defect == "parent_native":
        parent["payload"]["native_message_id"] = "different-native-message"
    elif defect == "parent_authentication":
        parent["payload"]["authenticated_member_route"] = False
    elif defect == "route_member":
        route["payload"]["routing_target_id"] = "other-member"
    elif defect == "route_cue":
        route["payload"]["cue_event_id"] = "different-cue"
    elif defect == "route_key":
        route["idempotency_key"] = "direct-intake-route:different-source"
    elif defect == "missing_route":
        events.remove(route)
    append_refusal = {
        "parent_scope": "messaging v5 envelope contradicts payload route: owner_scope",
        "parent_account": "messaging v5 envelope contradicts payload route: source_account_id",
        "parent_authentication": "intake route proof contradicts the route key",
    }.get(defect)
    log = EventLog(tmp_path / "audited.db", b"a" * 32)
    try:
        for event in events:
            envelope = EventEnvelope(
                **{
                    key: value
                    for key, value in event.items()
                    if key in EventEnvelope.model_fields
                }
            )
            if append_refusal is not None and event is parent:
                # The source envelope boundary already rejects this cross-owner
                # contradiction; never bypass it to reach a later audit layer.
                with pytest.raises(
                    AppendValidationError,
                    match=append_refusal,
                ):
                    await log.append(envelope)
                assert await log.count() == 0
                return
            await log.append(envelope)
        spoofed_member_binding = ProviderBinding(
            adapter_id="spoofed",
            kind="messaging-gmail-api",
            provider_family="google",
            owner_kind="member",
            owner_id=MEMBER,
            member_id=MEMBER,
            service_principal_id=None,
            provider_account_id=cue["source_account_id"],
            workspace="personal",
            permission_scope=("gmail.readonly",),
            binding_provenance="oauth-subject",
        )
        bindings = (
            (spoofed_member_binding,)
            if defect == "missing_service_binding"
            else (forwarded_service_binding(), spoofed_member_binding)
        )
        proof = await audit_historical_attribution(
            log,
            repository_root=REPO_ROOT,
            provider_bindings=bindings,
            hash_key=b"forwarded-attribution-fixture-key",
            through_event_id=cue["event_id"]
            if defect == "route_after_watermark"
            else None,
        )
    finally:
        await log.close()
    matched = [
        row
        for row in proof["groups"]
        if row["binding_state"] == "internal_forwarded_member_source_reconciled"
    ]
    assert bool(matched) is (defect == "none")
    valid = defect == "none"
    assert proof["state"] == ("healthy" if valid else "degraded")
    assert proof["source_event_count"] == 2
    assert (
        proof["unattributed_event_count"] == 0
        if valid
        else proof["unattributed_event_count"] >= 1
    )
    assert "Synthetic forwarded content" not in str(proof)
    assert "cos-service@example.invalid" not in str(proof)
    assert MEMBER not in str(proof)


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
@pytest.mark.parametrize("approval_version", [1, 2])
async def test_internal_cos_attribution_requires_exact_private_approval_context(
    tmp_path: Path, defect: str, approval_version: int
) -> None:
    events = _chain()
    approved, attempt = events[1], events[3]
    if approval_version == 2:
        from adminme.events.schemas.cos_responses import upcast_response_approval

        approved["schema_version"] = 2
        approved["payload"] = upcast_response_approval(approved["payload"])
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


def _native_forward_pair(
    version: int, call_kind: str, transport: str = "imessage"
) -> tuple[dict, dict]:
    account = "fixture-cos-native-account"
    actor = "fixture-native-sender"
    native = "fixture-native-message"
    digest = hashlib.sha256(
        json.dumps([transport, account, actor, native], separators=(",", ":")).encode()
    ).hexdigest()
    scope = f"private:{MEMBER}"
    source = {
        "event_id": "ev_01A",
        "tenant_id": "fixture-native-tenant",
        "type": "triage.intent.submitted",
        "schema_version": 2,
        "source_adapter": "openclaw-submission",
        "source_account_id": account,
        "owner_scope": scope,
        "visibility_scope": scope,
        "actor_identity": MEMBER,
        "sensitivity": "normal",
        "correlation_id": "fixture-original-call",
        "causation_id": None,
        "raw_ref": "request-sha256:" + "a" * 64,
        "idempotency_key": f"openclaw-forward:{digest}",
        "payload": {
            "intent_id": f"forward_{digest[:48]}",
            "member_id": MEMBER,
            "client_request_id": digest,
            "source_surface": "openclaw",
            "submission_kind": "member_forwarded",
            "source_event_id": None,
            "kind_hint": None,
            "intake_channel": "apple_imessage",
            "text": "Synthetic private native forward.",
            "evidence_issues": [],
            "evidence_complete": True,
        },
    }
    audit = {
        "operation": "submissions.forward",
        "result_event_ids": [source["event_id"]],
        "channel_id": transport,
        "account_id": account,
        "message_id": native,
        "call_kind": call_kind,
    }
    if version == 1:
        audit.update(actor_id=actor, member_id=MEMBER, correlation_id="fixture-call")
    else:
        audit.update(
            actor_principal_id=COS_SERVICE_PRINCIPAL_ID,
            represented_service_principal_id=COS_SERVICE_PRINCIPAL_ID,
            operator_principal_id="operator:openclaw:automation",
            context_scope="member_private",
            context_member_id=MEMBER,
            on_behalf_of_member_id=MEMBER,
            resource_owner_principal_id=MEMBER,
            audience=[MEMBER],
            visibility=[scope],
            approving_member_ids=[MEMBER],
            approval_quorum="member_owner",
            authority_grant_id="fixture-historical-grant",
            permissions=["read_context"],
            openclaw_actor_id=actor,
            request_correlation_id="fixture-call",
            causation_event_ids=[source["event_id"]],
        )
    receipt = {
        "event_id": "ev_01B",
        "type": "openclaw.tool_call.recorded",
        "schema_version": version,
        "tenant_id": source["tenant_id"],
        "source_adapter": "openclaw-plugin",
        "source_account_id": account,
        "owner_scope": scope,
        "visibility_scope": scope,
        "actor_identity": MEMBER if version == 1 else COS_SERVICE_PRINCIPAL_ID,
        "correlation_id": "fixture-call",
        "causation_id": source["event_id"],
        "idempotency_key": (
            f"openclaw:{MEMBER}:openclaw.tool_call.recorded:submissions.forward:fixture-call"
        ),
        "payload": audit,
    }
    return source, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("call_kind", ["bridge", "slash_command"])
@pytest.mark.parametrize("transport", ["imessage", "bluebubbles"])
async def test_native_submission_attribution_accepts_completed_original_or_retry_receipt(
    version: int, call_kind: str, transport: str
) -> None:
    source, receipt = _native_forward_pair(version, call_kind, transport)
    receipt["raw_ref"] = (
        "request-sha256:"
        + hashlib.sha256(
            json.dumps(
                receipt["payload"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )

    class Receipts:
        async def get_by_causation(self, event_id: str) -> list[dict]:
            assert event_id == source["event_id"]
            return [receipt]

    # No current provider binding or delegation is needed for historical owner
    # attribution. A completed retry may have a different request correlation.
    assert await _openclaw_submission_owner_matches(Receipts(), source, "ev_01C")  # type: ignore[arg-type]
    assert not await _openclaw_submission_owner_matches(Receipts(), source, "ev_01A")  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "key", "value"),
    [
        ("source", "schema_version", 1),
        ("source", "source_adapter", "messaging-gmail-api"),
        ("source", "source_account_id", "other-account"),
        ("source", "owner_scope", "private:other-member"),
        ("source", "visibility_scope", "shared:household"),
        ("source", "actor_identity", "other-member"),
        ("source", "tenant_id", "other-tenant"),
        ("source", "sensitivity", "privileged"),
        ("source", "idempotency_key", "openclaw-forward:" + "0" * 64),
        ("source", "raw_ref", None),
        ("payload", "member_id", "other-member"),
        ("payload", "intent_id", "forward_wrong"),
        ("payload", "client_request_id", "0" * 64),
        ("payload", "source_event_id", "unrelated-parent"),
        ("payload", "submission_kind", "manual"),
        ("payload", "intake_channel", "gmail"),
        ("payload", "evidence_complete", False),
        ("receipt", "event_id", "ev_019"),
        ("receipt", "type", "openclaw.model_call.recorded"),
        ("receipt", "schema_version", 3),
        ("receipt", "source_adapter", "untrusted-adapter"),
        ("receipt", "source_account_id", "other-account"),
        ("receipt", "tenant_id", "other-tenant"),
        ("receipt", "owner_scope", "private:other-member"),
        ("receipt", "visibility_scope", "shared:household"),
        ("receipt", "actor_identity", "other-service"),
        ("receipt", "causation_id", "other-source"),
        ("receipt", "correlation_id", "other-call"),
        ("receipt", "idempotency_key", "unrelated-receipt"),
        ("audit", "operation", "tools.state"),
        ("audit", "result_event_ids", ["other-source"]),
        ("audit", "account_id", "other-account"),
        ("audit", "channel_id", "discord"),
        ("audit", "openclaw_actor_id", "other-actor"),
        ("audit", "message_id", "other-native-message"),
        ("audit", "message_id", None),
        ("audit", "call_kind", "tool"),
        ("audit", "context_scope", "household_shared"),
        ("audit", "context_member_id", "other-member"),
        ("audit", "on_behalf_of_member_id", "other-member"),
        ("audit", "resource_owner_principal_id", "other-member"),
        ("audit", "represented_service_principal_id", "other-service"),
        ("audit", "actor_principal_id", "other-service"),
        ("audit", "operator_principal_id", "other-operator"),
        ("audit", "audience", ["other-member"]),
        ("audit", "visibility", ["shared:household"]),
        ("audit", "approving_member_ids", ["other-member"]),
        ("audit", "approval_quorum", "service_self"),
        ("audit", "authority_grant_id", None),
        ("audit", "permissions", ["prepare_external_actions"]),
        ("audit", "request_correlation_id", "other-call"),
        ("audit", "causation_event_ids", ["other-source"]),
    ],
)
async def test_native_submission_attribution_refuses_inconsistent_receipt_coordinates(
    target: str, key: str, value: object
) -> None:
    source, receipt = copy.deepcopy(_native_forward_pair(2, "bridge"))
    {
        "source": source,
        "payload": source["payload"],
        "receipt": receipt,
        "audit": receipt["payload"],
    }[target][key] = value
    # Rehash altered receipt payloads so the ownership checks, not only the
    # payload-integrity check, must reject every inconsistent coordinate.
    receipt["raw_ref"] = (
        "request-sha256:"
        + hashlib.sha256(
            json.dumps(
                receipt["payload"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )

    class Receipts:
        async def get_by_causation(self, event_id: str) -> list[dict]:
            return [receipt]

    assert not await _openclaw_submission_owner_matches(Receipts(), source, "ev_01C")  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["none", "missing_receipt", "payload_digest"])
async def test_native_submission_attribution_keeps_sensitive_incomplete_sources_private(
    defect: str,
) -> None:
    source, receipt = _native_forward_pair(2, "bridge")
    source["sensitivity"] = "sensitive"
    source["payload"]["evidence_complete"] = False
    source["payload"]["evidence_issues"] = ["unsupported_channel_attachment"]
    receipt["raw_ref"] = (
        "request-sha256:"
        + hashlib.sha256(
            json.dumps(
                receipt["payload"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
    )
    if defect == "payload_digest":
        receipt["raw_ref"] = "request-sha256:" + "0" * 64

    class Receipts:
        async def get_by_causation(self, event_id: str) -> list[dict]:
            return [] if defect == "missing_receipt" else [receipt]

    matched = await _openclaw_submission_owner_matches(Receipts(), source, "ev_01C")  # type: ignore[arg-type]
    assert matched is (defect == "none")
