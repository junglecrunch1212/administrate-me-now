"""Finite metadata scans over isolated encrypted retained-history fixtures.

Raw insertion deliberately models historical/corrupt storage, not accepted Books
publication. No runtime, provider, grant or production data is involved.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import sqlcipher3

from adminme.events import log as log_module
from adminme.events.log import EventLog

TENANT, OWNER = "fixture-tenant", "fixture-owner"
KEY = b"b" * 32
PUBLICATION = "finance.publication_accepted"
NOISE = "fixture.unrelated_history"
NOISE_ROWS = 25_000


def row(
    number,
    *,
    kind=PUBLICATION,
    tenant=TENANT,
    owner=OWNER,
    payload=None,
    visibility=None,
    version=1,
):
    scope = "private:" + owner
    if payload is None:
        payload = (
            json.dumps(
                {
                    "publication": {
                        "tenant_id": tenant,
                        "owner_id": owner,
                        "book_id": "fixture-book",
                        "entity_id": "fixture-entity",
                        "audience_policy_id": "fixture-policy",
                        "audience_policy_version": "1",
                        "content_sha256": "a" * 64,
                        "publication_id": "fixture-publication",
                        "prior_publication_id": None,
                        "financial_value_canary": 7654321,
                    }
                }
            )
            if kind == PUBLICATION
            else '{"unrelated_private_canary":"excluded"}'
        )
    return (
        f"fixture-history-{number:08}",
        number,
        tenant,
        scope,
        kind,
        version,
        version,
        visibility or scope,
        payload,
    )


def insert(log, rows):
    conn = log._conn
    conn.execute("BEGIN")
    try:
        conn.executemany(
            "INSERT INTO events (event_id,event_at_ms,tenant_id,owner_scope,type,"
            "version,schema_version,visibility_scope,payload) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.execute("COMMIT")
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


def adversarial_statistics(log):
    conn = log._conn
    conn.execute("ANALYZE")
    # Encourage an owner scan in the unfenced reference query. These statistics
    # describe only this disposable synthetic DB; event rows/triggers are intact.
    conn.execute("UPDATE sqlite_stat1 SET stat='25002 25002'")
    conn.execute(
        "UPDATE sqlite_stat1 SET stat='25002 1 1' "
        "WHERE idx='idx_events_owner_scope_time'"
    )
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='sqlite_stat4'").fetchone():
        conn.execute("DELETE FROM sqlite_stat4")
    conn.execute("ANALYZE sqlite_schema")


@pytest.fixture
async def log(tmp_path):
    value = EventLog(tmp_path / "history.db", KEY)
    try:
        yield value
    finally:
        await value.close()


async def captured_read(log):
    statements = []
    await log._run_locked(log._conn.set_trace_callback, statements.append)
    try:
        result = await log.read_finance_binding_metadata(TENANT, OWNER)
    finally:
        await log._run_locked(log._conn.set_trace_callback, None)

    def plans():
        return [
            (sql, [r[3] for r in log._conn.execute("EXPLAIN QUERY PLAN " + sql)])
            for sql in statements
            if sql.startswith("SELECT ")
        ]

    return result, await log._run_locked(plans)


@pytest.mark.parametrize("populated", [False, True])
async def test_unrelated_owner_history_cannot_exhaust_binding_scan(log, populated):
    await log._run_locked(insert, log, (row(i, kind=NOISE) for i in range(NOISE_ROWS)))
    if populated:
        await log._run_locked(insert, log, [row(NOISE_ROWS)])
    await log._run_locked(adversarial_statistics, log)

    def unfenced_reference():
        # Preserve the failing query shape and real VM ceiling as regression
        # evidence, not as a statement about the unobserved production exception.
        sql = (
            "SELECT event_id FROM events WHERE tenant_id=? AND owner_scope=? "
            "AND type IN (?,?) ORDER BY event_id DESC LIMIT 1"
        )
        args = (TENANT, "private:" + OWNER, PUBLICATION, "finance.projection_verified")
        plan = [r[3] for r in log._conn.execute("EXPLAIN QUERY PLAN " + sql, args)]
        assert any("idx_events_owner_scope_time" in detail for detail in plan), plan
        steps = 0

        def bounded():
            nonlocal steps
            steps += 1000
            return int(steps > 100_000)

        log._conn.set_progress_handler(bounded, 1000)
        try:
            with pytest.raises(sqlcipher3.DatabaseError):
                log._conn.execute(sql, args).fetchone()
            assert steps > 100_000
        finally:
            log._conn.set_progress_handler(None, 0)

    await log._run_locked(unfenced_reference)
    result, plans = await captured_read(log)
    assert result["state"] == "complete", result
    assert len(result["rows"]) == int(populated)
    assert result["through_event_id"] == (row(NOISE_ROWS)[0] if populated else None)
    encoded = json.dumps(result)
    assert "financial_value_canary" not in encoded
    assert "7654321" not in encoded and "unrelated_private_canary" not in encoded
    scans = [(sql, plan) for sql, plan in plans if "type IN" in sql]
    assert len(scans) == 2
    for _, plan in scans:
        assert any(
            "idx_events_type_id_time_causation" in detail
            and "SEARCH" in detail
            and "type=?" in detail
            for detail in plan
        ), plan
        assert not any("idx_events_owner_scope_time" in detail for detail in plan)
    probes = [(sql, plan) for sql, plan in plans if "json_extract" in sql]
    assert len(probes) == int(populated)
    for _, plan in probes:
        assert any(
            "sqlite_autoindex_events_1" in detail and "event_id=?" in detail
            for detail in plan
        ), plan
    assert await log.count() == NOISE_ROWS + int(populated)
    assert not await log._run_locked(lambda: log._conn.in_transaction)


@pytest.mark.parametrize(
    "legacy_version,index",
    [
        (3, "idx_events_type_time"),
        (5, "idx_events_type_id_time"),
    ],
)
async def test_read_only_history_uses_existing_legacy_index(
    tmp_path, legacy_version, index
):
    class LegacyLog(EventLog):
        MIGRATIONS = tuple(
            item for item in EventLog.MIGRATIONS if item[0] <= legacy_version
        )

    path = tmp_path / "legacy.db"
    legacy = LegacyLog(path, KEY)
    try:
        await legacy._run_locked(insert, legacy, [row(1)])
    finally:
        await legacy.close()
    archived = EventLog(path, KEY, read_only=True)
    try:
        result, plans = await captured_read(archived)
        assert result["state"] == "complete" and len(result["rows"]) == 1
        for sql, plan in plans:
            if "type IN" in sql:
                assert any(index in detail and "type=?" in detail for detail in plan), (
                    plan
                )
        versions = await archived._run_locked(
            lambda: [
                r[0]
                for r in archived._conn.execute(
                    "SELECT version FROM _schema_version ORDER BY version"
                )
            ]
        )
        assert versions == list(range(1, legacy_version + 1))
    finally:
        await archived.close()
    assert not path.read_bytes().startswith(b"SQLite format 3")


@pytest.mark.parametrize("limit", ["vm", "time"])
async def test_own_progress_interrupt_has_safe_limit_reason_and_cleans_up(
    log, monkeypatch, limit
):
    # Many selected-type rows belonging to a different owner still count toward
    # the unchanged finite scan ceiling. An index is not an unlimited scan grant.
    await log._run_locked(
        insert, log, (row(i, owner="fixture-other") for i in range(NOISE_ROWS))
    )
    ticks = 0

    def clock():
        nonlocal ticks
        ticks += 1
        return float(ticks) if limit == "time" else 0.0

    with monkeypatch.context() as patch:
        patch.setattr(log_module, "time", SimpleNamespace(monotonic=clock))
        result = await log.read_finance_binding_metadata(TENANT, OWNER)
    assert result == {
        "state": "history_incomplete",
        "through_event_id": None,
        "rows": [],
        "reason": "history_scan_limit",
    }
    assert await log.count() == NOISE_ROWS
    assert not await log._run_locked(lambda: log._conn.in_transaction)


async def test_row_ceiling_and_other_owner_tenant_filters_are_preserved(log):
    await log._run_locked(
        insert,
        log,
        [row(1), row(2, owner="fixture-other"), row(3, tenant="fixture-other-tenant")],
    )
    result = await log.read_finance_binding_metadata(TENANT, OWNER)
    assert result["state"] == "complete" and len(result["rows"]) == 1
    assert result["through_event_id"] == row(1)[0]
    assert result["rows"][0]["owner_id"] == OWNER
    await log._run_locked(insert, log, (row(i) for i in range(4, 260)))
    limited = await log.read_finance_binding_metadata(TENANT, OWNER)
    assert limited["reason"] == "history_scan_limit" and limited["rows"] == []
    assert limited["state"] == "history_incomplete"
    assert await log.count() == 259


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("scope", "history_scope_mismatch"),
        ("json", "history_read_unavailable"),
        ("version", "unsupported_event_version"),
    ],
)
async def test_scope_mismatch_and_other_database_errors_remain_closed(
    log, fault, expected
):
    entry = (
        row(1, visibility="shared:household")
        if fault == "scope"
        else row(1, version=99)
        if fault == "version"
        else row(1, payload="{malformed_private_canary")
    )
    await log._run_locked(insert, log, [entry])
    result = await log.read_finance_binding_metadata(TENANT, OWNER)
    assert result["state"] == (
        "schema_unavailable" if fault == "version" else "history_incomplete"
    )
    assert result["rows"] == [] and result["reason"] == expected
    assert "malformed_private_canary" not in json.dumps(result)
    assert "malformed JSON" not in json.dumps(result)
    assert not await log._run_locked(lambda: log._conn.in_transaction)
    assert await log.count() == 1
