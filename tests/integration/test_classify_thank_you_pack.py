"""Integration tests for the `classify_thank_you_candidate` pack.

Three fixture-driven tests (kleins_hosted_us, reciprocal_coffee,
coparent_pickup) exercise the full wrapper round-trip against a mocked
`/tools/invoke` gateway, plus one handler-direct test for the
urgency-coercion safety net per [REFERENCE_EXAMPLES.md §3 lines 1389-1395].

Per 09a's failure-mode-handler-direct discipline, every test calls
`run_skill()` (or the handler) directly; we never route through a
bus + subscriber.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from adminme.events.log import EventLog
from adminme.events.registry import ensure_autoloaded
from adminme.lib.session import Session
from adminme.lib.skill_runner.pack_loader import invalidate_cache, load_pack
from adminme.lib.skill_runner.wrapper import (
    OPENCLAW_GATEWAY_URL,
    SkillContext,
    SkillResult,
    _Runtime,
    _set_runtime_for_tests,
    run_skill,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = REPO_ROOT / "packs" / "skills" / "classify_thank_you_candidate"
FIXTURE_DIR = PACK_ROOT / "tests" / "fixtures"

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


def _session() -> Session:
    return Session(  # fixture:tenant_data:ok
        tenant_id="stice-test",
        auth_member_id="m-test",
        auth_role="principal",
        view_member_id="m-test",
        view_role="principal",
        dm_scope="per_channel_peer",
        source="product_api_internal",
        correlation_id="corr-thank-you-1",
    )


class _MockTransport(httpx.MockTransport):
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
    transport: httpx.MockTransport,
) -> _Runtime:
    raw_dir = tmp_path / "raw_events"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def client_factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=10.0)

    return _Runtime(
        event_log=event_log,
        raw_events_dir=raw_dir,
        httpx_client_factory=client_factory,
    )


def _ok_responder(parsed_json: dict, *, invocation_id: str = "inv-thank-you-1"):
    def responder(request: httpx.Request) -> httpx.Response:
        result: dict[str, Any] = {
            "details": {"json": parsed_json},
            "invocation_id": invocation_id,
            "tokens_in": 120,
            "tokens_out": 60,
            "cost_usd": 0.0002,
        }
        return httpx.Response(200, json={"ok": True, "result": result})

    return responder


async def _read_all_events(log: EventLog) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for ev in log.read_since():
        events.append(ev)
    return events


def _load_fixture(name: str) -> dict[str, Any]:
    with (FIXTURE_DIR / f"{name}.yaml").open() as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict)
    return data


def _gateway_response_from_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Build a plausible model response that satisfies the fixture's
    expected_output constraints with concrete confidence."""
    expected = fixture["expected_output"]
    confidence_min = float(expected["confidence_min"])
    concrete_confidence = min(1.0, confidence_min + 0.05)
    reason = str(expected["reasons_must_include_any_of"][0])
    response: dict[str, Any] = {
        "is_candidate": bool(expected["is_candidate"]),
        "confidence": concrete_confidence,
        "reasons": [reason],
    }
    if expected["is_candidate"]:
        response["urgency"] = expected["urgency"]
        response["suggested_medium"] = expected["suggested_medium"]
    return response


def _assert_matches_expected(output: dict[str, Any], fixture: dict[str, Any]) -> None:
    expected = fixture["expected_output"]
    assert output["is_candidate"] is bool(expected["is_candidate"])
    assert output["confidence"] >= float(expected["confidence_min"])
    if expected["is_candidate"]:
        assert output["urgency"] == expected["urgency"]
        assert output["suggested_medium"] == expected["suggested_medium"]
    must_include = [s.lower() for s in expected["reasons_must_include_any_of"]]
    rendered = " | ".join(str(r).lower() for r in output["reasons"])
    assert any(needle in rendered for needle in must_include), (
        f"none of {must_include!r} in reasons {output['reasons']!r}"
    )


async def _run_fixture(
    fixture_name: str, event_log: EventLog, tmp_path: Path
) -> SkillResult:
    fixture = _load_fixture(fixture_name)
    transport = _MockTransport(_ok_responder(_gateway_response_from_fixture(fixture)))
    runtime = _runtime_for(event_log, tmp_path, transport=transport)
    _set_runtime_for_tests(runtime)
    try:
        ctx = SkillContext(session=_session())
        result = await run_skill(
            "classify_thank_you_candidate", fixture["input"], ctx
        )
    finally:
        _set_runtime_for_tests(None)

    assert isinstance(result, SkillResult)
    _assert_matches_expected(result.output, fixture)

    # Exactly one skill.call.recorded event landed.
    events = await _read_all_events(event_log)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "skill.call.recorded"
    assert ev["payload"]["skill_name"] == "skill:classify_thank_you_candidate"

    # Request body went to the OpenClaw gateway URL.
    assert len(transport.requests) == 1
    assert str(transport.requests[0].url) == OPENCLAW_GATEWAY_URL

    return result


async def test_kleins_hosted_us_is_candidate(
    event_log: EventLog, tmp_path: Path
) -> None:
    await _run_fixture("kleins_hosted_us", event_log, tmp_path)


async def test_reciprocal_coffee_not_candidate(
    event_log: EventLog, tmp_path: Path
) -> None:
    await _run_fixture("reciprocal_coffee", event_log, tmp_path)


async def test_coparent_pickup_not_candidate(
    event_log: EventLog, tmp_path: Path
) -> None:
    await _run_fixture("coparent_pickup", event_log, tmp_path)


def test_handler_direct_safety_net_for_missing_urgency() -> None:
    """Handler-direct test: kleins_hosted_us-shaped output minus urgency
    should be coerced to is_candidate=false with `missing_urgency` reason.
    Mirrors tests/unit/fixtures/handler_raises_pack's direct-call discipline.
    """
    invalidate_cache()
    loaded = load_pack(PACK_ROOT)
    assert loaded.handler_post_process is not None
    fixture = _load_fixture("kleins_hosted_us")

    # Build a malformed model output: dropped urgency / suggested_medium.
    raw = {
        "is_candidate": True,
        "confidence": 0.9,
        "reasons": list(fixture["expected_output"]["reasons_must_include_any_of"][:1]),
    }
    out = loaded.handler_post_process(raw, fixture["input"], None)
    assert out["is_candidate"] is False
    assert out["confidence"] == 0.9
    assert "missing_urgency" in out["reasons"]
