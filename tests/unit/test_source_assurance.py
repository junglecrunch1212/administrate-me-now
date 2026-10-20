"""Source assurance distinguishes empty, pending, and silently missing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlcipher3

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.source_assurance import (
    audit_source_assurance,
    legacy_effect_event_fingerprint,
    load_source_obligations,
)


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
async def test_missing_summary_accounts_for_rows_beyond_detail_display_limit(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "bounded-missing-detail.db", b"m" * 32)
    try:
        source_ids: list[str] = []
        for index in range(21):
            message = _message("tenant-missing-summary")
            payload = {
                **message.payload,
                "native_message_id": f"source-assurance-message-{index:02d}",
            }
            source_ids.append(
                await log.append(
                    message.model_copy(
                        update={
                            "event_at_ms": message.event_at_ms + index,
                            "idempotency_key": (
                                f"source-assurance-message-{index:02d}"
                            ),
                            "payload": payload,
                        }
                    )
                )
            )

        audit = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities={
                "pipeline:pipeline:noise_filtering": {
                    "state": "degraded",
                    "details": {"checkpoint_event_id": source_ids[0]},
                }
            },
        )

        assert audit["counts"]["missing"] == 21
        assert len(audit["missing"]) == 20
        assert audit["missing_summary"] == {
            "contract": "source-assurance-missing-summary-v1",
            "total_count": 21,
            "detail_limit": 20,
            "groups": [
                {
                    "event_type": "messaging.received",
                    "mode": "candidate_assessment",
                    "reason": "assessment_event_absent_after_checkpoint_or_grace",
                    "count": 21,
                    "first_event_id": source_ids[0],
                    "last_event_id": source_ids[-1],
                }
            ],
            "content_exposed": False,
        }
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_event_log_read_only_connection_reads_but_cannot_append(
    tmp_path: Path,
) -> None:
    path = tmp_path / "read-only-event-log.db"
    key = b"r" * 32
    writable = EventLog(path, key)
    try:
        event_id = await writable.append(_message("tenant-read-only"))
    finally:
        await writable.close()

    read_only = EventLog(path, key, read_only=True)
    try:
        assert await read_only.latest_event_id() == event_id
        with pytest.raises(
            sqlcipher3.DatabaseError, match="readonly|read-only|query only"
        ):
            await read_only.append(_message("tenant-read-only"))
    finally:
        await read_only.close()


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


async def _append_write_pair(
    log: EventLog,
    *,
    suffix: str,
    version: int,
    provider_called: bool | None = None,
    suppressed: bool = True,
    mismatched_correlation: bool = False,
) -> tuple[str, str]:
    fingerprint = "a" * 64
    request_payload: dict[str, object] = {
        "command_id": f"command-{suffix}",
        "member_id": "member-fixture",
        "provider": "google_tasks",
        "provider_account_id": "account-fixture",
        "operation": "task.create",
        "target_external_id": None,
        "expected_provider_revision": None,
        "payload": {"fixture": True},
        "requested_at": "2026-08-07T12:00:00Z",
    }
    if version == 2:
        request_payload.update(
            {
                "domain_approval_event_id": f"domain-{suffix}",
                "openclaw_approval_event_id": f"openclaw-{suffix}",
                "request_fingerprint": fingerprint,
                "privacy_guard_version": "fixture-v1",
                "origin_proposal_id": None,
            }
        )
    request_id = await log.append(
        EventEnvelope(
            event_at_ms=1_786_100_000_000,
            tenant_id="tenant-effect-accounting",
            type="external_sync.write_requested",
            schema_version=version,
            occurred_at="2026-08-07T12:00:00Z",
            source_adapter="fixture-request",
            source_account_id="account-fixture",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key=f"request-{suffix}",
            payload=request_payload,
        )
    )
    result_payload: dict[str, object] = {
        "command_id": f"command-{suffix}",
        "member_id": "member-fixture",
        "provider": "google_tasks",
        "operation": "task.create",
        "external_id": None,
        "provider_revision": None,
        "result": {"reason": "observation_mode_active"},
        "completed_at": "2026-08-07T12:00:01Z",
        "suppressed": suppressed,
    }
    if version == 2:
        result_payload.update(
            {
                "command_event_id": request_id,
                "domain_approval_event_id": f"domain-{suffix}",
                "openclaw_approval_event_id": f"openclaw-{suffix}",
                "provider_account_id": "account-fixture",
                "request_fingerprint": (
                    "b" * 64 if mismatched_correlation else fingerprint
                ),
                "provider_called": provider_called,
                "origin_proposal_id": None,
            }
        )
    result_id = await log.append(
        EventEnvelope(
            event_at_ms=1_786_100_000_001,
            tenant_id="tenant-effect-accounting",
            type="external_sync.write_completed",
            schema_version=version,
            occurred_at="2026-08-07T12:00:01Z",
            source_adapter="fixture-observation-gate",
            source_account_id="account-fixture",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            causation_id=request_id,
            idempotency_key=f"result-{suffix}",
            payload=result_payload,
        )
    )
    return request_id, result_id


@pytest.mark.asyncio
async def test_provider_effect_accounting_proves_v2_observation_suppression(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "effect-accounting.db", b"e" * 32)
    try:
        await _append_write_pair(
            log, suffix="suppressed", version=2, provider_called=False
        )
        audit = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities={},
            provider_effect_window_started_at="2026-08-07T11:00:00Z",
        )
        assert audit["provider_effects"] == {
            "contract": "provider-effect-accounting-v1",
            "state": "healthy",
            "counts": {
                "write_requests": 1,
                "terminal_results": 1,
                "provider_called_true": 0,
                "provider_called_false": 1,
                "provider_called_unknown": 0,
                "suppressed_true": 1,
                "suppressed_false_or_unknown": 0,
                "missing_terminal": 0,
                "duplicate_terminal": 0,
                "approval_correlation_missing": 0,
                "duplicate_request": 0,
                "malformed_request": 0,
                "orphan_terminal": 0,
                "legacy_reconciled_terminal": 0,
                "invalid_legacy_reconciliation": 0,
                "duplicate_legacy_reconciliation": 0,
            },
            "zero_effect_state": "healthy",
            "observation_window": {
                "state": "healthy",
                "started_at_configured": True,
                "started_at": "2026-08-07T11:00:00+00:00",
                "ended_at": None,
                "counts": {
                    "write_requests": 1,
                    "terminal_results": 1,
                    "provider_called_true": 0,
                    "provider_called_false": 1,
                    "provider_called_unknown": 0,
                    "suppressed_true": 1,
                    "suppressed_false_or_unknown": 0,
                    "missing_terminal": 0,
                    "duplicate_terminal": 0,
                    "approval_correlation_missing": 0,
                    "duplicate_request": 0,
                    "malformed_request": 0,
                    "orphan_terminal": 0,
                    "legacy_reconciled_terminal": 0,
                    "invalid_legacy_reconciliation": 0,
                    "duplicate_legacy_reconciliation": 0,
                },
                "content_exposed": False,
            },
            "content_exposed": False,
        }
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_authorized_live_effect_preserves_integrity_and_new_zero_effect_window(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "live-effect.db", b"p" * 32)
    try:
        await _append_write_pair(
            log,
            suffix="live",
            version=2,
            provider_called=True,
            suppressed=False,
        )
        audit = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities={},
            provider_effect_window_started_at=(
                datetime.now(timezone.utc) + timedelta(seconds=1)
            ).isoformat(),
        )
        effects = audit["provider_effects"]
        assert effects["state"] == "healthy"
        assert effects["zero_effect_state"] == "nonzero"
        assert effects["counts"]["provider_called_true"] == 1
        assert effects["observation_window"]["state"] == "healthy"
        assert effects["observation_window"]["counts"]["provider_called_true"] == 0
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_legacy_or_mismatched_effect_evidence_blocks_zero_effect_claim(
    tmp_path: Path,
) -> None:
    legacy = EventLog(tmp_path / "legacy-effect.db", b"l" * 32)
    try:
        await _append_write_pair(legacy, suffix="legacy", version=1)
        legacy_audit = await audit_source_assurance(
            legacy, repository_root=REPO_ROOT, capabilities={}
        )
        assert legacy_audit["state"] == "degraded"
        assert legacy_audit["provider_effects"]["state"] == "degraded"
        assert (
            legacy_audit["provider_effects"]["counts"]["provider_called_unknown"] == 1
        )
    finally:
        await legacy.close()

    mismatch = EventLog(tmp_path / "mismatch-effect.db", b"x" * 32)
    try:
        await _append_write_pair(
            mismatch,
            suffix="mismatch",
            version=2,
            provider_called=False,
            mismatched_correlation=True,
        )
        mismatch_audit = await audit_source_assurance(
            mismatch, repository_root=REPO_ROOT, capabilities={}
        )
        assert mismatch_audit["provider_effects"]["state"] == "degraded"
        assert (
            mismatch_audit["provider_effects"]["counts"]["approval_correlation_missing"]
            == 1
        )
    finally:
        await mismatch.close()


@pytest.mark.parametrize(
    ("suppressed", "provider_called"),
    [(False, True), (True, False)],
)
@pytest.mark.asyncio
async def test_additive_legacy_effect_reconciliation_restores_integrity(
    tmp_path: Path,
    suppressed: bool,
    provider_called: bool,
) -> None:
    log = EventLog(tmp_path / "legacy-reconciled.db", b"r" * 32)
    try:
        suffix = f"legacy-reconciled-{'suppressed' if suppressed else 'called'}"
        request_id, terminal_id = await _append_write_pair(
            log,
            suffix=suffix,
            version=1,
            suppressed=suppressed,
        )
        request = await log.get(request_id)
        terminal = await log.get(terminal_id)
        assert request is not None and terminal is not None
        await log.append(
            EventEnvelope(
                event_at_ms=1_786_100_000_002,
                tenant_id="tenant-effect-accounting",
                type="external_sync.legacy_effect_reconciled",
                schema_version=1,
                occurred_at="2026-08-07T12:00:02Z",
                source_adapter="migration:legacy-provider-effect",
                source_account_id="account-fixture",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="privileged",
                correlation_id=f"command-{suffix}",
                causation_id=terminal_id,
                idempotency_key="legacy-effect-reconciliation-fixture",
                payload={
                    "request_event_id": request_id,
                    "terminal_event_id": terminal_id,
                    "command_id": f"command-{suffix}",
                    "provider_account_id": "account-fixture",
                    "provider_called": provider_called,
                    "suppressed": suppressed,
                    "legacy_approval_state": "pre_governance_absent",
                    "reconciliation_basis": "v1_completed_terminal_evidence",
                    "request_fingerprint": legacy_effect_event_fingerprint(request),
                    "terminal_fingerprint": legacy_effect_event_fingerprint(terminal),
                    "reconciled_at": "2026-08-07T12:00:02Z",
                },
            )
        )
        audit = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities={},
            provider_effect_window_started_at=(
                datetime.now(timezone.utc) + timedelta(seconds=1)
            ).isoformat(),
        )
        effects = audit["provider_effects"]
        assert audit["state"] == "healthy"
        assert effects["state"] == "healthy"
        assert effects["counts"]["provider_called_true"] == int(provider_called)
        assert effects["counts"]["provider_called_false"] == int(not provider_called)
        assert effects["counts"]["suppressed_true"] == int(suppressed)
        assert effects["counts"]["provider_called_unknown"] == 0
        assert effects["counts"]["approval_correlation_missing"] == 0
        assert effects["counts"]["legacy_reconciled_terminal"] == 1
        assert effects["observation_window"]["state"] == "healthy"
    finally:
        await log.close()
