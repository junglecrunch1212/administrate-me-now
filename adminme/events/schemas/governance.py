"""
Governance / observation-mode schemas.

Per SYSTEM_INVARIANTS.md §6 (observation mode, governance), DECISIONS.md §D5,
and ADMINISTRATEME_BUILD.md §AUTHORITY/§OBSERVATION/§GOVERNANCE (lines
2053-2168). All events here are emitted from product code or outbound
wrappers — not from projections — so the projection-emit canary in
scripts/verify_invariants.sh does not need updating [§2.2].

Five event types land in this module (08b):
- ``write.denied``        — guardedWrite refusal at one of the three layers
- ``review_request``      — held-for-review (action_gate == 'review')
- ``observation.suppressed`` — outbound() short-circuited an external side effect
- ``observation.enabled`` / ``observation.disabled`` — observation toggle audit
- ``external.sent``       — companion to suppressed; emitted on success path

All schemas register at v1 per [D7].
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from adminme.events.registry import registry


# ---------------------------------------------------------------------------
# observation.suppressed v1
#
# Carries the full would-have-sent payload so the observation-review pane can
# render exactly what would have fired. ``observation_mode_active`` defaults
# to True because the event is emitted only when observation is on; the field
# is preserved so downstream queries can join with ``external.sent`` without
# branching on event type. Per CONSOLE_PATTERNS.md §11 / DIAGRAMS.md §9.
# ---------------------------------------------------------------------------


class ObservationSuppressedV1(BaseModel):
    model_config = {"extra": "forbid"}
    attempted_action: str = Field(min_length=1)
    attempted_at: str = Field(min_length=1)
    target_channel: str = Field(min_length=1)
    target_identifier: str = Field(min_length=1)
    would_have_sent_payload: dict
    reason: Literal[
        "observation_mode_active",
        "governance_review",
        "governance_deny",
        "governance_hard_refuse",
        "rate_limit",
    ]
    session_correlation_id: str | None = None
    observation_mode_active: bool = True


# ---------------------------------------------------------------------------
# write.denied v1
#
# Emitted by guarded_write.check on any non-pass exit. ``layer_failed``
# attributes the refusal unambiguously per [§6.6]. ``payload_echo`` carries
# the original payload so audit can replay the attempt; sensitive content in
# payloads is the caller's responsibility to elide before invoking
# guarded_write.
# ---------------------------------------------------------------------------


class WriteDeniedV1(BaseModel):
    model_config = {"extra": "forbid"}
    layer_failed: Literal["allowlist", "governance", "rate_limit"]
    reason: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    payload_echo: dict
    review_id: str | None = None
    retry_after_s: int | None = None
    actor_identity: str | None = None


# ---------------------------------------------------------------------------
# review_request v1
#
# Emitted when action_gate == 'review'. The action is HELD; an operator
# decides later. Per [§6.8] / CONSOLE_PATTERNS.md §3.
# ---------------------------------------------------------------------------


class ReviewRequestV1(BaseModel):
    model_config = {"extra": "forbid"}
    review_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    payload: dict
    requested_at: str = Field(min_length=1)
    actor_identity: str | None = None


# ---------------------------------------------------------------------------
# observation.enabled / observation.disabled v1
#
# State-change audit. Per [§6.16] observation defaults to ON for new
# instances; explicit toggles emit one of these so the audit log records who
# flipped it and why.
# ---------------------------------------------------------------------------


class ObservationEnabledV1(BaseModel):
    model_config = {"extra": "forbid"}
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    prior_state: bool
    enabled_at: str = Field(min_length=1)


class ObservationDisabledV1(BaseModel):
    model_config = {"extra": "forbid"}
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    prior_state: bool
    disabled_at: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# external.sent v1
#
# Success-path companion to ``observation.suppressed``. Same payload shape
# (modulo the suppression-only fields) so a query can union the two streams
# to inspect "every outbound attempt, whether it fired or not". Emitted only
# from outbound() per [§6.14].
# ---------------------------------------------------------------------------


class ExternalSentV1(BaseModel):
    model_config = {"extra": "forbid"}
    action: str = Field(min_length=1)
    sent_at: str = Field(min_length=1)
    target_channel: str = Field(min_length=1)
    target_identifier: str = Field(min_length=1)
    payload: dict
    session_correlation_id: str | None = None


registry.register("observation.suppressed", 1, ObservationSuppressedV1)
registry.register("write.denied", 1, WriteDeniedV1)
registry.register("review_request", 1, ReviewRequestV1)
registry.register("observation.enabled", 1, ObservationEnabledV1)
registry.register("observation.disabled", 1, ObservationDisabledV1)
registry.register("external.sent", 1, ExternalSentV1)
