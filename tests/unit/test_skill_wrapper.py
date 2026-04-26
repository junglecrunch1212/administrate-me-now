"""Tests for `adminme.lib.skill_runner.wrapper`.

The 14-test pyramid called out in prompt 09a:
  1. happy path against mocked /tools/invoke
  2. input validation fails
  3. sensitivity refusal
  4. scope refusal
  5. provider fallback (5xx -> next provider succeeds)
  6. all providers 5xx -> openclaw_unreachable
  7. malformed 200 envelope -> openclaw_malformed_response
  8. timeout -> openclaw_timeout
  9. handler raises -> handler_raised + defensive default returned
 10. output validation fails -> output_invalid + defensive default returned
 11. observation_mode_active + outbound-affecting pack -> suppressed
 12. dry_run=True -> suppressed
 13. inputs >50KB -> spilled to raw_events/skill_large_inputs/
 14. token/cost graceful degradation per [ADR-0002]

Per prompt-09a's failure-mode-handler-direct discipline, every test calls
`run_skill()` directly and asserts on the in-memory event log; we never
route through a bus + subscriber.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from adminme.events.log import EventLog
from adminme.events.registry import ensure_autoloaded
from adminme.lib.session import Session
from adminme.lib.skill_runner.pack_loader import invalidate_cache
from adminme.lib.skill_runner.wrapper import (
    OPENCLAW_GATEWAY_URL,
    OpenClawResponseMalformed,
    OpenClawTimeout,
    OpenClawUnreachable,
    SkillContext,
    SkillInputInvalid,
    SkillOutputInvalid,
    SkillResult,
    SkillScopeInsufficient,
    SkillSensitivityRefused,
    _Runtime,
    _set_runtime_for_tests,
    run_skill,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFY_TEST_PACK = REPO_ROOT / "packs" / "skills" / "classify_test"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

TEST_KEY = b"k" * 32


@pytest.fixture(autouse=True)
def _clear_pack_cache() -> None:
    invalidate_cache()
    yield
    invalidate_cache()


@pytest.fixture
async def event_log(tmp_path: Path):
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    ensure_autoloaded()
    try:
        yield log
    finally:
        await log.close()


def _session(tenant_id: str = "tenant-test") -> Session:
    return Session(
        tenant_id=tenant_id,
        auth_member_id="m-test",
        auth_role="principal",
        view_member_id="m-test",
        view_role="principal",
        dm_scope="per_channel_peer",
        source="product_api_internal",
        correlation_id="corr-test-1",
    )


class _MockTransport(httpx.MockTransport):
    """Records every outgoing request so tests can assert on body shape."""

    def __init__(self, responder):
        self.requests: list[httpx.Request] = []

        def _wrapped(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return responder(request)

        super().__init__(_wrapped)


def _runtime_for(
    event_log: EventLog,
    tmp_path: Path,
    *,
    transport: httpx.MockTransport | None = None,
) -> _Runtime:
    raw_dir = tmp_path / "raw_events"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if transport is None:
        client_factory = None
    else:
        def client_factory():
            return httpx.AsyncClient(transport=transport, timeout=10.0)
    return _Runtime(
        event_log=event_log,
        raw_events_dir=raw_dir,
        httpx_client_factory=client_factory,
    )


def _ok_responder(parsed_json: dict, *, invocation_id: str = "inv-1",
                  input_tokens: int | None = 11,
                  output_tokens: int | None = 22,
                  cost_usd: float | None = 0.0001):
    def responder(request: httpx.Request) -> httpx.Response:
        result: dict[str, Any] = {
            "details": {"json": parsed_json},
            "invocation_id": invocation_id,
        }
        if input_tokens is not None:
            result["tokens_in"] = input_tokens
        if output_tokens is not None:
            result["tokens_out"] = output_tokens
        if cost_usd is not None:
            result["cost_usd"] = cost_usd
        return httpx.Response(200, json={"ok": True, "result": result})
    return responder


async def _read_all_events(log: EventLog) -> list[dict[str, Any]]:
    events = []
    async for ev in log.read_since():
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_records_event_and_posts_correct_body(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        _ok_responder({"is_thing": True, "confidence": 0.9})
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        result = await run_skill("classify_test", {"text": "hi"}, ctx)
    finally:
        _set_runtime_for_tests(None)

    assert isinstance(result, SkillResult)
    assert result.output == {"is_thing": True, "confidence": 0.9}
    assert result.openclaw_invocation_id == "inv-1"
    assert result.provider == "anthropic/claude-haiku-4-5"
    assert result.input_tokens == 11
    assert result.output_tokens == 22

    # Single recorded event was emitted, with the expected envelope shape.
    events = await _read_all_events(event_log)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "skill.call.recorded"
    assert ev["schema_version"] == 2
    assert ev["correlation_id"] == "corr-test-1"
    assert ev["actor_identity"] == "m-test"
    assert ev["payload"]["skill_name"] == "skill:classify_test"
    assert ev["payload"]["provider"] == "anthropic/claude-haiku-4-5"

    # Request body matches ADR-0002 contract.
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == OPENCLAW_GATEWAY_URL
    body = json.loads(request.content)
    assert body["tool"] == "llm-task"
    assert body["action"] == "json"
    assert body["dryRun"] is False
    args = body["args"]
    assert args["provider"] == "anthropic"
    assert args["model"] == "claude-haiku-4-5"
    assert args["maxTokens"] == 200
    assert args["timeoutMs"] == 5000
    assert args["input"] == {"text": "hi"}
    assert "schema" in args


# ---------------------------------------------------------------------------
# 2. Input validation fails
# ---------------------------------------------------------------------------


async def test_input_invalid_emits_failed_no_http_call(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(lambda r: pytest.fail("HTTP must not fire"))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(SkillInputInvalid):
            await run_skill("classify_test", {"wrong_field": "hi"}, ctx)
    finally:
        _set_runtime_for_tests(None)

    assert transport.requests == []
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["type"] == "skill.call.failed"
    assert events[0]["payload"]["failure_class"] == "input_invalid"


# ---------------------------------------------------------------------------
# 3. Sensitivity refusal
# ---------------------------------------------------------------------------


async def test_sensitivity_refused_emits_failed_no_http_call(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(lambda r: pytest.fail("HTTP must not fire"))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(SkillSensitivityRefused):
            await run_skill(
                "classify_test",
                {"text": "secret memo"},
                ctx,
                input_sensitivities={"text": "privileged"},
            )
    finally:
        _set_runtime_for_tests(None)

    assert transport.requests == []
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["payload"]["failure_class"] == "sensitivity_refused"


# ---------------------------------------------------------------------------
# 4. Scope refusal
# ---------------------------------------------------------------------------


async def test_scope_insufficient_emits_failed_no_http_call(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(lambda r: pytest.fail("HTTP must not fire"))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(SkillScopeInsufficient):
            await run_skill(
                str(FIXTURE_DIR / "scope_required_pack"),
                {"text": "hi"},
                ctx,
            )
    finally:
        _set_runtime_for_tests(None)

    assert transport.requests == []
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["payload"]["failure_class"] == "scope_insufficient"


# ---------------------------------------------------------------------------
# 5. Provider fallback
# ---------------------------------------------------------------------------


async def test_provider_fallback_uses_second_provider(
    event_log: EventLog, tmp_path: Path
) -> None:
    call_count = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(503, json={"ok": False, "error": {"type": "x", "message": "first 5xx"}})
        return _ok_responder({"is_thing": True, "confidence": 0.5})(request)

    transport = _MockTransport(responder)
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        result = await run_skill(
            str(FIXTURE_DIR / "multi_provider_pack"), {"text": "hi"}, ctx
        )
    finally:
        _set_runtime_for_tests(None)

    assert call_count["n"] == 2
    assert result.provider == "anthropic/claude-sonnet-4-6"
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["type"] == "skill.call.recorded"


# ---------------------------------------------------------------------------
# 6. All providers 5xx
# ---------------------------------------------------------------------------


async def test_all_providers_5xx_emits_unreachable(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        lambda r: httpx.Response(500, json={"ok": False, "error": {"type": "x", "message": "down"}})
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(OpenClawUnreachable):
            await run_skill(
                str(FIXTURE_DIR / "multi_provider_pack"), {"text": "hi"}, ctx
            )
    finally:
        _set_runtime_for_tests(None)

    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["payload"]["failure_class"] == "openclaw_unreachable"
    # Exactly three providers in pack manifest, three POST attempts.
    assert len(transport.requests) == 3


# ---------------------------------------------------------------------------
# 7. Malformed 200 response
# ---------------------------------------------------------------------------


async def test_malformed_response_envelope_raises(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        lambda r: httpx.Response(200, json={"result": {"details": {"json": {}}}})
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(OpenClawResponseMalformed):
            await run_skill("classify_test", {"text": "hi"}, ctx)
    finally:
        _set_runtime_for_tests(None)

    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["payload"]["failure_class"] == "openclaw_malformed_response"


# ---------------------------------------------------------------------------
# 8. Timeout
# ---------------------------------------------------------------------------


async def test_timeout_emits_openclaw_timeout(
    event_log: EventLog, tmp_path: Path
) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    transport = _MockTransport(responder)
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(OpenClawTimeout):
            await run_skill("classify_test", {"text": "hi"}, ctx)
    finally:
        _set_runtime_for_tests(None)

    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["payload"]["failure_class"] == "openclaw_timeout"


# ---------------------------------------------------------------------------
# 9. Handler raises -> defensive default
# ---------------------------------------------------------------------------


async def test_handler_raises_returns_defensive_default(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        _ok_responder({"is_thing": True, "confidence": 0.9})
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        result = await run_skill(
            str(FIXTURE_DIR / "handler_raises_pack"),
            {"text": "anything"},
            ctx,
        )
    finally:
        _set_runtime_for_tests(None)

    assert result.output == {"is_thing": False, "confidence": 0.0}
    events = await _read_all_events(event_log)
    failed = [e for e in events if e["type"] == "skill.call.failed"]
    assert len(failed) == 1
    assert failed[0]["payload"]["failure_class"] == "handler_raised"

    # raw_events/skill_validation_failures/ has the saved raw response.
    fail_dir = tmp_path / "raw_events" / "skill_validation_failures"
    assert fail_dir.exists()
    saved = list(fail_dir.iterdir())
    assert len(saved) == 1


# ---------------------------------------------------------------------------
# 10. Output validation fails
# ---------------------------------------------------------------------------


async def test_output_invalid_raises_when_no_defensive_default(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        _ok_responder({"is_thing": True, "confidence": 0.9})
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        with pytest.raises(SkillOutputInvalid):
            await run_skill(
                str(FIXTURE_DIR / "output_invalid_pack"),
                {"text": "hi"},
                ctx,
            )
    finally:
        _set_runtime_for_tests(None)

    events = await _read_all_events(event_log)
    failed = [e for e in events if e["type"] == "skill.call.failed"]
    assert len(failed) == 1
    assert failed[0]["payload"]["failure_class"] == "output_invalid"
    fail_dir = tmp_path / "raw_events" / "skill_validation_failures"
    assert any(fail_dir.iterdir())


# ---------------------------------------------------------------------------
# 11. Observation-mode short-circuit
# ---------------------------------------------------------------------------


async def test_observation_mode_short_circuits_outbound_affecting_pack(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(lambda r: pytest.fail("HTTP must not fire"))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(
            session=_session(),
            observation_mode_active=True,
        )
        result = await run_skill(
            str(FIXTURE_DIR / "outbound_affecting_pack"),
            {"text": "hi"},
            ctx,
        )
    finally:
        _set_runtime_for_tests(None)

    # Defensive default from on_failure surfaces as the result output.
    assert result.output == {"ok": False}
    assert result.provider == "suppressed"
    assert transport.requests == []
    events = await _read_all_events(event_log)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "skill.call.suppressed"
    assert ev["payload"]["reason"] == "observation_mode_active"


# ---------------------------------------------------------------------------
# 12. dry_run short-circuit
# ---------------------------------------------------------------------------


async def test_dry_run_suppresses_call(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(lambda r: pytest.fail("HTTP must not fire"))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session(), dry_run=True)
        result = await run_skill("classify_test", {"text": "hi"}, ctx)
    finally:
        _set_runtime_for_tests(None)

    # classify_test has no on_failure default → empty dict.
    assert result.output == {}
    assert result.provider == "suppressed"
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["payload"]["reason"] == "dry_run"


# ---------------------------------------------------------------------------
# 13. Large input spillover
# ---------------------------------------------------------------------------


async def test_large_inputs_spill_to_raw_events(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        _ok_responder({"is_thing": True, "confidence": 0.9})
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        big = {"text": "x" * 60_000}
        ctx = SkillContext(session=_session())
        result = await run_skill("classify_test", big, ctx)
    finally:
        _set_runtime_for_tests(None)

    assert result.output == {"is_thing": True, "confidence": 0.9}
    spill_dir = tmp_path / "raw_events" / "skill_large_inputs"
    spilled = list(spill_dir.iterdir())
    assert len(spilled) == 1

    events = await _read_all_events(event_log)
    recorded = [e for e in events if e["type"] == "skill.call.recorded"]
    assert len(recorded) == 1
    payload_inputs = recorded[0]["payload"]["inputs"]
    assert "_spilled_to" in payload_inputs
    assert payload_inputs["_spilled_to"].endswith(".json")


# ---------------------------------------------------------------------------
# 14. Token / cost graceful degradation
# ---------------------------------------------------------------------------


async def test_missing_tokens_in_response_records_none(
    event_log: EventLog, tmp_path: Path
) -> None:
    """Per [ADR-0002]: when /tools/invoke omits tokens_in / tokens_out /
    cost_usd, the wrapper records None rather than skipping the event."""
    transport = _MockTransport(
        _ok_responder(
            {"is_thing": True, "confidence": 0.9},
            input_tokens=None,
            output_tokens=None,
            cost_usd=None,
        )
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        result = await run_skill("classify_test", {"text": "hi"}, ctx)
    finally:
        _set_runtime_for_tests(None)

    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.cost_usd is None
    events = await _read_all_events(event_log)
    recorded = [e for e in events if e["type"] == "skill.call.recorded"]
    assert recorded[0]["payload"]["input_tokens"] is None
    assert recorded[0]["payload"]["cost_usd"] is None
