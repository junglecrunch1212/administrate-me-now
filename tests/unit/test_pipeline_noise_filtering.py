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
        self.calls.append(
            _RecordedAppend(
                envelope=envelope,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        )
        self._next_id += 1
        return f"fake-event-id-{self._next_id}"


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
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "source_channel": "gmail",
            "from_identifier": "ada@example.com",
            "to_identifier": "me@example.com",
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
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "from_number": "+15551234567",
            "to_number": "+15559999999",
            "body": body,
            "received_at": EventEnvelope.now_utc_iso(),
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
async def test_empty_body_returns_without_emit() -> None:
    log = _FakeEventLog()
    calls: list[Any] = []

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        calls.append((args, kwargs))
        return _make_skill_result()

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    event = _messaging_received_event(body_text=None)
    await pipeline.handle(event, ctx)
    assert log.calls == []
    assert calls == []  # skill must not have been called


@pytest.mark.asyncio
async def test_skill_success_emits_classified() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(
        skill_id: str, inputs: dict[str, Any], skill_ctx: SkillContext
    ) -> SkillResult:
        assert skill_id == "skill:classify_message_nature"
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
    ]
    envelope = log.calls[0].envelope
    assert envelope.type == "messaging.classified"
    assert envelope.payload["classification"] == "transactional"
    assert envelope.payload["confidence"] == 0.91
    assert envelope.payload["skill_name"] == "classify_message_nature"
    assert envelope.payload["skill_version"] == "4.0.0"
    assert envelope.payload["source_event_id"] == "trigger-eid-001"
    assert log.calls[0].causation_id == "trigger-eid-001"


@pytest.mark.asyncio
async def test_skill_failure_emits_defensive_default() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise OpenClawTimeout("openclaw timed out")

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    await pipeline.handle(_messaging_received_event(), ctx)
    assert len(log.calls) == 2
    envelope = log.calls[0].envelope
    assert envelope.type == "messaging.classified"
    assert envelope.payload["classification"] == "personal"
    assert envelope.payload["confidence"] == 0.0
    assert envelope.payload["skill_version"] == "4.0.0"
    assert log.calls[1].envelope.payload["reply_signal"] == "unknown"


@pytest.mark.asyncio
async def test_skill_input_invalid_emits_defensive_default() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
        raise SkillInputInvalid("bad input shape")

    ctx = _make_ctx(log, stub_run_skill)
    pipeline = NoiseFilteringPipeline()
    await pipeline.handle(_messaging_received_event(), ctx)
    assert len(log.calls) == 2
    envelope = log.calls[0].envelope
    assert envelope.payload["classification"] == "personal"
    assert envelope.payload["confidence"] == 0.0


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
    assert len(log.calls) == 2
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
    assert log.calls[3].envelope.schema_version == 2
    assert log.calls[3].causation_id == "fake-event-id-3"


@pytest.mark.asyncio
async def test_candidates_require_an_exact_private_member_scope() -> None:
    log = _FakeEventLog()

    async def stub_run_skill(*args: Any, **kwargs: Any) -> SkillResult:
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
        "messaging.classified",
        "messaging.semantic_features_extracted",
    ]


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
