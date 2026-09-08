"""Synthetic source-neutral evidence for the credential-free browser fixture."""

from __future__ import annotations

import hashlib
from typing import Any

from adminme.events.envelope import EventEnvelope
from adminme.source_policy import SourceCoordinate, source_coordinate_for_event


async def seed_attention_sources(
    runtime: Any, member_id: str, now: Any, *, future: bool = False
) -> dict[str, Any]:
    """Exercise real log, projection, Settings, and learning routes; no provider."""
    cases = (
        [(6, "survey", False, False)]
        if future
        else [
            (1, "survey", False, False),
            (2, "preparation", True, False),
            (3, "survey", False, True),
            (4, "receipt", False, False),
        ]
    )
    proposal_ids = []
    for index, purpose, independent, protected in cases:
        coordinate = SourceCoordinate(
            tenant_id=runtime.settings.instance.tenant_id,
            owner_principal_id=member_id,
            owner_kind="member",
            context_member_id=member_id,
            adapter_category="communication",
            adapter_kind="fixture-future-messages",
            adapter_instance_id="visual-attention-messages",
            provider="fixture",
            provider_account_id="visual-attention-account",
            workspace="personal",
            source_kind="messaging.received",
            source_class="passive_communication",
            trusted_route=f"member_private:{member_id}",
            native_container_kind="conversation",
            native_container_id="visual-attention-conversation",
        )
        clause = f"Fixture {purpose} source {index}."
        common = {
            "event_at_ms": int(now.timestamp() * 1000),
            "tenant_id": runtime.settings.instance.tenant_id,
            "occurred_at": now.isoformat(),
            "source_account_id": coordinate.provider_account_id,
            "owner_scope": f"private:{member_id}",
            "visibility_scope": f"private:{member_id}",
            "sensitivity": "normal",
            "actor_identity": member_id,
        }
        source = EventEnvelope(
            **common,
            type="messaging.received",
            schema_version=3,
            source_adapter="fixture-future-messages",
            idempotency_key=f"visual-attention-source:{index}",
            payload={
                "provider": "fixture",
                "provider_account_id": coordinate.provider_account_id,
                "member_id": member_id,
                "source_channel": "sms",
                "workspace": "personal",
                "native_message_id": f"visual-attention-message-{index}",
                "thread_id": coordinate.native_container_id,
                "from_identifier": "fixture-sender@example.invalid",
                "to_identifier": "fixture-member@example.invalid",
                "subject": clause,
                "body_text": clause,
                "received_at": now.isoformat(),
                "attachments": [],
            },
        )
        source_id = await runtime.events.write(source)
        coordinate = source_coordinate_for_event(source.model_dump(mode="json"))
        proposal_id = f"visual-attention-proposal-{index}"
        digest = hashlib.sha256(proposal_id.encode()).hexdigest()
        proposal = EventEnvelope(
            **common,
            type="triage.item.proposed",
            schema_version=5,
            source_adapter="deterministic_candidate_eligibility",
            idempotency_key=f"visual-attention-proposal:{index}",
            payload={
                "proposal_id": proposal_id,
                "target_kind": "task",
                "owner_member_id": member_id,
                "title": clause,
                "candidate": {"sender_digest": "a" * 64},
                "source_event_id": source_id,
                "route": {"member_id": member_id},
                "eligibility_rule_ids": ["visual-attention-fixture"],
                "interpretation": {
                    "contract": "triage-context-interpretation-v3",
                    "context_fingerprint": digest,
                    "thread_key": digest,
                    "anchor_event_id": source_id,
                    "compact_summary": clause,
                    "thread_state": "new",
                    "action_owner": "member",
                    "lifecycle_state": "open",
                    "evidence_refs": [{"source_event_id": source_id, "quote": clause}],
                    "context_source_event_ids": [source_id],
                    "context_sources": [
                        {
                            "source_event_id": source_id,
                            "relation": "thread",
                            "reason_codes": ["provider_thread_id"],
                            "relationship_source_event_ids": [],
                        }
                    ],
                    "assessment_state": "verified",
                    "obligation_assessment": {
                        "purpose": purpose,
                        "actionability": "required" if independent else "informational",
                        "condition_state": "not_applicable",
                        "independent_obligation": independent,
                        "evidence": [
                            {
                                "source_event_id": source_id,
                                "quote": clause,
                                "role": "action",
                            }
                        ],
                    },
                },
            },
        )
        receipt = EventEnvelope(
            **common,
            type="triage.reconciliation.decided",
            schema_version=1,
            source_adapter="triage-reconciliation-v1",
            idempotency_key=f"visual-attention-receipt:{index}",
            payload={
                "reconciliation_id": f"recon_{digest[:32]}",
                "proposal_id": proposal_id,
                "primary_proposal_id": None,
                "matter_id": f"ctx_reconciled_{digest[:32]}",
                "owner_member_id": member_id,
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
                    "reason_codes": [],
                },
                "source_coordinate": coordinate.model_dump(mode="json"),
                "freshness": "current",
                "priority_rank": 60,
                "priority_basis": ["explicit_member_instruction"]
                if protected
                else ["standard_actionable_candidate"],
                "reason_codes": ["fixture_admission"],
                "decided_at": now.isoformat(),
                "admission_active": True,
                "raw_cross_member_join": False,
                "weak_similarity_changed_identity": False,
                "weak_similarity_transferred_ownership": False,
                "weak_similarity_authorized_action": False,
                "provider_effects": False,
                "llm_authority": False,
            },
        )
        event_ids = await runtime.events.write_batch([proposal, receipt])
        await runtime.wait_for_projection("triage", event_ids[-1])
        proposal_ids.append(proposal_id)
    return {"proposal_ids": proposal_ids, "provider_effects": 0}
