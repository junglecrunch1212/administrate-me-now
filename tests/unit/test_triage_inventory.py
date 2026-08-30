"""Complete cursor and matter grouping proofs for CO-014 Triage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import sqlcipher3

from adminme.lib.session import build_internal_session
from adminme.context_fabric import queue_lane
from adminme.projections.triage import TriageProjection
from adminme.projections.triage.queries import (
    matter_for_proposal,
    pending_for_sources,
    triage_page_for_member,
)


MEMBER = "fixture-triage-member"  # fixture:tenant_data:ok
TENANT = "fixture-triage-tenant"  # fixture:tenant_data:ok


def _event(
    index: int, *, thread_key: str | None = None, title: str | None = None
) -> dict[str, Any]:
    interpretation = None
    if thread_key:
        interpretation = {
            "thread_key": thread_key,
            "compact_summary": f"Matter {thread_key}",
            "action_owner": "member",
            "lifecycle_state": "open",
            "evidence_refs": [
                {"source_event_id": f"source-{index}", "quote": "fixture"}
            ],
        }
    return {
        "event_id": f"event-{index:04d}",
        "tenant_id": TENANT,
        "type": "triage.item.proposed",
        "schema_version": 3,
        "occurred_at": f"2030-01-{1 + index // 24:02d}T{index % 24:02d}:00:00Z",
        "owner_scope": f"private:{MEMBER}",
        "visibility_scope": f"private:{MEMBER}",
        "sensitivity": "normal",
        "payload": {
            "proposal_id": f"proposal-{index:04d}",
            "target_kind": "task",
            "owner_member_id": MEMBER,
            "title": title or f"Fixture task {index:04d}",
            "candidate": {},
            "route": {"member_id": MEMBER},
            "source_event_id": f"source-{index}",
            "confidence": 0.99,
            "reasons": ["fixture evidence"],
            "interpretation": interpretation,
        },
    }


def _projection() -> tuple[TriageProjection, sqlcipher3.Connection]:
    projection = TriageProjection()
    conn = sqlcipher3.connect(":memory:")
    conn.row_factory = sqlcipher3.Row
    conn.executescript(Path(projection.schema_path).read_text("utf-8"))
    projection.migrate_schema(conn)
    return projection, conn


def test_every_candidate_is_reachable_once_across_stable_pages() -> None:
    projection, conn = _projection()
    for index in range(520):
        projection.apply(_event(index), conn)
    session = build_internal_session(MEMBER, "principal", TENANT)
    cursor = None
    seen_matters: list[str] = []
    seen_candidates: list[str] = []
    while True:
        page = triage_page_for_member(
            conn,
            session,
            member_id=MEMBER,
            lens="all_candidates",
            cursor=cursor,
            limit=17,
        )
        seen_matters.extend(matter["context_id"] for matter in page["matters"])
        seen_candidates.extend(
            candidate["proposal_id"]
            for matter in page["matters"]
            for candidate in matter["candidates"]
        )
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen_matters) == len(set(seen_matters)) == 520
    assert len(seen_candidates) == len(set(seen_candidates)) == 520


def test_attention_lane_requires_real_fact_and_ignores_provider_category() -> None:
    assert queue_lane(
        {
            "target_kind": "task",
            "title": "Ordinary fixture task",
            "candidate": {"due": None, "provider_labels": ["IMPORTANT"]},
        }
    ) == "standard"
    assert queue_lane(
        {
            "target_kind": "task",
            "title": "New sign-in from an unfamiliar device",
            "candidate": {"due": None, "provider_labels": ["SPAM"]},
        }
    ) == "security"
    assert queue_lane(
        {
            "target_kind": "task",
            "title": "Ordinary fixture task",
            "candidate": {"due": "2030-01-04", "provider_labels": ["PROMOTIONS"]},
        }
    ) == "time_bound"


def test_complete_lens_counts_and_inventory_filters_are_cursor_bound() -> None:
    projection, conn = _projection()
    for index in range(8):
        event = _event(index)
        event["payload"]["eligibility_rule_ids"] = [
            (
                "candidate_policy_v1:adapter:gmail:review"
                if index < 5
                else "candidate_policy_v1:surface:triage:review"
            )
        ]
        if index in {1, 6}:
            event["payload"]["candidate"] = {"due": f"2030-02-{index + 1:02d}"}
        if index == 2:
            event["payload"]["target_kind"] = "commitment"
            event["payload"]["candidate"] = {
                "owed_to_party_id": "fixture-party"
            }
        projection.apply(event, conn)
    session = build_internal_session(MEMBER, "principal", TENANT)

    page = triage_page_for_member(
        conn,
        session,
        member_id=MEMBER,
        lens="all_candidates",
        source_id="adapter:gmail",
        sort="newest",
        limit=2,
    )
    assert page["selection"] == {
        "sort": "newest",
        "source_id": "adapter:gmail",
        "target_kind": None,
        "candidates": 5,
        "matters": 5,
    }
    assert page["inventory"]["views"]["ready"]["candidates"] == 3
    assert page["inventory"]["views"]["low_signal"]["candidates"] == 5
    assert page["inventory"]["views"]["all_candidates"]["candidates"] == 8
    assert page["inventory"]["facets"]["sources"][0]["label"] == "Gmail"
    assert [
        candidate["proposal_id"]
        for matter in page["matters"]
        for candidate in matter["candidates"]
    ] == ["proposal-0004", "proposal-0003"]
    assert page["next_cursor"] is not None

    with pytest.raises(ValueError, match="stale triage cursor"):
        triage_page_for_member(
            conn,
            session,
            member_id=MEMBER,
            lens="all_candidates",
            source_id="adapter:gmail",
            sort="oldest",
            cursor=page["next_cursor"],
            limit=2,
        )

    only_commitments = triage_page_for_member(
        conn,
        session,
        member_id=MEMBER,
        lens="all_candidates",
        target_kind="commitment",
    )
    assert only_commitments["selection"]["candidates"] == 1
    assert only_commitments["matters"][0]["candidates"][0]["source_id"] == (
        "adapter:gmail"
    )


def test_cursor_refuses_root_promotion_after_concurrent_resolution() -> None:
    projection, conn = _projection()
    for event in (
        _event(0, thread_key="matter-a"),
        _event(10, thread_key="matter-a"),
        _event(5, thread_key="matter-b"),
        _event(20, thread_key="matter-c"),
    ):
        projection.apply(event, conn)
    session = build_internal_session(MEMBER, "principal", TENANT)

    first_page = triage_page_for_member(
        conn, session, member_id=MEMBER, lens="all_candidates", limit=2
    )
    assert first_page["next_cursor"] is not None

    dismissed = _event(30)
    dismissed.update(event_id="event-9000", type="triage.item.dismissed")
    dismissed["payload"] = {
        "proposal_id": "proposal-0000",
        "dismissed_at": "2030-02-01T00:00:00Z",
        "dismissed_by_member_id": MEMBER,
        "reason": "not_actionable",
    }
    projection.apply(dismissed, conn)

    with pytest.raises(ValueError, match="stale triage cursor"):
        triage_page_for_member(
            conn,
            session,
            member_id=MEMBER,
            lens="all_candidates",
            cursor=first_page["next_cursor"],
            limit=2,
        )

    refreshed = triage_page_for_member(
        conn, session, member_id=MEMBER, lens="all_candidates", limit=2
    )
    matter_a = next(
        matter
        for matter in refreshed["matters"]
        if any(
            candidate["proposal_id"] == "proposal-0010"
            for candidate in matter["candidates"]
        )
    )
    assert [item["proposal_id"] for item in matter_a["candidates"]] == ["proposal-0010"]


def test_cursor_refuses_concurrent_scoped_insert_but_ignores_other_member() -> None:
    projection, conn = _projection()
    for index in (10, 20, 30):
        projection.apply(_event(index), conn)
    session = build_internal_session(MEMBER, "principal", TENANT)
    first_page = triage_page_for_member(
        conn, session, member_id=MEMBER, lens="all_candidates", limit=2
    )
    assert first_page["next_cursor"] is not None

    other_member_event = _event(5)
    other_member_event["event_id"] = "event-other-member"
    other_member_event["owner_scope"] = "private:other-member"
    other_member_event["visibility_scope"] = "private:other-member"
    other_member_event["payload"].update(
        proposal_id="proposal-other-member",
        owner_member_id="other-member",
        route={"member_id": "other-member"},
    )
    projection.apply(other_member_event, conn)
    unaffected = triage_page_for_member(
        conn,
        session,
        member_id=MEMBER,
        lens="all_candidates",
        cursor=first_page["next_cursor"],
        limit=2,
    )
    assert [
        candidate["proposal_id"]
        for matter in unaffected["matters"]
        for candidate in matter["candidates"]
    ] == ["proposal-0030"]

    scoped_insert = _event(5)
    scoped_insert["event_id"] = "event-scoped-insert"
    scoped_insert["payload"]["proposal_id"] = "proposal-scoped-insert"
    projection.apply(scoped_insert, conn)
    with pytest.raises(ValueError, match="stale triage cursor"):
        triage_page_for_member(
            conn,
            session,
            member_id=MEMBER,
            lens="all_candidates",
            cursor=first_page["next_cursor"],
            limit=2,
        )


def test_cross_surface_candidate_lookup_reads_only_visible_source_rows() -> None:
    projection, conn = _projection()
    for index in range(125):
        projection.apply(_event(index), conn)
    session = build_internal_session(MEMBER, "principal", TENANT)
    linked = pending_for_sources(
        conn,
        session,
        member_id=MEMBER,
        source_event_ids=["source-1", "source-124", "not-visible"],
    )
    assert [item["proposal_id"] for item in linked] == [
        "proposal-0124",
        "proposal-0001",
    ]


def test_exact_matter_lookup_ignores_cursor_position_without_crossing_scope() -> None:
    projection, conn = _projection()
    for index in range(125):
        projection.apply(_event(index), conn)
    session = build_internal_session(MEMBER, "principal", TENANT)

    first_page = triage_page_for_member(
        conn, session, member_id=MEMBER, lens="all_candidates", limit=10
    )
    assert "proposal-0124" not in {
        item["proposal_id"]
        for matter in first_page["matters"]
        for item in matter["candidates"]
    }
    focused = matter_for_proposal(
        conn,
        session,
        member_id=MEMBER,
        proposal_id="proposal-0124",
    )
    assert focused is not None
    assert focused["context_id"].startswith("ctx_source_")
    assert [item["proposal_id"] for item in focused["candidates"]] == ["proposal-0124"]

    assert (
        matter_for_proposal(
            conn,
            build_internal_session("other-member", "principal", TENANT),
            member_id="other-member",
            proposal_id="proposal-0124",
        )
        is None
    )


def test_one_provider_conversation_is_one_matter_with_independent_candidates() -> None:
    projection, conn = _projection()
    for index in range(5):
        projection.apply(
            _event(index, thread_key="shared-thread", title=f"Instruction {index}"),
            conn,
        )
    session = build_internal_session(MEMBER, "principal", TENANT)
    page = triage_page_for_member(
        conn, session, member_id=MEMBER, lens="all_candidates"
    )
    assert page["inventory"]["total_candidates"] == 5
    assert page["inventory"]["total_matters"] == 1
    [matter] = page["matters"]
    assert matter["candidate_count"] == 5
    assert [item["proposal_id"] for item in matter["candidates"]] == [
        f"proposal-{index:04d}" for index in range(5)
    ]


def test_exact_duplicate_is_retained_as_evidence_not_an_extra_decision() -> None:
    projection, conn = _projection()
    projection.apply(_event(1, thread_key="same", title="Pay fixture bill"), conn)
    projection.apply(_event(2, thread_key="same", title="Pay fixture bill"), conn)
    rows = conn.execute(
        """SELECT proposal_id, decision_disposition, duplicate_of_proposal_id
             FROM triage_items ORDER BY proposal_id"""
    ).fetchall()
    assert tuple(rows[0]) == ("proposal-0001", "decision", None)
    assert tuple(rows[1]) == ("proposal-0002", "evidence_only", "proposal-0001")


def test_extracted_candidate_never_deduplicates_against_its_parent_intent() -> None:
    projection, conn = _projection()
    parent = _event(1)
    parent.update(type="triage.intent.submitted")
    parent["payload"] = {
        "intent_id": "member-intent-1",
        "member_id": MEMBER,
        "text": "Review the fixture instruction",
        "source_surface": "triage",
        "kind_hint": "task",
    }
    projection.apply(parent, conn)
    child = _event(2, title="Review the fixture instruction")
    child["payload"].update(
        source_event_id="event-0001",
        parent_intent_id="member-intent-1",
    )
    projection.apply(child, conn)
    rows = {
        row["proposal_id"]: dict(row)
        for row in conn.execute(
            """SELECT proposal_id, state, decision_disposition,
                      duplicate_of_proposal_id
                 FROM triage_items"""
        )
    }
    parent_receipt = conn.execute(
        "SELECT state, dismissal_event_id, dismissal_request_id "
        "FROM triage_items WHERE proposal_id='member-intent-1'"
    ).fetchone()
    assert tuple(parent_receipt) == ("dismissed", None, None)
    assert rows["proposal-0002"]["state"] == "pending_review"
    assert rows["proposal-0002"]["decision_disposition"] == "decision"
    assert rows["proposal-0002"]["duplicate_of_proposal_id"] is None


def test_enrichment_reconciles_duplicate_state_and_pending_intent_can_dismiss() -> None:
    projection, conn = _projection()
    projection.apply(_event(1, thread_key="same", title="Keep this fixture"), conn)
    projection.apply(_event(2, thread_key="same", title="Different fixture"), conn)
    enriched = _event(3)
    enriched.update(event_id="event-9000", type="triage.item.enriched")
    enriched["payload"] = {
        "proposal_id": "proposal-0002",
        "enriched_at": "2030-01-02T00:00:00Z",
        "enriched_by_member_id": MEMBER,
        "title": "Keep this fixture",
        "target_kind": "task",
        "candidate_updates": {"due": "2030-01-05T12:00:00Z"},
        "route": {"member_id": MEMBER},
    }
    projection.apply(enriched, conn)
    reconciled = conn.execute(
        """SELECT decision_disposition, duplicate_of_proposal_id, queue_lane
             FROM triage_items WHERE proposal_id='proposal-0002'"""
    ).fetchone()
    assert tuple(reconciled) == (
        "evidence_only",
        "proposal-0001",
        "member_promise",
    )

    intent = _event(4)
    intent.update(event_id="event-9001", type="triage.intent.submitted")
    intent["payload"] = {
        "intent_id": "pending-intent",
        "member_id": MEMBER,
        "text": "Unclassified fixture note",
        "source_surface": "triage",
        "kind_hint": None,
    }
    projection.apply(intent, conn)
    dismissed = _event(5)
    dismissed.update(event_id="event-9002", type="triage.item.dismissed")
    dismissed["idempotency_key"] = (
        f"triage:triage.item.dismissed:{MEMBER}:dismiss-request-0001"
    )
    dismissed["payload"] = {
        "proposal_id": "pending-intent",
        "dismissed_at": "2030-01-03T00:00:00Z",
        "dismissed_by_member_id": MEMBER,
        "reason": "not_actionable",
    }
    projection.apply(dismissed, conn)
    receipt = conn.execute(
        "SELECT state, dismissal_event_id, dismissal_request_id "
        "FROM triage_items WHERE proposal_id='pending-intent'"
    ).fetchone()
    assert tuple(receipt) == (
        "dismissed",
        "event-9002",
        "dismiss-request-0001",
    )


def test_v3_supersession_cannot_overwrite_a_newer_member_enrichment() -> None:
    projection, conn = _projection()
    proposal = _event(1, title="Legacy fixture interpretation")
    projection.apply(proposal, conn)
    enriched = {
        **_event(2),
        "event_id": "event-9000",
        "type": "triage.item.enriched",
        "schema_version": 1,
        "payload": {
            "proposal_id": "proposal-0001",
            "enriched_at": "2030-01-02T00:00:00Z",
            "enriched_by_member_id": MEMBER,
            "title": "Member-corrected legitimate commitment",
            "candidate_updates": {"note": "Reviewed by the member."},
        },
    }
    projection.apply(enriched, conn)
    stale_supersession = {
        **_event(3),
        "event_id": "event-9001",
        "type": "triage.item.superseded",
        "schema_version": 3,
        "payload": {
            "proposal_id": "proposal-0001",
            "superseded_at": "2030-01-02T00:00:01Z",
            "replacement_source_event_id": "source-1",
            "reason": "eligibility_rule_replaced",
            "expected_last_event_id": "event-0001",
        },
    }
    projection.apply(stale_supersession, conn)

    row = conn.execute(
        "SELECT state, title, resolved_by, resolution_reason, last_event_id "
        "FROM triage_items WHERE proposal_id='proposal-0001'"
    ).fetchone()
    assert tuple(row) == (
        "pending_review",
        "Member-corrected legitimate commitment",
        None,
        None,
        "event-9000",
    )


def test_v3_supersession_applies_to_the_exact_unchanged_revision() -> None:
    projection, conn = _projection()
    projection.apply(_event(1, title="Untouched legacy interpretation"), conn)
    supersession = {
        **_event(2),
        "event_id": "event-9000",
        "type": "triage.item.superseded",
        "schema_version": 3,
        "payload": {
            "proposal_id": "proposal-0001",
            "superseded_at": "2030-01-02T00:00:00Z",
            "replacement_source_event_id": "source-1",
            "reason": "eligibility_rule_replaced",
            "expected_last_event_id": "event-0001",
        },
    }
    projection.apply(supersession, conn)

    row = conn.execute(
        "SELECT state, resolved_by, resolution_reason, last_event_id "
        "FROM triage_items WHERE proposal_id='proposal-0001'"
    ).fetchone()
    assert tuple(row) == (
        "dismissed",
        "system:source_changed",
        "eligibility_rule_replaced",
        "event-9000",
    )


def test_pending_provider_action_is_an_action_specific_triage_receipt() -> None:
    projection, conn = _projection()
    projection.apply(_event(1), conn)
    confirmation = {
        **_event(2),
        "event_id": "event-9998",
        "type": "triage.item.confirmed",
        "schema_version": 2,
        "idempotency_key": (
            f"triage:triage.item.confirmed:{MEMBER}:confirm-request-0001"
        ),
        "payload": {
            "proposal_id": "proposal-0001",
            "confirmed_at": "2030-01-02T00:00:00Z",
            "confirmed_by_member_id": MEMBER,
            "expected_last_event_id": "event-0001",
            "candidate_fingerprint": "fixture-fingerprint",
        },
    }
    projection.apply(confirmation, conn)
    pending = {
        **_event(3),
        "event_id": "event-9999",
        "type": "member.provider_action.confirmed",
        "causation_id": "event-9998",
        "payload": {
            "action_id": "fixture-action",
            "member_id": MEMBER,
            "provider": "apple_calendar",
            "provider_account_id": MEMBER,
            "operation": "create_event",
            "command_payload": {},
            "request_fingerprint": "fixture-fingerprint",
            "confirmed_at": "2030-01-02T00:01:00Z",
            "origin_proposal_id": "proposal-0001",
        },
    }
    projection.apply(pending, conn)
    receipt = conn.execute(
        """SELECT state, resolution_reason, domain_approval_event_id,
                  confirmation_event_id, confirmation_request_id,
                  materialization_event_id, last_event_id
             FROM triage_items WHERE proposal_id='proposal-0001'"""
    ).fetchone()
    assert tuple(receipt) == (
        "approved",
        "approved_awaiting_openclaw",
        "event-9998",
        "event-9998",
        "confirm-request-0001",
        "event-9999",
        "event-9999",
    )


def test_commitment_decisions_project_exact_member_request_receipts() -> None:
    projection, conn = _projection()

    for index, action in enumerate(("confirmed", "dismissed"), start=1):
        commitment_id = f"commitment-{action}"
        proposed = {
            **_event(index),
            "type": "commitment.proposed",
            "payload": {
                "commitment_id": commitment_id,
                "owed_by_member_id": MEMBER,
                "text_summary": f"Fixture commitment {action}",
                "confidence": 0.9,
                "classify_reasons": ["fixture"],
            },
        }
        projection.apply(proposed, conn)
        decision = {
            **_event(index + 10),
            "type": f"commitment.{action}",
            "idempotency_key": (
                f"command:commitment.{action}:{commitment_id}:{action}-request"
            ),
            "payload": {
                "commitment_id": commitment_id,
                f"{action}_at": "2030-01-03T00:00:00Z",
                (
                    "confirmed_by_member_id"
                    if action == "confirmed"
                    else "dismissed_by_party_id"
                ): MEMBER,
            },
        }
        projection.apply(decision, conn)

    confirmed = conn.execute(
        "SELECT confirmation_event_id, confirmation_request_id "
        "FROM triage_items WHERE proposal_id='commitment-confirmed'"
    ).fetchone()
    dismissed = conn.execute(
        "SELECT dismissal_event_id, dismissal_request_id "
        "FROM triage_items WHERE proposal_id='commitment-dismissed'"
    ).fetchone()
    assert tuple(confirmed) == (
        "event-0011",
        "confirmed-request",
    )
    assert tuple(dismissed) == (
        "event-0012",
        "dismissed-request",
    )


def test_continuous_long_chat_is_bounded_into_temporal_matters() -> None:
    projection, conn = _projection()
    for index in range(101):
        projection.apply(_event(index, thread_key="years-long-chat"), conn)
    session = build_internal_session(MEMBER, "principal", TENANT)
    page = triage_page_for_member(
        conn, session, member_id=MEMBER, lens="all_candidates", limit=10
    )
    assert page["inventory"]["total_matters"] == 2
    assert sorted(matter["candidate_count"] for matter in page["matters"]) == [1, 100]


def test_matter_limit_counts_distinct_sources_not_candidate_interleaving() -> None:
    projection, conn = _projection()
    events = [
        _event(1, thread_key="interleaved"),
        _event(2, thread_key="interleaved"),
        _event(3, thread_key="interleaved"),
    ]
    events[2]["payload"]["source_event_id"] = "source-1"
    for event in events:
        projection.apply(event, conn)
    [session] = conn.execute("SELECT * FROM triage_context_sessions").fetchall()
    assert session["source_count"] == 2
    assert (
        conn.execute("SELECT COUNT(*) FROM triage_context_session_sources").fetchone()[
            0
        ]
        == 2
    )


def test_exact_existing_task_and_recurrence_become_evidence_not_new_work() -> None:
    projection, conn = _projection()
    projection.apply(
        {
            **_event(1),
            "event_id": "task-created-1",
            "type": "task.created",
            "payload": {
                "task_id": "task-existing",
                "title": "File fixture return",
                "owner_member_id": MEMBER,
            },
        },
        conn,
    )
    projection.apply(_event(2, title="File fixture return"), conn)
    recurrence = _event(3, title="File the fixture return every Friday")
    recurrence["payload"]["target_kind"] = "recurrence"
    recurrence["payload"]["candidate"] = {
        "kind": "fixture_return",
        "rrule": "FREQ=WEEKLY;BYDAY=FR",
    }
    projection.apply(recurrence, conn)
    projection.apply(
        {
            **_event(4),
            "event_id": "recurrence-added-1",
            "type": "recurrence.added",
            "payload": {
                "recurrence_id": "recurrence-existing",
                "linked_kind": "household",
                "linked_id": "fixture-household",
                "kind": "fixture_return",
                "rrule": "FREQ=WEEKLY;BYDAY=FR",
                "next_occurrence": "2030-01-11T12:00:00Z",
                "owner_member_id": MEMBER,
            },
        },
        conn,
    )
    rows = {
        row["proposal_id"]: dict(row)
        for row in conn.execute(
            """SELECT proposal_id, decision_disposition,
                      existing_object_kind, existing_object_id
                 FROM triage_items"""
        )
    }
    assert rows["proposal-0002"]["decision_disposition"] == "attach_existing"
    assert rows["proposal-0002"]["existing_object_id"] == "task-existing"
    assert rows["proposal-0003"]["decision_disposition"] == "attach_existing"
    assert rows["proposal-0003"]["existing_object_id"] == "recurrence-existing"


def test_exact_existing_list_item_matches_reminder_and_list_item_candidates() -> None:
    projection, conn = _projection()
    for index, target_kind in enumerate(("reminder", "list_item"), start=1):
        proposal = _event(index, title="Bring the fixture folder")
        proposal["payload"]["target_kind"] = target_kind
        projection.apply(proposal, conn)
    provider_event = _event(3)
    provider_event.update(event_id="list-item-added-1", type="list_item.added")
    provider_event["payload"] = {
        "item_external_id": "provider-item-existing",
        "list_external_id": "provider-list-existing",
        "title": "Bring the fixture folder",
        "owner_member_id": MEMBER,
    }
    projection.apply(provider_event, conn)
    rows = conn.execute(
        """SELECT target_kind, decision_disposition, existing_object_id
             FROM triage_items ORDER BY target_kind"""
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("list_item", "attach_existing", "provider-item-existing"),
        ("reminder", "attach_existing", "provider-item-existing"),
    ]


def test_v6_migration_preserves_every_temporal_matter_and_matches_replay() -> None:
    projection, replay = _projection()
    events = [
        _event(index, thread_key="long-lived-provider-thread") for index in range(101)
    ]
    for event in events:
        projection.apply(event, replay)

    legacy = sqlcipher3.connect(":memory:")
    legacy.row_factory = sqlcipher3.Row
    legacy.executescript(
        """
        CREATE TABLE triage_items (
            proposal_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
            source_kind TEXT NOT NULL, target_kind TEXT,
            owner_member_id TEXT NOT NULL, title TEXT NOT NULL,
            candidate_json TEXT NOT NULL DEFAULT '{}', route_json TEXT,
            source_event_id TEXT NOT NULL, parent_intent_id TEXT,
            candidate_key TEXT, extraction_event_id TEXT,
            eligibility_rule_ids_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL, reasons_json TEXT NOT NULL DEFAULT '[]',
            interpretation_json TEXT, state TEXT NOT NULL,
            proposed_at TEXT NOT NULL, resolved_at TEXT, resolved_by TEXT,
            resolution_reason TEXT,
            materialized_event_ids_json TEXT NOT NULL DEFAULT '[]',
            domain_approval_event_id TEXT, materialization_event_id TEXT,
            outcome_event_id TEXT, owner_scope TEXT NOT NULL,
            visibility_scope TEXT NOT NULL, sensitivity TEXT NOT NULL,
            last_event_id TEXT NOT NULL,
            PRIMARY KEY (tenant_id, proposal_id)
        );
        """
    )
    legacy_columns = [
        "proposal_id",
        "tenant_id",
        "source_kind",
        "target_kind",
        "owner_member_id",
        "title",
        "candidate_json",
        "route_json",
        "source_event_id",
        "parent_intent_id",
        "candidate_key",
        "extraction_event_id",
        "eligibility_rule_ids_json",
        "confidence",
        "reasons_json",
        "interpretation_json",
        "state",
        "proposed_at",
        "resolved_at",
        "resolved_by",
        "resolution_reason",
        "materialized_event_ids_json",
        "domain_approval_event_id",
        "materialization_event_id",
        "outcome_event_id",
        "owner_scope",
        "visibility_scope",
        "sensitivity",
        "last_event_id",
    ]
    column_sql = ", ".join(legacy_columns)
    placeholders = ", ".join("?" for _ in legacy_columns)
    for row in replay.execute(f"SELECT {column_sql} FROM triage_items"):
        legacy.execute(
            f"INSERT INTO triage_items ({column_sql}) VALUES ({placeholders})",
            tuple(row),
        )

    # Production opens the current schema before invoking the additive migration.
    legacy.executescript(Path(projection.schema_path).read_text("utf-8"))
    projection.migrate_schema(legacy)

    migrated_items = legacy.execute(
        """SELECT proposal_id, context_id, context_kind, title_fingerprint,
                  object_match_fingerprint, queue_lane, evidence_state,
                  ownership_state, lifecycle_state
             FROM triage_items ORDER BY proposal_id"""
    ).fetchall()
    replayed_items = replay.execute(
        """SELECT proposal_id, context_id, context_kind, title_fingerprint,
                  object_match_fingerprint, queue_lane, evidence_state,
                  ownership_state, lifecycle_state
             FROM triage_items ORDER BY proposal_id"""
    ).fetchall()
    assert [tuple(row) for row in migrated_items] == [
        tuple(row) for row in replayed_items
    ]
    migrated_sessions = legacy.execute(
        "SELECT * FROM triage_context_sessions ORDER BY sequence"
    ).fetchall()
    replayed_sessions = replay.execute(
        "SELECT * FROM triage_context_sessions ORDER BY sequence"
    ).fetchall()
    assert [tuple(row) for row in migrated_sessions] == [
        tuple(row) for row in replayed_sessions
    ]
    assert [row[3] for row in migrated_sessions] == [0, 1]
    migrated_sources = legacy.execute(
        "SELECT * FROM triage_context_session_sources ORDER BY sequence, source_event_id"
    ).fetchall()
    replayed_sources = replay.execute(
        "SELECT * FROM triage_context_session_sources ORDER BY sequence, source_event_id"
    ).fetchall()
    assert [tuple(row) for row in migrated_sources] == [
        tuple(row) for row in replayed_sources
    ]
