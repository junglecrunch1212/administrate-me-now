"""Append-only, bounded terminal source-assessment recovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.session import build_internal_session
from adminme.lib.skill_runner import OpenClawUnreachable, SkillResult, run_skill
from adminme.lib.skill_runner.wrapper import _Runtime
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


@dataclass
class _Observation:
    active: bool = True

    async def is_active(self) -> bool:
        return self.active


def _source(
    *,
    member: str = MEMBER,
    sensitivity: str = "normal",
    tenant_id: str = TENANT,
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=1_786_400_000_000,
        tenant_id=tenant_id,
        type="messaging.received",
        schema_version=3,
        occurred_at="2026-08-11T12:00:00Z",
        source_adapter="gmail",
        source_account_id="fixture-account",
        owner_scope=f"private:{member}",
        visibility_scope=f"private:{member}",
        sensitivity=sensitivity,  # type: ignore[arg-type]
        idempotency_key=f"recovery-source:{tenant_id}:{member}:{sensitivity}",
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
    tenant_id: str = TENANT,
    member: str = MEMBER,
) -> str:
    return await log.append(
        EventEnvelope(
            event_at_ms=1_786_400_000_001,
            tenant_id=tenant_id,
            type="triage.intent.features_extracted",
            schema_version=3,
            occurred_at="2026-08-11T12:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope=f"private:{member}",
            visibility_scope=f"private:{member}",
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
    log: EventLog,
    sink: _Sink,
    run_skill: Any,
    request_id: str,
    *,
    observation: _Observation | None = None,
) -> PipelineContext:
    return PipelineContext(
        session=build_internal_session("pipeline_runner", "device", TENANT),
        event_log=log,
        event_sink=sink,
        run_skill_fn=run_skill,
        outbound_fn=lambda *args, **kwargs: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=observation or _Observation(),  # type: ignore[arg-type]
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
        retry_prior_failure: list[bool] = []

        async def recovered(
            _skill: str, _inputs: dict[str, Any], skill_ctx: Any
        ) -> SkillResult:
            nonlocal calls
            calls += 1
            retry_prior_failure.append(bool(skill_ctx.retry_prior_failure))
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
        assert retry_prior_failure == [True]

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
@pytest.mark.parametrize("crash_after_provider_call", [1, 2])
async def test_mid_retry_restart_never_repeats_a_spent_invocation_identity(
    tmp_path: Path,
    crash_after_provider_call: int,
) -> None:
    """A crash after base/recovery-1 cannot replay either spent identity."""

    class _FixtureCrash(BaseException):
        pass

    log = EventLog(tmp_path / "mid-retry-restart.db", b"x" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(log, source_id)
        request = await _append_request(
            log, source_id, failed_id, "request-mid-retry-1"
        )
        provider_requests: list[httpx.Request] = []

        def unavailable(request_value: httpx.Request) -> httpx.Response:
            provider_requests.append(request_value)
            return httpx.Response(503, text="fixture gateway unavailable")

        transport = httpx.MockTransport(unavailable)

        def client_factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=transport, timeout=10.0)

        raw_events_dir = tmp_path / "raw-events"
        raw_events_dir.mkdir()
        runtime = _Runtime(
            event_sink=_Sink(log),  # type: ignore[arg-type]
            event_log=log,
            raw_events_dir=raw_events_dir,
            httpx_client_factory=client_factory,
        )

        async def crash_after_recovery_one(
            skill_id: str, inputs: dict[str, Any], skill_ctx: Any
        ) -> SkillResult:
            try:
                return await run_skill(
                    skill_id,
                    inputs,
                    skill_ctx,
                    _runtime=runtime,
                )
            except OpenClawUnreachable:
                if len(provider_requests) == crash_after_provider_call:
                    raise _FixtureCrash
                raise

        async def durable_unavailable(
            skill_id: str, inputs: dict[str, Any], skill_ctx: Any
        ) -> SkillResult:
            return await run_skill(
                skill_id,
                inputs,
                skill_ctx,
                _runtime=runtime,
            )

        invalidate_cache()
        first_pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
        globals_map = (
            first_pipeline._run_skill_with_transient_recovery.__func__.__globals__
        )
        prior_delays = globals_map["_TRANSIENT_RECOVERY_DELAYS_SECONDS"]
        globals_map["_TRANSIENT_RECOVERY_DELAYS_SECONDS"] = (0.0, 0.0)
        try:
            with pytest.raises(_FixtureCrash):
                await first_pipeline.handle(
                    request,
                    _context(
                        log,
                        _Sink(log),
                        crash_after_recovery_one,
                        request["event_id"],
                    ),
                )
            assert len(provider_requests) == crash_after_provider_call

            invalidate_cache()
            restarted_pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
            await restarted_pipeline.handle(
                request,
                _context(
                    log,
                    _Sink(log),
                    durable_unavailable,
                    request["event_id"],
                ),
            )
            await restarted_pipeline.handle(
                request,
                _context(
                    log,
                    _Sink(log),
                    durable_unavailable,
                    request["event_id"],
                ),
            )
        finally:
            globals_map["_TRANSIENT_RECOVERY_DELAYS_SECONDS"] = prior_delays

        assert len(provider_requests) == 3
        failures = [
            event async for event in log.read_since(None, types=["skill.call.failed"])
        ]
        assert len(failures) == 3
        failure_keys = [str(event["idempotency_key"]) for event in failures]
        assert sum(":recovery-1:" in key for key in failure_keys) == 1
        assert sum(":recovery-2:" in key for key in failure_keys) == 1
        latest = await latest_source_assessment(log, source_id)
        assert latest is not None
        assert latest["schema_version"] == 4
        assert latest["payload"]["assessment_outcome"] == "failed"
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_reopened_log_appends_recovery_after_request_and_failed_history(
    tmp_path: Path,
) -> None:
    """A fresh process generator cannot place recovery behind old history."""

    database = tmp_path / "reopened.db"
    key = b"w" * 32
    seed_log = EventLog(database, key)
    try:
        source_id = await seed_log.append(_source())
        failed_id = await _failed_assessment(seed_log, source_id)
        request = await _append_request(
            seed_log, source_id, failed_id, "request-reopened-1"
        )
    finally:
        await seed_log.close()

    reopened_log = EventLog(database, key)
    try:

        async def recovered(*_args: Any, **_kwargs: Any) -> SkillResult:
            return _successful_result()

        invalidate_cache()
        pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
        await pipeline.handle(
            request,
            _context(
                reopened_log,
                _Sink(reopened_log),
                recovered,
                request["event_id"],
            ),
        )

        latest = await latest_source_assessment(reopened_log, source_id)
        assert latest is not None
        assert latest["schema_version"] == 4
        assert latest["event_id"] > request["event_id"] > failed_id
        audit = await audit_source_assurance(
            reopened_log,
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
    finally:
        await reopened_log.close()


@pytest.mark.asyncio
async def test_delivery_requires_observation_and_refusal_is_terminal(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "observation.db", b"o" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(log, source_id)
        request = await _append_request(
            log, source_id, failed_id, "request-observation-1"
        )
        calls = 0
        observation = _Observation(active=False)

        async def recovered(*_args: Any, **_kwargs: Any) -> SkillResult:
            nonlocal calls
            calls += 1
            return _successful_result()

        invalidate_cache()
        pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
        ctx = _context(
            log,
            _Sink(log),
            recovered,
            request["event_id"],
            observation=observation,
        )
        await pipeline.handle(request, ctx)
        observation.active = True
        await pipeline.handle(request, ctx)

        refusals = [
            event
            async for event in log.read_since(
                None, types=["source.assessment.recovery_refused"]
            )
        ]
        assert calls == 0
        assert len(refusals) == 1
        assert refusals[0]["payload"]["reason"] == "observation_inactive"
        assert refusals[0]["event_id"] > request["event_id"]
        latest = await latest_source_assessment(log, source_id)
        assert latest is not None
        assert latest["event_id"] == failed_id
    finally:
        await log.close()


@pytest.mark.asyncio
async def test_distinct_concurrent_requests_are_single_flight_per_source(
    tmp_path: Path,
) -> None:
    log = EventLog(tmp_path / "single-flight.db", b"f" * 32)
    try:
        source_id = await log.append(_source())
        failed_id = await _failed_assessment(log, source_id)
        first = await _append_request(log, source_id, failed_id, "request-flight-1")
        second = await _append_request(log, source_id, failed_id, "request-flight-2")
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def recovered(*_args: Any, **_kwargs: Any) -> SkillResult:
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()
            return _successful_result()

        invalidate_cache()
        pipeline: Any = load_pipeline_pack(PACK_ROOT).instance
        first_task = asyncio.create_task(
            pipeline.handle(
                first,
                _context(log, _Sink(log), recovered, first["event_id"]),
            )
        )
        await entered.wait()
        second_task = asyncio.create_task(
            pipeline.handle(
                second,
                _context(log, _Sink(log), recovered, second["event_id"]),
            )
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first_task, second_task)

        related = await log.get_by_causation(source_id)
        replacements = [
            event
            for event in related
            if event["type"] == "triage.intent.features_extracted"
            and event["schema_version"] == 4
        ]
        refusals = [
            event
            async for event in log.read_since(
                None, types=["source.assessment.recovery_refused"]
            )
        ]
        assert calls == 1
        assert len(replacements) == 1
        assert len(refusals) == 1
        assert refusals[0]["payload"]["reason"] == "failed_assessment_not_current"
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

        other_tenant_source = await log.append(
            _source(member="member-other-tenant", tenant_id="tenant-other")
        )
        with pytest.raises(SourceAssessmentRecoveryRefused) as source_tenant:
            await validate_recovery_target(
                log,
                SourceAssessmentRecoveryTarget(
                    source_event_id=other_tenant_source,
                    failed_assessment_event_id=failed_id,
                    expected_failure_reason="language_seam_OpenClawUnreachable",
                ),
                required_tenant_id=TENANT,
            )
        assert source_tenant.value.code == "tenant_mismatch"

        assessment_member = "member-assessment-tenant"
        assessment_source = await log.append(_source(member=assessment_member))
        cross_tenant_assessment = await _failed_assessment(
            log,
            assessment_source,
            tenant_id="tenant-other",
            member=assessment_member,
        )
        with pytest.raises(SourceAssessmentRecoveryRefused) as assessment_tenant:
            await validate_recovery_target(
                log,
                SourceAssessmentRecoveryTarget(
                    source_event_id=assessment_source,
                    failed_assessment_event_id=cross_tenant_assessment,
                    expected_failure_reason="language_seam_OpenClawUnreachable",
                ),
                required_tenant_id=TENANT,
            )
        assert assessment_tenant.value.code == "tenant_mismatch"
    finally:
        await log.close()
