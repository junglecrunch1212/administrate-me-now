"""Handler-direct unit tests for identity_resolution.

Construct a fake PipelineContext and capture event_log.append calls,
exercising _classify_identifier (helper) and the three branches of
``handle()``: outbound-skip, miss-with-empty-candidates (party.created
+ identifier.added), miss-with-above-threshold-candidate
(identity.merge_suggested), miss-with-below-threshold-candidate
(party.created + identifier.added).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.pipelines.base import PipelineContext

PACK_ROOT = (
    Path(__file__).resolve().parents[2]
    / "packs"
    / "pipelines"
    / "identity_resolution"
)


def _import_handler_module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "test_identity_resolution_handler",
        PACK_ROOT / "handler.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_handler = _import_handler_module()
IdentityResolutionPipeline = _handler.IdentityResolutionPipeline
_classify_identifier = _handler._classify_identifier
_email_score = _handler._email_score


@dataclass
class _RecordedAppend:
    envelope: EventEnvelope
    correlation_id: str | None
    causation_id: str | None


@dataclass
class _FakeEventLog:
    calls: list[_RecordedAppend] = field(default_factory=list)
    _next_id: int = 0

    async def append(
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


def _make_ctx(event_log: Any) -> PipelineContext:
    # Session built without going through build_internal_session — we
    # only touch its tenant_id field if anything; the handler doesn't
    # read session attrs in the degenerate path. Use a minimal stand-in.
    from adminme.lib.session import build_internal_session

    session = build_internal_session("pipeline_runner", "device", "tenant-test")
    return PipelineContext(
        session=session,
        event_log=event_log,
        run_skill_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        outbound_fn=lambda *a, **kw: None,  # type: ignore[arg-type, return-value]
        guarded_write=None,
        observation_manager=None,
        triggering_event_id="trigger-eid-001",
        correlation_id="corr-001",
    )


def _messaging_received_event(
    *, source_channel: str = "gmail", from_id: str = "ada@example.com"
) -> dict[str, Any]:
    return {
        "event_id": "trigger-eid-001",
        "type": "messaging.received",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "source_channel": source_channel,
            "from_identifier": from_id,
            "to_identifier": "me@example.com",
            "received_at": EventEnvelope.now_utc_iso(),
        },
    }


def _telephony_sms_event(*, from_number: str = "+15551234567") -> dict[str, Any]:
    return {
        "event_id": "trigger-eid-002",
        "type": "telephony.sms_received",
        "event_at_ms": 1700000000000,
        "tenant_id": "tenant-test",
        "owner_scope": "shared:household",
        "visibility_scope": "shared:household",
        "payload": {
            "from_number": from_number,
            "to_number": "+15559999999",
            "body": "hello",
            "received_at": EventEnvelope.now_utc_iso(),
        },
    }


# ---------------------------------------------------------------------------
# helper: _classify_identifier
# ---------------------------------------------------------------------------


def test_email_classification() -> None:
    event = _messaging_received_event(
        source_channel="gmail", from_id="Ada@Example.com "
    )
    from_id, kind, value_normalized = _classify_identifier(
        event["type"], event["payload"]
    )
    assert from_id == "Ada@Example.com "
    assert kind == "email"
    assert value_normalized == "ada@example.com"


def test_imessage_classification() -> None:
    event = _messaging_received_event(
        source_channel="imessage_adminme", from_id="Ada@me.COM"
    )
    _, kind, value_normalized = _classify_identifier(
        event["type"], event["payload"]
    )
    assert kind == "imessage_handle"
    assert value_normalized == "ada@me.com"


def test_phone_classification() -> None:
    event = _telephony_sms_event(from_number="+1 (555) 123-4567")
    from_id, kind, value_normalized = _classify_identifier(
        event["type"], event["payload"]
    )
    assert from_id == "+1 (555) 123-4567"
    assert kind == "phone"
    assert value_normalized == "15551234567"


# ---------------------------------------------------------------------------
# handle(): branch coverage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messaging_sent_returns_without_emit() -> None:
    log = _FakeEventLog()
    ctx = _make_ctx(log)
    pipeline = IdentityResolutionPipeline()
    event = _messaging_received_event()
    event["type"] = "messaging.sent"
    await pipeline.handle(event, ctx)
    assert log.calls == []


@pytest.mark.asyncio
async def test_unknown_sender_emits_party_and_identifier() -> None:
    log = _FakeEventLog()
    ctx = _make_ctx(log)
    pipeline = IdentityResolutionPipeline(candidate_loader=lambda kind: [])
    event = _messaging_received_event(from_id="unknown@example.com")
    await pipeline.handle(event, ctx)
    assert len(log.calls) == 2
    assert log.calls[0].envelope.type == "party.created"
    assert log.calls[1].envelope.type == "identifier.added"
    for call in log.calls:
        assert call.causation_id == "trigger-eid-001"
        assert call.correlation_id == "corr-001"
    party_payload = log.calls[0].envelope.payload
    ident_payload = log.calls[1].envelope.payload
    assert party_payload["display_name"] == "unknown@example.com"
    assert ident_payload["party_id"] == party_payload["party_id"]
    assert ident_payload["kind"] == "email"
    assert ident_payload["value_normalized"] == "unknown@example.com"
    assert ident_payload["primary_for_kind"] is True
    assert ident_payload["verified"] is False


@pytest.mark.asyncio
async def test_above_threshold_emits_merge_suggested() -> None:
    # Existing identifier "ada@example.com" — incoming "adam@example.com"
    # — same domain, lev distance 1 over local part length 4 → score 0.75.
    # That's below threshold; bump similarity by feeding a closer candidate.
    # "ada@example.com" vs "adb@example.com" → lev=1, len=3 → 0.667. Still
    # too low. Use lev=1 over length 8: "augustus@example.com" vs
    # "augustub@example.com" → 1 - 1/8 = 0.875 ≥ 0.85. Use that.
    candidates = [
        {"party_id": "party_existing", "value_normalized": "augustub@example.com"}
    ]
    log = _FakeEventLog()
    ctx = _make_ctx(log)
    pipeline = IdentityResolutionPipeline(
        candidate_loader=lambda kind: candidates
    )
    event = _messaging_received_event(
        source_channel="gmail", from_id="augustus@example.com"
    )
    # Sanity-check the score so the test fails clearly if the heuristic
    # drifts.
    score = _email_score("augustus@example.com", "augustub@example.com")
    assert score >= 0.85, f"sanity check: score={score}"
    await pipeline.handle(event, ctx)
    assert len(log.calls) == 1
    assert log.calls[0].envelope.type == "identity.merge_suggested"
    payload = log.calls[0].envelope.payload
    assert payload["surviving_party_id"] == "party_existing"
    assert payload["candidate_value"] == "augustus@example.com"
    assert payload["candidate_kind"] == "email"
    assert payload["candidate_value_normalized"] == "augustus@example.com"
    assert payload["confidence"] == round(score, 4)
    assert payload["heuristic"] == "email_domain_match"
    assert payload["source_event_id"] == "trigger-eid-001"
    assert log.calls[0].causation_id == "trigger-eid-001"


@pytest.mark.asyncio
async def test_below_threshold_emits_party_created() -> None:
    # Different local-part lengths → score below 0.85.
    candidates = [
        {"party_id": "party_existing", "value_normalized": "bob@example.com"}
    ]
    log = _FakeEventLog()
    ctx = _make_ctx(log)
    pipeline = IdentityResolutionPipeline(
        candidate_loader=lambda kind: candidates
    )
    event = _messaging_received_event(
        source_channel="gmail", from_id="alice@example.com"
    )
    await pipeline.handle(event, ctx)
    types = [c.envelope.type for c in log.calls]
    assert types == ["party.created", "identifier.added"]
    assert all(c.causation_id == "trigger-eid-001" for c in log.calls)


@pytest.mark.asyncio
async def test_exact_match_returns_without_emit() -> None:
    candidates = [
        {"party_id": "party_existing", "value_normalized": "ada@example.com"}
    ]
    log = _FakeEventLog()
    ctx = _make_ctx(log)
    pipeline = IdentityResolutionPipeline(
        candidate_loader=lambda kind: candidates
    )
    event = _messaging_received_event(
        source_channel="gmail", from_id="ada@example.com"
    )
    await pipeline.handle(event, ctx)
    assert log.calls == []
