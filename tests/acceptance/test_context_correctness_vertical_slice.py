"""Process proof for source time, conservative context, and exact correction."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
) -> dict[str, Any]:
    headers = [
        {"name": "From", "value": "fixture-counterparty@example.invalid"},
        {"name": "To", "value": "fixture-member@example.invalid"},
        {"name": "Subject", "value": "Fixture account authorization"},
        {"name": "Message-ID", "value": rfc_message_id},
    ]
    if references:
        headers.append({"name": "References", "value": " ".join(references)})
    return {
        "id": native_id,
        "threadId": thread_id,
        "internalDate": str(internal_date_ms),
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {
                "data": base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii")
            },
        },
    }


async def _preload_gmail_replay_fixture(
    settings: RuntimeSettings,
) -> tuple[str, str, str, str, str]:
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
                        "member_id": MEMBER,
                        "workspace": "personal",
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
        return first_id, weak_id, other_member_id, current_id, old_proposal_id
    finally:
        await bus.stop()
        await event_log.close()


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
    )
    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
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
                references=["<fixture-current@example.invalid>"],
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
                                "relationship_source_event_ids": [source_id],
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

        source = client.portal.call(runtime.event_log.get, source_id)
        assert source["occurred_at"] == "2024-01-31T15:00:00Z"
        assert source["recorded_at"] != source["occurred_at"]
        initial_pack = client.portal.call(
            build_message_context_pack, source, runtime.event_log
        )
        assert [message.source_event_id for message in initial_pack.messages] == [
            source_id,
            related_id,
        ]

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
        assert [message.source_event_id for message in corrected_pack.messages] == [
            source_id
        ]
        assert corrected_pack.messages[0].body_text == (
            "Also archive the fixture receipt."
        )

        relinked = client.post(
            "/api/member/v1/context-relationships/confirm",
            headers=AUTH,
            json={
                "request_id": "fixture-context-relink-0001",
                "source_event_id": source_id,
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
        assert [message.source_event_id for message in restored_pack.messages] == [
            source_id,
            related_id,
        ]
        assert restored_pack.messages[0].body_text == (
            "Please review the fixture transfer. Also archive the fixture receipt."
        )

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


def test_gmail_full_replay_supersedes_crossed_wire_and_keeps_valid_sibling(
    tmp_path: Path,
) -> None:
    """Actual Gmail mapping reaches the v21 full-replay production runner."""

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
    )
    first_id, weak_id, other_member_id, current_id, old_proposal_id = asyncio.run(
        _preload_gmail_replay_fixture(settings)
    )

    app = create_application(settings)
    with TestClient(app) as client:
        assert client.portal is not None
        runtime = app.state.runtime
        client.portal.call(
            runtime.wait_for_subscriber,
            "pipeline:pipeline:noise_filtering",
            current_id,
        )
        superseded = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.superseded")
            if event["payload"]["proposal_id"] == old_proposal_id
        ]
        assert len(superseded) == 1
        assert superseded[0]["payload"]["replacement_source_event_id"] == current_id
        client.portal.call(
            runtime.wait_for_projection, "triage", superseded[0]["event_id"]
        )

        proposals = [
            event
            for event in client.portal.call(_events, runtime, "triage.item.proposed")
            if event["payload"].get("source_event_id") == current_id
        ]
        assert len(proposals) == 1
        proposal = proposals[0]
        interpretation = proposal["payload"]["interpretation"]
        assert interpretation["context_source_event_ids"] == [first_id, current_id]
        assert weak_id not in interpretation["context_source_event_ids"]
        assert other_member_id not in interpretation["context_source_event_ids"]

        client.portal.call(runtime.wait_for_projection, "triage", proposal["event_id"])
        page = client.get("/api/member/v1/surfaces/triage", headers=AUTH)
        assert page.status_code == 200, page.text
        items = page.json()["items"]
        assert old_proposal_id not in {item["proposal_id"] for item in items}
        valid = next(
            item
            for item in items
            if item["proposal_id"] == proposal["payload"]["proposal_id"]
        )
        assert valid["state"] == "pending_review"
        assert valid["review_state"]["human_gate_required"] is True

        pipeline_status = client.portal.call(runtime.pipelines.status)
        assert pipeline_status["pipeline:noise_filtering"]["version"] == "21.0.0"
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
