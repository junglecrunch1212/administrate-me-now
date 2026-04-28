"""RewardReadyV1 schema-registration canary.

Asserts the event type is registered at v1 per [D7] and that the
Pydantic model enforces the required-field contract from [BUILD.md
§1620, CONSOLE_PATTERNS.md §8].
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.domain import RewardReadyV1


def test_reward_ready_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("reward.ready", 1)
    assert model is RewardReadyV1
    assert registry.latest_version("reward.ready") == 1


def test_reward_ready_minimum_payload_validates() -> None:
    instance = RewardReadyV1(
        member_id="member-a",
        tier="done",
        template_id="default-done",
        template_text="✓",
    )
    assert instance.tier == "done"
    assert instance.triggering_task_id is None
    assert instance.triggering_commitment_id is None


def test_reward_ready_rejects_invalid_tier() -> None:
    with pytest.raises(ValidationError):
        RewardReadyV1(
            member_id="member-a",
            tier="bogus",  # type: ignore[arg-type]
            template_id="t",
            template_text="✓",
        )
