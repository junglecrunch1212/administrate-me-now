"""Source assurance distinguishes empty, pending, and silently missing."""

from __future__ import annotations

from pathlib import Path

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.source_assurance import audit_source_assurance, load_source_obligations


REPO_ROOT = Path(__file__).resolve().parents[2]


def _message(tenant_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_786_100_000_000,
        tenant_id=tenant_id,
        type="messaging.received",
        schema_version=3,
        occurred_at="2026-08-07T12:00:00Z",
        source_adapter="gmail",
        source_account_id="fixture-account",
        owner_scope="private:member-fixture",
        visibility_scope="private:member-fixture",
        sensitivity="normal",
        idempotency_key="source-assurance-message",
        payload={
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "member_id": "member-fixture",
            "source_channel": "gmail",
            "native_message_id": "source-assurance-message",
            "thread_id": "source-assurance-thread",
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "workspace": "personal",
            "subject": "A harmless update",
            "body_text": "Thanks for the update.",
            "received_at": "2026-08-07T12:00:00Z",
            "provider_labels": ["INBOX"],
            "attachments": [],
        },
    )


@pytest.mark.asyncio
async def test_checkpointed_source_without_assessment_is_missing_not_empty(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", b"s" * 32)
    try:
        source_id = await log.append(_message("tenant-source-assurance"))
        capabilities = {
            "pipeline:pipeline:noise_filtering": {
                "state": "healthy",
                "details": {"checkpoint_event_id": source_id},
            }
        }
        missing = await audit_source_assurance(
            log, repository_root=REPO_ROOT, capabilities=capabilities
        )
        assert missing["state"] == "degraded"
        assert missing["counts"]["missing"] == 1
        assert missing["counts"]["no_candidate"] == 0

        assessment_id = await log.append(
            EventEnvelope(
                event_at_ms=1_786_100_000_001,
                tenant_id="tenant-source-assurance",
                type="triage.intent.features_extracted",
                schema_version=2,
                occurred_at="2026-08-07T12:00:01Z",
                source_adapter="pipeline:noise_filtering",
                source_account_id="pipeline",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                causation_id=source_id,
                idempotency_key="source-assurance-assessment",
                payload={
                    "intent_id": f"source:{source_id}",
                    "source_event_id": source_id,
                    "skill_call_event_id": None,
                    "extractor_id": "fixture-source-assessor",
                    "language": "en",
                    "candidates": [],
                    "extraction_state": "complete",
                    "extracted_at": "2026-08-07T12:00:01Z",
                    "assessment_outcome": "no_candidate",
                    "reason_codes": ["language_assessment_completed"],
                },
            )
        )
        assert assessment_id > source_id
        accounted = await audit_source_assurance(
            log, repository_root=REPO_ROOT, capabilities=capabilities
        )
        assert accounted["state"] == "healthy"
        assert accounted["counts"]["missing"] == 0
        assert accounted["counts"]["no_candidate"] == 1
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_audit_uses_a_fixed_event_watermark(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "watermark.db", b"w" * 32)
    try:
        source_id = await log.append(_message("tenant-watermark"))
        await log.append(
            EventEnvelope(
                event_at_ms=1_786_100_000_001,
                tenant_id="tenant-watermark",
                type="triage.intent.features_extracted",
                schema_version=2,
                occurred_at="2026-08-07T12:00:01Z",
                source_adapter="pipeline:noise_filtering",
                source_account_id="pipeline",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                causation_id=source_id,
                idempotency_key="watermark-assessment",
                payload={
                    "intent_id": f"source:{source_id}",
                    "source_event_id": source_id,
                    "skill_call_event_id": None,
                    "extractor_id": "fixture-source-assessor",
                    "language": "en",
                    "candidates": [],
                    "extraction_state": "complete",
                    "extracted_at": "2026-08-07T12:00:01Z",
                    "assessment_outcome": "no_candidate",
                    "reason_codes": ["language_assessment_completed"],
                },
            )
        )
        capabilities = {
            "pipeline:pipeline:noise_filtering": {
                "state": "healthy",
                "details": {"checkpoint_event_id": source_id},
            }
        }

        bounded = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities=capabilities,
            through_event_id=source_id,
        )
        current = await audit_source_assurance(
            log, repository_root=REPO_ROOT, capabilities=capabilities
        )

        assert bounded["watermark_event_id"] == source_id
        assert bounded["counts"]["missing"] == 1
        assert current["counts"]["no_candidate"] == 1
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_legacy_unavailable_assessment_is_failed_not_no_candidate(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "legacy-failure.db", b"f" * 32)
    try:
        source_id = await log.append(_message("tenant-source-failure"))
        await log.append(
            EventEnvelope(
                event_at_ms=1_786_100_000_001,
                tenant_id="tenant-source-failure",
                type="triage.intent.features_extracted",
                schema_version=1,
                occurred_at="2026-08-07T12:00:01Z",
                source_adapter="pipeline:noise_filtering",
                source_account_id="pipeline",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                causation_id=source_id,
                idempotency_key="source-assurance-legacy-failure",
                payload={
                    "intent_id": f"source:{source_id}",
                    "source_event_id": source_id,
                    "skill_call_event_id": None,
                    "extractor_id": "legacy-assessor",
                    "language": None,
                    "candidates": [],
                    "extraction_state": "unavailable",
                    "extracted_at": "2026-08-07T12:00:01Z",
                },
            )
        )
        audit = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities={
                "pipeline:pipeline:noise_filtering": {
                    "state": "healthy",
                    "details": {"checkpoint_event_id": source_id},
                }
            },
        )
        assert audit["state"] == "degraded"
        assert audit["counts"]["failed"] == 1
        assert audit["counts"]["no_candidate"] == 0
        assert audit["failed"][0]["reason"] == "assessment_failed"
    finally:
        await log.close()


def test_catalog_covers_all_five_adapter_categories_and_member_capture() -> None:
    obligations = load_source_obligations(REPO_ROOT)
    modes = {obligation.mode for obligation in obligations}
    event_types = {obligation.event_type for obligation in obligations}
    assert {
        "candidate_assessment",
        "canonical_projection",
        "canonical_observation",
        "terminal_action",
        "action_request_terminal",
    } <= modes
    assert {
        "messaging.received",
        "calendar.event_added",
        "money_flow.provider_observed",
        "member_knowledge.note_added",
        "action.executed",
        "triage.intent.submitted",
    } <= event_types
