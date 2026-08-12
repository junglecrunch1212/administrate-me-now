"""CO-014 proofs for scoped, rebuildable context infrastructure."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlcipher3

from adminme.lib.session import build_internal_session
from adminme.projections.context_fabric import ContextFabricProjection
from adminme.projections.context_fabric.queries import (
    bounded_traversal,
    feedback_pattern_counts,
)


MEMBER = "fixture-context-member"  # fixture:tenant_data:ok
OTHER = "fixture-other-member"  # fixture:tenant_data:ok


def _conn(projection: ContextFabricProjection) -> sqlcipher3.Connection:
    conn = sqlcipher3.connect(":memory:")
    conn.row_factory = sqlcipher3.Row
    conn.executescript(Path(projection.schema_path).read_text("utf-8"))
    return conn


def _event(
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    member: str = MEMBER,
    causation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "event_at_ms": 1,
        "tenant_id": "fixture-context-tenant",  # fixture:tenant_data:ok
        "type": event_type,
        "schema_version": 1,
        "occurred_at": "2030-01-01T00:00:00Z",
        "recorded_at": "2030-01-01T00:00:00Z",
        "source_adapter": "fixture",
        "source_account_id": "fixture-account",
        "owner_scope": f"private:{member}",
        "visibility_scope": f"private:{member}",
        "sensitivity": "normal",
        "correlation_id": None,
        "causation_id": causation_id,
        "payload": payload,
    }


def test_context_fabric_replay_is_exact_and_model_edges_have_no_authority() -> None:
    projection = ContextFabricProjection()
    events = [
        _event(
            "source-1",
            "messaging.received",
            {
                "member_id": MEMBER,
                "provider": "gmail",
                "provider_account_id": "fixture-account",
                "workspace": "personal",
                "source_channel": "gmail",
                "thread_id": "provider-thread-1",
                "body_text": "private body must not be copied into the fabric",
            },
        ),
        _event(
            "proposal-event-1",
            "triage.item.proposed",
            {
                "proposal_id": "proposal-1",
                "target_kind": "task",
                "owner_member_id": MEMBER,
                "title": "Return the fixture form",
                "candidate": {},
                "source_event_id": "source-1",
                "reasons": ["exact fixture span"],
                "interpretation": {
                    "thread_key": "code-owned-thread-key",
                    "compact_summary": "Fixture form",
                    "related_source_event_ids": ["weak-related-source"],
                },
            },
            causation_id="source-1",
        ),
        _event(
            "sms-source-1",
            "telephony.sms_received",
            {
                "from_number": "+15555550101",
                "to_number": "+15555550102",
                "body": "private SMS body must not be copied into the fabric",
                "received_at": "2030-01-01T00:00:00Z",
                "carrier_message_id": "carrier-fixture-1",
            },
        ),
    ]
    first = _conn(projection)
    second = _conn(projection)
    for event in events:
        projection.apply(event, first)
        projection.apply(event, second)
    for event in events:
        projection.apply(event, first)
    tables = ["contexts", "context_nodes", "context_edges"]
    for table in tables:
        left = sorted(tuple(row) for row in first.execute(f"SELECT * FROM {table}"))
        right = sorted(tuple(row) for row in second.execute(f"SELECT * FROM {table}"))
        assert left == right
    columns = {
        row[1] for row in first.execute("PRAGMA table_info(context_nodes)").fetchall()
    }
    assert not {"body", "body_text", "subject", "content"} & columns
    source_node = first.execute(
        "SELECT provider, provider_account_id, workspace FROM context_nodes "
        "WHERE tenant_id=? AND node_id='event:source-1'",
        ("fixture-context-tenant",),
    ).fetchone()
    assert tuple(source_node) == ("gmail", "fixture-account", "personal")
    assert all(
        row[0] is None for row in first.execute("SELECT label FROM contexts").fetchall()
    )
    serialized_fabric = " ".join(
        str(tuple(row))
        for table in tables
        for row in first.execute(f"SELECT * FROM {table}")
    )
    assert "+15555550101" not in serialized_fabric
    assert "private SMS body" not in serialized_fabric
    sms_node = first.execute(
        "SELECT provider, provider_account_id FROM context_nodes "
        "WHERE tenant_id=? AND node_id='event:sms-source-1'",
        ("fixture-context-tenant",),
    ).fetchone()
    assert tuple(sms_node) == ("twilio_sms", "fixture-account")
    session = build_internal_session(MEMBER, "principal", "fixture-context-tenant")
    traversal = bounded_traversal(
        first,
        session,
        start_node_id="triage:proposal-1",
        member_id=MEMBER,
        max_depth=4,
        max_nodes=100,
    )
    assert "event:weak-related-source" not in traversal["node_ids"]
    assert all(edge["authority"] != "llm_proposed" for edge in traversal["edges"])
    assert len({edge["edge_id"] for edge in traversal["edges"]}) == len(
        traversal["edges"]
    )


def test_context_traversal_fails_closed_across_member_scope() -> None:
    projection = ContextFabricProjection()
    conn = _conn(projection)
    projection.apply(
        _event(
            "other-source",
            "messaging.received",
            {
                "member_id": OTHER,
                "provider": "gmail",
                "provider_account_id": "other-account",
                "workspace": "personal",
                "source_channel": "gmail",
                "thread_id": "other-thread",
            },
            member=OTHER,
        ),
        conn,
    )
    session = build_internal_session(MEMBER, "principal", "fixture-context-tenant")
    result = bounded_traversal(
        conn,
        session,
        start_node_id="event:other-source",
        member_id=MEMBER,
    )
    assert result["node_ids"] == []
    assert result["edges"] == []


def test_context_traversal_applies_role_and_sensitivity_scope() -> None:
    projection = ContextFabricProjection()
    conn = _conn(projection)
    sensitive = _event(
        "sensitive-source",
        "messaging.received",
        {
            "member_id": MEMBER,
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "source_channel": "gmail",
            "thread_id": "sensitive-thread",
        },
    )
    sensitive["sensitivity"] = "sensitive"
    projection.apply(sensitive, conn)
    child = build_internal_session(MEMBER, "child", "fixture-context-tenant")
    hidden = bounded_traversal(
        conn,
        child,
        start_node_id="event:sensitive-source",
        member_id=MEMBER,
    )
    assert hidden["node_ids"] == []
    principal = build_internal_session(MEMBER, "principal", "fixture-context-tenant")
    visible = bounded_traversal(
        conn,
        principal,
        start_node_id="event:sensitive-source",
        member_id=MEMBER,
    )
    assert "event:sensitive-source" in visible["node_ids"]


def test_context_split_moves_membership_and_scope_mismatch_is_ignored() -> None:
    projection = ContextFabricProjection()
    conn = _conn(projection)
    projection.apply(
        _event(
            "confirm-1",
            "context.membership.confirmed",
            {
                "context_id": "context-source",
                "object_kind": "task",
                "object_id": "task-one",
                "confirmed_by_member_id": MEMBER,
                "confirmed_at": "2030-01-01T00:00:00Z",
                "expected_last_event_id": None,
            },
        ),
        conn,
    )
    projection.apply(
        _event(
            "split-2",
            "context.split",
            {
                "source_context_id": "context-source",
                "target_context_id": "context-target",
                "object_kind": "task",
                "object_ids": ["task-one"],
                "split_by_member_id": MEMBER,
                "split_at": "2030-01-02T00:00:00Z",
            },
        ),
        conn,
    )
    edges = conn.execute(
        """SELECT to_node_id, status FROM context_edges
             WHERE tenant_id=? AND from_node_id='task:task-one'
             ORDER BY to_node_id""",
        ("fixture-context-tenant",),
    ).fetchall()
    assert [tuple(row) for row in edges] == [
        ("context-source", "revoked"),
        ("context-target", "active"),
    ]

    forged = _event(
        "feedback-3",
        "triage.feedback.recorded",
        {
            "feedback_id": "feedback-forged",
            "proposal_id": "proposal-forged",
            "member_id": OTHER,
            "reason": "spam",
            "recorded_at": "2030-01-03T00:00:00Z",
        },
    )
    projection.apply(forged, conn)
    assert conn.execute("SELECT COUNT(*) FROM triage_feedback").fetchone()[0] == 0


def test_matter_episode_uses_provider_source_time_not_ingestion_time() -> None:
    projection = ContextFabricProjection()
    conn = _conn(projection)
    base_payload = {
        "target_kind": "task",
        "owner_member_id": MEMBER,
        "title": "Review the fixture message",
        "candidate": {},
        "reasons": ["exact fixture span"],
        "interpretation": {
            "thread_key": "reused-provider-thread",
            "compact_summary": "Fixture review",
            "related_source_event_ids": [],
        },
    }
    first = _event(
        "proposal-time-1",
        "triage.item.proposed",
        {
            **base_payload,
            "proposal_id": "proposal-time-1",
            "source_event_id": "source-time-1",
            "source_occurred_at": "2024-01-31T15:00:00Z",
        },
        causation_id="source-time-1",
    )
    second = _event(
        "proposal-time-2",
        "triage.item.proposed",
        {
            **base_payload,
            "proposal_id": "proposal-time-2",
            "source_event_id": "source-time-2",
            "source_occurred_at": "2026-08-10T16:36:06Z",
        },
        causation_id="source-time-2",
    )

    projection.apply(first, conn)
    projection.apply(second, conn)

    sessions = conn.execute(
        """SELECT sequence, first_proposed_at, source_count
             FROM context_matter_sessions ORDER BY sequence"""
    ).fetchall()
    assert [tuple(row) for row in sessions] == [
        (0, "2024-01-31T15:00:00Z", 1),
        (1, "2026-08-10T16:36:06Z", 1),
    ]


def test_exact_context_corrections_are_replayable_and_reversible() -> None:
    projection = ContextFabricProjection()
    first = _conn(projection)
    rebuilt = _conn(projection)
    rejected = _event(
        "correction-1",
        "context.relationship.rejected",
        {
            "relationship_id": "relationship-fixture",
            "source_event_id": "source-one",
            "related_source_event_id": "source-two",
            "member_id": MEMBER,
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "algorithm": "message-context-pack-v2",
            "rejected_by_member_id": MEMBER,
            "rejected_at": "2030-01-01T00:00:00Z",
            "reason": "unrelated_sources",
        },
    )
    confirmed = _event(
        "correction-2",
        "context.relationship.confirmed",
        {
            "relationship_id": "relationship-fixture",
            "source_event_id": "source-one",
            "related_source_event_id": "source-two",
            "member_id": MEMBER,
            "provider": "gmail",
            "provider_account_id": "fixture-account",
            "workspace": "personal",
            "algorithm": "message-context-pack-v2",
            "confirmed_by_member_id": MEMBER,
            "confirmed_at": "2030-01-02T00:00:00Z",
            "reason": "member_reinstated",
        },
    )
    corrected = _event(
        "correction-3",
        "context.source_text.corrected",
        {
            "correction_id": "text-fixture",
            "source_event_id": "source-one",
            "proposal_id": "proposal-fixture",
            "candidate_key": "candidate-fixture",
            "member_id": MEMBER,
            "algorithm": "authored-message-text-v2",
            "authored_text_digest": "b" * 64,
            "evidence_start": 0,
            "evidence_end": 12,
            "corrected_by_member_id": MEMBER,
            "corrected_at": "2030-01-03T00:00:00Z",
            "reason": "quoted_or_forwarded_history",
            "evidence_quote_digest": "a" * 64,
        },
    )
    reinstated = _event(
        "correction-4",
        "context.source_text.reinstated",
        {
            "correction_id": "text-fixture",
            "source_event_id": "source-one",
            "member_id": MEMBER,
            "reinstated_by_member_id": MEMBER,
            "reinstated_at": "2030-01-04T00:00:00Z",
            "reason": "member_reinstated",
        },
    )
    events = [rejected, confirmed, corrected, reinstated]
    for event in events:
        projection.apply(event, first)
    for event in events:
        projection.apply(event, rebuilt)
    for event in events:
        projection.apply(event, first)

    relationship = first.execute(
        "SELECT algorithm, state, last_event_id FROM context_relationship_decisions"
    ).fetchone()
    source_text = first.execute(
        """SELECT state, evidence_quote_digest, last_event_id
             FROM context_source_text_corrections"""
    ).fetchone()
    assert tuple(relationship) == (
        "message-context-pack-v2",
        "confirmed",
        "correction-2",
    )
    assert tuple(source_text) == ("reinstated", "a" * 64, "correction-4")
    for table in (
        "context_relationship_decisions",
        "context_source_text_corrections",
    ):
        assert [tuple(row) for row in first.execute(f"SELECT * FROM {table}")] == [
            tuple(row) for row in rebuilt.execute(f"SELECT * FROM {table}")
        ]


def test_wrong_context_feedback_never_becomes_broad_sender_or_account_pattern() -> None:
    projection = ContextFabricProjection()
    conn = _conn(projection)
    for index in range(3):
        projection.apply(
            _event(
                f"feedback-{index}",
                "triage.feedback.recorded",
                {
                    "feedback_id": f"feedback-{index}",
                    "proposal_id": f"proposal-{index}",
                    "member_id": MEMBER,
                    "reason": "wrong_context",
                    "context_id": "context-exact-fixture",
                    "target_kind": "task",
                    "source_adapter": "gmail",
                    "provider_account_id": "fixture-account",
                    "workspace": "personal",
                    "sender_digest": "b" * 64,
                    "recorded_at": "2030-01-03T00:00:00Z",
                },
            ),
            conn,
        )
    session = build_internal_session(MEMBER, "principal", "fixture-context-tenant")

    patterns = feedback_pattern_counts(conn, session, member_id=MEMBER)

    assert patterns == [
        {
            "scope_kind": "context",
            "scope_value": "context-exact-fixture",
            "reason": "wrong_context",
            "occurrences": 3,
        }
    ]
