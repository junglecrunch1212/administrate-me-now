"""Schema-level round-trip tests for event types added in prompt 09a.

Three additions:
- `SkillCallRecordedV2` accepts `None` for token / cost fields per
  [ADR-0002] graceful-degradation clause.
- `SkillCallFailedV1` (system event) round-trips.
- `SkillCallSuppressedV1` (system event) round-trips.

The existing `test_event_validation.py` covers the v2 happy path with all
metric fields populated; this module extends to the relaxed-fields path.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry


@pytest.fixture(autouse=True, scope="module")
def _autoload_schemas() -> None:
    ensure_autoloaded()


def test_skill_call_recorded_v2_accepts_none_tokens_and_cost() -> None:
    # ADR-0002: when /tools/invoke response omits tokens_in / tokens_out /
    # cost_usd, the wrapper records None rather than skipping the event.
    payload = {
        "skill_name": "classify_test",
        "skill_version": "0.1.0",
        "openclaw_invocation_id": None,
        "inputs": {"text": "hi"},
        "outputs": {"is_thing": True, "confidence": 0.9},
        "provider": "anthropic/claude-haiku-4-5",
        "input_tokens": None,
        "output_tokens": None,
        "cost_usd": None,
        "duration_ms": 12,
    }
    model = registry.validate("skill.call.recorded", 2, payload)
    assert model.input_tokens is None
    assert model.output_tokens is None
    assert model.cost_usd is None


def test_skill_call_recorded_v2_still_accepts_populated_metrics() -> None:
    payload = {
        "skill_name": "classify_test",
        "skill_version": "0.1.0",
        "openclaw_invocation_id": "inv-1",
        "inputs": {"text": "hi"},
        "outputs": {"is_thing": True, "confidence": 0.9},
        "provider": "anthropic/claude-haiku-4-5",
        "input_tokens": 11,
        "output_tokens": 22,
        "cost_usd": 0.0001,
        "duration_ms": 12,
    }
    model = registry.validate("skill.call.recorded", 2, payload)
    assert model.input_tokens == 11
    assert model.cost_usd == pytest.approx(0.0001)


def test_skill_call_failed_v1_roundtrip() -> None:
    payload = {
        "skill_name": "classify_test",
        "skill_version": "0.1.0",
        "failure_class": "input_invalid",
        "error_detail": "missing required field 'text'",
        "correlation_id": "corr-1",
        "provider_attempted": None,
        "duration_ms_until_failure": 0,
    }
    model = registry.validate("skill.call.failed", 1, payload)
    assert model.failure_class == "input_invalid"
    assert model.duration_ms_until_failure == 0


def test_skill_call_failed_v1_rejects_unknown_failure_class() -> None:
    payload = {
        "skill_name": "classify_test",
        "skill_version": "0.1.0",
        "failure_class": "not_a_real_class",
        "error_detail": "x",
    }
    with pytest.raises(Exception):
        registry.validate("skill.call.failed", 1, payload)


def test_skill_call_suppressed_v1_roundtrip() -> None:
    payload = {
        "skill_name": "classify_test",
        "skill_version": "0.1.0",
        "reason": "observation_mode_active",
        "would_have_sent": {"text": "would have classified this"},
        "correlation_id": "corr-1",
    }
    model = registry.validate("skill.call.suppressed", 1, payload)
    assert model.reason == "observation_mode_active"


def test_skill_call_suppressed_v1_rejects_unknown_reason() -> None:
    payload = {
        "skill_name": "classify_test",
        "skill_version": "0.1.0",
        "reason": "vibes",
        "would_have_sent": {},
    }
    with pytest.raises(ValidationError):
        # Bypass registry's wrapper; we want the raw pydantic rejection.
        from adminme.events.schemas.system import SkillCallSuppressedV1

        SkillCallSuppressedV1.model_validate(payload)
