"""DigestComposedV1 and ParalysisTriggeredV1 schema-registration canaries.

Asserts both event types register at v1 per [D7], the Pydantic models
enforce the required-field contracts from [BUILD.md §1289] (digest
validation guard) and [BUILD.md §1297-1302] (deterministic paralysis
templates), and ``extra="forbid"`` rejects unknown payload keys —
matching the shape established by ``test_reward_ready_registered.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adminme.events.envelope import EventEnvelope
from adminme.events.registry import ensure_autoloaded, registry
from adminme.events.schemas.domain import DigestComposedV1, ParalysisTriggeredV1


def test_digest_composed_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("digest.composed", 1)
    assert model is DigestComposedV1
    assert registry.latest_version("digest.composed") == 1


def test_paralysis_triggered_registered_at_v1() -> None:
    ensure_autoloaded()
    model = registry.get("paralysis.triggered", 1)
    assert model is ParalysisTriggeredV1
    assert registry.latest_version("paralysis.triggered") == 1


def test_digest_composed_minimum_payload_validates() -> None:
    instance = DigestComposedV1(
        member_id="member-a",
        body_text="Today: 2 commitments, 1 calendar event.",
        profile_format="fog_aware",
        validation_failed=False,
        delivered=True,
        today_iso="2026-05-05",
    )
    assert instance.profile_format == "fog_aware"
    assert instance.delivered is True
    assert instance.validation_failed is False


def test_digest_composed_sentinel_path_validates() -> None:
    """Sentinel path per [BUILD.md §1289]: validation_failed=True with
    delivered=False and the canonical sentinel body text."""
    instance = DigestComposedV1(
        member_id="member-a",
        body_text="No morning brief available; underlying data changed.",
        profile_format="fog_aware",
        validation_failed=True,
        delivered=False,
        today_iso="2026-05-05",
    )
    assert instance.validation_failed is True
    assert instance.delivered is False


def test_digest_composed_rejects_invalid_profile_format() -> None:
    with pytest.raises(ValidationError):
        DigestComposedV1(
            member_id="member-a",
            body_text="x",
            profile_format="bogus",  # type: ignore[arg-type]
            validation_failed=False,
            delivered=True,
            today_iso="2026-05-05",
        )


def test_digest_composed_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        DigestComposedV1(
            member_id="member-a",
            body_text="x",
            profile_format="fog_aware",
            validation_failed=False,
            delivered=True,
            today_iso="2026-05-05",
            unknown_field="nope",  # type: ignore[call-arg]
        )


def test_paralysis_triggered_minimum_payload_validates() -> None:
    instance = ParalysisTriggeredV1(
        member_id="member-a",
        template_id="paralysis-low-1",
        template_text="One small thing: pick up your water glass.",
        triggered_at="2026-05-05T15:00:00Z",
    )
    assert instance.template_id == "paralysis-low-1"


def test_paralysis_triggered_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        ParalysisTriggeredV1(
            member_id="member-a",
            template_id="paralysis-low-1",
            template_text="One small thing.",
            triggered_at="2026-05-05T15:00:00Z",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_digest_composed_round_trips_through_envelope() -> None:
    envelope = EventEnvelope(
        event_at_ms=1700000000000,
        tenant_id="tenant-test",
        type="digest.composed",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="morning_digest",
        source_account_id="pipeline",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload={
            "member_id": "member-a",
            "body_text": "Today's plan.",
            "profile_format": "fog_aware",
            "validation_failed": False,
            "delivered": True,
            "today_iso": "2026-05-05",
        },
    )
    validated = registry.validate(
        "digest.composed", 1, envelope.payload
    )
    assert isinstance(validated, DigestComposedV1)


def test_paralysis_triggered_round_trips_through_envelope() -> None:
    envelope = EventEnvelope(
        event_at_ms=1700000000000,
        tenant_id="tenant-test",
        type="paralysis.triggered",
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="paralysis_detection",
        source_account_id="pipeline",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        sensitivity="normal",
        payload={
            "member_id": "member-a",
            "template_id": "paralysis-low-1",
            "template_text": "One small thing.",
            "triggered_at": "2026-05-05T15:00:00Z",
        },
    )
    validated = registry.validate(
        "paralysis.triggered", 1, envelope.payload
    )
    assert isinstance(validated, ParalysisTriggeredV1)
