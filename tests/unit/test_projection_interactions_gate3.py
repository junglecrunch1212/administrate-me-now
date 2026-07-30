"""Gate 3 interaction projection upgrade and replay regression tests."""

from __future__ import annotations

from pathlib import Path

import sqlcipher3

from adminme.projections.interactions import InteractionsProjection
from adminme.projections.interactions.handlers import apply_event


def test_existing_interactions_schema_adds_columns_before_native_index() -> None:
    conn = sqlcipher3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE interactions (tenant_id TEXT, channel_specific TEXT)"
        )
        InteractionsProjection().migrate_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(interactions)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(interactions)")}
        assert {
            "native_message_id",
            "classification",
            "classification_confidence",
        } <= columns
        assert "idx_interactions_tenant_native_message" in indexes
    finally:
        conn.close()


def test_old_source_redelivery_cannot_regress_resolved_participant() -> None:
    projection = InteractionsProjection()
    conn = sqlcipher3.connect(":memory:")
    conn.row_factory = sqlcipher3.Row
    try:
        schema = Path(projection.schema_path).read_text(encoding="utf-8")
        conn.executescript(schema)
        projection.migrate_schema(conn)
        source = {
            "event_id": "ev_001",
            "event_at_ms": 1,
            "tenant_id": "tenant-a",
            "type": "messaging.received",
            "occurred_at": "2026-07-30T12:05:00Z",
            "owner_scope": "private:member-a",
            "visibility_scope": "private:member-a",
            "sensitivity": "normal",
            "payload": {
                "source_channel": "fixture-cat-a",
                "native_message_id": "native-1",
                "from_identifier": "sender@fixture.invalid",
                "to_identifier": "member@fixture.invalid",
                "received_at": "2026-07-30T12:05:00Z",
            },
        }
        resolved = {
            **source,
            "event_id": "ev_002",
            "type": "interaction.participant_resolved",
            "payload": {
                "source_event_id": "ev_001",
                "interaction_id": "ix_ev_001",
                "party_id": "party-sender",
                "role": "from",
                "identifier_kind": "email",
                "identifier_normalized": "sender@fixture.invalid",
            },
        }
        apply_event(source, conn)
        apply_event(resolved, conn)
        apply_event(source, conn)

        interaction = conn.execute(
            "SELECT last_event_id FROM interactions WHERE interaction_id='ix_ev_001'"
        ).fetchone()
        participant = conn.execute(
            "SELECT party_id FROM interaction_participants "
            "WHERE interaction_id='ix_ev_001' AND role='from'"
        ).fetchone()
        assert interaction["last_event_id"] == "ev_002"
        assert participant["party_id"] == "party-sender"
    finally:
        conn.close()
