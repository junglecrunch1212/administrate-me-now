"""Handler-direct unit tests for noise_filtering.

Construct a fake PipelineContext with a stubbed run_skill_fn and verify
the four branches of ``handle()``: empty body returns silently, skill
success emits ``messaging.classified`` with the model's classification,
skill timeout / input-invalid emits the defensive default, and SMS
events feed the body field rather than body_text.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.lib.skill_runner import (
    OpenClawTimeout,
    SkillContext,
    SkillInputInvalid,
    SkillResult,
)
from adminme.pipelines.base import PipelineContext

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


def _make_ctx(event_log: Any, run_skill_fn: Any) -> PipelineContext:
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
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "private:member-fixture",
        "visibility_scope": "private:member-fixture",
        "occurred_at": "2026-08-08T12:00:00Z",
        "payload": {
            "member_id": "member-fixture",
            "source_channel": "gmail",
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
    assert envelope.payload["skill_version"] == "5.0.0"
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
    assert envelope.payload["skill_version"] == "5.0.0"
    assert log.calls[1].envelope.payload["reply_signal"] == "unknown"
    assert log.calls[2].envelope.payload["assessment_outcome"] == "failed"


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
    assert log.calls[2].envelope.schema_version == 3
    assert log.calls[3].envelope.schema_version == 2
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
    assert proposals[-1].idempotency_key.startswith("triage:message-candidate:v10:")


@pytest.mark.asyncio
async def test_reviewed_replay_uses_v9_after_prior_generations() -> None:
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
        "messaging:classified:v5:trigger-eid-001",
        "messaging:classified:v6:trigger-eid-001",
        "messaging:classified:v7:trigger-eid-001",
        "messaging:classified:v8:trigger-eid-001",
        "messaging:classified:v9:trigger-eid-001",
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
        proposal.idempotency_key.startswith("triage:message-candidate:v10:")
        for proposal in proposals
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
        assert superseded[0].schema_version == 2
        assert superseded[0].payload == {
            "proposal_id": proposal_id,
            "superseded_at": "2026-08-08T12:00:00Z",
            "replacement_source_event_id": source_event_id,
            "reason": "eligibility_rule_replaced",
        }
