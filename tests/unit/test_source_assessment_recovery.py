"""Append-only, bounded terminal source-assessment recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner import OpenClawUnreachable, SkillResult
from adminme.pipelines.base import PipelineContext
from adminme.pipelines.pack_loader import invalidate_cache, load_pipeline_pack
from adminme.source_assessment_recovery import (
    SourceAssessmentRecoveryBatch,
    SourceAssessmentRecoveryRefused,
    SourceAssessmentRecoveryTarget,
    build_recovery_request_envelopes,
    latest_source_assessment,
    validate_recovery_target,
)
from adminme.source_assurance import audit_source_assurance


REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "packs" / "pipelines" / "noise_filtering"
TENANT = "tenant-recovery"
MEMBER = "member-recovery"


@dataclass
class _Sink:
    log: EventLog
    fail_recovery_assessment_once: bool = False

    @property
    def tenant_id(self) -> str | None:
        return TENANT

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        if (
            self.fail_recovery_assessment_once
            and envelope.type == "triage.intent.features_extracted"
            and envelope.schema_version == 4
        ):
            self.fail_recovery_assessment_once = False
            raise RuntimeError("fixture crash before replacement assessment append")
        return await self.log.append(
            envelope,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def write_batch(
        self,
        envelopes: list[EventEnvelope],
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> list[str]:
        return await self.log.append_batch(
            envelopes,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


def _source(*, member: str = MEMBER, sensitivity: str = "normal") -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_786_400_000_000,
        tenant_id=TENANT,
        type="messaging.received",
        schema_version=3,
        occurred_at="2026-08-11T12:00:00Z",
        source_adapter="gmail",
        source_account_id="fixture-account",
        owner_scope=f"private:{member}",
        visibility_scope=f"private:{member}",
        sensitivity=sensitivity,  # type: ignore[arg-type]
        idempotency_key=f"recovery-source:{member}:{sensitivity}",
        payload={
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "member_id": member,
            "source_channel": "gmail",
            "native_message_id": f"recovery-message:{member}:{sensitivity}",
            "thread_id": "recovery-thread",
            "from_identifier": "sender@example.invalid",
            "to_identifier": "member@example.invalid",
            "workspace": "personal",
            "subject": "Fixture recovery",
            "body_text": "A private fixture message with no actionable request.",
            "received_at": "2026-08-11T12:00:00Z",
            "provider_labels": ["INBOX"],
            "attachments": [],
        },
    )


async def _failed_assessment(
    log: EventLog,
    source_event_id: str,
    *,
    reason: str = "language_seam_OpenClawUnreachable",
) -> str:
    return await log.append(
        EventEnvelope(
            event_at_ms=1_786_400_000_001,
            tenant_id=TENANT,
            type="triage.intent.features_extracted",
            schema_version=3,
            occurred_at="2026-08-11T12:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope=f"private:{MEMBER}",
            visibility_scope=f"private:{MEMBER}",
            sensitivity="normal",
            idempotency_key=f"triage:message-features:v19:{source_event_id}",
            payload={
                "intent_id": f"source:{source_event_id}",
                "source_event_id": source_event_id,
                "skill_call_event_id": None,
                "extractor_id": "fixture-source-assessor",
                "language": None,
                "candidates": [],
                "extraction_state": "unavailable",
                "extracted_at": "2026-08-11T12:00:01Z",
                "assessment_outcome": "failed",
                "reason_codes": [reason, "no_eligible_candidate"],
            },
        ),
        causation_id=source_event_id,
    )


def _batch(
    source_id: str, failed_id: str, request_id: str
) -> SourceAssessmentRecoveryBatch:
    return SourceAssessmentRecoveryBatch(
        request_id=request_id,
        targets=[
            SourceAssessmentRecoveryTarget(
                source_event_id=source_id,
                failed_assessment_event_id=failed_id,
                expected_failure_reason="language_seam_OpenClawUnreachable",
            )
        ],
    )


def _context(
    log: EventLog, sink: _Sink, run_skill: Any, request_id: str
) -> PipelineContext:
    return PipelineContext(
        session=build_internal_session("pipeline_runner", "device", TENANT),
        event_log=log,
        event_sink=sink,
        run_skill_fn=run_skill,
        outbound_fn=lambda *args, **kwargs: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id=request_id,
        correlation_id="fixture-recovery",
    )


def _successful_result() -> SkillResult:
    return SkillResult(
        output={
            "classification": "personal",
            "confidence": 0.8,
            "reasons": ["fixture_completed"],
            "reply_signal": "unknown",
            "automated": False,
            "transactional_due": False,
            "security_code": False,
            "person_to_person": True,
            "deadline": None,
            "candidates": [],
        },
        openclaw_invocation_id="fixture-invocation",
        provider="openclaw-fixture",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        duration_ms=1,
    )


async def _append_request(
    log: EventLog, source_id: str, failed_id: str, request_id: str
) -> dict[str, Any]:
    [envelope] = await build_recovery_request_envelopes(
        log,
        _batch(source_id, failed_id, request_id),
        tenant_id=TENANT,
        requested_by_actor=MEMBER,
        authority="member_principal",
        required_owner_scope=f"private:{MEMBER}",
    )
    request_event_id = await log.append(envelope, correlation_id="fixture-recovery")
    request_event = await log.get(request_event_id)
    assert request_event is not None
    return request_event


@pytest.mark.asyncio
async def test_recovery_appends_newer_success_and_preserves_failed_history(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "events.db", b"r" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(log, source_id)
        request = await _append_request(log, source_id, failed_id, "request-success-1")
        calls = 0

        async def recovered(*_args: Any, **_kwargs: Any) -> SkillResult:
            nonlocal calls
            calls += 1
            return _successful_result()

        invalidate_cache()
        pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
        await pipeline.handle(
            request, _context(log, _Sink(log), recovered, request["event_id"])
        )
        latest = await latest_source_assessment(log, source_id)
        assert latest is not None
        assert latest["event_id"] > failed_id
        assert latest["schema_version"] == 4
        assert latest["payload"]["assessment_outcome"] == "no_candidate"
        assert latest["payload"]["supersedes_assessment_event_id"] == failed_id
        assert latest["payload"]["recovery_request_event_id"] == request["event_id"]
        related = await log.get_by_causation(source_id)
        assert any(event["event_id"] == failed_id for event in related)
        assert calls == 1

        audit = await audit_source_assurance(
            log,
            repository_root=REPO_ROOT,
            capabilities={
                "pipeline:pipeline:noise_filtering": {
                    "state": "healthy",
                    "details": {"checkpoint_event_id": request["event_id"]},
                }
            },
        )
        assert audit["state"] == "healthy"
        assert audit["counts"]["failed"] == 0
        assert audit["recovery"]["counts"]["succeeded"] == 1
        assert audit["content_exposed"] is False
        assert audit["recovery"]["content_exposed"] is False
        assert "private fixture message" not in str(audit).lower()
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_duplicate_and_restart_during_recovery_reuse_one_invocation(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "restart.db", b"t" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(log, source_id)
        request = await _append_request(log, source_id, failed_id, "request-restart-1")
        provider_calls = 0
        cached: dict[str, SkillResult] = {}

        async def durable_skill(
            _skill: str, _inputs: dict, skill_ctx: Any
        ) -> SkillResult:
            nonlocal provider_calls
            key = str(skill_ctx.invocation_idempotency_key)
            if key not in cached:
                provider_calls += 1
                cached[key] = _successful_result()
            return cached[key]

        invalidate_cache()
        first_pipeline = load_pipeline_pack(PACK_ROOT).instance
        with pytest.raises(RuntimeError, match="fixture crash"):
            await first_pipeline.handle(
                request,
                _context(
                    log,
                    _Sink(log, fail_recovery_assessment_once=True),
                    durable_skill,
                    request["event_id"],
                ),
            )

        invalidate_cache()
        restarted_pipeline = load_pipeline_pack(PACK_ROOT).instance
        await restarted_pipeline.handle(
            request,
            _context(log, _Sink(log), durable_skill, request["event_id"]),
        )
        await restarted_pipeline.handle(
            request,
            _context(log, _Sink(log), durable_skill, request["event_id"]),
        )
        assert provider_calls == 1
        related = await log.get_by_causation(source_id)
        replacements = [
            event
            for event in related
            if event["type"] == "triage.intent.features_extracted"
            and event["schema_version"] == 4
        ]
        assert len(replacements) == 1
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_failed_recovery_is_truthful_bounded_and_creates_no_domain_object(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "bounded.db", b"u" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(log, source_id)
        request = await _append_request(log, source_id, failed_id, "request-failed-1")
        invocation_keys: list[str] = []

        async def unavailable(
            _skill: str, _inputs: dict, skill_ctx: Any
        ) -> SkillResult:
            invocation_keys.append(str(skill_ctx.invocation_idempotency_key))
            raise OpenClawUnreachable("fixture gateway unavailable")

        invalidate_cache()
        pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
        globals_map = pipeline._run_skill_with_transient_recovery.__func__.__globals__
        prior_delays = globals_map["_TRANSIENT_RECOVERY_DELAYS_SECONDS"]
        globals_map["_TRANSIENT_RECOVERY_DELAYS_SECONDS"] = (0.0, 0.0)
        try:
            await pipeline.handle(
                request,
                _context(log, _Sink(log), unavailable, request["event_id"]),
            )
            await pipeline.handle(
                request,
                _context(log, _Sink(log), unavailable, request["event_id"]),
            )
        finally:
            globals_map["_TRANSIENT_RECOVERY_DELAYS_SECONDS"] = prior_delays

        assert len(invocation_keys) == 3
        assert invocation_keys[1] == f"{invocation_keys[0]}:recovery-1"
        assert invocation_keys[2] == f"{invocation_keys[0]}:recovery-2"
        latest = await latest_source_assessment(log, source_id)
        assert latest is not None
        assert latest["payload"]["assessment_outcome"] == "failed"
        assert latest["payload"]["reason_codes"][0] == (
            "language_seam_OpenClawUnreachable"
        )
        all_events = [event async for event in log.read_since()]
        assert not any(
            event["type"].startswith(("task.", "commitment.", "external_sync."))
            for event in all_events
        )
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_selection_rejects_nonretryable_privileged_and_cross_member_sources(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "selection.db", b"v" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(
            log, source_id, reason="language_seam_SkillInputInvalid"
        )
        target = SourceAssessmentRecoveryTarget(
            source_event_id=source_id,
            failed_assessment_event_id=failed_id,
            expected_failure_reason="language_seam_SkillInputInvalid",
        )
        with pytest.raises(SourceAssessmentRecoveryRefused) as nonretryable:
            await validate_recovery_target(log, target)
        assert nonretryable.value.code == "failure_not_retryable"

        privileged_source = await log.append(
            _source(member="member-privileged", sensitivity="privileged")
        )
        with pytest.raises(SourceAssessmentRecoveryRefused) as privileged:
            await validate_recovery_target(
                log,
                SourceAssessmentRecoveryTarget(
                    source_event_id=privileged_source,
                    failed_assessment_event_id=failed_id,
                    expected_failure_reason="language_seam_OpenClawUnreachable",
                ),
            )
        assert privileged.value.code == "source_privileged"

        with pytest.raises(SourceAssessmentRecoveryRefused) as cross_member:
            await validate_recovery_target(
                log,
                SourceAssessmentRecoveryTarget(
                    source_event_id=source_id,
                    failed_assessment_event_id=failed_id,
                    expected_failure_reason="language_seam_SkillInputInvalid",
                ),
                required_owner_scope="private:another-member",
            )
        assert cross_member.value.code == "request_scope_mismatch"
    finally:
        await log.close()
