"""Shared five-surface presentation contracts remain descriptive only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from adminme.surface_contracts import (
    attention_explanation,
    provider_action_state,
    surface_freshness,
    surface_link,
)


NOW = datetime(2026, 8, 6, 14, tzinfo=timezone.utc)


def test_exact_link_encodes_object_without_authorizing_action() -> None:
    value = surface_link(
        "comms",
        object_kind="thread",
        object_id="gmail:account:thread/one",
        evidence_event_ids=["event-a"],
    )
    assert value["href"] == "/member/comms?thread=gmail%3Aaccount%3Athread%2Fone"
    assert value["evidence_event_ids"] == ["event-a"]


def test_attention_and_provider_action_contracts_name_the_decision_boundary() -> None:
    attention = attention_explanation(
        rule_id="comms.focus-v2",
        reasons=["Explicit reply signal"],
        signals={"reply_signal": "required"},
    )
    assert attention["decision_maker"] == "rules_not_model"
    action = provider_action_state(
        provider="google_tasks",
        provider_account_id="fixture-account",
        action="complete",
        available=True,
        reason="Provider capability is available",
    )
    assert action["available"] is True
    assert action["authorization"] == "not_granted_by_this_contract"


def test_freshness_distinguishes_empty_stale_degraded_and_not_configured() -> None:
    empty = surface_freshness(
        generated_at=NOW,
        latest_source_at=None,
        stale_after_seconds=60,
        row_count=0,
    )
    stale = surface_freshness(
        generated_at=NOW,
        latest_source_at=NOW - timedelta(minutes=2),
        stale_after_seconds=60,
        row_count=1,
    )
    degraded = surface_freshness(
        generated_at=NOW,
        latest_source_at=NOW,
        stale_after_seconds=60,
        row_count=1,
        degraded_reason="Provider checkpoint failed",
    )
    disabled = surface_freshness(
        generated_at=NOW,
        latest_source_at=None,
        stale_after_seconds=60,
        row_count=0,
        configured=False,
    )
    assert [empty["state"], stale["state"], degraded["state"], disabled["state"]] == [
        "healthy_empty",
        "stale",
        "degraded",
        "not_configured",
    ]
