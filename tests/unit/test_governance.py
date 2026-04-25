"""
Unit tests for adminme.lib.governance.

Covers the three-layer guardedWrite per CONSOLE_PATTERNS.md §3 + DIAGRAMS.md
§3 + SYSTEM_INVARIANTS.md §6.5-6.8:

- agent allowlist (layer 1) — glob patterns, agent-id derivation
- governance action_gate (layer 2) — allow / review / deny / hard_refuse
- rate limit (layer 3) — sliding-window mechanics per CONSOLE_PATTERNS.md §4

Plus the cross-cutting invariants:
- short-circuit-on-first-refusal ordering [§6.6]
- write.denied.layer_failed correctness for each layer
- hard_refuse non-overridable even with admin-equivalent session [§6.7]
- review_request event structure [§6.8]
- forbidden-party hard-refuse coverage
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from adminme.events.log import EventLog
from adminme.lib.governance import (
    ActionGateConfig,
    AgentAllowlist,
    GuardedWrite,
    RateLimiter,
    derive_agent_id,
    load_agent_allowlist,
    load_governance_config,
)
from adminme.lib.session import Session, build_internal_session

TEST_KEY = b"g" * 32

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
GOVERNANCE_FIXTURE = FIXTURES / "governance" / "sample_governance.yaml"
AUTHORITY_FIXTURE = FIXTURES / "authority" / "sample_authority.yaml"


def _principal_session(tenant_id: str = "tenant-a") -> Session:
    """Construct a principal-role session for tests.

    Uses build_internal_session as a tenant-agnostic factory; tests do not
    care about a specific household identity (per [§12.4])."""
    return build_internal_session(
        actor="member-principal", role="principal", tenant_id=tenant_id
    )


def _user_session(member: str = "member-principal") -> Session:
    """Construct a session that derives to ``user:<member>`` agent_id."""
    # Use Session directly to control the source field.
    return Session(
        tenant_id="tenant-a",
        auth_member_id=member,
        auth_role="principal",
        view_member_id=member,
        view_role="principal",
        dm_scope="per_channel_peer",
        source="node_console",
    )


def _xlsx_reverse_session() -> Session:
    return build_internal_session(
        actor="xlsx_reverse", role="device", tenant_id="tenant-a"
    )


@pytest.fixture
async def log(tmp_path: Path):
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    try:
        yield log
    finally:
        await log.close()


@pytest.fixture
def config() -> ActionGateConfig:
    return load_governance_config(GOVERNANCE_FIXTURE)


@pytest.fixture
def allowlist() -> AgentAllowlist:
    return load_agent_allowlist(AUTHORITY_FIXTURE)


# ---------------------------------------------------------------------------
# Glob pattern matching in AgentAllowlist
# ---------------------------------------------------------------------------


def test_allowlist_exact_match(allowlist: AgentAllowlist) -> None:
    assert allowlist.is_allowed("daemon:xlsx_workbooks", "xlsx.regenerated")


def test_allowlist_glob_match(allowlist: AgentAllowlist) -> None:
    # daemon:xlsx_reverse → task.*
    assert allowlist.is_allowed("daemon:xlsx_reverse", "task.created")
    assert allowlist.is_allowed("daemon:xlsx_reverse", "task.deleted")


def test_allowlist_wildcard_agent(allowlist: AgentAllowlist) -> None:
    assert allowlist.is_allowed("user:any-member-id", "task.created")
    assert allowlist.is_allowed("user:other-member-id", "outbound.send")


def test_allowlist_refuses_unmatched_action(allowlist: AgentAllowlist) -> None:
    # daemon:xlsx_reverse cannot emit outbound.send
    assert not allowlist.is_allowed("daemon:xlsx_reverse", "outbound.send")


def test_allowlist_refuses_unknown_agent(allowlist: AgentAllowlist) -> None:
    assert not allowlist.is_allowed("daemon:not_registered", "task.created")


def test_allowlist_glob_does_not_overmatch_segment_boundaries() -> None:
    # 'task.*' must not match 'tasks.created' (different prefix segment).
    al = AgentAllowlist([("user:*", ["task.*"])])
    assert al.is_allowed("user:abc", "task.created")
    assert not al.is_allowed("user:abc", "tasks.created")


# ---------------------------------------------------------------------------
# Session → agent_id derivation
# ---------------------------------------------------------------------------


def test_derive_agent_id_xlsx_reverse_daemon() -> None:
    s = _xlsx_reverse_session()
    assert derive_agent_id(s) == "daemon:xlsx_reverse"


def test_derive_agent_id_node_console_user() -> None:
    s = _user_session("member-abc")
    assert derive_agent_id(s) == "user:member-abc"


def test_derive_agent_id_internal() -> None:
    s = build_internal_session("internal_caller", "device", "tenant-a")
    assert derive_agent_id(s) == "system:internal"


# ---------------------------------------------------------------------------
# RateLimiter (sliding window)
# ---------------------------------------------------------------------------


def test_rate_limiter_admits_first_call() -> None:
    rl = RateLimiter()
    assert rl.check_and_record("k", window_s=60, max_n=3)


def test_rate_limiter_admits_up_to_max_then_denies() -> None:
    rl = RateLimiter()
    assert rl.check_and_record("k", window_s=60, max_n=2)
    assert rl.check_and_record("k", window_s=60, max_n=2)
    assert not rl.check_and_record("k", window_s=60, max_n=2)


def test_rate_limiter_decision_carries_retry_after_s() -> None:
    rl = RateLimiter()
    rl.check_and_record("k", window_s=60, max_n=1)
    decision = rl.decide("k", window_s=60, max_n=1)
    assert decision.allowed is False
    # retry_after_s rounded up; window is 60s so it must be 1..60.
    assert decision.retry_after_s is not None
    assert 1 <= decision.retry_after_s <= 60


def test_rate_limiter_keys_isolated() -> None:
    rl = RateLimiter()
    assert rl.check_and_record("a", window_s=60, max_n=1)
    # Different key still has budget.
    assert rl.check_and_record("b", window_s=60, max_n=1)
    # Same key as 'a' is now full.
    assert not rl.check_and_record("a", window_s=60, max_n=1)


def test_rate_limiter_window_reopens_after_passage() -> None:
    """Use injected time_fn to advance virtual time past the window."""
    now = [1000.0]
    rl = RateLimiter(time_fn=lambda: now[0])
    assert rl.check_and_record("k", window_s=10, max_n=1)
    assert not rl.check_and_record("k", window_s=10, max_n=1)
    now[0] += 11
    # Window has passed; bucket should re-prune and admit.
    assert rl.check_and_record("k", window_s=10, max_n=1)


# ---------------------------------------------------------------------------
# Config loaders
# ---------------------------------------------------------------------------


def test_governance_config_loads_action_gates(config: ActionGateConfig) -> None:
    assert config.gate("task.create") == "allow"
    assert config.gate("task.delete") == "review"
    assert config.gate("send_as_principal") == "hard_refuse"
    assert config.gate("noise.create") == "deny"


def test_governance_config_unknown_action_defaults_allow(
    config: ActionGateConfig,
) -> None:
    assert config.gate("anything.never_seen") == "allow"


def test_governance_config_rate_limit_default(config: ActionGateConfig) -> None:
    rl = config.rate_limit_for("anything.never_seen")
    assert rl.window_s == 60
    assert rl.max_n == 60


def test_governance_config_per_action_rate_limit(config: ActionGateConfig) -> None:
    rl = config.rate_limit_for("outbound.send")
    assert rl.window_s == 60
    assert rl.max_n == 20


def test_governance_config_carries_forbidden_parties(
    config: ActionGateConfig,
) -> None:
    parties = {tuple(d.items()) for d in config.forbidden_outbound_parties}
    assert (("send_to", "opposing_counsel"),) in parties


# ---------------------------------------------------------------------------
# GuardedWrite — three-layer ordering + per-layer denials
# ---------------------------------------------------------------------------


@pytest.fixture
async def gw(
    log: EventLog,
    config: ActionGateConfig,
    allowlist: AgentAllowlist,
):
    return GuardedWrite(
        config=config,
        limiter=RateLimiter(),
        allowlist=allowlist,
        event_log=log,
    )


async def _read_events_of_type(log: EventLog, type_: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    async for ev in log.read_since(types=[type_]):
        out.append(ev)
    return out


async def test_layer1_allowlist_denial(
    gw: GuardedWrite, log: EventLog
) -> None:
    # daemon:xlsx_reverse cannot do outbound.send.
    s = _xlsx_reverse_session()
    result = await gw.check(s, "outbound.send", {"to": "x"})
    assert result.pass_ is False
    assert result.layer_failed == "allowlist"
    denials = await _read_events_of_type(log, "write.denied")
    assert len(denials) == 1
    assert denials[0]["payload"]["layer_failed"] == "allowlist"
    assert denials[0]["payload"]["agent_id"] == "daemon:xlsx_reverse"


async def test_layer2_governance_deny(
    gw: GuardedWrite, log: EventLog
) -> None:
    s = _user_session()
    # noise.create is gated as 'deny' but the user:* agent_id allows it.
    # Wait — user:* allowlist doesn't include noise.create, so allowlist
    # would refuse first. To exercise governance deny we need an agent
    # with a wide allowlist and a denied action.  system:internal has
    # actions: ["*"], so use that.
    s = build_internal_session("internal_caller", "device", "tenant-a")
    result = await gw.check(s, "noise.create", {"id": "n1"})
    assert result.pass_ is False
    assert result.layer_failed == "governance"
    assert result.reason == "deny"
    denials = await _read_events_of_type(log, "write.denied")
    assert len(denials) == 1
    assert denials[0]["payload"]["reason"] == "denied_by_governance"


async def test_layer2_hard_refuse_non_overridable(
    gw: GuardedWrite, log: EventLog
) -> None:
    """Even an admin-equivalent (system:internal, ['*'] allowlist) session
    cannot bypass hard_refuse [§6.7]."""
    s = build_internal_session("internal_caller", "device", "tenant-a")
    result = await gw.check(s, "send_as_principal", {})
    assert result.pass_ is False
    assert result.layer_failed == "governance"
    assert result.reason == "hard_refuse"
    denials = await _read_events_of_type(log, "write.denied")
    assert len(denials) == 1
    assert denials[0]["payload"]["reason"] == "hard_refuse_by_governance"


async def test_layer2_review_emits_review_request_not_denial(
    gw: GuardedWrite, log: EventLog
) -> None:
    s = build_internal_session("internal_caller", "device", "tenant-a")
    result = await gw.check(s, "task.delete", {"task_id": "t1"})
    assert result.pass_ is False
    assert result.layer_failed == "governance"
    assert result.reason == "held_for_review"
    assert result.review_id is not None
    # No write.denied for review path; one review_request instead.
    denials = await _read_events_of_type(log, "write.denied")
    reviews = await _read_events_of_type(log, "review_request")
    assert denials == []
    assert len(reviews) == 1
    assert reviews[0]["payload"]["action"] == "task.delete"
    assert reviews[0]["payload"]["review_id"] == result.review_id


async def test_layer3_rate_limit_exhaustion(
    log: EventLog,
    config: ActionGateConfig,
    allowlist: AgentAllowlist,
) -> None:
    # burst.action: window 1s, max 2.  But governance.yaml doesn't have
    # an allowlist entry for it.  Use system:internal which has '*'.
    # Rebuild config with burst.action gated as 'allow' (the default).
    # The fixture's burst.action gate is implicitly 'allow' (not in dict).
    gw = GuardedWrite(
        config=config,
        limiter=RateLimiter(),
        allowlist=allowlist,
        event_log=log,
    )
    s = build_internal_session("internal_caller", "device", "tenant-a")
    r1 = await gw.check(s, "burst.action", {})
    r2 = await gw.check(s, "burst.action", {})
    r3 = await gw.check(s, "burst.action", {})
    assert r1.pass_ is True
    assert r2.pass_ is True
    assert r3.pass_ is False
    assert r3.layer_failed == "rate_limit"
    assert r3.retry_after_s is not None and r3.retry_after_s >= 1
    denials = await _read_events_of_type(log, "write.denied")
    assert len(denials) == 1
    assert denials[0]["payload"]["layer_failed"] == "rate_limit"
    assert denials[0]["payload"]["retry_after_s"] == r3.retry_after_s


# ---------------------------------------------------------------------------
# Short-circuit ordering: layer-1 refusal does NOT touch layers 2/3
# ---------------------------------------------------------------------------


async def test_short_circuit_layer1_does_not_consume_rate_budget(
    gw: GuardedWrite, log: EventLog
) -> None:
    """A layer-1 refusal must not record a timestamp in the rate-limit
    bucket — otherwise the audit log would conflate ineligible attempts
    with eligible-but-throttled ones [CONSOLE_PATTERNS.md §3]."""
    s = _xlsx_reverse_session()
    # outbound.send is forbidden for daemon:xlsx_reverse.
    for _ in range(50):
        result = await gw.check(s, "outbound.send", {})
        assert result.pass_ is False
        assert result.layer_failed == "allowlist"
    # All 50 are allowlist-denied, none rate-limit-denied.
    denials = await _read_events_of_type(log, "write.denied")
    assert len(denials) == 50
    assert all(d["payload"]["layer_failed"] == "allowlist" for d in denials)


async def test_short_circuit_governance_runs_before_rate_limit(
    gw: GuardedWrite, log: EventLog
) -> None:
    """A hard_refuse must short-circuit rather than first hit the rate
    limiter (which would also consume budget)."""
    s = build_internal_session("internal_caller", "device", "tenant-a")
    for _ in range(5):
        result = await gw.check(s, "send_as_principal", {})
        assert result.layer_failed == "governance"
        assert result.reason == "hard_refuse"
    # Now an allowed action should still work — rate budget not consumed
    # by hard_refuse attempts.
    result = await gw.check(s, "task.create", {"task_id": "x", "title": "x"})
    assert result.pass_ is True


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_all_three_pass(gw: GuardedWrite, log: EventLog) -> None:
    s = _user_session()
    result = await gw.check(s, "task.create", {"task_id": "t1", "title": "x"})
    assert result.pass_ is True
    assert result.layer_failed is None
    # No denial emitted on the happy path.
    denials = await _read_events_of_type(log, "write.denied")
    assert denials == []


# ---------------------------------------------------------------------------
# write.denied event structure correctness
# ---------------------------------------------------------------------------


async def test_write_denied_event_carries_full_attribution(
    gw: GuardedWrite, log: EventLog
) -> None:
    s = _xlsx_reverse_session()
    payload = {"to": "someone", "body": "hi"}
    result = await gw.check(s, "outbound.send", payload)
    assert result.pass_ is False
    denials = await _read_events_of_type(log, "write.denied")
    p = denials[0]["payload"]
    assert p["layer_failed"] == "allowlist"
    assert p["agent_id"] == "daemon:xlsx_reverse"
    assert p["action"] == "outbound.send"
    # payload_echo carries the original
    assert p["payload_echo"] == payload
    # actor_identity attribution per UT-7 carry-forward
    assert p["actor_identity"] == s.auth_member_id


async def test_review_request_event_carries_full_payload(
    gw: GuardedWrite, log: EventLog
) -> None:
    s = build_internal_session("internal_caller", "device", "tenant-a")
    payload = {"task_id": "t1"}
    result = await gw.check(s, "task.delete", payload)
    reviews = await _read_events_of_type(log, "review_request")
    assert len(reviews) == 1
    p = reviews[0]["payload"]
    assert p["payload"] == payload
    assert p["action"] == "task.delete"
    assert p["review_id"] == result.review_id


# ---------------------------------------------------------------------------
# Forbidden-party config plumbing
# ---------------------------------------------------------------------------


def test_forbidden_outbound_parties_loaded_from_governance_config(
    config: ActionGateConfig,
) -> None:
    items = {tuple(d.items()) for d in config.forbidden_outbound_parties}
    # Three entries from sample_governance.yaml.
    assert (("send_to", "opposing_counsel"),) in items
    assert (("reference_in_outbound", "privileged_medical"),) in items
    assert (("reference_in_outbound", "privileged_legal"),) in items
