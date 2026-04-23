"""
Unit tests for prompt 04's schema-validation integration in EventLog.append.

Covers:
 1. One happy-path append per registered canonical schema (15 events).
 2. 5 invalid-payload appends across a variety of schemas.
 3. Unknown-schema strict mode (default) — append raises.
 4. Unknown-schema permissive mode (ADMINME_ALLOW_UNKNOWN_SCHEMAS=1) — warns
    and proceeds.
 5. skill.call.recorded v1 is not registered; v2 is the latest — exercises
    the reserved-slot behavior described in schemas/domain.py.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from adminme.events.envelope import EventEnvelope
from adminme.events.log import AppendValidationError, EventLog
from adminme.events.registry import registry

TEST_KEY = b"k" * 32


def _envelope(event_type: str, schema_version: int, payload: dict) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id="tenant-a",
        type=event_type,
        schema_version=schema_version,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test:inproc",
        source_account_id="test-account",
        owner_scope="shared:household",
        visibility_scope="shared:household",
        payload=payload,
    )


@pytest.fixture
async def log(tmp_path: Path):
    log = EventLog(tmp_path / "events.db", TEST_KEY)
    try:
        yield log
    finally:
        await log.close()


# ---------------------------------------------------------------------------
# 15 valid appends — one per registered canonical schema
# ---------------------------------------------------------------------------


async def test_valid_messaging_received(log: EventLog) -> None:
    env = _envelope(
        "messaging.received",
        1,
        {
            "source_channel": "email",
            "from_identifier": "alice@example.com",
            "to_identifier": "bob@example.com",
            "thread_id": "thr-1",
            "subject": "hi",
            "body_text": "hey",
            "body_html": None,
            "received_at": "2026-04-23T12:00:00Z",
            "attachments": [],
        },
    )
    assert await log.append(env)


async def test_valid_messaging_sent(log: EventLog) -> None:
    env = _envelope(
        "messaging.sent",
        1,
        {
            "source_channel": "email",
            "to_identifier": "alice@example.com",
            "thread_id": "thr-1",
            "subject": "re: hi",
            "body_text": "hey back",
            "sent_at": "2026-04-23T12:05:00Z",
            "delivery_status": "sent",
        },
    )
    assert await log.append(env)


async def test_valid_telephony_sms_received(log: EventLog) -> None:
    env = _envelope(
        "telephony.sms_received",
        1,
        {
            "from_number": "+15551234567",
            "to_number": "+15557654321",
            "body": "running late",
            "received_at": "2026-04-23T13:00:00Z",
            "carrier_message_id": None,
        },
    )
    assert await log.append(env)


async def test_valid_calendar_event_added(log: EventLog) -> None:
    env = _envelope(
        "calendar.event_added",
        1,
        {
            "source": "calendar:google",
            "external_event_id": "evt-123",
            "calendar_id": "cal-primary",
            "summary": "dentist",
            "start": "2026-04-24T09:00:00Z",
            "end": "2026-04-24T10:00:00Z",
            "location": "123 main st",
            "attendees": [],
            "body": None,
        },
    )
    assert await log.append(env)


async def test_valid_artifact_received(log: EventLog) -> None:
    env = _envelope(
        "artifact.received",
        1,
        {
            "source": "documents:gmail_attach",
            "external_artifact_id": "att-42",
            "mime_type": "application/pdf",
            "size_bytes": 12345,
            "filename": "invoice.pdf",
            "sha256": "a" * 64,
            "artifact_ref": "artifact://" + ("a" * 64),
            "received_at": "2026-04-23T14:00:00Z",
        },
    )
    assert await log.append(env)


async def test_valid_party_created(log: EventLog) -> None:
    env = _envelope(
        "party.created",
        1,
        {
            "party_id": "p-alice",
            "kind": "person",
            "display_name": "Alice Example",
            "sort_name": "Example, Alice",
            "nickname": None,
            "pronouns": "she/her",
            "notes": None,
            "attributes": {},
        },
    )
    assert await log.append(env)


async def test_valid_identifier_added(log: EventLog) -> None:
    env = _envelope(
        "identifier.added",
        1,
        {
            "identifier_id": "id-1",
            "party_id": "p-alice",
            "kind": "email",
            "value": "alice@example.com",
            "value_normalized": "alice@example.com",
            "verified": True,
            "primary_for_kind": True,
        },
    )
    assert await log.append(env)


async def test_valid_membership_added(log: EventLog) -> None:
    env = _envelope(
        "membership.added",
        1,
        {
            "membership_id": "m-1",
            "party_id": "p-alice",
            "parent_party_id": "p-household",
            "role": "member",
            "started_at": "2026-01-01T00:00:00Z",
            "attributes": {},
        },
    )
    assert await log.append(env)


async def test_valid_relationship_added(log: EventLog) -> None:
    env = _envelope(
        "relationship.added",
        1,
        {
            "relationship_id": "r-1",
            "party_a": "p-alice",
            "party_b": "p-bob",
            "label": "spouse",
            "direction": "mutual",
            "since": "2020-06-01",
            "attributes": {},
        },
    )
    assert await log.append(env)


async def test_valid_commitment_proposed(log: EventLog) -> None:
    env = _envelope(
        "commitment.proposed",
        1,
        {
            "commitment_id": "c-1",
            "kind": "reply",
            "owed_by_member_id": "m-james",
            "owed_to_party_id": "p-kate",
            "text_summary": "Reply to Kate's kitchen proposal",
            "suggested_due": "2026-04-26T00:00:00Z",
            "urgency": "this_week",
            "confidence": 0.87,
            "strength": "confident",
            "source_interaction_id": "int-1",
            "source_message_preview": "hey any interest in...",
            "classify_reasons": ["contains scheduling proposal"],
        },
    )
    assert await log.append(env)


async def test_valid_commitment_confirmed(log: EventLog) -> None:
    env = _envelope(
        "commitment.confirmed",
        1,
        {
            "commitment_id": "c-1",
            "confirmed_by_member_id": "m-james",
            "confirmed_at": "2026-04-23T15:00:00Z",
            "note": None,
        },
    )
    assert await log.append(env)


async def test_valid_task_created(log: EventLog) -> None:
    env = _envelope(
        "task.created",
        1,
        {
            "task_id": "t-1",
            "title": "Reply to Kate",
            "description": None,
            "owner_member_id": "m-james",
            "due": "2026-04-26T00:00:00Z",
            "energy": "low",
            "effort_min": 15,
            "source_commitment_id": "c-1",
        },
    )
    assert await log.append(env)


async def test_valid_task_completed(log: EventLog) -> None:
    env = _envelope(
        "task.completed",
        1,
        {
            "task_id": "t-1",
            "completed_by_member_id": "m-james",
            "completed_at": "2026-04-23T16:00:00Z",
            "note": "replied via email",
        },
    )
    assert await log.append(env)


async def test_valid_skill_call_recorded_v2(log: EventLog) -> None:
    env = _envelope(
        "skill.call.recorded",
        2,
        {
            "skill_name": "classify_commitment_candidate",
            "skill_version": "3.2.1",
            "openclaw_invocation_id": "inv-abc",
            "inputs": {"message": "hey"},
            "outputs": {"is_commitment": True, "confidence": 0.87},
            "provider": "anthropic",
            "input_tokens": 120,
            "output_tokens": 35,
            "cost_usd": 0.0023,
            "duration_ms": 842,
        },
    )
    assert await log.append(env)


async def test_valid_observation_suppressed(log: EventLog) -> None:
    env = _envelope(
        "observation.suppressed",
        1,
        {
            "attempted_action": "messaging.send",
            "attempted_at": "2026-04-23T17:00:00Z",
            "target_channel": "imessage",
            "target_identifier": "+15551234567",
            "would_have_sent_payload": {"body": "running late"},
            "reason": "observation_mode_active",
            "session_correlation_id": None,
        },
    )
    assert await log.append(env)


# ---------------------------------------------------------------------------
# Invalid payloads across 5 schemas
# ---------------------------------------------------------------------------


async def test_invalid_commitment_confidence_out_of_range(log: EventLog) -> None:
    env = _envelope(
        "commitment.proposed",
        1,
        {
            "commitment_id": "c-1",
            "kind": "reply",
            "owed_by_member_id": "m-a",
            "owed_to_party_id": "p-b",
            "text_summary": "x",
            "confidence": 1.5,  # violates le=1.0
            "strength": "confident",
        },
    )
    with pytest.raises(AppendValidationError, match="commitment.proposed"):
        await log.append(env)


async def test_invalid_messaging_received_missing_required(log: EventLog) -> None:
    env = _envelope(
        "messaging.received",
        1,
        {
            # missing source_channel
            "from_identifier": "a",
            "to_identifier": "b",
            "received_at": "2026-04-23T12:00:00Z",
        },
    )
    with pytest.raises(AppendValidationError, match="messaging.received"):
        await log.append(env)


async def test_invalid_telephony_bad_number_format(log: EventLog) -> None:
    env = _envelope(
        "telephony.sms_received",
        1,
        {
            "from_number": "not-a-number",
            "to_number": "+15557654321",
            "body": "hi",
            "received_at": "2026-04-23T13:00:00Z",
        },
    )
    with pytest.raises(AppendValidationError, match="telephony.sms_received"):
        await log.append(env)


async def test_invalid_artifact_bad_sha256(log: EventLog) -> None:
    env = _envelope(
        "artifact.received",
        1,
        {
            "source": "documents:test",
            "mime_type": "application/pdf",
            "size_bytes": 1,
            "sha256": "too short",
            "artifact_ref": "artifact://x",
            "received_at": "2026-04-23T14:00:00Z",
        },
    )
    with pytest.raises(AppendValidationError, match="artifact.received"):
        await log.append(env)


async def test_invalid_task_negative_effort(log: EventLog) -> None:
    env = _envelope(
        "task.created",
        1,
        {
            "task_id": "t-1",
            "title": "do thing",
            "effort_min": -5,
        },
    )
    with pytest.raises(AppendValidationError, match="task.created"):
        await log.append(env)


# ---------------------------------------------------------------------------
# Unknown schema — strict (default) and permissive
# ---------------------------------------------------------------------------


async def test_unknown_schema_strict_raises(log: EventLog) -> None:
    env = _envelope("never.registered.thing", 1, {})
    with pytest.raises(AppendValidationError, match="no schema registered"):
        await log.append(env)


async def test_unknown_schema_permissive_proceeds(
    log: EventLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ADMINME_ALLOW_UNKNOWN_SCHEMAS", "1")
    env = _envelope("draft.thing", 1, {"foo": "bar"})
    eid = await log.append(env)
    got = await log.get(eid)
    assert got is not None
    assert got["type"] == "draft.thing"
    assert got["payload"] == {"foo": "bar"}


# ---------------------------------------------------------------------------
# skill.call.recorded reserved-v1 slot
# ---------------------------------------------------------------------------


async def test_skill_call_recorded_v1_is_unregistered_v2_is_latest() -> None:
    assert registry.get("skill.call.recorded", 1) is None
    assert registry.latest_version("skill.call.recorded") == 2


# ---------------------------------------------------------------------------
# 15 canonical schemas are all registered after autoload
# ---------------------------------------------------------------------------


def test_fifteen_canonical_schemas_registered() -> None:
    expected = {
        ("messaging.received", 1),
        ("messaging.sent", 1),
        ("telephony.sms_received", 1),
        ("calendar.event_added", 1),
        ("artifact.received", 1),
        ("party.created", 1),
        ("identifier.added", 1),
        ("membership.added", 1),
        ("relationship.added", 1),
        ("commitment.proposed", 1),
        ("commitment.confirmed", 1),
        ("task.created", 1),
        ("task.completed", 1),
        ("skill.call.recorded", 2),
        ("observation.suppressed", 1),
    }
    missing = expected - set(registry._by_key.keys())
    assert not missing, f"missing canonical schemas: {missing}"


# ---------------------------------------------------------------------------
# Correlation / causation kwargs per D8 addition 2
# ---------------------------------------------------------------------------


async def test_correlation_and_causation_kwargs_override_envelope(log: EventLog) -> None:
    parent = _envelope(
        "party.created",
        1,
        {
            "party_id": "p-a",
            "kind": "person",
            "display_name": "A",
            "sort_name": "A",
            "attributes": {},
        },
    )
    parent_id = await log.append(parent, correlation_id="corr-1", causation_id=None)

    child = _envelope(
        "identifier.added",
        1,
        {
            "identifier_id": "id-1",
            "party_id": "p-a",
            "kind": "email",
            "value": "a@example.com",
            "value_normalized": "a@example.com",
            "verified": False,
            "primary_for_kind": True,
        },
    )
    child_id = await log.append(
        child, correlation_id="corr-1", causation_id=parent_id
    )

    fetched_child = await log.get(child_id)
    assert fetched_child is not None
    assert fetched_child["correlation_id"] == "corr-1"
    assert fetched_child["causation_id"] == parent_id
