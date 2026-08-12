"""Process proof for source time, conservative context, and exact correction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from adminme.events.envelope import EventEnvelope
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
        runtime = app.state.runtime
        source_id = client.portal.call(
            runtime.events.write,
            _message(
                runtime,
                native_id="fixture-current",
                thread_id="fixture-primary-thread",
                body="Please review the fixture transfer.",
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
                            },
                            {
                                "source_event_id": related_id,
                                "relation": "related",
                                "reason_codes": ["rfc_reference"],
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

        corrected_pack = client.portal.call(
            build_message_context_pack, source, runtime.event_log
        )
        assert [message.source_event_id for message in corrected_pack.messages] == [
            source_id
        ]
        assert corrected_pack.messages[0].body_text == ""

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
            "Please review the fixture transfer."
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
