"""Process proof for source time, conservative context, and exact correction."""

from __future__ import annotations

import asyncio
import base64
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.adapters.base import AdapterContext
from adminme.adapters.loader import load_builtin_adapters
from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.message_context import build_message_context_pack
from adminme.runtime.application import RuntimeSettings, create_application


REPO_ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-context-correctness-adult"  # fixture:tenant_data:ok
AUTH = {"Tailscale-User-Login": "context-correctness.ts.net"}  # fixture:tenant_data:ok


async def _events(runtime: Any, event_type: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    assert runtime.event_log is not None
    async for event in runtime.event_log.read_since(types=[event_type]):
        values.append(event)
    return values


async def _settle_context_fixture(runtime: Any) -> str | None:
    """Drain source-derived work before testing interactive corrections/replay."""
    deadline = asyncio.get_running_loop().time() + 60.0
    stable_event_id: str | None = None
    stable_cycles = 0
    statuses: dict[str, dict[str, Any]] = {}
    while asyncio.get_running_loop().time() < deadline:
        statuses = {
            name: await runtime.bus.subscriber_status(name)
            for name in sorted(runtime.bus._subscribers)  # noqa: SLF001
        }
        assert not any(row["degraded"] for row in statuses.values()), statuses
        latest = await runtime.event_log.latest_event_id()
        drained = all(row["lag_count"] == 0 for row in statuses.values())
        if drained and latest == stable_event_id:
            stable_cycles += 1
        else:
            stable_event_id = latest
            stable_cycles = 0
        if stable_cycles >= 2:
            return latest
        await asyncio.sleep(0.01)
    raise AssertionError(f"context fixture did not settle: {statuses}")


def _message(
    runtime: Any,
    *,
    native_id: str,
    thread_id: str,
    body: str,
    occurred_at: str,
    rfc_message_id: str,
    references: list[str] | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_786_373_766_000,
        tenant_id=runtime.settings.instance.tenant_id,
        type="messaging.received",
        schema_version=4,
        occurred_at=occurred_at,
        source_adapter="messaging-gmail-api",
        source_account_id="fixture-context-account",
        owner_scope=f"private:{MEMBER}",
        visibility_scope=f"private:{MEMBER}",
        sensitivity="normal",
        idempotency_key=f"fixture-context-message:{native_id}",
        payload={
            "source_channel": "gmail",
            "from_identifier": "fixture-counterparty@example.invalid",
            "to_identifier": "fixture-member@example.invalid",
            "thread_id": thread_id,
            "subject": "Fixture account review",
            "body_text": body,
            "received_at": occurred_at,
            "attachments": [],
            "native_message_id": native_id,
            "member_id": MEMBER,
            "provider": "gmail",
            "provider_account_id": "fixture-context-account",
            "workspace": "personal",
            "to_identifiers": ["fixture-member@example.invalid"],
            "cc_identifiers": [],
            "bcc_identifiers": [],
            "reply_to_identifier": None,
            "rfc_message_id": rfc_message_id,
            "references": references or [],
            "in_reply_to": None,
            "provider_labels": ["INBOX"],
            "unread": True,
            "archived": False,
            "starred": False,
            "important": False,
        },
    )


def _raw_gmail_message(
    *,
    native_id: str,
    thread_id: str,
    body: str,
    internal_date_ms: int,
    rfc_message_id: str,
    references: list[str] | None = None,
    body_html: str | None = None,
    sent: bool = False,
) -> dict[str, Any]:
    headers = [
        {
            "name": "From",
            "value": (
                "fixture-member@example.invalid"
                if sent
                else "fixture-counterparty@example.invalid"
            ),
        },
        {
            "name": "To",
            "value": (
                "fixture-counterparty@example.invalid"
                if sent
                else "fixture-member@example.invalid"
            ),
        },
        {"name": "Subject", "value": "Fixture account authorization"},
        {"name": "Message-ID", "value": rfc_message_id},
    ]
    if references:
        headers.append({"name": "References", "value": " ".join(references)})
    payload: dict[str, Any] = {
        "mimeType": "text/plain",
        "headers": headers,
        "body": {
            "data": base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
        },
    }
    if body_html is not None:
        payload = {
            "mimeType": "multipart/alternative",
            "headers": headers,
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {
                        "data": base64.urlsafe_b64encode(body.encode("utf-8")).decode(
                            "ascii"
                        )
                    },
                },
                {
                    "mimeType": "text/html",
                    "body": {
                        "data": base64.urlsafe_b64encode(
                            body_html.encode("utf-8")
                        ).decode("ascii")
                    },
                },
            ],
        }
    return {
        "id": native_id,
        "threadId": thread_id,
        "internalDate": str(internal_date_ms),
        "labelIds": ["SENT"] if sent else ["INBOX", "UNREAD"],
        "payload": payload,
    }


async def _preload_gmail_replay_fixture(
    settings: RuntimeSettings,
) -> tuple[str, str, str, str, str, str, str, str, str]:
    """Emit actual Gmail facts, then seed the reviewed v20 durable cursor."""

    event_log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(event_log, settings.instance.bus_checkpoint_path)
    events = EventWriter(event_log, bus)
    [loaded_adapter] = load_builtin_adapters(
        {
            "adapters": [
                {
                    "kind": "messaging-gmail-api",
                    "config": {
                        "service_account_key_ref": "op://fixture-vault/item/field",
                        "user_email": "fixture-member@example.invalid",
                        "provider_account_id": "fixture-member@example.invalid",
                        "member_id": MEMBER,
                        "workspace": "personal",
                        "permission_scope": ["gmail.readonly"],
                        "binding_provenance": "service-account-delegation",
                        "ownership_attested": True,
                    },
                }
            ]
        },
        repository_root=REPO_ROOT,
    )
    adapter: Any = loaded_adapter
    now = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)
    ctx = AdapterContext(
        event_sink=events,
        config={},
        correlation_id_factory=lambda: "fixture-context-replay",
        now_factory=lambda: now,
    )
    other_adapter = adapter.__class__(
        config={
            **adapter._config,
            "member_id": "fixture-context-other-member",
        }
    )
    try:
        first_id = await adapter._emit_message_observation(
            ctx=ctx,
            message=_raw_gmail_message(
                native_id="fixture-replay-first",
                thread_id="fixture-replay-primary",
                body="An earlier fixture account update.",
                internal_date_ms=1_706_702_400_000,
                rfc_message_id="<fixture-replay-first@example.invalid>",
            ),
            fingerprint="1" * 64,
        )
        weak_id = await adapter._emit_message_observation(
            ctx=ctx,
            message=_raw_gmail_message(
                native_id="fixture-replay-weak",
                thread_id="fixture-replay-unrelated",
                body="A separate fixture authorization update.",
                internal_date_ms=1_706_704_200_000,
                rfc_message_id="<fixture-replay-weak@example.invalid>",
            ),
            fingerprint="2" * 64,
        )
        other_member_id = await other_adapter._emit_message_observation(
            ctx=ctx,
            message=_raw_gmail_message(
                native_id="fixture-replay-other-member",
                thread_id="fixture-replay-primary",
                body="Please complete the fixture authorization.",
                internal_date_ms=1_706_705_100_000,
                rfc_message_id="<fixture-replay-other@example.invalid>",
            ),
            fingerprint="3" * 64,
        )
        for index in range(11):
            await adapter._emit_message_observation(
                ctx=ctx,
                message=_raw_gmail_message(
                    native_id=f"fixture-replay-intermediate-{index:02d}",
                    thread_id="fixture-replay-primary",
                    body=f"Routine fixture context update {index}.",
                    internal_date_ms=1_706_702_460_000 + (index * 60_000),
                    rfc_message_id=(
                        f"<fixture-replay-intermediate-{index:02d}@example.invalid>"
                    ),
                ),
                fingerprint=f"{index + 10:064x}",
            )
        legacy_sent_id = await events.write(
            EventEnvelope(
                event_at_ms=1_706_705_900_000,
                tenant_id=settings.instance.tenant_id,
                type="messaging.sent",
                schema_version=3,
                occurred_at="2024-01-31T15:58:20Z",
                source_adapter="messaging-gmail-api",
                source_account_id="fixture-context-account",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture-replay-legacy-sent-v3",
                raw_ref=(
                    "gmail://messages/fixture-replay-legacy-sent-v3#sha256="
                    + ("6" * 64)
                ),
                payload={
                    "source_channel": "gmail",
                    "to_identifier": "fixture-counterparty@example.invalid",
                    "thread_id": "fixture-replay-legacy-sent-thread",
                    "subject": "Historical fixture reply",
                    "body_text": "I will authorize the old fixture transfer.",
                    "sent_at": "2024-01-31T15:58:20Z",
                    "delivery_status": "sent",
                    "member_id": MEMBER,
                    "provider": "gmail",
                    "provider_account_id": "fixture-context-account",
                    "workspace": "personal",
                    "native_message_id": "fixture-replay-legacy-sent-v3",
                    "in_reply_to_interaction_id": None,
                    "rfc_message_id": (
                        "<fixture-replay-legacy-sent-v3@example.invalid>"
                    ),
                    "references": [],
                    "in_reply_to": None,
                },
            )
        )
        legacy_assessment_id = await events.write(
            EventEnvelope(
                event_at_ms=1_786_373_765_900,
                tenant_id=settings.instance.tenant_id,
                type="triage.intent.features_extracted",
                schema_version=3,
                occurred_at="2026-08-10T16:36:05Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key=(f"triage:message-features:v20:{legacy_sent_id}"),
                payload={
                    "intent_id": f"source:{legacy_sent_id}",
                    "source_event_id": legacy_sent_id,
                    "skill_call_event_id": None,
                    "extractor_id": "legacy-contextual-nlp-v20",
                    "language": None,
                    "candidates": [
                        {
                            "candidate_key": "legacy-sent-false-commitment",
                            "target_kind": "commitment",
                            "title": "Authorize the old fixture transfer",
                            "fields": {},
                            "evidence_spans": [],
                            "confidence": 0.9,
                            "reasons": ["legacy authored-text interpretation"],
                        }
                    ],
                    "extraction_state": "complete",
                    "extracted_at": "2026-08-10T16:36:05Z",
                    "assessment_outcome": "eligible_candidates",
                    "reason_codes": ["legacy_language_candidate"],
                },
            ),
            causation_id=legacy_sent_id,
        )
        legacy_proposal_id = "fixture-replay-legacy-sent-false-commitment"
        await events.write(
            EventEnvelope(
                event_at_ms=1_786_373_766_000,
                tenant_id=settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=3,
                occurred_at="2026-08-10T16:36:06Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture-replay-legacy-sent-false-commitment",
                payload={
                    "proposal_id": legacy_proposal_id,
                    "target_kind": "commitment",
                    "owner_member_id": MEMBER,
                    "title": "Authorize the old fixture transfer",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": legacy_sent_id,
                    "confidence": 0.9,
                    "reasons": ["legacy authored-text interpretation"],
                    "parent_intent_id": None,
                    "candidate_key": "legacy-sent-false-commitment",
                    "extraction_event_id": legacy_assessment_id,
                    "eligibility_rule_ids": ["grounded_language_exact_quote_v1"],
                },
            ),
            causation_id=legacy_assessment_id,
        )
        current_id = await adapter._emit_message_observation(
            ctx=ctx,
            message=_raw_gmail_message(
                native_id="fixture-replay-current",
                thread_id="fixture-replay-primary",
                body=(
                    "[fixture:context-correctness-replay] "
                    "Please complete the fixture authorization."
                ),
                internal_date_ms=1_706_706_000_000,
                rfc_message_id="<fixture-replay-current@example.invalid>",
            ),
            fingerprint="4" * 64,
        )
        stable_fallback_source_id = await adapter._emit_message_observation(
            ctx=ctx,
            message=_raw_gmail_message(
                native_id="fixture-replay-stable-fallback",
                thread_id="fixture-replay-stable-fallback-thread",
                body="Please complete the fixture checklist",
                internal_date_ms=1_706_706_060_000,
                rfc_message_id="<fixture-replay-stable-fallback@example.invalid>",
            ),
            fingerprint="7" * 64,
        )
        stable_fallback_assessment_id = await events.write(
            EventEnvelope(
                event_at_ms=1_786_373_766_050,
                tenant_id=settings.instance.tenant_id,
                type="triage.intent.features_extracted",
                schema_version=3,
                occurred_at="2026-08-10T16:36:05Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key=(
                    f"triage:message-features:v20:{stable_fallback_source_id}"
                ),
                payload={
                    "intent_id": f"source:{stable_fallback_source_id}",
                    "source_event_id": stable_fallback_source_id,
                    "skill_call_event_id": None,
                    "extractor_id": "legacy-contextual-nlp-v20",
                    "language": None,
                    "candidates": [
                        {
                            "candidate_key": "message-fallback-2",
                            "target_kind": "task",
                            "title": "Please complete the fixture checklist",
                            "fields": {},
                            "evidence_spans": [],
                            "confidence": None,
                            "reasons": [
                                "inbound_explicit_directive_or_due_obligation_v1"
                            ],
                        }
                    ],
                    "extraction_state": "complete",
                    "extracted_at": "2026-08-10T16:36:05Z",
                    "assessment_outcome": "eligible_candidates",
                    "reason_codes": ["eligible_candidate_found"],
                },
            ),
            causation_id=stable_fallback_source_id,
        )
        stable_fallback_proposal_id = "fixture-replay-stable-fallback-proposal"
        await events.write(
            EventEnvelope(
                event_at_ms=1_786_373_766_075,
                tenant_id=settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=3,
                occurred_at="2026-08-10T16:36:05Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key=stable_fallback_proposal_id,
                payload={
                    "proposal_id": stable_fallback_proposal_id,
                    "target_kind": "task",
                    "owner_member_id": MEMBER,
                    "title": "Please complete the fixture checklist",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": stable_fallback_source_id,
                    "confidence": None,
                    "reasons": ["inbound_explicit_directive_or_due_obligation_v1"],
                    "parent_intent_id": None,
                    "candidate_key": "message-fallback-2",
                    "extraction_event_id": stable_fallback_assessment_id,
                    "eligibility_rule_ids": [
                        "inbound_explicit_directive_or_due_obligation_v1",
                        "candidate_fields_task_v1",
                    ],
                    "interpretation": {
                        "contract": "triage-context-interpretation-v1",
                        "context_fingerprint": "d" * 64,
                        "thread_key": "fixture-replay-stable-fallback",
                        "anchor_event_id": stable_fallback_source_id,
                        "compact_summary": "Complete the fixture checklist",
                        "case_summary": "A stale contextualized fixture fallback.",
                        "why_member": "The exact source contains a directive.",
                        "next_step": "Review the exact deterministic ask.",
                        "thread_state": "waiting_on_member",
                        "action_owner": "member",
                        "lifecycle_state": "open",
                        "unresolved_questions": [],
                        "evidence_refs": [
                            {
                                "source_event_id": stable_fallback_source_id,
                                "quote": "Please complete the fixture checklist",
                            }
                        ],
                        "context_source_event_ids": [stable_fallback_source_id],
                        "context_sources": [
                            {
                                "source_event_id": stable_fallback_source_id,
                                "relation": "thread",
                                "reason_codes": ["provider_thread_id"],
                            }
                        ],
                        "related_source_event_ids": [],
                    },
                },
            ),
            causation_id=stable_fallback_assessment_id,
        )
        old_proposal_id = "fixture-replay-old-crossed-wire"
        old_proposal_event_id = await events.write(
            EventEnvelope(
                event_at_ms=1_786_373_766_100,
                tenant_id=settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=3,
                occurred_at="2026-08-10T16:36:06Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture-replay-old-crossed-wire",
                payload={
                    "proposal_id": old_proposal_id,
                    "target_kind": "task",
                    "owner_member_id": MEMBER,
                    "title": "Old unrelated fixture authorization task",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": first_id,
                    "confidence": 0.8,
                    "reasons": ["retired weak context inference"],
                    "parent_intent_id": None,
                    "candidate_key": "old-weak-cross-thread",
                    "extraction_event_id": None,
                    "eligibility_rule_ids": ["explicit_action_verb_v1"],
                    "interpretation": {
                        "contract": "triage-context-interpretation-v1",
                        "context_fingerprint": "c" * 64,
                        "thread_key": "fixture-replay-primary",
                        "anchor_event_id": first_id,
                        "compact_summary": "Old unrelated fixture task",
                        "case_summary": "A deliberately stale replay fixture.",
                        "why_member": "A retired weak match.",
                        "next_step": "Supersede this untouched candidate.",
                        "thread_state": "unclear",
                        "action_owner": "member",
                        "lifecycle_state": "open",
                        "unresolved_questions": [],
                        "evidence_refs": [
                            {
                                "source_event_id": weak_id,
                                "quote": "A separate fixture authorization update.",
                            }
                        ],
                        "context_source_event_ids": [first_id, weak_id],
                        "context_sources": [
                            {
                                "source_event_id": first_id,
                                "relation": "thread",
                                "reason_codes": ["provider_thread_id"],
                            },
                            {
                                "source_event_id": weak_id,
                                "relation": "related",
                                "reason_codes": ["participant_overlap"],
                            },
                        ],
                        "related_source_event_ids": [weak_id],
                    },
                },
            ),
            causation_id=first_id,
        )

        async def noop(_event: dict[str, Any]) -> None:
            return None

        bus.subscribe(
            "pipeline:pipeline:noise_filtering",
            [
                "messaging.received",
                "messaging.sent",
                "telephony.sms_received",
                "source.assessment.recovery_requested",
            ],
            noop,
            consumer_version="20.0.0",
        )
        await bus.set_checkpoint(
            "pipeline:pipeline:noise_filtering", old_proposal_event_id
        )
        return (
            first_id,
            weak_id,
            other_member_id,
            legacy_sent_id,
            legacy_proposal_id,
            current_id,
            old_proposal_id,
            stable_fallback_source_id,
            stable_fallback_proposal_id,
        )
    finally:
        await bus.stop()
        await event_log.close()


async def _preload_gmail_sent_quote_fixture(settings: RuntimeSettings) -> str:
    event_log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(event_log, settings.instance.bus_checkpoint_path)
    events = EventWriter(event_log, bus)
    [adapter] = load_builtin_adapters(
        {
            "adapters": [
                {
                    "kind": "messaging-gmail-api",
                    "config": {
                        "service_account_key_ref": "op://fixture-vault/item/field",
                        "user_email": "fixture-member@example.invalid",
                        "provider_account_id": "fixture-member@example.invalid",
                        "member_id": MEMBER,
                        "workspace": "personal",
                        "permission_scope": ["gmail.readonly"],
                        "binding_provenance": "service-account-delegation",
                        "ownership_attested": True,
                    },
                }
            ]
        },
        repository_root=REPO_ROOT,
    )
    ctx = AdapterContext(
        event_sink=events,
        config={},
        correlation_id_factory=lambda: "fixture-context-sent-quote",
        now_factory=lambda: datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
    )
    quote = "I will authorize the old fixture transfer."
    try:
        return await adapter._emit_message_observation(
            ctx=ctx,
            message=_raw_gmail_message(
                native_id="fixture-sent-quoted-only",
                thread_id="fixture-sent-quoted-only-thread",
                body=quote,
                body_html=f'<div class="gmail_quote">{quote}</div>',
                sent=True,
                internal_date_ms=1_706_706_000_000,
                rfc_message_id="<fixture-sent-quoted-only@example.invalid>",
            ),
            fingerprint="5" * 64,
        )
    finally:
        await bus.stop()
        await event_log.close()


def test_gmail_sent_multipart_quote_boundary_survives_production_mapping(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "sent-quote-instance"
    instance.mkdir()
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"s" * 32,
        repository_root=REPO_ROOT,
        member_identities={"context-correctness.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        legacy_personal_obligation_compatibility=True,
    )
    source_id = asyncio.run(_preload_gmail_sent_quote_fixture(settings))

    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            source_id,
        )
        source = client.portal.call(runtime.event_log.get, source_id)
        assert source["type"] == "messaging.sent"
        assert source["schema_version"] == 4
        assert "gmail_quote" in source["payload"]["body_html"]
        assessments = [
            event
            for event in client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            if event.get("causation_id") == source_id
        ]
        assert len(assessments) == 1
        assert assessments[0]["payload"]["assessment_outcome"] == "no_candidate"
        assert [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"].get("source_event_id") == source_id
        ] == []


def test_exact_context_correction_round_trip_through_production_composition(
    tmp_path: Path,
) -> None:
    instance = tmp_path / "instance"
    instance.mkdir()
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"x" * 32,
        repository_root=REPO_ROOT,
        member_identities={"context-correctness.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        openclaw_enabled=False,
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        legacy_personal_obligation_compatibility=True,
    )
    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        old_edge_id = client.portal.call(
            runtime.events.write,
            _message(
                runtime,
                native_id="fixture-old-edge",
                thread_id="fixture-primary-thread",
                body="An older exact fixture edge.",
                occurred_at="2024-01-31T14:00:00Z",
                rfc_message_id="<fixture-old-edge@example.invalid>",
            ),
        )
        for index in range(11):
            client.portal.call(
                runtime.events.write,
                _message(
                    runtime,
                    native_id=f"fixture-intermediate-{index:02d}",
                    thread_id="fixture-primary-thread",
                    body=f"Routine fixture update {index}.",
                    occurred_at=f"2024-01-31T14:{index + 1:02d}:00Z",
                    rfc_message_id=(
                        f"<fixture-intermediate-{index:02d}@example.invalid>"
                    ),
                ),
            )
        source_id = client.portal.call(
            runtime.events.write,
            _message(
                runtime,
                native_id="fixture-current",
                thread_id="fixture-primary-thread",
                body=(
                    "Please review the fixture transfer. "
                    "Also archive the fixture receipt."
                ),
                occurred_at="2024-01-31T15:00:00Z",
                rfc_message_id="<fixture-current@example.invalid>",
            ),
        )
        related_id = client.portal.call(
            runtime.events.write,
            _message(
                runtime,
                native_id="fixture-related",
                thread_id="fixture-separate-thread",
                body="The fixture transfer review is ready.",
                occurred_at="2024-01-31T16:00:00Z",
                rfc_message_id="<fixture-related@example.invalid>",
                references=["<fixture-old-edge@example.invalid>"],
            ),
        )
        proposal_id = "fixture-context-correction-proposal"
        proposal_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_373_766_100,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=4,
                occurred_at="2026-08-10T16:36:06Z",
                source_adapter="pipeline:noise-filtering",
                source_account_id=MEMBER,
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                causation_id=source_id,
                idempotency_key="fixture-context-correction-proposal",
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": "task",
                    "owner_member_id": MEMBER,
                    "title": "Review the fixture transfer",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": source_id,
                    "confidence": 0.8,
                    "reasons": ["exact fixture evidence"],
                    "parent_intent_id": None,
                    "candidate_key": "fixture-context-correction",
                    "extraction_event_id": None,
                    "eligibility_rule_ids": ["exact_source_quote_grounded_v1"],
                    "source_occurred_at": "2024-01-31T15:00:00Z",
                    "interpretation": {
                        "contract": "triage-context-interpretation-v2",
                        "context_fingerprint": "a" * 64,
                        "thread_key": "fixture-episode-thread",
                        "anchor_event_id": source_id,
                        "compact_summary": "Review the fixture transfer",
                        "case_summary": "A bounded fixture review.",
                        "why_member": "The exact authored text requests review.",
                        "next_step": "Review the source before deciding.",
                        "thread_state": "waiting_on_member",
                        "action_owner": "member",
                        "lifecycle_state": "open",
                        "unresolved_questions": [],
                        "evidence_refs": [
                            {
                                "source_event_id": source_id,
                                "quote": "Please review the fixture transfer.",
                            }
                        ],
                        "context_source_event_ids": [source_id, related_id],
                        "context_sources": [
                            {
                                "source_event_id": source_id,
                                "relation": "thread",
                                "reason_codes": ["provider_thread_id"],
                                "relationship_source_event_ids": [],
                            },
                            {
                                "source_event_id": related_id,
                                "relation": "related",
                                "reason_codes": ["rfc_reference"],
                                "relationship_source_event_ids": [old_edge_id],
                            },
                        ],
                        "related_source_event_ids": [related_id],
                    },
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", proposal_event_id)
        client.portal.call(
            runtime.wait_for_projection, "context_fabric", proposal_event_id
        )
        # Source consumers can still append assessments after both projections
        # pass this proposal. Drain that preparation work before exercising
        # the correction endpoints and their unchanged response deadlines.
        client.portal.call(_settle_context_fixture, runtime)

        source = client.portal.call(runtime.event_log.get, source_id)
        assert source["occurred_at"] == "2024-01-31T15:00:00Z"
        assert source["recorded_at"] != source["occurred_at"]
        initial_pack = client.portal.call(
            build_message_context_pack, source, runtime.event_log
        )
        assert old_edge_id not in {
            message.source_event_id for message in initial_pack.messages
        }
        assert initial_pack.messages[-1].source_event_id == related_id
        assert initial_pack.messages[-1].relationship_source_event_ids == (old_edge_id,)

        page = client.get(
            f"/api/member/v1/surfaces/triage?triage_item={proposal_id}",
            headers=AUTH,
        )
        assert page.status_code == 200, page.text
        item = next(
            value
            for value in page.json()["items"]
            if value["proposal_id"] == proposal_id
        )
        assert item["source_context"]["occurred_at"] == "2024-01-31T15:00:00Z"
        assert item["source_context"]["recorded_at"] != "2024-01-31T15:00:00Z"
        assert item["source_context"]["correction_capabilities"] == {
            "unrelated_source_event_ids": [related_id],
            "quoted_or_forwarded_history": True,
        }
        browser_code = client.get("/member-assets/member.js").text
        assert "canCorrectQuotedHistory" in browser_code
        assert "correctableRelatedSourceIds.has" in browser_code

        dismissed = client.post(
            f"/api/member/v1/triage/{proposal_id}/dismiss",
            headers=AUTH,
            json={
                "request_id": "fixture-context-dismiss-0001",
                "expected_last_event_id": item["last_event_id"],
                "reason": "Exact fixture context correction",
                "feedback_reason": "wrong_context",
                "context_correction": {
                    "unrelated_source_event_ids": [related_id],
                    "quoted_or_forwarded_history": True,
                },
            },
        )
        assert dismissed.status_code == 200, dismissed.text
        assert len(dismissed.json()["context_correction_event_ids"]) == 2
        assert (
            len(client.portal.call(_events, runtime, "context.relationship.rejected"))
            == 1
        )
        assert (
            len(client.portal.call(_events, runtime, "context.source_text.corrected"))
            == 1
        )
        text_correction = client.portal.call(
            _events, runtime, "context.source_text.corrected"
        )[0]

        corrected_pack = client.portal.call(
            build_message_context_pack, source, runtime.event_log
        )
        assert related_id not in {
            message.source_event_id for message in corrected_pack.messages
        }
        corrected_source = next(
            message
            for message in corrected_pack.messages
            if message.source_event_id == source_id
        )
        assert corrected_source.body_text == ("Also archive the fixture receipt.")

        relinked = client.post(
            "/api/member/v1/context-relationships/confirm",
            headers=AUTH,
            json={
                "request_id": "fixture-context-relink-0001",
                "source_event_id": old_edge_id,
                "related_source_event_id": related_id,
            },
        )
        assert relinked.status_code == 200, relinked.text
        reinstated = client.post(
            "/api/member/v1/context-source-text/reinstate",
            headers=AUTH,
            json={
                "request_id": "fixture-context-reinstate-0001",
                "source_event_id": source_id,
                "correction_id": text_correction["payload"]["correction_id"],
            },
        )
        assert reinstated.status_code == 200, reinstated.text
        restored_pack = client.portal.call(
            build_message_context_pack, source, runtime.event_log
        )
        assert restored_pack.messages[-1].source_event_id == related_id
        restored_source = next(
            message
            for message in restored_pack.messages
            if message.source_event_id == source_id
        )
        assert restored_source.body_text == (
            "Please review the fixture transfer. Also archive the fixture receipt."
        )

        settled_watermark = client.portal.call(_settle_context_fixture, runtime)
        context = runtime.projections.connection("context_fabric")
        assert tuple(
            context.execute(
                "SELECT algorithm, state FROM context_relationship_decisions"
            ).fetchone()
        ) == ("message-context-pack-v2", "confirmed")
        assert (
            context.execute(
                "SELECT state FROM context_source_text_corrections"
            ).fetchone()[0]
            == "reinstated"
        )
        client.portal.call(runtime.projections.rebuild, "context_fabric")
        assert client.portal.call(_settle_context_fixture, runtime) == settled_watermark
        rebuilt = runtime.projections.connection("context_fabric")
        assert tuple(
            rebuilt.execute(
                "SELECT algorithm, state FROM context_relationship_decisions"
            ).fetchone()
        ) == ("message-context-pack-v2", "confirmed")
        assert (
            rebuilt.execute(
                "SELECT state FROM context_source_text_corrections"
            ).fetchone()[0]
            == "reinstated"
        )
        assert client.portal.call(_events, runtime, "task.created") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


@pytest.mark.parametrize(
    ("member_action", "expected_state"),
    [
        ("enriched", "pending_review"),
        ("dismissed", "dismissed"),
        ("confirmed", "confirmed"),
    ],
)
def test_stale_system_supersession_cannot_override_member_action(
    tmp_path: Path,
    member_action: str,
    expected_state: str,
) -> None:
    """Member action wins stale supersession and suppresses its replacement."""

    instance = tmp_path / "supersession-race-instance"
    instance.mkdir()
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"c" * 32,
        repository_root=REPO_ROOT,
        member_identities={"context-correctness.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        openclaw_enabled=False,
        legacy_personal_obligation_compatibility=True,
    )
    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        source_id = client.portal.call(
            runtime.events.write,
            _message(
                runtime,
                native_id="fixture-supersession-race-source",
                thread_id="fixture-supersession-race-thread",
                body="A fixture item that the member will correct.",
                occurred_at="2024-01-31T15:00:00Z",
                rfc_message_id="<fixture-supersession-race@example.invalid>",
            ),
        )
        proposal_id = "fixture-supersession-race-proposal"
        proposal_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_373_766_100,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=3,
                occurred_at="2026-08-10T16:36:06Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture-supersession-race-proposal",
                payload={
                    "proposal_id": proposal_id,
                    "target_kind": "commitment",
                    "owner_member_id": MEMBER,
                    "title": "Legacy fixture interpretation",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": source_id,
                    "confidence": 0.9,
                    "reasons": ["legacy authored-text interpretation"],
                    "parent_intent_id": None,
                    "candidate_key": "fixture-supersession-race",
                    "extraction_event_id": None,
                    "eligibility_rule_ids": ["grounded_language_exact_quote_v1"],
                    "interpretation": {
                        "contract": "triage-context-interpretation-v1",
                        "context_fingerprint": "e" * 64,
                        "thread_key": "fixture-supersession-race-thread",
                        "anchor_event_id": source_id,
                        "compact_summary": "Legacy fixture interpretation",
                        "case_summary": "Context that replay will replace.",
                        "why_member": "The source appeared to contain an ask.",
                        "next_step": "Review the fixture interpretation.",
                        "thread_state": "waiting_on_member",
                        "action_owner": "member",
                        "lifecycle_state": "open",
                        "unresolved_questions": [],
                        "evidence_refs": [
                            {
                                "source_event_id": source_id,
                                "quote": "A fixture item that the member will correct.",
                            }
                        ],
                        "context_source_event_ids": [source_id],
                        "context_sources": [
                            {
                                "source_event_id": source_id,
                                "relation": "thread",
                                "reason_codes": ["provider_thread_id"],
                            }
                        ],
                        "related_source_event_ids": [],
                    },
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", proposal_event_id)
        if member_action == "enriched":
            action_payload = {
                "proposal_id": proposal_id,
                "enriched_at": "2026-08-10T16:36:07Z",
                "enriched_by_member_id": MEMBER,
                "title": "Member-corrected legitimate commitment",
                "candidate_updates": {
                    "note": "The member reviewed this interpretation."
                },
            }
        elif member_action == "dismissed":
            action_payload = {
                "proposal_id": proposal_id,
                "dismissed_at": "2026-08-10T16:36:07Z",
                "dismissed_by_member_id": MEMBER,
                "reason": "wrong_context",
            }
        else:
            action_payload = {
                "proposal_id": proposal_id,
                "confirmed_at": "2026-08-10T16:36:07Z",
                "confirmed_by_member_id": MEMBER,
                "materialized_event_ids": ["fixture-member-confirmed-object"],
            }
        action_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_373_766_200,
                tenant_id=runtime.settings.instance.tenant_id,
                type=f"triage.item.{member_action}",
                schema_version=1,
                occurred_at="2026-08-10T16:36:07Z",
                source_adapter="member-triage",
                source_account_id=MEMBER,
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key=f"fixture-supersession-race-{member_action}",
                payload=action_payload,
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", action_event_id)
        stale_supersession_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_373_766_300,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.item.superseded",
                schema_version=3,
                occurred_at="2026-08-10T16:36:08Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key="fixture-supersession-race-stale",
                payload={
                    "proposal_id": proposal_id,
                    "superseded_at": "2026-08-10T16:36:08Z",
                    "replacement_source_event_id": source_id,
                    "reason": "eligibility_rule_replaced",
                    "expected_last_event_id": proposal_event_id,
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", stale_supersession_id)
        replacement_id = "fixture-supersession-race-replacement"
        replacement_event_id = client.portal.call(
            runtime.events.write,
            EventEnvelope(
                event_at_ms=1_786_373_766_400,
                tenant_id=runtime.settings.instance.tenant_id,
                type="triage.item.proposed",
                schema_version=4,
                occurred_at="2026-08-10T16:36:09Z",
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                owner_scope=f"private:{MEMBER}",
                visibility_scope=f"private:{MEMBER}",
                sensitivity="normal",
                idempotency_key=(
                    "triage:message-candidate:v22:fixture-race:"
                    "fixture-supersession-race:commitment"
                ),
                payload={
                    "proposal_id": replacement_id,
                    "target_kind": "commitment",
                    "owner_member_id": MEMBER,
                    "title": "Legacy fixture interpretation",
                    "candidate": {},
                    "route": {"member_id": MEMBER},
                    "source_event_id": source_id,
                    "confidence": None,
                    "reasons": ["inbound_explicit_directive_or_due_obligation_v1"],
                    "parent_intent_id": None,
                    "candidate_key": "fixture-supersession-race",
                    "extraction_event_id": None,
                    "eligibility_rule_ids": [
                        "inbound_explicit_directive_or_due_obligation_v1"
                    ],
                    "interpretation": None,
                    "source_occurred_at": "2024-01-31T15:00:00Z",
                },
            ),
        )
        client.portal.call(runtime.wait_for_projection, "triage", replacement_event_id)

        triage = runtime.projections.connection("triage")
        predecessor = triage.execute(
            """SELECT state, title, last_event_id FROM triage_items
                WHERE tenant_id=? AND proposal_id=?""",
            (runtime.settings.instance.tenant_id, proposal_id),
        ).fetchone()
        assert predecessor is not None
        assert predecessor["state"] == expected_state
        assert predecessor["title"] == (
            "Member-corrected legitimate commitment"
            if member_action == "enriched"
            else "Legacy fixture interpretation"
        )
        assert predecessor["last_event_id"] == action_event_id
        assert (
            triage.execute(
                "SELECT 1 FROM triage_items WHERE tenant_id=? AND proposal_id=?",
                (runtime.settings.instance.tenant_id, replacement_id),
            ).fetchone()
            is None
        )
        assert (
            client.portal.call(_events, runtime, "triage.item.superseded")[-1][
                "event_id"
            ]
            == stale_supersession_id
        )
        assert client.portal.call(_events, runtime, "task.created") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )


def _assert_predecessor_can_read_supersession(
    envelope: dict[str, Any], proposal_event_id: str
) -> None:
    """Exercise the frozen 890a0b5 supersession dispatch and SQL guard.

    Only that predecessor boundary is retained here; it must consume an actual
    production event without importing today's more permissive V4 handler.
    """

    payload = envelope["payload"]
    # This exact >=3 indexing in the predecessor caused rollback to crash when
    # the first V4 writer omitted the legacy field.
    expected_last_event_id = (
        str(payload["expected_last_event_id"])
        if int(envelope.get("schema_version") or 1) >= 3
        else None
    )
    with sqlite3.connect(":memory:") as conn:
        conn.execute(
            """CREATE TABLE triage_items (
                tenant_id TEXT, proposal_id TEXT, state TEXT, resolved_at TEXT,
                resolved_by TEXT, materialized_event_ids_json TEXT,
                resolution_reason TEXT, last_event_id TEXT)"""
        )
        for current_revision in (
            proposal_event_id,
            f"{proposal_event_id}-subsequent-routing",
            f"{proposal_event_id}-subsequent-member-enrichment",
        ):
            assert current_revision < envelope["event_id"]
            conn.execute("DELETE FROM triage_items")
            conn.execute(
                """INSERT INTO triage_items
                    (tenant_id, proposal_id, state, last_event_id)
                    VALUES (?, ?, 'pending_review', ?)""",
                (envelope["tenant_id"], payload["proposal_id"], current_revision),
            )
            # Frozen _resolve from predecessor 890a0b5; do not substitute the
            # current content-revision implementation for this rollback proof.
            conn.execute(
                """UPDATE triage_items
                      SET state=?, resolved_at=?, resolved_by=?,
                          materialized_event_ids_json=?, resolution_reason=?, last_event_id=?
                    WHERE tenant_id=? AND proposal_id=?
                      AND (state='pending_review' OR (?=1 AND state='pending_interpretation'))
                      AND (? IS NULL OR last_event_id=?)
                      AND last_event_id < ?""",
                (
                    "dismissed",
                    payload["superseded_at"],
                    "system:source_changed",
                    "[]",
                    payload["reason"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    payload["proposal_id"],
                    1,
                    expected_last_event_id,
                    expected_last_event_id,
                    envelope["event_id"],
                ),
            )
            state, last_event_id = conn.execute(
                "SELECT state, last_event_id FROM triage_items"
            ).fetchone()
            if current_revision == proposal_event_id:
                assert (state, last_event_id) == ("dismissed", envelope["event_id"])
            else:
                assert (state, last_event_id) == ("pending_review", current_revision)


def test_gmail_full_replay_supersedes_crossed_wire_and_keeps_valid_sibling(
    tmp_path: Path,
) -> None:
    """Actual Gmail mapping reaches the v23 full-replay production runner."""

    instance = tmp_path / "replay-instance"
    instance.mkdir()
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"r" * 32,
        repository_root=REPO_ROOT,
        member_identities={"context-correctness.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        legacy_personal_obligation_compatibility=True,
    )
    (
        first_id,
        weak_id,
        other_member_id,
        legacy_sent_id,
        legacy_proposal_id,
        current_id,
        old_proposal_id,
        stable_fallback_source_id,
        stable_fallback_proposal_id,
    ) = asyncio.run(_preload_gmail_replay_fixture(settings))

    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            stable_fallback_source_id,
        )
        superseded = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.superseded")
            if event["payload"]["proposal_id"] == old_proposal_id
        ]
        assert len(superseded) == 1
        assert superseded[0]["payload"]["replacement_source_event_id"] == current_id
        legacy_superseded = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.superseded")
            if event["payload"]["proposal_id"] == legacy_proposal_id
        ]
        assert len(legacy_superseded) == 1
        assert legacy_superseded[0]["payload"]["replacement_source_event_id"] == (
            legacy_sent_id
        )
        assert legacy_superseded[0]["payload"]["reason"] == (
            "eligibility_rule_replaced"
        )
        client.portal.call(
            runtime.wait_for_projection, "triage", legacy_superseded[0]["event_id"]
        )

        proposals = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"].get("source_event_id") == current_id
        ]
        assert len(proposals) == 1
        proposal = proposals[0]
        interpretation = proposal["payload"]["interpretation"]
        assert first_id not in interpretation["context_source_event_ids"]
        assert current_id in interpretation["context_source_event_ids"]
        assert weak_id not in interpretation["context_source_event_ids"]
        assert other_member_id not in interpretation["context_source_event_ids"]

        legacy_assessments = [
            event
            for event in client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            if event.get("causation_id") == legacy_sent_id
        ]
        assert len(legacy_assessments) == 2
        assert {
            event["payload"]["assessment_outcome"] for event in legacy_assessments
        } == {"eligible_candidates", "no_candidate"}
        current_legacy_assessment = next(
            event
            for event in legacy_assessments
            if event["payload"]["assessment_outcome"] == "no_candidate"
        )
        assert current_legacy_assessment["payload"]["reason_codes"] == [
            "legacy_gmail_sent_quote_boundary_unavailable"
        ]
        legacy_proposals = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"].get("source_event_id") == legacy_sent_id
        ]
        assert len(legacy_proposals) == 1
        assert legacy_proposals[0]["payload"]["proposal_id"] == legacy_proposal_id

        replay_assessments = client.portal.call(
            _events, runtime, "triage.intent.features_extracted"
        )
        deferred_first = next(
            event
            for event in replay_assessments
            if event.get("causation_id") == first_id
            and event.get("idempotency_key")
            == f"triage:message-features:v23:{first_id}"
        )
        assert deferred_first["payload"]["assessment_outcome"] == "no_candidate"
        assert deferred_first["payload"]["extractor_id"] == (
            "thread-anchor-deferred-v1"
        )
        assert deferred_first["payload"]["reason_codes"][:3] == [
            "language_assessment_deferred_to_latest_thread_message",
            "no_eligible_candidate",
            "deferred_to_latest_thread_message",
        ]
        language_calls = client.portal.call(_events, runtime, "skill.call.recorded")
        assert not any(
            event.get("causation_id") == first_id for event in language_calls
        )
        assert any(event.get("causation_id") == current_id for event in language_calls)

        stable_fallback_supersessions = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.superseded")
            if event["payload"]["proposal_id"] == stable_fallback_proposal_id
        ]
        assert len(stable_fallback_supersessions) == 1
        stable_fallback_proposals = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"].get("source_event_id") == stable_fallback_source_id
        ]
        assert len(stable_fallback_proposals) == 2
        original_stable_proposal = next(
            event
            for event in stable_fallback_proposals
            if event["payload"]["proposal_id"] == stable_fallback_proposal_id
        )
        replacement_stable_proposal = next(
            event
            for event in stable_fallback_proposals
            if event["payload"]["proposal_id"] != stable_fallback_proposal_id
        )
        assert original_stable_proposal["payload"]["interpretation"]["contract"] == (
            "triage-context-interpretation-v1"
        )
        assert (
            stable_fallback_supersessions[0]["payload"][
                "expected_content_revision_event_id"
            ]
            == original_stable_proposal["event_id"]
        )
        _assert_predecessor_can_read_supersession(
            stable_fallback_supersessions[0], original_stable_proposal["event_id"]
        )
        assert replacement_stable_proposal["schema_version"] == 5
        assert replacement_stable_proposal["payload"]["interpretation"]["contract"] == (
            "triage-context-interpretation-v3"
        )
        assert (
            replacement_stable_proposal["payload"]["interpretation"][
                "obligation_assessment"
            ]["independent_obligation"]
            is True
        )
        stable_fallback_assessments = [
            event
            for event in client.portal.call(
                _events, runtime, "triage.intent.features_extracted"
            )
            if event.get("causation_id") == stable_fallback_source_id
        ]
        assert len(stable_fallback_assessments) == 2
        assert {
            event["payload"]["assessment_outcome"]
            for event in stable_fallback_assessments
        } == {"eligible_candidates"}
        current_stable_assessment = max(
            stable_fallback_assessments, key=lambda event: event["event_id"]
        )
        assert current_stable_assessment["idempotency_key"] == (
            f"triage:message-features:v23:{stable_fallback_source_id}"
        )
        client.portal.call(
            runtime.wait_for_projection,
            "triage",
            replacement_stable_proposal["event_id"],
        )
        stable_page = client.get(
            "/api/member/v1/surfaces/triage"
            f"?triage_item={replacement_stable_proposal['payload']['proposal_id']}",
            headers=AUTH,
        )
        assert stable_page.status_code == 200, stable_page.text
        stable_item = next(
            item
            for item in stable_page.json()["items"]
            if item["proposal_id"]
            == replacement_stable_proposal["payload"]["proposal_id"]
        )
        assert stable_item["state"] == "pending_review"
        assert stable_item["review_state"]["human_gate_required"] is True

        client.portal.call(runtime.wait_for_projection, "triage", proposal["event_id"])
        page = client.get("/api/member/v1/surfaces/triage", headers=AUTH)
        assert page.status_code == 200, page.text
        items = page.json()["items"]
        assert old_proposal_id not in {item["proposal_id"] for item in items}
        assert legacy_proposal_id not in {item["proposal_id"] for item in items}
        assert stable_fallback_proposal_id not in {
            item["proposal_id"] for item in items
        }
        assert replacement_stable_proposal["payload"]["proposal_id"] in {
            item["proposal_id"] for item in items
        }
        valid = next(
            item
            for item in items
            if item["proposal_id"] == proposal["payload"]["proposal_id"]
        )
        assert valid["state"] == "pending_review"
        assert valid["review_state"]["human_gate_required"] is True

        pipeline_status = client.portal.call(runtime.pipelines.status)
        assert pipeline_status["pipeline:noise_filtering"]["version"] == "23.0.0"
        assert (
            pipeline_status["pipeline:noise_filtering"]["subscriber_status"][
                "lag_count"
            ]
            == 0
        )
        assert client.portal.call(_events, runtime, "task.created") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
