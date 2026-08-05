"""Owner isolation, provenance, and replay tests for member knowledge."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from adminme.projections.member_knowledge.handlers import apply_event
from adminme.projections.member_knowledge.queries import get_for_owner


def _connection() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema = (
        Path(__file__).resolve().parents[2]
        / "adminme"
        / "projections"
        / "member_knowledge"
        / "schema.sql"
    ).read_text(encoding="utf-8")
    conn.executescript(schema)
    return conn


def _event(*, owner_scope: str = "private:member-a") -> dict[str, object]:
    return {
        "event_id": "evt-knowledge-001",
        "tenant_id": "tenant-a",
        "type": "member_knowledge.note_added",
        "owner_scope": owner_scope,
        "visibility_scope": owner_scope,
        "sensitivity": "sensitive",
        "source_adapter": "apple-notes",
        "source_account_id": "icloud-member-a",
        "correlation_id": "corr-knowledge-001",
        "causation_id": "bridge-envelope-001",
        "payload": {
            "knowledge_id": "knowledge-001",
            "member_id": "member-a",
            "title": "Private note",
            "body": "Remember the blue folder.",
            "source_kind": "apple_note",
            "source_external_id": "note-icloud-001",
            "content_sha256": "a" * 64,
            "captured_at": "2026-05-28T12:00:00Z",
            "supersedes_event_id": None,
        },
    }


def test_replay_is_idempotent_and_preserves_complete_provenance() -> None:
    conn = _connection()
    try:
        event = _event()
        apply_event(event, conn)
        apply_event(event, conn)
        assert conn.execute("SELECT COUNT(*) FROM member_knowledge").fetchone()[0] == 1
        row = get_for_owner(
            conn,
            tenant_id="tenant-a",
            member_id="member-a",
            knowledge_id="knowledge-001",
        )
        assert row is not None
        assert row["owner_scope"] == "private:member-a"
        assert row["source_adapter"] == "apple-notes"
        assert row["source_account_id"] == "icloud-member-a"
        assert row["correlation_id"] == "corr-knowledge-001"
        assert row["causation_id"] == "bridge-envelope-001"
        assert row["last_event_id"] == "evt-knowledge-001"
    finally:
        conn.close()


def test_owner_scope_must_match_payload_member() -> None:
    conn = _connection()
    try:
        with pytest.raises(ValueError, match="owner_scope"):
            apply_event(_event(owner_scope="private:member-b"), conn)
        assert conn.execute("SELECT COUNT(*) FROM member_knowledge").fetchone()[0] == 0
    finally:
        conn.close()
