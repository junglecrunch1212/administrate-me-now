"""Handler-direct unit tests for noise_filtering.

Construct a fake PipelineContext with a stubbed run_skill_fn and verify
the four branches of ``handle()``: empty body returns silently, skill
success emits ``messaging.classified`` with the model's classification,
skill timeout / input-invalid emits the defensive default, and SMS
events feed the body field rather than body_text.
"""

from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import IdempotencyConflict
from adminme.lib.skill_runner import (
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillInputInvalid,
    SkillResult,
)
from adminme.pipelines.base import PipelineContext
from adminme.triage_intent import IntentCandidate

PACK_ROOT = (
    Path(__file__).resolve().parents[2] / "packs" / "pipelines" / "noise_filtering"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_noise_filtering_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
NoiseFilteringPipeline = _handler.NoiseFilteringPipeline


@dataclass
class _RecordedAppend:
    event_id: str
    envelope: EventEnvelope
    correlation_id: str | None
    causation_id: str | None


@dataclass
class _FakeEventLog:
    calls: list[_RecordedAppend] = field(default_factory=list)
    _next_id: int = 0

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        self._next_id += 1
        event_id = f"fake-event-id-{self._next_id}"
        self.calls.append(
            _RecordedAppend(
                event_id=event_id,
                envelope=envelope,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        )
        return event_id

    async def get_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        for call in self.calls:
            if (
                call.envelope.tenant_id == tenant_id
                and call.envelope.idempotency_key == idempotency_key
            ):
                return {
                    **call.envelope.model_dump(mode="json"),
                    "event_id": call.event_id,
                    "causation_id": call.causation_id,
                }
        return None

    async def get_by_causation(self, causation_id: str) -> list[dict[str, Any]]:
        return [
            {
                **call.envelope.model_dump(mode="json"),
                "event_id": call.event_id,
                "causation_id": call.causation_id,
            }
            for call in self.calls
            if call.causation_id == causation_id
        ]


@dataclass
class _RacingProposalEventLog(_FakeEventLog):
    """Force two proposal producers through the read-before-append window."""

    proposal_reads: int = 0
    proposal_gate: asyncio.Event = field(default_factory=asyncio.Event)

    async def get_by_idempotency(
        self, tenant_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        existing = await super().get_by_idempotency(tenant_id, idempotency_key)
        if (
            existing is None
            and idempotency_key.startswith("triage:message-candidate:v19:")
            and self.proposal_reads < 2
        ):
            self.proposal_reads += 1
            if self.proposal_reads == 2:
                self.proposal_gate.set()
            await self.proposal_gate.wait()
            return None
        return existing

    async def write(
        self,
        envelope: EventEnvelope,
        *,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> str:
        if envelope.idempotency_key is not None:
            existing = await _FakeEventLog.get_by_idempotency(
                self, envelope.tenant_id, envelope.idempotency_key
            )
            if existing is not None:
                raise IdempotencyConflict(
                    "idempotency key already belongs to a different event identity"
                )
        return await super().write(
            envelope,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


@dataclass
class _ContextEventLog(_FakeEventLog):
    source_events: list[dict[str, Any]] = field(default_factory=list)

    async def read_since(
        self,
        *,
        owner_scope: str | None = None,
        types: list[str] | None = None,
        **_kwargs: Any,
    ):  # type: ignore[no-untyped-def]
        values = [
            *self.source_events,
            *[
                {
                    **call.envelope.model_dump(mode="json"),
                    "event_id": call.event_id,
                    "causation_id": call.causation_id,
                }
                for call in self.calls
            ],
        ]
        for value in values:
            if owner_scope is not None and value.get("owner_scope") != owner_scope:
                continue
            if types is not None and value.get("type") not in types:
                continue
            yield value


@dataclass
class _RacingSupersessionEventLog(_ContextEventLog):
    gated_proposal_event_id: str = ""
    descendants_inspected: asyncio.Event = field(default_factory=asyncio.Event)
    resume_supersession: asyncio.Event = field(default_factory=asyncio.Event)
    gate_used: bool = False

    async def get_by_causation(self, causation_id: str) -> list[dict[str, Any]]:
        descendants = await super().get_by_causation(causation_id)
        if causation_id == self.gated_proposal_event_id and not self.gate_used:
            self.gate_used = True
            self.descendants_inspected.set()
            await self.resume_supersession.wait()
        return descendants


def _make_ctx(
    event_log: Any,
    run_skill_fn: Any,
    *,
    candidate_policy_fn: Any | None = None,
) -> PipelineContext:
    from adminme.lib.session import build_internal_session

    session = build_internal_session("pipeline_runner", "device", "tenant-test")
    return PipelineContext(
        session=session,
        event_log=event_log,
        event_sink=event_log,
        run_skill_fn=run_skill_fn,
        outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id="trigger-eid-001",
        correlation_id="corr-001",
        candidate_policy_fn=candidate_policy_fn,
    )


def _messaging_received_event(
    *, body_text: str | None = "Hello from a real friend!"
) -> dict[str, Any]:
    return {
        "event_id": "trigger-eid-001",
        "type": "messaging.received",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "private:member-fixture",
        "visibility_scope": "private:member-fixture",
        "payload": {
            "source_channel": "gmail",
            "from_identifier": "ada@example.com",
            "to_identifier": "me@example.com",
            "member_id": "member-fixture",
            "body_text": body_text,
            "received_at": EventEnvelope.now_utc_iso(),
        },
    }


def _telephony_sms_event(*, body: str = "your code is 123456") -> dict[str, Any]:
    return {
        "event_id": "trigger-eid-002",
        "type": "telephony.sms_received",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "private:member-fixture",
        "visibility_scope": "private:member-fixture",
        "payload": {
            "from_number": "+15551234567",
            "to_number": "+15559999999",
            "body": body,
            "received_at": EventEnvelope.now_utc_iso(),
            "member_id": "member-fixture",
        },
    }


def _messaging_sent_event(*, body_text: str) -> dict[str, Any]:
    return {
        "event_id": "trigger-eid-sent-001",
        "type": "messaging.sent",
        "schema_version": 4,
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "private:member-fixture",
        "visibility_scope": "private:member-fixture",
        "occurred_at": "2026-08-08T12:00:00Z",
        "payload": {
            "member_id": "member-fixture",
            "source_channel": "gmail",
            "provider": "gmail",
            "to_identifier": "recipient@example.invalid",
            "body_text": body_text,
            "sent_at": "2026-08-08T12:00:00Z",
        },
    }


def _make_skill_result(
    *,
    classification: str = "transactional",
    confidence: float = 0.91,
    reasons: list[str] | None = None,
    **features: Any,
) -> SkillResult:
    return SkillResult(
        output={
            "classification": classification,
            "confidence": confidence,
            "reasons": reasons or ["stubbed"],
            "reply_signal": features.get("reply_signal", "unknown"),
            "automated": features.get("automated", False),
            "transactional_due": features.get("transactional_due", False),
            "security_code": features.get("security_code", False),
            "person_to_person": features.get("person_to_person", False),
            "deadline": features.get("deadline"),
            "candidates": features.get("candidates", []),
        },
        openclaw_invocation_id="stub-id",
        provider="stub",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.0001,
        duration_ms=42,
    )


# ---------------------------------------------------------------------------
# branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_body_records_no_candidate_without_calling_model() -> None:
    log = _FakeEventLog()
    calls: list[Any] = []

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        calls.append((args, kwargs))
        return _make_skill_result()

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    event = _messaging_received_event(body_text=None)
    await pipeline.handle(event, ctx)
    assert [call.envelope.type for call in log.calls] == [
        "triage.intent.features_extracted"
    ]
    assert log.calls[0].envelope.payload["assessment_outcome"] == "no_candidate"
    assert calls == []  # skill must not have been called


@pytest.mark.asyncio
async def test_html_only_body_uses_canonical_authored_text() -> None:
    log = _FakeEventLog()
    calls: list[dict[str, Any]] = []

    async def stub_run_skill(
        _skill_id: str, inputs: dict[str, Any], _skill_ctx: SkillContext
    ) -> SkillResult:
        calls.append(inputs)
        return _make_skill_result()

    event = _messaging_received_event(body_text=None)
    event["payload"]["body_html"] = (
        "<div>Please review the fixture authorization.</div>"
    )

    await NoiseFilteringPipeline().handle(event, _make_ctx(log, stub_run_skill))

    assert calls[0]["body_text"] == "Please review the fixture authorization."


@pytest.mark.asyncio
async def test_uncertain_authored_text_records_terminal_no_candidate() -> None:
    log = _FakeEventLog()
    calls: list[Any] = []

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        calls.append((args, kwargs))
        return _make_skill_result()

    event = _messaging_received_event(
        body_text=(
            "Sent: Tuesday, February 1\n"
            "To: member@example.invalid\n"
            "Subject: Earlier request\n"
            "Please wire fixture funds."
        )
    )

    await NoiseFilteringPipeline().handle(event, _make_ctx(log, stub_run_skill))

    assessment = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert assessment.payload["assessment_outcome"] == "no_candidate"
    assert assessment.payload["reason_codes"] == ["authored_text_uncertain"]
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("member_action", [None, "enriched", "dismissed"])
async def test_legacy_sent_replay_retires_only_untouched_prior_proposal(
    member_action: str | None,
) -> None:
    source = _messaging_sent_event(
        body_text="I will authorize the old fixture transfer."
    )
    source.update(
        {
            "schema_version": 3,
            "source_adapter": "messaging-gmail-api",
            "source_account_id": "fixture-account",
        }
    )
    source["payload"].update(
        {
            "thread_id": "legacy-sent-thread",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
        }
    )
    log = _ContextEventLog(source_events=[source])
    proposal_id = "legacy-sent-prior-proposal"
    proposal_event_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000001,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=3,
            occurred_at="2026-08-08T12:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="legacy-sent-prior-proposal-event",
            payload={
                "proposal_id": proposal_id,
                "target_kind": "commitment",
                "owner_member_id": "member-fixture",
                "title": "Authorize the old fixture transfer",
                "candidate": {},
                "route": {"member_id": "member-fixture"},
                "source_event_id": source["event_id"],
                "candidate_key": "legacy-sent-candidate",
                "eligibility_rule_ids": ["grounded_language_exact_quote_v1"],
                "confidence": 0.9,
                "reasons": ["legacy authored-text interpretation"],
            },
        ),
        causation_id="legacy-sent-v20-assessment",
    )
    if member_action == "enriched":
        await log.write(
            EventEnvelope(
                event_at_ms=1700000000002,
                tenant_id="tenant-test",
                type="triage.item.enriched",
                schema_version=1,
                occurred_at="2026-08-08T12:00:02Z",
                source_adapter="member-triage",
                source_account_id="member-fixture",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                idempotency_key="legacy-sent-member-enrichment",
                payload={
                    "proposal_id": proposal_id,
                    "enriched_at": "2026-08-08T12:00:02Z",
                    "enriched_by_member_id": "member-fixture",
                    "candidate_updates": {"note": "Member reviewed this item."},
                },
            ),
            causation_id=proposal_event_id,
        )
    elif member_action == "dismissed":
        await log.write(
            EventEnvelope(
                event_at_ms=1700000000002,
                tenant_id="tenant-test",
                type="triage.item.dismissed",
                schema_version=1,
                occurred_at="2026-08-08T12:00:02Z",
                source_adapter="member-triage",
                source_account_id="member-fixture",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                idempotency_key="legacy-sent-member-dismissal",
                payload={
                    "proposal_id": proposal_id,
                    "dismissed_at": "2026-08-08T12:00:02Z",
                    "dismissed_by_member_id": "member-fixture",
                    "reason": "Member already decided this item.",
                },
            ),
            causation_id=proposal_event_id,
        )

    calls: list[Any] = []

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        calls.append((args, kwargs))
        return _make_skill_result()

    await NoiseFilteringPipeline().handle(source, _make_ctx(log, stub_run_skill))

    assessment = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert assessment.payload["assessment_outcome"] == "no_candidate"
    assert assessment.payload["reason_codes"] == [
        "legacy_gmail_sent_quote_boundary_unavailable"
    ]
    superseded = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.superseded"
    ]
    assert bool(superseded) is (member_action is None)
    if superseded:
        assert superseded[0].payload == {
            "proposal_id": proposal_id,
            "superseded_at": "2026-08-08T12:00:00Z",
            "replacement_source_event_id": source["event_id"],
            "reason": "eligibility_rule_replaced",
            "expected_last_event_id": proposal_event_id,
        }
    assert calls == []


@pytest.mark.asyncio
async def test_legacy_sent_supersession_carries_stale_revision_through_race() -> None:
    source = _messaging_sent_event(
        body_text="I will authorize the old fixture transfer."
    )
    source.update(
        {
            "schema_version": 3,
            "source_adapter": "messaging-gmail-api",
            "source_account_id": "fixture-account",
        }
    )
    source["payload"].update(
        {
            "thread_id": "legacy-sent-race-thread",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
        }
    )
    log = _RacingSupersessionEventLog(source_events=[source])
    proposal_event_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000001,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=3,
            occurred_at="2026-08-08T12:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="legacy-sent-race-proposal-event",
            payload={
                "proposal_id": "legacy-sent-race-proposal",
                "target_kind": "commitment",
                "owner_member_id": "member-fixture",
                "title": "Authorize the old fixture transfer",
                "candidate": {},
                "route": {"member_id": "member-fixture"},
                "source_event_id": source["event_id"],
                "candidate_key": "legacy-sent-race-candidate",
                "eligibility_rule_ids": ["grounded_language_exact_quote_v1"],
                "confidence": 0.9,
                "reasons": ["legacy authored-text interpretation"],
            },
        )
    )
    log.gated_proposal_event_id = proposal_event_id

    async def should_not_run(*_args: Any, **_kwargs: Any) -> SkillResult:
        raise AssertionError("unreliable legacy text reached the language seam")

    replay = asyncio.create_task(
        NoiseFilteringPipeline().handle(source, _make_ctx(log, should_not_run))
    )
    await asyncio.wait_for(log.descendants_inspected.wait(), timeout=1)
    enrichment_event_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000002,
            tenant_id="tenant-test",
            type="triage.item.enriched",
            schema_version=1,
            occurred_at="2026-08-08T12:00:02Z",
            source_adapter="member-triage",
            source_account_id="member-fixture",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="legacy-sent-race-member-enrichment",
            payload={
                "proposal_id": "legacy-sent-race-proposal",
                "enriched_at": "2026-08-08T12:00:02Z",
                "enriched_by_member_id": "member-fixture",
                "title": "Member-corrected legitimate commitment",
                "candidate_updates": {"note": "Reviewed by the member."},
            },
        ),
        causation_id=proposal_event_id,
    )
    log.resume_supersession.set()
    await asyncio.wait_for(replay, timeout=1)

    supersession_call = next(
        call for call in log.calls if call.envelope.type == "triage.item.superseded"
    )
    assert log.calls.index(
        next(call for call in log.calls if call.event_id == enrichment_event_id)
    ) < log.calls.index(supersession_call)
    assert supersession_call.envelope.schema_version == 3
    assert supersession_call.envelope.payload["expected_last_event_id"] == (
        proposal_event_id
    )


@pytest.mark.asyncio
async def test_skill_success_emits_classified() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        assert skill_id == "skill:classify_message_nature"
        assert skill_ctx.invocation_idempotency_key is not None
        assert skill_ctx.invocation_idempotency_key.startswith("message-nature:")
        assert ":v9:" not in skill_ctx.invocation_idempotency_key
        assert skill_ctx.retry_prior_failure is True
        assert inputs["body_text"] == "Hello from a real friend!"
        assert inputs["source_channel"] == "gmail"
        assert inputs["from_identifier"] == "ada@example.com"
        assert inputs["from_party_known"] is False
        return _make_skill_result(classification="transactional", confidence=0.91)

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    await pipeline.handle(_messaging_received_event(), ctx)
    assert [call.envelope.type for call in log.calls] == [
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
    ]
    envelope = log.calls[0].envelope
    assert envelope.type == "messaging.classified"
    assert envelope.payload["classification"] == "transactional"
    assert envelope.payload["confidence"] == 0.91
    assert envelope.payload["skill_name"] == "classify_message_nature"
    assert envelope.payload["skill_version"] == "7.0.0"
    assert envelope.payload["source_event_id"] == "trigger-eid-001"
    assert log.calls[0].causation_id == "trigger-eid-001"


@pytest.mark.asyncio
async def test_skill_failure_emits_defensive_default() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise OpenClawTimeout("openclaw timed out")

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    event = _messaging_received_event()
    event["owner_scope"] = "private:member-fixture"
    event["visibility_scope"] = "private:member-fixture"
    event["payload"]["member_id"] = "member-fixture"
    await pipeline.handle(event, ctx)
    assert len(log.calls) == 3
    envelope = log.calls[0].envelope
    assert envelope.type == "messaging.classified"
    assert envelope.payload["classification"] == "personal"
    assert envelope.payload["confidence"] == 0.0
    assert envelope.payload["skill_version"] == "7.0.0"
    assert log.calls[1].envelope.payload["reply_signal"] == "unknown"
    assert log.calls[2].envelope.payload["assessment_outcome"] == "failed"


@pytest.mark.asyncio
async def test_transient_language_failure_recovers_before_terminal_failure() -> None:
    log = _FakeEventLog()
    calls = 0
    invocation_keys: list[str] = []

    async def transient_then_recovered(
        _skill_id: str, _inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        nonlocal calls
        calls += 1
        invocation_keys.append(str(skill_ctx.invocation_idempotency_key))
        if calls == 1:
            raise OpenClawTimeout("one transient timeout")
        return _make_skill_result(classification="transactional", confidence=0.9)

    await NoiseFilteringPipeline().handle(
        _messaging_received_event(), _make_ctx(log, transient_then_recovered)
    )

    assert calls == 2
    assert invocation_keys[1] == f"{invocation_keys[0]}:recovery-1"
    assessments = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    ]
    assert assessments[-1].payload["assessment_outcome"] == "no_candidate"
    assert assessments[-1].payload["reason_codes"][0] == (
        "language_assessment_completed"
    )


@pytest.mark.asyncio
async def test_second_recovery_uses_its_own_durable_invocation_identity() -> None:
    log = _FakeEventLog()
    invocation_keys: list[str] = []

    async def recover_on_final_attempt(
        _skill_id: str, _inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        invocation_keys.append(str(skill_ctx.invocation_idempotency_key))
        if len(invocation_keys) == 1:
            raise OpenClawUnreachable("base failure")
        if len(invocation_keys) == 2:
            raise OpenClawTimeout("first recovery stream timed out")
        return _make_skill_result(classification="transactional", confidence=0.9)

    await NoiseFilteringPipeline().handle(
        _messaging_received_event(), _make_ctx(log, recover_on_final_attempt)
    )

    assert invocation_keys[1] == f"{invocation_keys[0]}:recovery-1"
    assert invocation_keys[2] == f"{invocation_keys[0]}:recovery-2"
    assessment = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert assessment.payload["assessment_outcome"] == "no_candidate"


@pytest.mark.asyncio
async def test_concurrent_transient_recoveries_do_not_stampede_gateway() -> None:
    log = _FakeEventLog()
    attempts: dict[str, int] = {}
    invocation_keys: dict[str, list[str]] = {}
    active_recoveries = 0
    max_active_recoveries = 0

    async def transient_then_recovered(
        _skill_id: str, _inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        nonlocal active_recoveries, max_active_recoveries
        source_id = str(skill_ctx.triggering_event_id)
        attempts[source_id] = attempts.get(source_id, 0) + 1
        invocation_keys.setdefault(source_id, []).append(
            str(skill_ctx.invocation_idempotency_key)
        )
        if attempts[source_id] == 1:
            raise OpenClawTimeout("concurrent first-attempt timeout")
        active_recoveries += 1
        max_active_recoveries = max(max_active_recoveries, active_recoveries)
        await asyncio.sleep(0.01)
        active_recoveries -= 1
        return _make_skill_result(classification="transactional", confidence=0.9)

    first = _messaging_received_event(body_text="First message without a candidate.")
    first["event_id"] = "transient-source-1"
    second = _messaging_received_event(body_text="Second message without a candidate.")
    second["event_id"] = "transient-source-2"
    pipeline = NoiseFilteringPipeline()
    await asyncio.gather(
        pipeline.handle(first, _make_ctx(log, transient_then_recovered)),
        pipeline.handle(second, _make_ctx(log, transient_then_recovered)),
    )

    assert attempts == {"transient-source-1": 2, "transient-source-2": 2}
    assert all(keys[1] == f"{keys[0]}:recovery-1" for keys in invocation_keys.values())
    assert max_active_recoveries == 1
    assessments = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    ]
    assert len(assessments) == 2
    assert all(
        assessment.payload["assessment_outcome"] == "no_candidate"
        for assessment in assessments
    )


@pytest.mark.asyncio
async def test_skill_input_invalid_emits_defensive_default() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise SkillInputInvalid("bad input shape")

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    event = _messaging_received_event()
    event["owner_scope"] = "private:member-fixture"
    event["visibility_scope"] = "private:member-fixture"
    event["payload"]["member_id"] = "member-fixture"
    await pipeline.handle(event, ctx)
    assert len(log.calls) == 3
    envelope = log.calls[0].envelope
    assert envelope.payload["classification"] == "personal"
    assert envelope.payload["confidence"] == 0.0
    assert log.calls[2].envelope.payload["assessment_outcome"] == "failed"


@pytest.mark.asyncio
async def test_telephony_sms_uses_body_field() -> None:
    log = _FakeEventLog()
    captured: dict[str, Any] = {}

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        captured.update(inputs)
        return _make_skill_result(classification="noise", confidence=0.95)

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    await pipeline.handle(_telephony_sms_event(body="STOP HEYBANK"), ctx)
    assert captured["body_text"] == "STOP HEYBANK"
    assert captured["from_identifier"] == "+15551234567"
    assert captured["source_channel"] == "sms"
    assert len(log.calls) == 3
    assert log.calls[0].envelope.payload["classification"] == "noise"


@pytest.mark.asyncio
async def test_quiet_source_is_assessed_without_a_triage_proposal() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise OpenClawTimeout("classification may fail without reopening candidates")

    def policy(member_id: str, source_id: str) -> dict[str, Any]:
        assert (member_id, source_id) == ("member-fixture", "adapter:gmail")
        return {
            "source_id": source_id,
            "mode": "quiet",
            "language_enabled": True,
            "candidate_kinds": ["task"],
        }

    await NoiseFilteringPipeline().handle(
        _messaging_received_event(body_text="Please call me tomorrow."),
        _make_ctx(log, stub_run_skill, candidate_policy_fn=policy),
    )

    assert "triage.item.proposed" not in [call.envelope.type for call in log.calls]
    assessment = next(
        call.envelope.payload
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert assessment["assessment_outcome"] == "no_candidate"
    assert "candidate_policy_quiet" in assessment["reason_codes"]
    assert "candidate_policy_v1:adapter:gmail:quiet" in assessment["reason_codes"]


@pytest.mark.asyncio
async def test_language_toggle_cannot_contribute_candidates_or_skip_assessment() -> (
    None
):
    log = _FakeEventLog()
    captured: dict[str, Any] = {}

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        captured.update(inputs)
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "evidence_quote": "A message with no deterministic action pattern.",
                    "candidate": {},
                    "confidence": 0.99,
                }
            ]
        )

    def policy(_member_id: str, source_id: str) -> dict[str, Any]:
        return {
            "source_id": source_id,
            "mode": "review",
            "language_enabled": False,
            "candidate_kinds": ["task"],
        }

    await NoiseFilteringPipeline().handle(
        _messaging_received_event(
            body_text="A message with no deterministic action pattern."
        ),
        _make_ctx(log, stub_run_skill, candidate_policy_fn=policy),
    )

    assert captured["candidate_language_enabled"] is False
    assert "triage.item.proposed" not in [call.envelope.type for call in log.calls]
    assessment = next(
        call.envelope.payload
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert "candidate_language_disabled" in assessment["reason_codes"]


@pytest.mark.asyncio
async def test_outbound_member_promise_can_enter_triage_as_grounded_commitment() -> (
    None
):
    log = _FakeEventLog()
    source_text = "I will send the signed form tomorrow."

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        assert inputs["message_direction"] == "outbound"
        assert inputs["counterparty_identifier"] == "recipient@example.invalid"
        return _make_skill_result(
            classification="personal",
            candidates=[
                {
                    "target_kind": "commitment",
                    "title": "Send signed form",
                    "evidence_quote": source_text,
                    "candidate": {"kind": "follow_up", "untrusted": True},
                    "confidence": 0.94,
                    "reasons": ["first-person future promise"],
                }
            ],
        )

    await NoiseFilteringPipeline().handle(
        _messaging_sent_event(body_text=source_text),
        _make_ctx(log, stub_run_skill),
    )

    proposal = log.calls[-1].envelope.payload
    assert proposal["target_kind"] == "commitment"
    assert proposal["title"] == source_text
    assert proposal["candidate"] == {
        "kind": "other",
        "source_message_preview": source_text,
    }
    assert proposal["route"] == {"member_id": "member-fixture"}


@pytest.mark.asyncio
async def test_grounded_candidates_enter_triage_without_provider_route() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(
            reply_signal="required",
            person_to_person=True,
            candidates=[
                {
                    "target_kind": "calendar_event",
                    "title": "Review proposed appointment",
                    "evidence_quote": (
                        "Appointment on 2034-03-02T16:00:00-05:00 until "
                        "2034-03-02T17:00:00-05:00"
                    ),
                    "candidate": {
                        "start": "2034-03-02T16:00:00-05:00",
                        "end": "2034-03-02T17:00:00-05:00",
                        "unreviewed_model_field": "must not enter the event log",
                    },
                    "confidence": 0.91,
                    "reasons": ["Explicit date and time"],
                }
            ],
        )

    event = _messaging_received_event(
        body_text=(
            "Appointment on 2034-03-02T16:00:00-05:00 until 2034-03-02T17:00:00-05:00"
        )
    )
    event["owner_scope"] = "private:member-fixture"
    event["visibility_scope"] = "private:member-fixture"
    event["payload"]["member_id"] = "member-fixture"
    await NoiseFilteringPipeline().handle(event, _make_ctx(log, stub_run_skill))

    assert [call.envelope.type for call in log.calls] == [
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
        "triage.item.proposed",
    ]
    proposal = log.calls[3].envelope.payload
    assert proposal["target_kind"] == "calendar_event"
    assert proposal["route"] == {"member_id": "member-fixture"}
    assert proposal["source_event_id"] == "trigger-eid-001"
    assert proposal["candidate"] == {
        "start": "2034-03-02T16:00:00-05:00",
        "end": "2034-03-02T17:00:00-05:00",
        "event_type": "busy",
    }
    assert proposal["confidence"] == 0.91
    assert proposal["source_occurred_at"] == event["payload"]["received_at"]
    assert log.calls[2].envelope.schema_version == 3
    assert log.calls[3].envelope.schema_version == 4
    assert log.calls[3].causation_id == "fake-event-id-3"


@pytest.mark.asyncio
async def test_candidates_require_an_exact_private_member_scope() -> None:
    log = _FakeEventLog()
    calls: list[Any] = []

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        calls.append((args, kwargs))
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "title": "Review this candidate",
                    "evidence_quote": "Hello from a real friend!",
                    "candidate": {},
                    "confidence": 0.9,
                    "reasons": ["Fixture"],
                }
            ]
        )

    event = _messaging_received_event()
    event["owner_scope"] = "household"
    event["visibility_scope"] = "household"
    await NoiseFilteringPipeline().handle(event, _make_ctx(log, stub_run_skill))

    assert [call.envelope.type for call in log.calls] == [
        "triage.intent.features_extracted",
    ]
    assert log.calls[-1].envelope.payload["assessment_outcome"] == "quarantined"
    assert calls == []


@pytest.mark.asyncio
async def test_unexpected_language_seam_runtime_error_cannot_poison_ingestion() -> None:
    log = _FakeEventLog()

    async def broken_runtime(*args: Any, **kwargs: Any) -> SkillResult:
        raise RuntimeError("missing optional language runtime dependency")

    event = _messaging_received_event(body_text="Pay the water bill")
    event["owner_scope"] = "private:member-fixture"
    event["visibility_scope"] = "private:member-fixture"
    event["payload"]["member_id"] = "member-fixture"
    await NoiseFilteringPipeline().handle(event, _make_ctx(log, broken_runtime))

    assert [call.envelope.type for call in log.calls] == [
        "messaging.classified",
        "messaging.semantic_features_extracted",
        "triage.intent.features_extracted",
        "triage.item.proposed",
    ]
    assert log.calls[-1].envelope.payload["target_kind"] == "task"


@pytest.mark.asyncio
async def test_reviewed_replay_adds_new_nlp_kind_without_rewriting_fallback() -> None:
    log = _FakeEventLog()
    event = _messaging_received_event(body_text="Pay the electric bill by Friday.")
    event["owner_scope"] = "private:member-fixture"
    event["visibility_scope"] = "private:member-fixture"
    event["occurred_at"] = "2026-08-08T12:00:00Z"
    event["payload"]["member_id"] = "member-fixture"

    async def unavailable(*args: Any, **kwargs: Any) -> SkillResult:
        raise OpenClawTimeout("initial language seam timeout")

    await NoiseFilteringPipeline().handle(event, _make_ctx(log, unavailable))

    async def recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "evidence_quote": "Pay the electric bill by Friday.",
                    "candidate": {},
                    "confidence": 0.95,
                },
                {
                    "target_kind": "recurrence",
                    "evidence_quote": "Pay the electric bill by Friday.",
                    "candidate": {
                        "recurrence_text": "monthly",
                        "rrule": "FREQ=MONTHLY",
                        "kind": "bill_payment",
                        "trackable": True,
                    },
                    "confidence": 0.9,
                },
            ]
        )

    await NoiseFilteringPipeline().handle(event, _make_ctx(log, recovered))

    proposals = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.proposed"
    ]
    assert [proposal.payload["target_kind"] for proposal in proposals] == [
        "task",
        "recurrence",
    ]
    assert proposals[-1].idempotency_key.startswith("triage:message-candidate:v21:")


@pytest.mark.asyncio
async def test_terminal_failure_requires_explicit_recovery_before_later_replay() -> (
    None
):
    log = _FakeEventLog()
    event = _messaging_received_event(body_text="A message without a fixed rule.")

    async def first(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(classification="transactional", confidence=0.8)

    async def unavailable(*args: Any, **kwargs: Any) -> SkillResult:
        raise OpenClawTimeout("reviewed replay failure")

    async def recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(classification="noise", confidence=0.9)

    async def startup_order_recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(classification="personal", confidence=0.7)

    async def explicit_replay_recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(classification="transactional", confidence=0.6)

    async def source_assurance_recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(classification="personal", confidence=0.5)

    async def throughput_recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(classification="professional", confidence=0.55)

    pipeline = NoiseFilteringPipeline()
    await pipeline.handle(event, _make_ctx(log, first))
    await pipeline.handle(event, _make_ctx(log, unavailable))
    await pipeline.handle(event, _make_ctx(log, recovered))
    await pipeline.handle(event, _make_ctx(log, startup_order_recovered))
    await pipeline.handle(event, _make_ctx(log, explicit_replay_recovered))
    await pipeline.handle(event, _make_ctx(log, source_assurance_recovered))
    await pipeline.handle(event, _make_ctx(log, throughput_recovered))

    classified_keys = [
        call.envelope.idempotency_key
        for call in log.calls
        if call.envelope.type == "messaging.classified"
    ]
    assert classified_keys == [
        "messaging:classified:trigger-eid-001",
        "messaging:classified:v4:trigger-eid-001",
    ]


@pytest.mark.asyncio
async def test_exact_duplicate_source_observations_propose_each_candidate_once() -> (
    None
):
    log = _FakeEventLog()
    received_at = "2026-08-08T12:00:00Z"

    async def recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "evidence_quote": "Pay the electric bill by Friday.",
                    "candidate": {},
                    "confidence": 0.95,
                },
                {
                    "target_kind": "recurrence",
                    "evidence_quote": "Pay the electric bill by Friday.",
                    "candidate": {
                        "recurrence_text": "monthly",
                        "rrule": "FREQ=MONTHLY",
                        "kind": "bill_payment",
                        "trackable": True,
                    },
                    "confidence": 0.9,
                },
            ]
        )

    first = _messaging_received_event(body_text="Pay the electric bill by Friday.")
    first.update(
        {
            "event_id": "duplicate-source-1",
            "owner_scope": "private:member-fixture",
            "visibility_scope": "private:member-fixture",
            "occurred_at": received_at,
        }
    )
    first["payload"].update({"member_id": "member-fixture", "received_at": received_at})
    second = {
        **first,
        "event_id": "duplicate-source-2",
        "payload": dict(first["payload"]),
    }

    pipeline = NoiseFilteringPipeline()
    await pipeline.handle(first, _make_ctx(log, recovered))
    await pipeline.handle(second, _make_ctx(log, recovered))

    proposals = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.proposed"
    ]
    assert [proposal.payload["target_kind"] for proposal in proposals] == [
        "task",
        "recurrence",
    ]
    assert all(
        proposal.idempotency_key.startswith("triage:message-candidate:v21:")
        for proposal in proposals
    )


@pytest.mark.asyncio
async def test_concurrent_duplicate_sources_accept_first_equivalent_proposal() -> None:
    log = _RacingProposalEventLog()
    received_at = "2026-08-08T12:00:00Z"

    async def recovered(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "evidence_quote": "Pay the electric bill by Friday.",
                    "candidate": {},
                    "confidence": 0.95,
                }
            ]
        )

    first = _messaging_received_event(body_text="Pay the electric bill by Friday.")
    first.update(
        {
            "event_id": "concurrent-duplicate-source-1",
            "owner_scope": "private:member-fixture",
            "visibility_scope": "private:member-fixture",
            "occurred_at": received_at,
        }
    )
    first["payload"].update({"member_id": "member-fixture", "received_at": received_at})
    second = {
        **first,
        "event_id": "concurrent-duplicate-source-2",
        "payload": dict(first["payload"]),
    }

    pipeline = NoiseFilteringPipeline()
    await asyncio.gather(
        pipeline.handle(first, _make_ctx(log, recovered)),
        pipeline.handle(second, _make_ctx(log, recovered)),
    )

    proposals = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.proposed"
    ]
    assert len(proposals) == 1
    assert proposals[0].payload["target_kind"] == "task"


def test_repeated_candidate_alias_keeps_first_recorded_identity() -> None:
    first = IntentCandidate(
        candidate_key="context-stable-alias",
        target_kind="reminder",
        title="First grounded interpretation",
        fields={"notes": "first"},
        evidence_spans=[{"start": 0, "end": 5, "text": "first"}],
        confidence=0.4,
        reasons=["first_recorded"],
        eligibility_rule_ids=["exact_source_quote_grounded_v1"],
    )
    repeated = IntentCandidate(
        candidate_key="context-stable-alias",
        target_kind="reminder",
        title="Later conflicting interpretation",
        fields={"notes": "later"},
        evidence_spans=[{"start": 6, "end": 11, "text": "later"}],
        confidence=0.99,
        reasons=["later_recorded"],
        eligibility_rule_ids=["exact_source_quote_grounded_v1"],
    )
    distinct_kind = IntentCandidate(
        candidate_key="context-stable-alias",
        target_kind="task",
        title="Same alias in another typed namespace",
        fields={},
        evidence_spans=[{"start": 12, "end": 17, "text": "other"}],
        confidence=0.9,
        reasons=["distinct_kind"],
        eligibility_rule_ids=["exact_source_quote_grounded_v1"],
    )

    result = _handler._deduplicate_candidate_keys([first, repeated, distinct_kind])

    assert result == [first, distinct_kind]
    assert result[0].confidence == 0.4  # confidence never ranks the review candidate


@pytest.mark.asyncio
async def test_first_equivalent_writer_does_not_mask_semantic_key_conflict() -> None:
    log = _RacingProposalEventLog()
    key = "triage:message-candidate:v19:collision:task-key:task"
    base_payload = {
        "proposal_id": "proposal-first",
        "target_kind": "task",
        "owner_member_id": "member-fixture",
        "title": "Pay the electric bill",
        "candidate": {},
        "route": {"member_id": "member-fixture"},
        "source_event_id": "source-first",
        "parent_intent_id": None,
        "candidate_key": "task-key",
        "extraction_event_id": "extraction-first",
        "eligibility_rule_ids": ["exact_source_quote_grounded_v1"],
        "confidence": 0.9,
        "reasons": ["exact_source_quote_grounded_v1"],
        "interpretation": None,
    }

    def proposal(payload: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            event_at_ms=1700000000000,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=3,
            occurred_at="2026-08-08T12:00:00Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key=key,
            payload=payload,
        )

    await log.write(proposal(base_payload))
    conflicting = {
        **base_payload,
        "proposal_id": "proposal-second",
        "source_event_id": "source-second",
        "extraction_event_id": "extraction-second",
        "title": "Schedule an unrelated appointment",
    }
    with pytest.raises(IdempotencyConflict):
        await _handler._write_first_equivalent_event(
            ctx=_make_ctx(log, None),
            envelope=proposal(conflicting),
            correlation_id="corr-second",
            causation_id="extraction-second",
            ignored_payload_fields=frozenset(
                {"proposal_id", "source_event_id", "extraction_event_id"}
            ),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("member_decided", [False, True])
async def test_v9_retires_only_untouched_generic_action_verb_proposals(
    member_decided: bool,
) -> None:
    log = _FakeEventLog()
    source_event_id = "trigger-eid-001"
    feature_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000000,
            tenant_id="tenant-test",
            type="triage.intent.features_extracted",
            schema_version=1,
            occurred_at="2026-08-08T12:00:00Z",
            source_adapter="legacy-message-extractor",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key=f"triage:message-features:v7:{source_event_id}",
            payload={},
        ),
        causation_id=source_event_id,
    )
    proposal_id = "legacy-action-verb-proposal"
    proposal_event_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000001,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=2,
            occurred_at="2026-08-08T12:00:01Z",
            source_adapter="legacy-message-extractor",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="legacy-action-verb-candidate",
            payload={
                "proposal_id": proposal_id,
                "target_kind": "task",
                "candidate_key": "candidate-1",
                "eligibility_rule_ids": [
                    "explicit_action_verb_v1",
                    "candidate_fields_task_v1",
                ],
            },
        ),
        causation_id=feature_id,
    )
    if member_decided:
        await log.write(
            EventEnvelope(
                event_at_ms=1700000000002,
                tenant_id="tenant-test",
                type="triage.item.dismissed",
                schema_version=1,
                occurred_at="2026-08-08T12:00:02Z",
                source_adapter="member-triage",
                source_account_id="member-fixture",
                owner_scope="private:member-fixture",
                visibility_scope="private:member-fixture",
                sensitivity="normal",
                idempotency_key="legacy-action-verb-decision",
                payload={"proposal_id": proposal_id},
            ),
            causation_id=proposal_event_id,
        )

    async def no_candidate(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result()

    event = _messaging_received_event(body_text="A normal household update.")
    event["owner_scope"] = "private:member-fixture"
    event["visibility_scope"] = "private:member-fixture"
    event["occurred_at"] = "2026-08-08T12:00:00Z"
    event["payload"]["member_id"] = "member-fixture"
    await NoiseFilteringPipeline().handle(event, _make_ctx(log, no_candidate))

    superseded = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.superseded"
    ]
    assert bool(superseded) is not member_decided
    if superseded:
        assert superseded[0].schema_version == 3
        assert superseded[0].payload == {
            "proposal_id": proposal_id,
            "superseded_at": "2026-08-08T12:00:00Z",
            "replacement_source_event_id": source_event_id,
            "reason": "eligibility_rule_replaced",
            "expected_last_event_id": proposal_event_id,
        }


@pytest.mark.asyncio
async def test_new_thread_anchor_supersedes_untouched_prior_interpretation() -> None:
    prior = _messaging_received_event(body_text="Please return the signed form.")
    prior.update(
        {
            "event_id": "thread-source-prior",
            "occurred_at": "2034-02-01T10:00:00Z",
            "recorded_at": "2034-02-01T10:00:01Z",
            "source_account_id": "fixture-account",
        }
    )
    prior["payload"].update(
        {
            "thread_id": "thread-context-fixture",
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "received_at": "2034-02-01T10:00:00Z",
        }
    )
    current = _messaging_received_event(
        body_text="Please choose delivery option A or B."
    )
    current.update(
        {
            "event_id": "thread-source-current",
            "occurred_at": "2034-02-01T11:00:00Z",
            "recorded_at": "2034-02-01T11:00:01Z",
            "source_account_id": "fixture-account",
        }
    )
    current["payload"].update(
        {
            "thread_id": "thread-context-fixture",
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "received_at": "2034-02-01T11:00:00Z",
        }
    )
    log = _ContextEventLog(source_events=[prior, current])
    await log.write(
        EventEnvelope(
            event_at_ms=1700000000000,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=2,
            occurred_at="2034-02-01T10:00:01Z",
            source_adapter="legacy-message-extractor",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="prior-thread-proposal",
            payload={
                "proposal_id": "prior-thread-proposal",
                "target_kind": "task",
                "owner_member_id": "member-fixture",
                "title": "Please return the signed form.",
                "candidate": {"description": "Please return the signed form."},
                "route": {"member_id": "member-fixture"},
                "source_event_id": "thread-source-prior",
                "candidate_key": "legacy-candidate",
                "eligibility_rule_ids": ["exact_source_quote_grounded_v1"],
                "confidence": 0.8,
                "reasons": ["legacy"],
            },
        )
    )

    async def contextual(
        _skill_id: str, inputs: dict[str, Any], _skill_ctx: SkillContext
    ) -> SkillResult:
        current_alias = next(
            value["source_id"]
            for value in inputs["context_pack"]["messages"]
            if "choose delivery option" in value["body_text"]
        )
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "title": "Choose the delivery plan",
                    "evidence_source_id": current_alias,
                    "evidence_quote": "Please choose delivery option A or B.",
                    "candidate": {},
                    "confidence": 0.9,
                    "reasons": ["new ask"],
                    "action_owner": "member",
                    "lifecycle_state": "open",
                    "compact_summary": "Choose the delivery plan",
                    "case_summary": "A document delivery plan needs confirmation.",
                    "why_member": "The member is directly asked to confirm.",
                    "next_step": "Review the proposed delivery plan.",
                    "thread_state": "waiting_on_member",
                    "unresolved_questions": [],
                    "related_source_ids": [],
                }
            ]
        )

    await NoiseFilteringPipeline().handle(current, _make_ctx(log, contextual))

    superseded = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.superseded"
    ]
    assert len(superseded) == 1
    assert superseded[0].payload["proposal_id"] == "prior-thread-proposal"
    new_proposals = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.proposed"
        and call.envelope.payload.get("interpretation")
    ]
    assert len(new_proposals) == 1
    assert new_proposals[0].payload["source_event_id"] == "thread-source-current"


@pytest.mark.asyncio
@pytest.mark.parametrize("contextualized", [False, True])
async def test_v21_replay_keeps_or_replaces_still_eligible_same_source_fallback(
    contextualized: bool,
) -> None:
    source = _messaging_received_event(
        body_text="Please complete the fixture checklist"
    )
    source.update(
        {
            "occurred_at": "2034-02-01T10:00:00Z",
            "recorded_at": "2034-02-01T10:00:01Z",
            "source_account_id": "fixture-account",
        }
    )
    source["payload"].update(
        {
            "thread_id": "same-source-fallback-fixture",
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "received_at": "2034-02-01T10:00:00Z",
        }
    )
    log = _ContextEventLog(source_events=[source])
    assessment_event_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000000,
            tenant_id="tenant-test",
            type="triage.intent.features_extracted",
            schema_version=3,
            occurred_at="2034-02-01T10:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="triage:message-features:v20:trigger-eid-001",
            payload={
                "intent_id": "source:trigger-eid-001",
                "source_event_id": "trigger-eid-001",
                "skill_call_event_id": None,
                "extractor_id": "legacy-contextual-nlp-v20",
                "language": None,
                "candidates": [
                    {
                        "candidate_key": "message-fallback-1",
                        "target_kind": "task",
                        "title": "Please complete the fixture checklist",
                        "fields": {},
                        "evidence_spans": [],
                        "confidence": None,
                        "reasons": ["inbound_explicit_directive_or_due_obligation_v1"],
                    }
                ],
                "extraction_state": "complete",
                "extracted_at": "2034-02-01T10:00:01Z",
                "assessment_outcome": "eligible_candidates",
                "reason_codes": ["eligible_candidate_found"],
            },
        ),
        causation_id="trigger-eid-001",
    )
    proposal_payload: dict[str, Any] = {
        "proposal_id": "same-source-fallback-v20-proposal",
        "target_kind": "task",
        "owner_member_id": "member-fixture",
        "title": "Please complete the fixture checklist",
        "candidate": {},
        "route": {"member_id": "member-fixture"},
        "source_event_id": "trigger-eid-001",
        "confidence": None,
        "reasons": ["inbound_explicit_directive_or_due_obligation_v1"],
        "parent_intent_id": None,
        "candidate_key": "message-fallback-1",
        "extraction_event_id": assessment_event_id,
        "eligibility_rule_ids": [
            "inbound_explicit_directive_or_due_obligation_v1",
            "candidate_fields_task_v1",
        ],
    }
    if contextualized:
        proposal_payload["interpretation"] = {
            "context_fingerprint": "a" * 64,
            "anchor_event_id": "trigger-eid-001",
        }
    proposal_event_id = await log.write(
        EventEnvelope(
            event_at_ms=1700000000001,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=3,
            occurred_at="2034-02-01T10:00:01Z",
            source_adapter="deterministic_candidate_eligibility",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="same-source-fallback-v20-proposal",
            payload=proposal_payload,
        ),
        causation_id=assessment_event_id,
    )

    async def no_language_candidate(*_args: Any, **_kwargs: Any) -> SkillResult:
        return _make_skill_result()

    await NoiseFilteringPipeline().handle(source, _make_ctx(log, no_language_candidate))

    supersessions = [
        call
        for call in log.calls
        if call.envelope.type == "triage.item.superseded"
        and call.envelope.payload.get("proposal_id")
        == "same-source-fallback-v20-proposal"
    ]
    same_source_proposals = [
        call
        for call in log.calls
        if call.envelope.type == "triage.item.proposed"
        and call.envelope.payload.get("source_event_id") == "trigger-eid-001"
    ]
    if contextualized:
        assert len(supersessions) == 1
        assert len(same_source_proposals) == 2
        replacement = same_source_proposals[-1]
        assert replacement.event_id != proposal_event_id
        assert replacement.envelope.payload["proposal_id"] != (
            "same-source-fallback-v20-proposal"
        )
        assert replacement.envelope.payload["interpretation"] is None
    else:
        assert supersessions == []
        assert [call.event_id for call in same_source_proposals] == [proposal_event_id]
    current_assessment = next(
        call.envelope
        for call in log.calls
        if call.envelope.idempotency_key
        == "triage:message-features:v21:trigger-eid-001"
    )
    assert current_assessment.payload["assessment_outcome"] == "eligible_candidates"


@pytest.mark.asyncio
async def test_latest_anchor_reconciles_proposals_outside_model_window() -> None:
    source_events: list[dict[str, Any]] = []
    for index in range(13):
        source = _messaging_received_event(body_text=f"Routine fixture update {index}.")
        source.update(
            {
                "event_id": f"long-thread-source-{index:02d}",
                "occurred_at": f"2034-02-01T10:{index:02d}:00Z",
                "recorded_at": f"2034-02-01T10:{index:02d}:01Z",
                "source_account_id": "fixture-account",
            }
        )
        source["payload"].update(
            {
                "thread_id": "long-thread-context-fixture",
                "provider": "fixture",
                "provider_account_id": "fixture-account",
                "workspace": "personal",
                "received_at": f"2034-02-01T10:{index:02d}:00Z",
            }
        )
        source_events.append(source)
    log = _ContextEventLog(source_events=source_events)
    await log.write(
        EventEnvelope(
            event_at_ms=1700000000000,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=4,
            occurred_at="2034-02-01T10:00:01Z",
            source_adapter="noise_filtering",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="long-thread-prior-proposal",
            payload={
                "proposal_id": "long-thread-prior-proposal",
                "target_kind": "task",
                "owner_member_id": "member-fixture",
                "title": "Old exact fixture ask",
                "candidate": {"description": "Old exact fixture ask"},
                "route": {"member_id": "member-fixture"},
                "source_event_id": "long-thread-source-00",
                "confidence": 0.8,
                "reasons": ["grounded_language_exact_quote_v1"],
                "parent_intent_id": None,
                "candidate_key": "long-thread-old-candidate",
                "extraction_event_id": None,
                "eligibility_rule_ids": ["grounded_language_exact_quote_v1"],
                "source_occurred_at": "2034-02-01T10:00:00Z",
                "interpretation": {
                    "contract": "triage-context-interpretation-v2",
                    "context_fingerprint": "b" * 64,
                    "thread_key": "old-long-thread",
                    "anchor_event_id": "long-thread-source-00",
                    "compact_summary": "Old exact fixture ask",
                    "case_summary": "Old context.",
                    "why_member": "Old context.",
                    "next_step": "Review.",
                    "thread_state": "unclear",
                    "action_owner": "member",
                    "lifecycle_state": "open",
                    "unresolved_questions": [],
                    "evidence_refs": [
                        {
                            "source_event_id": "long-thread-source-00",
                            "quote": "Routine fixture update 0.",
                        }
                    ],
                    "context_source_event_ids": ["long-thread-source-00"],
                    "context_sources": [
                        {
                            "source_event_id": "long-thread-source-00",
                            "relation": "thread",
                            "reason_codes": ["provider_thread_id"],
                            "relationship_source_event_ids": [],
                        }
                    ],
                    "related_source_event_ids": [],
                },
            },
        )
    )

    async def no_candidate(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result()

    await NoiseFilteringPipeline().handle(
        source_events[-1], _make_ctx(log, no_candidate)
    )

    superseded = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.superseded"
    ]
    assert len(superseded) == 1
    assert superseded[0].payload["proposal_id"] == "long-thread-prior-proposal"


@pytest.mark.asyncio
async def test_wholly_quoted_reply_cannot_become_a_candidate() -> None:
    log = _FakeEventLog()

    async def quoted_candidate(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "title": "Wire fixture funds",
                    "evidence_quote": "Please wire the fixture funds.",
                    "candidate": {},
                    "confidence": 0.99,
                }
            ]
        )

    event = _messaging_received_event(
        body_text=(
            "On Tuesday, Fixture Sender wrote:\r\n> Please wire the fixture funds."
        )
    )
    await NoiseFilteringPipeline().handle(event, _make_ctx(log, quoted_candidate))

    assert not any(call.envelope.type == "triage.item.proposed" for call in log.calls)
    assessment = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert assessment.payload["assessment_outcome"] == "no_candidate"


@pytest.mark.asyncio
async def test_multipart_html_quote_boundary_blocks_plain_text_candidate() -> None:
    log = _FakeEventLog()
    calls: list[Any] = []

    async def quoted_candidate(*args: Any, **kwargs: Any) -> SkillResult:
        calls.append((args, kwargs))
        return _make_skill_result(
            candidates=[
                {
                    "target_kind": "task",
                    "title": "Wire fixture funds",
                    "evidence_quote": "Please wire the fixture funds.",
                    "candidate": {},
                    "confidence": 0.99,
                }
            ]
        )

    event = _messaging_received_event(body_text="Please wire the fixture funds.")
    event["payload"]["body_html"] = (
        '<div class="gmail_quote">Please wire the fixture funds.</div>'
    )

    await NoiseFilteringPipeline().handle(event, _make_ctx(log, quoted_candidate))

    assert calls == []
    assert not any(call.envelope.type == "triage.item.proposed" for call in log.calls)
    assessment = next(
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.intent.features_extracted"
    )
    assert assessment.payload["assessment_outcome"] == "no_candidate"


@pytest.mark.asyncio
async def test_replay_supersedes_untouched_weak_cross_thread_candidate() -> None:
    current = _messaging_received_event(
        body_text="A normal fixture update with no new ask."
    )
    current.update(
        {
            "event_id": "weak-source-current",
            "occurred_at": "2034-02-01T11:00:00Z",
            "source_account_id": "fixture-account",
        }
    )
    current["payload"].update(
        {
            "thread_id": "thread-current",
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "subject": "Fixture Holdings update",
            "received_at": "2034-02-01T11:00:00Z",
        }
    )
    weak_related = _messaging_received_event(
        body_text="Another Fixture Holdings account update."
    )
    weak_related.update(
        {
            "event_id": "weak-source-unrelated",
            "occurred_at": "2034-02-01T10:00:00Z",
            "source_account_id": "fixture-account",
        }
    )
    weak_related["payload"].update(
        {
            "thread_id": "thread-unrelated",
            "provider": "fixture",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "subject": "Fixture Holdings account",
            "received_at": "2034-02-01T10:00:00Z",
        }
    )
    log = _ContextEventLog(source_events=[weak_related, current])
    await log.write(
        EventEnvelope(
            event_at_ms=1700000000000,
            tenant_id="tenant-test",
            type="triage.item.proposed",
            schema_version=3,
            occurred_at="2034-02-01T11:00:01Z",
            source_adapter="noise_filtering",
            source_account_id="pipeline",
            owner_scope="private:member-fixture",
            visibility_scope="private:member-fixture",
            sensitivity="normal",
            idempotency_key="weak-context-prior-proposal",
            payload={
                "proposal_id": "weak-context-prior-proposal",
                "target_kind": "task",
                "owner_member_id": "member-fixture",
                "title": "Act on the unrelated fixture account",
                "candidate": {},
                "route": {"member_id": "member-fixture"},
                "source_event_id": "weak-source-current",
                "candidate_key": "weak-context-candidate",
                "eligibility_rule_ids": ["exact_source_quote_grounded_v1"],
                "confidence": 0.8,
                "reasons": ["legacy weak context"],
                "interpretation": {
                    "context_fingerprint": "b" * 64,
                    "thread_key": "legacy-weak-context",
                    "compact_summary": "Legacy weak context",
                    "related_source_event_ids": ["weak-source-unrelated"],
                },
            },
        )
    )

    async def no_candidate(*args: Any, **kwargs: Any) -> SkillResult:
        return _make_skill_result()

    await NoiseFilteringPipeline().handle(current, _make_ctx(log, no_candidate))

    superseded = [
        call.envelope
        for call in log.calls
        if call.envelope.type == "triage.item.superseded"
    ]
    assert len(superseded) == 1
    assert superseded[0].payload["proposal_id"] == "weak-context-prior-proposal"
    assert superseded[0].payload["reason"] == "eligibility_rule_replaced"
