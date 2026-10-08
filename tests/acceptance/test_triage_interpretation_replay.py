"""Versioned source reassessment through the active production composition."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.message_context import build_message_context_pack
from adminme.runtime.application import RuntimeSettings, create_application
from adminme.source_policy import source_coordinate_for_event


ROOT = Path(__file__).resolve().parents[2]
MEMBER = "fixture-interpretation-adult"  # fixture:tenant_data:ok
HEADERS = {"Tailscale-User-Login": "interpretation.ts.net"}
AT = "2034-02-01T10:00:00Z"
PROPOSAL = "fixture-legacy-interpretation"


async def _events(runtime: Any, event_type: str) -> list[dict[str, Any]]:
    return [event async for event in runtime.event_log.read_since(types=[event_type])]


async def _preload(
    settings: RuntimeSettings, *, actionable: bool, member_action: str | None
) -> tuple[str, str, str, str | None, str]:
    """Retained V2 interpretation followed by a routing receipt and optional edit."""
    event_log = EventLog(settings.instance, settings.encryption_key)
    bus = EventBus(event_log, settings.instance.bus_checkpoint_path)
    writer = EventWriter(event_log, bus)
    common = {
        "event_at_ms": 1_700_000_000_000,
        "tenant_id": settings.instance.tenant_id,
        "occurred_at": AT,
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "sensitivity": "normal",
    }
    quote = (
        "Please complete the fixture authorization."
        if actionable
        else "The fixture authorization has been completed."
    )
    try:
        source_id = await writer.write(
            EventEnvelope(
                **common,
                type="messaging.received",
                schema_version=4,
                source_adapter="messaging-gmail-api",
                source_account_id="fixture-interpretation-account",
                idempotency_key="fixture-interpretation-source",
                payload={
                    "source_channel": "gmail",
                    "from_identifier": "fixture-sender@example.invalid",
                    "to_identifier": "fixture-member@example.invalid",
                    "thread_id": "fixture-interpretation-thread",
                    "subject": "Fixture authorization",
                    "body_text": quote
                    + (
                        "\nPlease archive the fixture receipt." if member_action else ""
                    ),
                    "received_at": AT,
                    "attachments": [],
                    "native_message_id": "fixture-interpretation-native",
                    "member_id": MEMBER,
                    "provider": "gmail",
                    "provider_account_id": "fixture-interpretation-account",
                    "workspace": "personal",
                    "to_identifiers": ["fixture-member@example.invalid"],
                    "cc_identifiers": [],
                    "bcc_identifiers": [],
                    "reply_to_identifier": None,
                    "rfc_message_id": "<fixture-interpretation@example.invalid>",
                    "references": [],
                    "in_reply_to": None,
                    "provider_labels": ["INBOX"],
                    "unread": True,
                    "archived": False,
                    "starred": False,
                    "important": False,
                },
            )
        )
        source = await event_log.get(source_id)
        assert source is not None
        pack = await build_message_context_pack(source, event_log)
        identity = hashlib.sha256(
            f"{pack.thread_key}|task|{quote}".encode()
        ).hexdigest()[:16]
        proposal_event_id = await writer.write(
            EventEnvelope(
                **common,
                type="triage.item.proposed",
                schema_version=4,
                source_adapter="deterministic_candidate_eligibility",
                source_account_id="pipeline",
                idempotency_key=PROPOSAL,
                payload={
                    "proposal_id": PROPOSAL,
                    "target_kind": "task",
                    "owner_member_id": MEMBER,
                    "title": quote,
                    "candidate": {"description": quote},
                    "route": {"member_id": MEMBER},
                    "source_event_id": source_id,
                    "candidate_key": f"legacy-context-{identity}",
                    "eligibility_rule_ids": ["exact_source_quote_grounded_v1"],
                    "source_occurred_at": AT,
                    "interpretation": {
                        "contract": "triage-context-interpretation-v2",
                        # Deliberately identical source fingerprint. V23 must
                        # still replace an obsolete semantic assessment.
                        "context_fingerprint": pack.fingerprint,
                        "thread_key": pack.thread_key,
                        "anchor_event_id": source_id,
                        "compact_summary": quote,
                        "thread_state": "waiting_on_member",
                        "action_owner": "member",
                        "lifecycle_state": "open",
                        "evidence_refs": [
                            {"source_event_id": source_id, "quote": quote}
                        ],
                        "context_source_event_ids": [source_id],
                        "context_sources": [
                            {
                                "source_event_id": source_id,
                                "relation": "thread",
                                "reason_codes": ["provider_thread_id"],
                                "relationship_source_event_ids": [],
                            }
                        ],
                    },
                },
            ),
            causation_id=source_id,
        )
        coordinate = source_coordinate_for_event(source)
        digest = hashlib.sha256(PROPOSAL.encode()).hexdigest()
        receipt_id = await writer.write(
            EventEnvelope(
                **common,
                type="triage.reconciliation.decided",
                schema_version=1,
                source_adapter="triage-reconciliation-v1",
                source_account_id="pipeline",
                idempotency_key="fixture-interpretation-admission",
                payload={
                    "reconciliation_id": f"recon_{digest[:32]}",
                    "proposal_id": PROPOSAL,
                    "primary_proposal_id": None,
                    "matter_id": f"ctx_reconciled_{digest[:32]}",
                    "owner_member_id": MEMBER,
                    "source_event_id": source_id,
                    "evidence_source_event_ids": [source_id],
                    "member_authored_text_digest": None,
                    "relationships": [],
                    "decision": "admit",
                    "decision_revision": 1,
                    "supersedes_reconciliation_event_id": None,
                    "policy": {
                        "policy_key": coordinate.key,
                        "policy_revision": 0,
                        "policy_origin": "recommended_default",
                        "triage_route": "review",
                        "alert_route": "none",
                        "context_route": "metadata_only",
                        "reason_codes": ["fixture_review_policy"],
                    },
                    "source_coordinate": coordinate.model_dump(mode="json"),
                    "freshness": "current",
                    "priority_rank": 60,
                    "priority_basis": ["standard_actionable_candidate"],
                    "reason_codes": ["fixture_prior_admission"],
                    "decided_at": AT,
                    "admission_active": True,
                    "raw_cross_member_join": False,
                    "weak_similarity_changed_identity": False,
                    "weak_similarity_transferred_ownership": False,
                    "weak_similarity_authorized_action": False,
                    "provider_effects": False,
                    "llm_authority": False,
                },
            ),
            causation_id=proposal_event_id,
        )
        member_event_id = None
        if member_action:
            action_payload = (
                {
                    "proposal_id": PROPOSAL,
                    "enriched_at": AT,
                    "enriched_by_member_id": MEMBER,
                    "title": "Member retained this obligation",
                }
                if member_action == "enriched"
                else {
                    "proposal_id": PROPOSAL,
                    "action_id": "fixture-member-content-edit",
                    "member_id": MEMBER,
                    "action": "correct_extraction",
                    "action_revision": 1,
                    "expected_last_event_id": receipt_id,
                    "corrected_title": "Member retained this obligation",
                    "managed_at": AT,
                }
            )
            member_event_id = await writer.write(
                EventEnvelope(
                    **common,
                    type=f"triage.item.{member_action}",
                    schema_version=1,
                    source_adapter="member-triage",
                    source_account_id=MEMBER,
                    idempotency_key="fixture-interpretation-member-edit",
                    payload=action_payload,
                ),
                # The member acted on the revision shown after reconciliation.
                # Original-proposal descendant scans cannot protect this edit;
                # projection content revision must do so.
                causation_id=receipt_id,
            )
        return (
            source_id,
            proposal_event_id,
            receipt_id,
            member_event_id,
            pack.fingerprint,
        )
    finally:
        await bus.stop()
        await event_log.close()


@pytest.mark.parametrize(
    ("actionable", "member_action"),
    [(True, None), (False, None), (True, "enriched"), (True, "managed")],
)
def test_active_replay_replaces_same_source_old_contract_and_preserves_member_revision(
    tmp_path: Path, actionable: bool, member_action: str | None
) -> None:
    instance = tmp_path / "interpretation-replay"
    instance.mkdir()
    settings = RuntimeSettings.credential_free(
        instance,
        encryption_key=b"i" * 32,
        repository_root=ROOT,
        member_identities={"interpretation.ts.net": MEMBER},
        member_roles={MEMBER: "principal"},
        deterministic_openclaw_fixture=True,
        fixture_member_id=MEMBER,
        reconciliation_admission_enabled=True,
        human_triage_management_enabled=True,
        legacy_personal_obligation_compatibility=True,
    )
    source_id, prior_id, receipt_id, member_id, fingerprint = asyncio.run(
        _preload(settings, actionable=actionable, member_action=member_action)
    )
    with TestClient(create_application(settings)) as client:
        assert client.portal is not None
        runtime = client.app.state.runtime
        client.portal.call(
            runtime.wait_for_subscriber, "pipeline:pipeline:noise_filtering", source_id
        )
        proposals = client.portal.call(_events, runtime, "triage.item.proposed")
        replacements = [p for p in proposals if p["payload"]["proposal_id"] != PROPOSAL]
        assert len(replacements) == int(actionable) + int(member_action is not None)
        supersessions = client.portal.call(_events, runtime, "triage.item.superseded")
        # Scoped member-decision lookup now avoids even a redundant retirement
        # attempt. Untouched interpretations still require the exact receipt;
        # projection and sibling protections below remain independently checked.
        assert len(supersessions) == int(member_action is None)
        if supersessions:
            retirement = supersessions[0]
            assert retirement["schema_version"] == 4
            assert (
                retirement["payload"]["expected_content_revision_event_id"] == prior_id
            )
            assert prior_id < receipt_id < retirement["event_id"]
        for proposal in proposals:
            client.portal.call(
                runtime.wait_for_subscriber,
                "triage-reconciliation:admission",
                proposal["event_id"],
            )
        decisions = client.portal.call(
            _events, runtime, "triage.reconciliation.decided"
        )
        # Other pipelines may append events the triage projection does not
        # subscribe to. Await the actual triage inputs after admission settles.
        latest = max(
            event["event_id"] for event in [*proposals, *supersessions, *decisions]
        )
        client.portal.call(runtime.wait_for_projection, "triage", latest)
        triage = runtime.projections.connection("triage")
        prior = dict(
            triage.execute(
                "SELECT * FROM triage_items WHERE proposal_id=?", (PROPOSAL,)
            ).fetchone()
        )
        prior_diagnostic = {
            key: prior[key]
            for key in (
                "state",
                "resolved_by",
                "resolution_reason",
                "admission_decision",
                "proposal_event_id",
                "content_revision_event_id",
                "reconciliation_event_id",
            )
        }
        if member_action:
            assert prior["state"] == "pending_review", prior_diagnostic
            assert prior["title"] == "Member retained this obligation"
            assert prior["content_revision_event_id"] == member_id
            assert prior["admission_decision"] in {
                "admit",
                "unresolved",
                "conflict_review",
            }, prior_diagnostic
        else:
            assert prior["state"] == "dismissed"
            assert prior["resolved_by"] == "system:source_changed"
            assert prior["resolution_reason"] == "eligibility_rule_replaced"
            assert prior["content_revision_event_id"] == prior_id
        if replacements:
            replacement = next(
                proposal
                for proposal in replacements
                if "complete the fixture authorization"
                in proposal["payload"]["title"].lower()
            )
            assert replacement["payload"]["candidate_key"] != prior["candidate_key"]
            assert replacement["schema_version"] == 5
            interpretation = replacement["payload"]["interpretation"]
            assert interpretation["contract"] == "triage-context-interpretation-v3"
            assert interpretation["context_fingerprint"] == fingerprint
            assert (
                interpretation["obligation_assessment"]["independent_obligation"]
                is True
            )
            fresh = triage.execute(
                "SELECT * FROM triage_items WHERE proposal_id=?",
                (replacement["payload"]["proposal_id"],),
            ).fetchone()
            if member_action:
                assert fresh is None
                # The edited action is protected, not its entire source. A
                # distinct exact sibling from the same message remains usable.
                sibling = next(
                    proposal for proposal in replacements if proposal != replacement
                )
                sibling_row = triage.execute(
                    "SELECT * FROM triage_items WHERE proposal_id=?",
                    (sibling["payload"]["proposal_id"],),
                ).fetchone()
                assert sibling_row["state"] == "pending_review"
                assert sibling_row["admission_decision"] == "admit"
                assert sibling_row["duplicate_of_proposal_id"] is None
            else:
                assert fresh["state"] == "pending_review"
                assert fresh["admission_decision"] == "admit"
                assert fresh["duplicate_of_proposal_id"] is None
        page = client.get(
            "/api/member/v1/surfaces/triage?triage_limit=100", headers=HEADERS
        )
        assert page.status_code == 200, page.text
        client.portal.call(runtime.projections.rebuild, "triage")
        rebuilt = runtime.projections.connection("triage")
        after = dict(
            rebuilt.execute(
                "SELECT * FROM triage_items WHERE proposal_id=?", (PROPOSAL,)
            ).fetchone()
        )
        for key in (
            "state",
            "title",
            "content_revision_event_id",
            "proposal_action_fingerprint",
            "resolved_by",
            "resolution_reason",
        ):
            assert after[key] == prior[key]
        if member_action:
            # A verified terminal receipt still closes the protected local
            # interpretation; protection only defeats attention rerouting.
            preceding = max(
                (
                    event
                    for event in decisions
                    if event["payload"]["proposal_id"] == PROPOSAL
                ),
                key=lambda event: event["event_id"],
            )
            terminal_payload = {
                **preceding["payload"],
                "decision": "already_satisfied",
                "decision_revision": preceding["payload"]["decision_revision"] + 1,
                "supersedes_reconciliation_event_id": preceding["event_id"],
                "reason_codes": ["fixture_typed_completion"],
            }
            terminal_id = client.portal.call(
                runtime.events.write,
                EventEnvelope(
                    event_at_ms=0,
                    tenant_id=settings.instance.tenant_id,
                    type="triage.reconciliation.decided",
                    schema_version=1,
                    occurred_at=AT,
                    source_adapter="cross-source-reconciliation",
                    source_account_id="internal",
                    owner_scope=f"private:{MEMBER}",
                    visibility_scope=f"private:{MEMBER}",
                    sensitivity="normal",
                    idempotency_key="fixture-member-edited-completion",
                    payload=terminal_payload,
                ),
            )
            client.portal.call(runtime.wait_for_projection, "triage", terminal_id)
            terminal = (
                runtime.projections.connection("triage")
                .execute(
                    "SELECT state,resolution_reason,content_revision_event_id FROM triage_items WHERE proposal_id=?",
                    (PROPOSAL,),
                )
                .fetchone()
            )
            assert terminal["state"] == "dismissed"
            assert terminal["resolution_reason"] == "reconciliation:already_satisfied"
            assert terminal["content_revision_event_id"] == member_id
        assert client.portal.call(_events, runtime, "task.created") == []
        assert (
            client.portal.call(_events, runtime, "external_sync.write_requested") == []
        )
