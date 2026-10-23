"""Tests for `adminme.lib.skill_runner.wrapper`.

The 14-test pyramid called out in prompt 09a:
  1. happy path against mocked /tools/invoke
  2. input validation fails
  3. sensitivity refusal
  4. scope refusal
  5. provider routing is delegated to one OpenClaw invocation
  6. routed 5xx -> openclaw_unreachable without local fallback
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

import asyncio
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from adminme.events.log import EventLog
from adminme.events.writer import EventWriter
from adminme.events.registry import ensure_autoloaded
from adminme.lib.session import Session
from adminme.lib.skill_runner.pack_loader import invalidate_cache
from adminme.lib.skill_runner.wrapper import (
    OPENCLAW_AGENT_ID,
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
    _emit_failed,
    _emit_recorded,
    run_skill,
)

import adminme.lib.skill_runner.wrapper as skill_wrapper

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSIFY_TEST_PACK = REPO_ROOT / "packs" / "skills" / "classify_test"
NOTE_EXTRACTION_PACK = REPO_ROOT / "packs" / "skills" / "extract_note_semantics"
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

TEST_KEY = b"k" * 32


class _NoopBus:
    async def notify(
        self, _event_id: str, *, event_types: frozenset[str] | None = None
    ) -> None:
        return None


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
    gateway_token: str | None = None,
) -> _Runtime:
    raw_dir = tmp_path / "raw_events"
    raw_dir.mkdir(parents=True, exist_ok=True)
    if transport is None:
        client_factory = None
    else:

        def client_factory():
            return httpx.AsyncClient(transport=transport, timeout=10.0)

    return _Runtime(
        event_sink=EventWriter(event_log, _NoopBus()),  # type: ignore[arg-type]
        event_log=event_log,
        raw_events_dir=raw_dir,
        httpx_client_factory=client_factory,
        gateway_token=gateway_token,
    )


def _ok_responder(
    parsed_json: dict,
    *,
    invocation_id: str = "inv-1",
    input_tokens: int | None = 11,
    output_tokens: int | None = 22,
    cost_usd: float | None = 0.0001,
    provider: str | None = "anthropic",
    model: str | None = "claude-haiku-4-5",
):
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
        if provider is not None:
            result["provider"] = provider
        if model is not None:
            result["model"] = model
        return httpx.Response(200, json={"ok": True, "result": result})

    return responder


async def _read_all_events(log: EventLog) -> list[dict[str, Any]]:
    events = []
    async for ev in log.read_since():
        events.append(ev)
    return events


@pytest.mark.parametrize(
    ("details_provenance", "legacy_provenance", "expected_provider"),
    [
        (
            {"provider": "openai", "model": "gpt-5.6-sol"},
            {},
            "openai/gpt-5.6-sol",
        ),
        (
            {"provider": "openai", "model": "gpt-5.6-sol"},
            {"provider": "legacy-provider", "model": "legacy-model"},
            "openai/gpt-5.6-sol",
        ),
        (
            {},
            {"provider": "legacy-provider", "model": "legacy-model"},
            "legacy-provider/legacy-model",
        ),
        (
            {"provider": "nested-provider"},
            {"model": "legacy-model"},
            "openclaw-routed",
        ),
        (
            {"model": "nested-model"},
            {"provider": "legacy-provider"},
            "openclaw-routed",
        ),
        (
            {"provider": "nested-provider"},
            {"provider": "legacy-provider", "model": "legacy-model"},
            "legacy-provider/legacy-model",
        ),
        (
            {"provider": 42, "model": "nested-model"},
            {},
            "openclaw-routed",
        ),
        (
            {"provider": "openai", "model": " "},
            {},
            "openclaw-routed",
        ),
    ],
)
async def test_routed_provenance_uses_complete_returned_pair_without_invented_usage(
    event_log: EventLog,
    tmp_path: Path,
    details_provenance: dict[str, Any],
    legacy_provenance: dict[str, Any],
    expected_provider: str,
) -> None:
    output = {"is_thing": True, "confidence": 0.9}
    transport = _MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(output)}],
                    "details": {"json": output, **details_provenance},
                    **legacy_provenance,
                },
            },
        )
    )
    _set_runtime_for_tests(_runtime_for(event_log, tmp_path, transport=transport))
    try:
        result = await run_skill(
            "classify_test", {"text": "hi"}, SkillContext(session=_session())
        )
    finally:
        _set_runtime_for_tests(None)

    assert result.output == output
    assert result.provider == expected_provider
    assert result.openclaw_invocation_id is None
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.cost_usd is None
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["type"] == "skill.call.recorded"
    assert events[0]["payload"]["provider"] == expected_provider
    for field in (
        "openclaw_invocation_id",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    ):
        assert events[0]["payload"][field] is None
    assert len(transport.requests) == 1
    args = json.loads(transport.requests[0].content)["args"]
    assert "provider" not in args
    assert "model" not in args


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


async def test_happy_path_records_event_and_posts_correct_body(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
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
    assert body["agentId"] == OPENCLAW_AGENT_ID == "main"
    assert body["sessionKey"] == (
        "agent:main:adminme:tenant-test:m-test:per_channel_peer"
    )
    args = body["args"]
    assert "provider" not in args
    assert "model" not in args
    assert args["maxTokens"] == 200
    assert args["timeoutMs"] == 5000
    assert args["input"] == {"text": "hi"}
    assert "schema" in args


async def test_openclaw_session_keys_preserve_member_isolation(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    first = _session()
    second = Session(
        tenant_id=first.tenant_id,
        auth_member_id="m-second",
        auth_role=first.auth_role,
        view_member_id="m-second",
        view_role=first.view_role,
        dm_scope=first.dm_scope,
        source=first.source,
        correlation_id="corr-test-2",
    )

    await run_skill(
        "classify_test",
        {"text": "first"},
        SkillContext(session=first),
        _runtime=runtime,
    )
    await run_skill(
        "classify_test",
        {"text": "second"},
        SkillContext(session=second),
        _runtime=runtime,
    )

    bodies = [json.loads(request.content) for request in transport.requests]
    assert [body["agentId"] for body in bodies] == ["main", "main"]
    assert bodies[0]["sessionKey"].startswith("agent:main:adminme:")
    assert bodies[1]["sessionKey"].startswith("agent:main:adminme:")
    assert bodies[0]["sessionKey"] != bodies[1]["sessionKey"]


async def test_explicit_runtime_gateway_token_does_not_use_ambient_environment(
    event_log: EventLog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_TOKEN", raising=False)
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(
        event_log,
        tmp_path,
        transport=transport,
        gateway_token="injected-gateway-token",
    )

    await run_skill(
        "classify_test",
        {"text": "hi"},
        SkillContext(session=_session()),
        _runtime=runtime,
    )

    assert len(transport.requests) == 1
    assert transport.requests[0].headers["Authorization"] == (
        "Bearer injected-gateway-token"
    )


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
# 5. OpenClaw-owned provider routing
# ---------------------------------------------------------------------------


async def test_provider_fallback_is_not_reimplemented_by_administrateme(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        _ok_responder(
            {"is_thing": True, "confidence": 0.5},
            provider="openrouter",
            model="routed-model",
        )
    )
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        result = await run_skill(
            str(FIXTURE_DIR / "multi_provider_pack"), {"text": "hi"}, ctx
        )
    finally:
        _set_runtime_for_tests(None)

    assert len(transport.requests) == 1
    assert result.provider == "openrouter/routed-model"
    args = json.loads(transport.requests[0].content)["args"]
    assert "provider" not in args
    assert "model" not in args
    events = await _read_all_events(event_log)
    assert len(events) == 1
    assert events[0]["type"] == "skill.call.recorded"


# ---------------------------------------------------------------------------
# 6. OpenClaw routed failure
# ---------------------------------------------------------------------------


async def test_routed_5xx_emits_unreachable_without_local_retry(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(
        lambda r: httpx.Response(
            500, json={"ok": False, "error": {"type": "x", "message": "down"}}
        )
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
    assert len(transport.requests) == 1


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
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
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
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
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


async def test_dry_run_suppresses_call(event_log: EventLog, tmp_path: Path) -> None:
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
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
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


async def test_reviewed_invocation_identity_reuses_exact_duplicate_result(
    event_log: EventLog, tmp_path: Path
) -> None:
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        first = SkillContext(
            session=_session(),
            triggering_event_id="source-event-1",
            invocation_idempotency_key="message-fingerprint-1",
        )
        second = SkillContext(
            session=_session(),
            triggering_event_id="source-event-2",
            invocation_idempotency_key="message-fingerprint-1",
        )
        first_result = await run_skill("classify_test", {"text": "same"}, first)
        second_result = await run_skill("classify_test", {"text": "same"}, second)
    finally:
        _set_runtime_for_tests(None)

    assert first_result == second_result
    assert len(transport.requests) == 1
    events = await _read_all_events(event_log)
    assert [event["type"] for event in events] == ["skill.call.recorded"]


async def test_concurrent_reviewed_invocation_identity_is_single_flight(
    event_log: EventLog, tmp_path: Path
) -> None:
    """Concurrent exact calls make one request and return one durable result."""

    request_count = 0

    async def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        await asyncio.sleep(0.01)
        return _ok_responder(
            {"is_thing": True, "confidence": 0.9},
            invocation_id="inv-concurrent",
        )(_request)

    runtime = _runtime_for(
        event_log,
        tmp_path,
        transport=httpx.MockTransport(responder),
    )
    context = SkillContext(
        session=_session(),
        triggering_event_id="source-event-concurrent",
        invocation_idempotency_key="message-fingerprint-concurrent",
    )

    first, second = await asyncio.gather(
        run_skill("classify_test", {"text": "same"}, context, _runtime=runtime),
        run_skill("classify_test", {"text": "same"}, context, _runtime=runtime),
    )

    assert request_count == 1
    assert first == second
    events = await _read_all_events(event_log)
    assert [event["type"] for event in events] == ["skill.call.recorded"]
    assert (
        first.openclaw_invocation_id == events[0]["payload"]["openclaw_invocation_id"]
    )


async def test_cross_runtime_exact_invocations_converge_on_first_recorded_result(
    event_log: EventLog, tmp_path: Path
) -> None:
    """The append boundary resolves the race even without a shared lock."""

    request_count = 0
    both_requests_started = asyncio.Event()

    async def responder(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        request_number = request_count
        if request_count == 2:
            both_requests_started.set()
        await asyncio.wait_for(both_requests_started.wait(), timeout=1)
        return _ok_responder(
            {
                "is_thing": request_number == 1,
                "confidence": 0.9 if request_number == 1 else 0.8,
            },
            invocation_id=f"inv-cross-runtime-{request_number}",
        )(_request)

    transport = httpx.MockTransport(responder)
    first_runtime = _runtime_for(event_log, tmp_path / "first", transport=transport)
    second_runtime = _runtime_for(event_log, tmp_path / "second", transport=transport)
    context = SkillContext(
        session=_session(),
        triggering_event_id="source-event-cross-runtime",
        invocation_idempotency_key="message-fingerprint-cross-runtime",
    )

    first, second = await asyncio.gather(
        run_skill(
            "classify_test",
            {"text": "same"},
            context,
            _runtime=first_runtime,
        ),
        run_skill(
            "classify_test",
            {"text": "same"},
            context,
            _runtime=second_runtime,
        ),
    )

    assert request_count == 2
    assert first == second
    events = await _read_all_events(event_log)
    assert [event["type"] for event in events] == ["skill.call.recorded"]


async def test_reviewed_generation_retries_old_causal_failure_once(
    event_log: EventLog, tmp_path: Path
) -> None:
    unavailable = _MockTransport(
        lambda _request: httpx.Response(503, text="fixture unavailable")
    )
    failed_runtime = _runtime_for(event_log, tmp_path, transport=unavailable)
    with pytest.raises(OpenClawUnreachable):
        await run_skill(
            "classify_test",
            {"text": "same"},
            SkillContext(
                session=_session(),
                triggering_event_id="source-event-retry",
            ),
            _runtime=failed_runtime,
        )

    recovered = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    recovered_runtime = _runtime_for(event_log, tmp_path, transport=recovered)
    retry_ctx = SkillContext(
        session=_session(),
        triggering_event_id="source-event-retry",
        invocation_idempotency_key="message-fingerprint-retry",
        retry_prior_failure=True,
    )
    first = await run_skill(
        "classify_test", {"text": "same"}, retry_ctx, _runtime=recovered_runtime
    )
    duplicate = await run_skill(
        "classify_test", {"text": "same"}, retry_ctx, _runtime=recovered_runtime
    )

    assert first == duplicate
    assert len(unavailable.requests) == 1
    assert len(recovered.requests) == 1
    events = await _read_all_events(event_log)
    assert [event["type"] for event in events] == [
        "skill.call.failed",
        "skill.call.recorded",
    ]


async def test_reviewed_generation_does_not_repeat_its_own_terminal_failure(
    event_log: EventLog, tmp_path: Path
) -> None:
    unavailable = _MockTransport(
        lambda _request: httpx.Response(503, text="fixture unavailable")
    )
    runtime = _runtime_for(event_log, tmp_path, transport=unavailable)
    base_ctx = SkillContext(
        session=_session(),
        triggering_event_id="source-event-current-failure",
        invocation_idempotency_key="message-fingerprint-current-failure",
        retry_prior_failure=True,
    )
    recovery_ctx = SkillContext(
        session=_session(),
        triggering_event_id="source-event-current-failure",
        invocation_idempotency_key=("message-fingerprint-current-failure:recovery-1"),
        retry_prior_failure=True,
    )

    with pytest.raises(OpenClawUnreachable):
        await run_skill("classify_test", {"text": "same"}, base_ctx, _runtime=runtime)
    with pytest.raises(OpenClawUnreachable):
        await run_skill(
            "classify_test", {"text": "same"}, recovery_ctx, _runtime=runtime
        )
    with pytest.raises(OpenClawUnreachable):
        await run_skill("classify_test", {"text": "same"}, base_ctx, _runtime=runtime)
    with pytest.raises(OpenClawUnreachable):
        await run_skill(
            "classify_test", {"text": "same"}, recovery_ctx, _runtime=runtime
        )

    assert len(unavailable.requests) == 2
    events = await _read_all_events(event_log)
    assert [event["type"] for event in events] == [
        "skill.call.failed",
        "skill.call.failed",
    ]


def _versioned_test_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "versioned-skill"
    shutil.copytree(CLASSIFY_TEST_PACK, pack)
    for name in ("pack.yaml", "SKILL.md"):
        path = pack / name
        path.write_text(path.read_text().replace("0.1.0", "8.0.0"))
    return pack


def _upgrade_test_pack(pack: Path) -> None:
    for name in ("pack.yaml", "SKILL.md"):
        path = pack / name
        text = path.read_text().replace("8.0.0", "8.0.1")
        if name == "SKILL.md":
            text = text.replace(
                "version: 8.0.1",
                "version: 8.0.1\ncompatible_success_versions: [8.0.0]",
            ).replace("timeout_seconds: 5", "timeout_seconds: 120")
        path.write_text(text)
    invalidate_cache()


@pytest.mark.parametrize("suffix", ["", ":recovery-1", ":recovery-2"])
@pytest.mark.parametrize("spilled", [False, True])
async def test_compatible_patch_preserves_clean_success_and_actual_provenance(
    event_log: EventLog, tmp_path: Path, suffix: str, spilled: bool
) -> None:
    pack = _versioned_test_pack(tmp_path)
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    base = "fixture-scoped-fingerprint"
    context = SkillContext(
        session=_session(),
        triggering_event_id="fixture-original-delivery",
        invocation_idempotency_key=base + suffix,
        owner_scope="private:m-test",
        visibility_scope="private:m-test",
    )
    inputs = {"text": "same" * (20_000 if spilled else 1)}
    original = await run_skill(str(pack), inputs, context, _runtime=runtime)
    baseline = await _read_all_events(event_log)
    _upgrade_test_pack(pack)
    # A duplicate source delivery can have a different event id while keeping
    # the exact scoped input fingerprint, including a successful retry.
    replay_context = replace(
        context,
        triggering_event_id="fixture-duplicate-delivery",
        invocation_idempotency_key=base,
        compatible_success_identities=(base + ":recovery-1", base + ":recovery-2"),
    )
    for _restart in range(2):
        invalidate_cache()
        restarted = _runtime_for(event_log, tmp_path, transport=transport)
        reused = await run_skill(str(pack), inputs, replay_context, _runtime=restarted)
        assert reused == original
        assert reused.skill_version == "8.0.0"
        assert reused.recorded_event_id == baseline[0]["event_id"]
    assert len(transport.requests) == 1
    assert await _read_all_events(event_log) == baseline


@pytest.mark.parametrize(
    "field", ["owner_scope", "visibility_scope", "sensitivity", "inputs"]
)
async def test_compatible_patch_refuses_mismatched_scope_or_inputs(
    event_log: EventLog, tmp_path: Path, field: str
) -> None:
    pack = _versioned_test_pack(tmp_path)
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    context = SkillContext(session=_session(), triggering_event_id="fixture-exact")
    await run_skill(str(pack), {"text": "same"}, context, _runtime=runtime)
    _upgrade_test_pack(pack)
    changed = context if field == "inputs" else replace(context, **{field: "different"})
    with pytest.raises((SkillInputInvalid, SkillOutputInvalid)):
        await run_skill(
            str(pack),
            {"text": "different" if field == "inputs" else "same"},
            changed,
            _runtime=runtime,
        )
    assert len(transport.requests) == 1


async def test_patch_does_not_renew_failure_without_authorized_recovery(
    event_log: EventLog, tmp_path: Path
) -> None:
    pack = _versioned_test_pack(tmp_path)
    unavailable = _MockTransport(lambda request: httpx.Response(503))
    context = SkillContext(
        session=_session(),
        triggering_event_id="fixture-failed-source",
        invocation_idempotency_key="fixture-failed-invocation",
        retry_prior_failure=True,
    )
    runtime = _runtime_for(event_log, tmp_path, transport=unavailable)
    with pytest.raises(OpenClawUnreachable):
        await run_skill(str(pack), {"text": "same"}, context, _runtime=runtime)
    failure = (await _read_all_events(event_log))[0]
    _upgrade_test_pack(pack)
    success = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=success)
    for suffix in ("", ":recovery-1", ":recovery-2"):
        with pytest.raises(OpenClawUnreachable):
            await run_skill(
                str(pack),
                {"text": "same"},
                replace(
                    context,
                    triggering_event_id="fixture-second-delivery",
                    invocation_idempotency_key="fixture-failed-invocation" + suffix,
                    compatible_success_identities=(
                        "fixture-failed-invocation",
                        "fixture-failed-invocation:recovery-1",
                        "fixture-failed-invocation:recovery-2",
                    ),
                ),
                _runtime=runtime,
            )
    assert success.requests == []
    recovery = replace(
        context,
        invocation_idempotency_key="fixture-authorized-recovery-request",
        compatible_failure_recovery_authorized=True,
    )
    result = await run_skill(str(pack), {"text": "same"}, recovery, _runtime=runtime)
    assert result.skill_version == "8.0.1"
    assert json.loads(success.requests[0].content)["args"]["timeoutMs"] == 120_000
    assert await event_log.get(failure["event_id"]) == failure
    assert len(success.requests) == 1


async def test_compatible_patch_refuses_failed_default_recorded_as_success(
    event_log: EventLog, tmp_path: Path
) -> None:
    pack = _versioned_test_pack(tmp_path)
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    context = SkillContext(session=_session(), triggering_event_id="fixture-default")
    await _emit_failed(
        runtime,
        context,
        pack_id_str="skill:classify_test",
        pack_version="8.0.0",
        failure_class="output_invalid",
        error_detail="fixture invalid model output",
        provider_attempted="fixture",
        duration_ms=1,
    )
    await _emit_recorded(
        runtime,
        context,
        pack_id_str="skill:classify_test",
        pack_version="8.0.0",
        invocation_id="fixture-default-call",
        inputs_payload={"text": "same"},
        outputs={"is_thing": False, "confidence": 0.0},
        provider="fixture",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        duration_ms=1,
    )
    _upgrade_test_pack(pack)
    with pytest.raises(SkillOutputInvalid):
        await run_skill(str(pack), {"text": "same"}, context, _runtime=runtime)
    assert transport.requests == []
    assert len(await _read_all_events(event_log)) == 2


@pytest.mark.parametrize("actual_note_pack", [False, True])
async def test_total_http_deadline_covers_injected_streaming_client(
    event_log: EventLog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual_note_pack: bool,
) -> None:
    if actual_note_pack:
        pack = NOTE_EXTRACTION_PACK
        inputs = {
            "body_text": "Call the school office.",
            "body_format": "plain",
            "source_kind": "member_submission",
        }
    else:
        pack = _versioned_test_pack(tmp_path)
        _upgrade_test_pack(pack)
        inputs = {"text": "same"}
    actual_timeout = asyncio.timeout
    deadlines = []
    requests = []

    def short_deadline(seconds):
        deadlines.append(seconds)
        return actual_timeout(0.01)

    class StalledClient:
        async def post(self, *args, **kwargs):
            requests.append(kwargs["json"])
            await asyncio.Event().wait()

    runtime = replace(
        _runtime_for(event_log, tmp_path), httpx_client_factory=StalledClient
    )
    monkeypatch.setattr(skill_wrapper.asyncio, "timeout", short_deadline)
    with pytest.raises(OpenClawTimeout):
        await run_skill(
            str(pack),
            inputs,
            SkillContext(session=_session(), triggering_event_id="fixture-stalled"),
            _runtime=runtime,
        )
    assert deadlines == [122.0]
    assert len(requests) == 1
    assert requests[0]["args"]["timeoutMs"] == 120_000
    [failure] = await _read_all_events(event_log)
    assert failure["type"] == "skill.call.failed"
    assert failure["payload"]["failure_class"] == "openclaw_timeout"
    assert failure["payload"]["skill_version"] == (
        "2.1.1" if actual_note_pack else "8.0.1"
    )


@pytest.mark.parametrize("prior_failed", [False, True])
async def test_note_budget_patch_preserves_exact_prior_terminal_receipt(
    event_log: EventLog, tmp_path: Path, prior_failed: bool
) -> None:
    predecessor = tmp_path / "note-extraction-predecessor"
    shutil.copytree(NOTE_EXTRACTION_PACK, predecessor)
    for name in ("pack.yaml", "SKILL.md"):
        path = predecessor / name
        path.write_text(
            path.read_text()
            .replace("compatible_success_versions: [2.1.0]\n", "")
            .replace("version: 2.1.1", "version: 2.1.0")
            .replace("timeout_seconds: 120", "timeout_seconds: 12")
        )
    inputs = {
        "body_text": "Call the school office.",
        "body_format": "plain",
        "source_kind": "member_submission",
    }
    context = SkillContext(
        session=_session(),
        triggering_event_id="fixture-note-budget-source",
        invocation_idempotency_key="fixture-note-budget-extraction",
        owner_scope="private:m-test",
        visibility_scope="private:m-test",
        require_clean_result=True,
    )
    output = {"candidates": [], "reasons": ["fixture reviewed evidence"]}

    def predecessor_response(request):
        assert json.loads(request.content)["args"]["timeoutMs"] == 12_000
        if prior_failed:
            raise httpx.ReadTimeout("fixture bounded extraction", request=request)
        return _ok_responder(output)(request)

    prior_transport = _MockTransport(predecessor_response)
    runtime = _runtime_for(event_log, tmp_path, transport=prior_transport)
    if prior_failed:
        with pytest.raises(OpenClawTimeout):
            await run_skill(str(predecessor), inputs, context, _runtime=runtime)
        original = None
    else:
        original = await run_skill(str(predecessor), inputs, context, _runtime=runtime)
    baseline = await _read_all_events(event_log)
    assert len(baseline) == 1
    assert baseline[0]["payload"]["skill_version"] == "2.1.0"

    current_transport = _MockTransport(_ok_responder(output))
    current_runtime = _runtime_for(event_log, tmp_path, transport=current_transport)
    if prior_failed:
        with pytest.raises(OpenClawTimeout):
            await run_skill(
                str(NOTE_EXTRACTION_PACK), inputs, context, _runtime=current_runtime
            )
    else:
        reused = await run_skill(
            str(NOTE_EXTRACTION_PACK), inputs, context, _runtime=current_runtime
        )
        assert reused == original
        assert reused.skill_version == "2.1.0"
        assert reused.recorded_event_id == baseline[0]["event_id"]
    assert current_transport.requests == []
    assert len(prior_transport.requests) == 1
    assert await _read_all_events(event_log) == baseline


@pytest.mark.parametrize("failure_class", ["handler_raised", "output_invalid"])
async def test_clean_result_option_refuses_fresh_and_same_version_cached_defaults(
    event_log: EventLog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_class: str,
) -> None:
    pack_path = _versioned_test_pack(tmp_path)
    actual_load = skill_wrapper.load_pack

    def bad_handler(raw, inputs, context):
        if failure_class == "handler_raised":
            raise ValueError("fixture invalid postprocessor")
        return {"fixture_invalid": True}

    def selected_pack(path):
        pack = actual_load(path)
        return replace(
            pack,
            handler_post_process=bad_handler,
            skill_frontmatter={
                **pack.skill_frontmatter,
                "on_failure": {"is_thing": False, "confidence": 0.0},
            },
        )

    monkeypatch.setattr(skill_wrapper, "load_pack", selected_pack)
    transport = _MockTransport(_ok_responder({"is_thing": True, "confidence": 0.9}))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    context = SkillContext(
        session=_session(),
        triggering_event_id="fixture-old-default-source",
        invocation_idempotency_key="fixture-old-default-identity",
    )
    # Existing consumers retain their opt-out/default behavior.
    default = await run_skill(
        str(pack_path), {"text": "same"}, context, _runtime=runtime
    )
    assert default.output == {"is_thing": False, "confidence": 0.0}
    baseline = await _read_all_events(event_log)
    assert [event["type"] for event in baseline] == [
        "skill.call.failed",
        "skill.call.recorded",
    ]
    error_type = (
        skill_wrapper.SkillHandlerError
        if failure_class == "handler_raised"
        else SkillOutputInvalid
    )
    strict = replace(context, require_clean_result=True)
    for _ in range(2):
        with pytest.raises(error_type):
            await run_skill(
                str(pack_path),
                {"text": "same"},
                strict,
                _runtime=_runtime_for(event_log, tmp_path, transport=transport),
            )
    assert len(transport.requests) == 1
    assert await _read_all_events(event_log) == baseline
    fresh = replace(
        strict,
        triggering_event_id="fixture-fresh-clean-source",
        invocation_idempotency_key="fixture-fresh-clean-identity",
    )
    for _ in range(2):
        with pytest.raises(error_type):
            await run_skill(str(pack_path), {"text": "same"}, fresh, _runtime=runtime)
    assert len(transport.requests) == 2
    added = (await _read_all_events(event_log))[len(baseline) :]
    assert len(added) == 1 and added[0]["type"] == "skill.call.failed"
    assert added[0]["payload"]["failure_class"] == failure_class
