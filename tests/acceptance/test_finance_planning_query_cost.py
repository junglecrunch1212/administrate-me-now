"""Synthetic query-plan/VM-work diagnosis; raw mixed-load gates remain separate."""

from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from adminme.finance.planning_store import import_projection, query_projection
from adminme.runtime.application import create_application
from tests.acceptance.test_finance_disclosure import enabled_settings, import_fixture
from tests.fixtures.finance_planning import shared_projection


@pytest.mark.skipif(
    os.getenv("ADMINME_FINANCE_QUERY_COST") != "1",
    reason="Hosted synthetic query diagnosis only; never the family runtime",
)
def test_deep_full_horizon_pages_seek_before_reading_shared_bodies(tmp_path):
    app = create_application(enabled_settings(tmp_path))
    with TestClient(app) as client:
        assert client.portal is not None
        accounting, owner = import_fixture(client)
        value = shared_projection(accounting)
        runtime = app.state.runtime
        client.portal.call(import_projection, runtime, owner, value)
        manifest = max(value["detail_manifest"], key=lambda m: m["row_count"])
        context_id = manifest["context_id"]
        context = next(c for c in value["contexts"] if c["context_id"] == context_id)
        start = date.fromisoformat(context["as_of_date"]) + timedelta(days=1)
        end = start + timedelta(days=365)
        key = (
            owner.tenant_id,
            owner.auth_member_id,
            value["book_id"],
            value["projection_id"],
        )
        # The prior production query is retained solely as a matched diagnostic
        # reference. Same database/rows/parameters; no private source fixture.
        prior_sql = "SELECT e.event_json FROM finance_dated_event_bindings b JOIN finance_dated_shared_events e USING(tenant_id,owner_id,book_id,projection_id,record_id) WHERE b.tenant_id=? AND b.owner_id=? AND b.book_id=? AND b.projection_id=? AND b.context_id=? AND (json_extract(e.event_json,'$.earliest_date') IS NULL OR (json_extract(e.event_json,'$.earliest_date') < ? AND json_extract(e.event_json,'$.latest_date') >= ?)) ORDER BY b.ordinal LIMIT ? OFFSET ?"

        def diagnose(conn):
            results = []
            for offset in (0, 1409, 2816, 2817, 2818):
                prior_parameters = (
                    *key,
                    context_id,
                    end.isoformat(),
                    start.isoformat(),
                    2,
                    offset,
                )
                old_rows = conn.execute(prior_sql, prior_parameters).fetchall()
                expected = [
                    {**json.loads(row[0]), "context_id": context_id}
                    for row in old_rows[:1]
                ]
                measurements = {"prior": [], "candidate": []}
                statement_counts = {"prior": [], "candidate": []}
                candidate_statements = []
                for trial in range(12):
                    # Alternate order; diagnostics are not a timing pass gate.
                    for mode in (
                        ("prior", "candidate") if trial % 2 else ("candidate", "prior")
                    ):
                        ticks = 0

                        def progress():
                            nonlocal ticks
                            ticks += 1
                            return 0

                        conn.set_progress_handler(progress, 100)
                        conn.set_trace_callback(
                            candidate_statements.append if mode == "candidate" else None
                        )
                        began = time.perf_counter()
                        try:
                            if mode == "prior":
                                conn.execute(prior_sql, prior_parameters).fetchall()
                            else:
                                answer = query_projection(
                                    conn,
                                    owner,
                                    book_id=value["book_id"],
                                    projection_id=value["projection_id"],
                                    context_id=context_id,
                                    report_id="planning_events",
                                    horizon_days=365,
                                    offset=offset,
                                    limit=1,
                                )
                                assert answer["events"] == expected
                                assert answer["next_offset"] == (
                                    offset + 1 if len(old_rows) > 1 else None
                                )
                        finally:
                            measurements[mode].append(
                                (time.perf_counter() - began) * 1000
                            )
                            statement_counts[mode].append(ticks * 100)
                            conn.set_progress_handler(None, 0)
                            conn.set_trace_callback(None)
                page_sql = next(
                    sql
                    for sql in candidate_statements
                    if "finance_dated_event_bindings" in sql
                )
                candidate_steps = max(statement_counts["candidate"])
                prior_steps = min(statement_counts["prior"])
                if 1409 <= offset < 2818:
                    # Count the complete candidate query, including metadata,
                    # against just the old page SQL: deep pages must stop doing
                    # work proportional to all earlier event bodies.
                    assert candidate_steps < prior_steps / 4, (
                        offset,
                        candidate_steps,
                        prior_steps,
                    )
                results.append(
                    {
                        "offset": offset,
                        "candidate_max_vm_steps_rounded_100": candidate_steps,
                        "prior_min_vm_steps_rounded_100": prior_steps,
                        "candidate_query_plan": [
                            list(row)
                            for row in conn.execute("EXPLAIN QUERY PLAN " + page_sql)
                        ],
                        "prior_query_plan": [
                            list(row)
                            for row in conn.execute(
                                "EXPLAIN QUERY PLAN " + prior_sql, prior_parameters
                            )
                        ],
                        "candidate_full_query_ms": measurements["candidate"],
                        "prior_page_only_ms": measurements["prior"],
                    }
                )
            return results

        async def run():
            return await runtime.projections._read_native("money", diagnose)

        report = client.portal.call(run)
        print(
            "SYNTHETIC_PLANNING_QUERY_COST "
            + json.dumps(
                {
                    "diagnostic_only": True,
                    "rows": manifest["row_count"],
                    "offsets": report,
                }
            ),
            flush=True,
        )
