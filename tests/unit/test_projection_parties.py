"""
Unit tests for the parties projection (prompt 05).

Covers idempotency, rebuild correctness, unique-constraint behavior, and
tenant isolation. Per §12 invariant 1, queries take tenant_id as an
explicit keyword.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import sqlcipher3

from adminme.events.bus import EventBus
from adminme.events.envelope import EventEnvelope
from adminme.events.log import EventLog
from adminme.lib.instance_config import load_instance_config
from adminme.lib.session import Session, build_internal_session
from adminme.projections.parties import PartiesProjection
from adminme.projections.parties.queries import (
    all_parties,
    contact_records_for_person,
    find_party_by_identifier,
    get_party,
    list_household_members,
    list_visible_people,
    relationships_of,
    search_visible_people,
)
from adminme.projections.runner import ProjectionRunner

TEST_KEY = b"p" * 32


def _S(tenant_id: str = "tenant-a") -> Session:
    """Internal-actor Session for projection-read tests; carries tenant_id
    only. 08a + scope filtering use principal role so allowed_read accepts
    shared:household + private:<self> rows."""
    return build_internal_session("test_actor", "principal", tenant_id)


def test_existing_identifiers_schema_adds_scope_columns_before_index() -> None:
    conn = sqlcipher3.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE identifiers ("
            "identifier_id TEXT NOT NULL, tenant_id TEXT NOT NULL, "
            "party_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL, "
            "value_normalized TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0, "
            "primary_for_kind INTEGER NOT NULL DEFAULT 0, "
            "last_event_id TEXT NOT NULL, PRIMARY KEY (tenant_id, identifier_id))"
        )
        projection = PartiesProjection()
        schema = Path(projection.schema_path).read_text(encoding="utf-8")
        conn.executescript(schema)
        projection.migrate_schema(conn)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(identifiers)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(identifiers)")}
        assert {"owner_scope", "visibility_scope", "sensitivity", "revoked_at"} <= (
            columns
        )
        assert "idx_identifiers_scope" in indexes
    finally:
        conn.close()


@pytest.fixture
async def rig(tmp_path: Path):
    """Give each test an isolated instance + event log + bus + runner with
    the parties projection registered and started."""
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()
    config = load_instance_config(instance_dir)
    log = EventLog(config, TEST_KEY)
    bus = EventBus(log, config.bus_checkpoint_path)
    runner = ProjectionRunner(bus, log, config, encryption_key=TEST_KEY)
    runner.register(PartiesProjection())
    await runner.start()
    try:
        yield {
            "config": config,
            "log": log,
            "bus": bus,
            "runner": runner,
        }
    finally:
        await runner.stop()
        await log.close()


def _envelope(
    event_type: str,
    payload: dict[str, Any],
    *,
    tenant_id: str = "tenant-a",
    owner_scope: str = "shared:household",
    sensitivity: str = "normal",
) -> EventEnvelope:
    return EventEnvelope(
        event_at_ms=int(time.time() * 1000),
        tenant_id=tenant_id,
        type=event_type,
        schema_version=1,
        occurred_at=EventEnvelope.now_utc_iso(),
        source_adapter="test",
        source_account_id="test-acct",
        owner_scope=owner_scope,
        visibility_scope=owner_scope,
        sensitivity=sensitivity,
        payload=payload,
    )


async def _wait_for_checkpoint(bus: EventBus, subscriber_id: str, target: str) -> None:
    # Poll until the bus has processed up to target event id.
    import asyncio

    for _ in range(200):
        status = await bus.subscriber_status(subscriber_id)
        if status["checkpoint_event_id"] == target and status["lag_count"] == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"subscriber {subscriber_id} did not reach {target}: "
        f"{await bus.subscriber_status(subscriber_id)}"
    )


async def test_party_created_inserts_row(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    eid = await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
        )
    )
    await bus.notify(eid)
    await _wait_for_checkpoint(bus, "projection:parties", eid)

    conn = runner.connection("parties")
    row = get_party(conn, _S("tenant-a"), party_id="p1")
    assert row is not None
    assert row["display_name"] == "Ada"
    assert row["kind"] == "person"


async def test_party_created_idempotent(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    env = _envelope(
        "party.created",
        {"party_id": "p1", "kind": "person", "display_name": "Ada", "sort_name": "Ada"},
    )
    eid1 = await log.append(env)
    eid2 = await log.append(env)  # second append mints a new event_id
    await bus.notify(eid2)
    await _wait_for_checkpoint(bus, "projection:parties", eid2)

    conn = runner.connection("parties")
    rows = conn.execute(
        "SELECT count(*) FROM parties WHERE tenant_id = ? AND party_id = ?",
        ("tenant-a", "p1"),
    ).fetchone()
    assert rows[0] == 1
    assert eid1 != eid2


async def test_ambiguous_identifier_is_retained_and_resolution_fails_closed(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
        )
    )
    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p2",
                "kind": "person",
                "display_name": "Grace",
                "sort_name": "Grace",
            },
        )
    )
    await log.append(
        _envelope(
            "identifier.added",
            {
                "identifier_id": "i1",
                "party_id": "p1",
                "kind": "email",
                "value": "a@example.com",
                "value_normalized": "a@example.com",
                "verified": False,
                "primary_for_kind": True,
            },
        )
    )
    # Conflicting provider evidence is retained. Resolution must not guess.
    last = await log.append(
        _envelope(
            "identifier.added",
            {
                "identifier_id": "i2",
                "party_id": "p2",
                "kind": "email",
                "value": "a@example.com",
                "value_normalized": "a@example.com",
                "verified": False,
                "primary_for_kind": True,
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    rows = conn.execute(
        "SELECT party_id FROM identifiers "
        "WHERE tenant_id = ? AND kind = ? AND value_normalized = ?",
        ("tenant-a", "email", "a@example.com"),
    ).fetchall()
    assert [r["party_id"] for r in rows] == ["p1", "p2"]
    assert (
        find_party_by_identifier(
            conn,
            _S("tenant-a"),
            kind="email",
            value_normalized="a@example.com",
        )
        is None
    )


async def test_membership_roundtrip(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "hh",
                "kind": "household",
                "display_name": "Household A",
                "sort_name": "Household A",
            },
        )
    )
    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "m1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
        )
    )
    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "m2",
                "kind": "person",
                "display_name": "Grace",
                "sort_name": "Grace",
            },
        )
    )
    await log.append(
        _envelope(
            "membership.added",
            {
                "membership_id": "mem1",
                "party_id": "m1",
                "parent_party_id": "hh",
                "role": "principal",
            },
        )
    )
    last = await log.append(
        _envelope(
            "membership.added",
            {
                "membership_id": "mem2",
                "party_id": "m2",
                "parent_party_id": "hh",
                "role": "principal",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    members = list_household_members(conn, _S("tenant-a"), household_party_id="hh")
    assert {m["party_id"] for m in members} == {"m1", "m2"}


async def test_relationship_mutual_visible_from_both_sides(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    for pid, name in [("p1", "Ada"), ("p2", "Grace")]:
        await log.append(
            _envelope(
                "party.created",
                {
                    "party_id": pid,
                    "kind": "person",
                    "display_name": name,
                    "sort_name": name,
                },
            )
        )
    last = await log.append(
        _envelope(
            "relationship.added",
            {
                "relationship_id": "r1",
                "party_a": "p1",
                "party_b": "p2",
                "label": "sibling",
                "direction": "mutual",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    rels_a = relationships_of(conn, _S("tenant-a"), party_id="p1")
    rels_b = relationships_of(conn, _S("tenant-a"), party_id="p2")
    assert len(rels_a) == 1 and rels_a[0]["relationship_id"] == "r1"
    assert len(rels_b) == 1 and rels_b[0]["relationship_id"] == "r1"


async def test_find_party_by_identifier(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
        )
    )
    last = await log.append(
        _envelope(
            "identifier.added",
            {
                "identifier_id": "i1",
                "party_id": "p1",
                "kind": "email",
                "value": "Ada@Example.com",
                "value_normalized": "ada@example.com",
                "verified": True,
                "primary_for_kind": True,
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    party = find_party_by_identifier(
        conn, _S("tenant-a"), kind="email", value_normalized="ada@example.com"
    )
    assert party is not None and party["party_id"] == "p1"


async def test_rebuild_produces_equivalent_state(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    # Populate a deterministic corpus.
    for i in range(10):
        await log.append(
            _envelope(
                "party.created",
                {
                    "party_id": f"p{i}",
                    "kind": "person",
                    "display_name": f"Name{i}",
                    "sort_name": f"Name{i:02d}",
                },
            )
        )
    for i in range(10):
        await log.append(
            _envelope(
                "identifier.added",
                {
                    "identifier_id": f"id{i}",
                    "party_id": f"p{i}",
                    "kind": "email",
                    "value": f"p{i}@example.com",
                    "value_normalized": f"p{i}@example.com",
                    "verified": False,
                    "primary_for_kind": True,
                },
            )
        )
    last = await log.latest_event_id()
    assert last is not None
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    pre = {
        "parties": conn.execute("SELECT * FROM parties ORDER BY party_id").fetchall(),
        "identifiers": conn.execute(
            "SELECT * FROM identifiers ORDER BY identifier_id"
        ).fetchall(),
    }
    pre_dump = {k: [tuple(r) for r in v] for k, v in pre.items()}

    await runner.rebuild("parties")

    conn = runner.connection("parties")
    post = {
        "parties": conn.execute("SELECT * FROM parties ORDER BY party_id").fetchall(),
        "identifiers": conn.execute(
            "SELECT * FROM identifiers ORDER BY identifier_id"
        ).fetchall(),
    }
    post_dump = {k: [tuple(r) for r in v] for k, v in post.items()}
    assert pre_dump == post_dump


async def test_unsubscribed_event_ignored(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
        )
    )
    # Append an event the parties projection does not subscribe to.
    last = await log.append(
        _envelope(
            "task.created",
            {
                "task_id": "t1",
                "title": "Mow the lawn",
            },
        )
    )
    await bus.notify(last)
    # The subscriber's filter is types-aware, so last may never become the
    # checkpoint; instead wait for the preceding event_id. Simpler: wait for
    # lag_count to settle at the end.
    import asyncio

    for _ in range(200):
        status = await bus.subscriber_status("projection:parties")
        if status["lag_count"] == 0:
            break
        await asyncio.sleep(0.01)

    conn = runner.connection("parties")
    count = conn.execute("SELECT count(*) FROM parties").fetchone()[0]
    assert count == 1


async def test_tenant_isolation(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
            tenant_id="tenant-a",
        )
    )
    last = await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada's Twin",
                "sort_name": "Ada",
            },
            tenant_id="tenant-b",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    a = get_party(conn, _S("tenant-a"), party_id="p1")
    b = get_party(conn, _S("tenant-b"), party_id="p1")
    assert a is not None and a["display_name"] == "Ada"
    assert b is not None and b["display_name"] == "Ada's Twin"


async def test_get_party_missing_returns_none(rig: dict[str, Any]) -> None:
    runner = rig["runner"]
    conn = runner.connection("parties")
    assert get_party(conn, _S("tenant-a"), party_id="nope") is None


async def test_all_parties_filters_by_kind(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]

    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "p1",
                "kind": "person",
                "display_name": "Ada",
                "sort_name": "Ada",
            },
        )
    )
    last = await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "org1",
                "kind": "organization",
                "display_name": "ACME",
                "sort_name": "ACME",
            },
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    persons = all_parties(conn, _S("tenant-a"), kind="person")
    orgs = all_parties(conn, _S("tenant-a"), kind="organization")
    all_ = all_parties(conn, _S("tenant-a"))
    assert [p["party_id"] for p in persons] == ["p1"]
    assert [o["party_id"] for o in orgs] == ["org1"]
    assert len(all_) == 2


async def test_contact_record_qualifies_person_but_endpoint_does_not(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    await log.append(
        _envelope(
            "endpoint.observed",
            {
                "endpoint_id": "endpoint-marketing",
                "member_id": "test_actor",
                "kind": "email",
                "value": "automated@example.test",
                "value_normalized": "automated@example.test",
                "classification": "automated",
                "observed_at": EventEnvelope.now_utc_iso(),
                "evidence": ["inbound sender"],
            },
            owner_scope="private:test_actor",
        )
    )
    last = await log.append(
        _envelope(
            "contact.record.observed",
            {
                "contact_record_id": "contact-1",
                "member_id": "test_actor",
                "provider": "fixture",
                "provider_account_id": "fixture-account",
                "container_external_id": "contacts",
                "external_id": "person-1",
                "provider_revision": "rev-1",
                "kind": "person",
                "display_name": "Example Person",
                "identifiers": [
                    {
                        "kind": "email",
                        "value": "person@example.test",
                        "value_normalized": "person@example.test",
                        "primary": True,
                    }
                ],
                "observed_at": EventEnvelope.now_utc_iso(),
            },
            owner_scope="private:test_actor",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    visible = list_visible_people(conn, _S("tenant-a"))
    assert [person["display_name"] for person in visible] == ["Example Person"]
    records = contact_records_for_person(
        conn, _S("tenant-a"), party_id=visible[0]["party_id"]
    )
    assert records[0]["fields"] == {}
    assert records[0]["provider_revision"] == "rev-1"
    other = build_internal_session("other-adult", "principal", "tenant-a")
    assert list_visible_people(conn, other) == []


async def test_people_search_terms_are_member_isolated(rig: dict[str, Any]) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    await log.append(
        _envelope(
            "party.created",
            {
                "party_id": "shared-person",
                "kind": "person",
                "display_name": "Shared Person",
                "sort_name": "Shared Person",
            },
        )
    )
    await log.append(
        _envelope(
            "member_relationship.profile_set",
            {
                "profile_id": "profile-a",
                "member_id": "adult-a",
                "party_id": "shared-person",
                "circle": "private-alpha-circle",
                "desired_cadence_days": None,
                "cues_enabled": False,
                "favorite": False,
                "suppressed": False,
                "suppression_reason": None,
                "tags": ["confidential-alpha-tag"],
                "changed_at": EventEnvelope.now_utc_iso(),
            },
            owner_scope="private:adult-a",
        )
    )
    last = await log.append(
        _envelope(
            "relationship_fact.confirmed",
            {
                "fact_id": "fact-a",
                "member_id": "adult-a",
                "party_id": "shared-person",
                "kind": "context",
                "label": "private context",
                "value": "confidential-alpha-fact",
                "source_event_id": "source-a",
                "confirmed_at": EventEnvelope.now_utc_iso(),
                "confirmed_by_member_id": "adult-a",
            },
            owner_scope="private:adult-a",
        )
    )
    await bus.notify(last)
    await _wait_for_checkpoint(bus, "projection:parties", last)

    conn = runner.connection("parties")
    adult_a = build_internal_session("adult-a", "principal", "tenant-a")
    adult_b = build_internal_session("adult-b", "principal", "tenant-a")
    assert [
        row["party_id"]
        for row in search_visible_people(conn, adult_a, query="confidential-alpha-fact")
    ] == ["shared-person"]
    assert search_visible_people(conn, adult_b, query="confidential-alpha-fact") == []
    assert search_visible_people(conn, adult_b, query="confidential-alpha-tag") == []
    assert [
        row["party_id"]
        for row in search_visible_people(conn, adult_b, query="Shared Person")
    ] == ["shared-person"]


async def test_party_merge_and_allocated_split_are_reversible(
    rig: dict[str, Any],
) -> None:
    log = rig["log"]
    bus = rig["bus"]
    runner = rig["runner"]
    for party_id, name in (("survivor", "Survivor"), ("duplicate", "Duplicate")):
        await log.append(
            _envelope(
                "party.created",
                {
                    "party_id": party_id,
                    "kind": "person",
                    "display_name": name,
                    "sort_name": name,
                },
            )
        )
    await log.append(
        _envelope(
            "identifier.added",
            {
                "identifier_id": "duplicate-email",
                "party_id": "duplicate",
                "kind": "email",
                "value": "duplicate@example.test",
                "value_normalized": "duplicate@example.test",
                "verified": True,
                "primary_for_kind": True,
            },
        )
    )
    merged = await log.append(
        _envelope(
            "party.merged",
            {
                "surviving_party_id": "survivor",
                "merged_party_id": "duplicate",
                "merged_at": EventEnvelope.now_utc_iso(),
                "merged_by_member_id": "test_actor",
                "rationale": "acceptance correction",
            },
        )
    )
    await bus.notify(merged)
    await _wait_for_checkpoint(bus, "projection:parties", merged)

    conn = runner.connection("parties")
    archived = get_party(conn, _S(), party_id="duplicate")
    assert archived is not None
    assert archived["qualification"] == "archived"
    assert archived["merged_into_party_id"] == "survivor"
    assert (
        conn.execute(
            "SELECT party_id FROM identifiers WHERE tenant_id=? AND identifier_id=?",
            ("tenant-a", "duplicate-email"),
        ).fetchone()["party_id"]
        == "survivor"
    )

    split = await log.append(
        _envelope(
            "party.split",
            {
                "merged_party_id": "survivor",
                "restored_party_id": "duplicate",
                "split_at": EventEnvelope.now_utc_iso(),
                "split_by_member_id": "test_actor",
                "rationale": "undo mistaken merge",
                "identifier_ids": ["duplicate-email"],
            },
        )
    )
    await bus.notify(split)
    await _wait_for_checkpoint(bus, "projection:parties", split)
    restored = get_party(conn, _S(), party_id="duplicate")
    assert restored is not None
    assert restored["qualification"] == "qualified"
    assert restored["merged_into_party_id"] is None
    assert (
        conn.execute(
            "SELECT party_id FROM identifiers WHERE tenant_id=? AND identifier_id=?",
            ("tenant-a", "duplicate-email"),
        ).fetchone()["party_id"]
        == "duplicate"
    )
