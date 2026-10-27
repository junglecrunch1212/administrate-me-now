"""Synthetic Books boundary through the production runtime/event composition root."""

from __future__ import annotations
import copy
import json
from dataclasses import replace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from adminme.finance.importer import import_publication
from adminme.finance.publication import (
    PublicationError,
    content_hash,
    schema_hash,
    validate_publication,
)
from adminme.finance.queries import publication_status, query_publication
from adminme.lib.session import build_internal_session
from adminme.runtime.application import RuntimeSettings, create_application

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/finance/accepted-publication.synthetic.json"


def publication(tenant: str = "tenant-synthetic") -> dict:
    p = json.loads(FIXTURE.read_text())
    p["tenant_id"] = tenant
    p["content_sha256"] = content_hash(p)
    return p


def settings(tmp_path: Path, enabled: bool = True) -> RuntimeSettings:
    return replace(
        RuntimeSettings.credential_free(
            tmp_path,
            encryption_key=b"f" * 32,
            repository_root=ROOT,
            member_roles={"member-synthetic": "principal"},
        ),
        finance_publications_enabled=enabled,
    )


def test_contract_hash_and_unchanged_scenarios():
    p = publication()
    assert (
        schema_hash()
        == (
            ROOT
            / "adminme/finance/contracts/household-books.accepted-publication.v1.sha256"
        )
        .read_text()
        .strip()
    )
    assert validate_publication(p) == p
    scenarios = next(r for r in p["reports"] if r["report_id"] == "scenarios")
    assert [m["value_minor"] for m in scenarios["metrics"]] == [
        12500,
        13500,
        14500,
        15500,
    ]


@pytest.mark.parametrize(
    "mutation", ["pending", "conflict", "release", "owner", "missing", "url", "period"]
)
def test_reject_invalid_publication(mutation):
    p = publication()
    metric = p["reports"][0]["metrics"][0]
    if mutation == "pending":
        metric["revision_ids"] = ["revision-pending"]
    elif mutation == "conflict":
        p["accepted_journal_revision_ids"].append("revision-pending")
    elif mutation == "release":
        p["reports"][0]["release_state"] = "ready"
    elif mutation == "owner":
        metric["owner_id"] = "member-other"
    elif mutation == "missing":
        metric["value_minor"] = None
    elif mutation == "url":
        p["source_references"][0]["source_url"] = "https://example.invalid/private"
    elif mutation == "period":
        metric["period_end"] = "2026-02-28"
    p["content_sha256"] = content_hash(p)
    with pytest.raises(PublicationError):
        validate_publication(p)


def test_atomic_import_replay_and_owner_isolation(tmp_path):
    configured = settings(tmp_path)
    p = publication(configured.instance.tenant_id)
    owner = build_internal_session("member-synthetic", "principal", p["tenant_id"])
    other = build_internal_session("member-other", "principal", p["tenant_id"])
    app = create_application(configured)
    with TestClient(app) as client:
        runtime = app.state.runtime
        assert client.portal
        first = client.portal.call(import_publication, runtime, owner, p)
        repeat = client.portal.call(import_publication, runtime, owner, p)
        assert repeat["idempotent"] and first["event_id"] == repeat["event_id"]

        async def read(session, report):
            return query_publication(
                runtime.projections.connection("money"),
                session,
                p["book_id"],
                p["publication_id"],
                report,
            )

        result = client.portal.call(read, owner, "scenarios")
        assert [m["value_minor"] for m in result["metrics"]] == [
            12500,
            13500,
            14500,
            15500,
        ]
        assert (
            client.portal.call(read, other, "scenarios")["state"] == "missing_evidence"
        )
        assert client.portal.call(read, owner, "cash")["metrics"] == []
        conflict = copy.deepcopy(p)
        conflict["reports"][0]["metrics"][0]["value_minor"] = 999
        conflict["content_sha256"] = content_hash(conflict)
        with pytest.raises(PublicationError, match="conflict"):
            client.portal.call(import_publication, runtime, owner, conflict)
        partial = copy.deepcopy(p)
        partial["publication_id"] = "publication-2"
        partial["prior_publication_id"] = p["publication_id"]
        partial["reports"][0]["metrics"][0]["revision_ids"] = ["revision-pending"]
        partial["content_sha256"] = content_hash(partial)
        with pytest.raises(PublicationError):
            client.portal.call(import_publication, runtime, owner, partial)
        assert (
            client.portal.call(read, owner, "actuals")["metrics"][0]["value_minor"]
            == 12500
        )
    restarted = create_application(configured)
    with TestClient(restarted) as client:
        runtime = restarted.state.runtime
        assert client.portal

        async def inspect():
            return publication_status(
                runtime.projections.connection("money"), owner, p["book_id"]
            )

        assert client.portal.call(inspect)["publication_id"] == "publication-1"
        assert client.portal.call(import_publication, runtime, owner, p)["idempotent"]


def test_disabled_ingress_and_pending_tail_never_advance(tmp_path):
    configured = settings(tmp_path, False)
    app = create_application(configured)
    with TestClient(app) as client:
        assert client.portal
        p = publication(configured.instance.tenant_id)
        s = build_internal_session("member-synthetic", "principal", p["tenant_id"])
        with pytest.raises(PublicationError, match="disabled"):
            client.portal.call(import_publication, app.state.runtime, s, p)


def test_paused_recovery_cannot_be_cleared_or_ingested(tmp_path):
    configured = settings(tmp_path)
    p = publication(configured.instance.tenant_id)
    owner = build_internal_session("member-synthetic", "principal", p["tenant_id"])
    app = create_application(configured)
    with TestClient(app) as client:
        assert client.portal
        client.portal.call(import_publication, app.state.runtime, owner, p)
        for clear in (True, False):
            changed = copy.deepcopy(p)
            changed["publication_id"] = "publication-2"
            changed["prior_publication_id"] = p["publication_id"]
            if clear:
                changed["recovery_state"] = "clear"
            else:
                changed["accepted_journal_revision_ids"].append("revision-pending")
                changed["excluded_pending_revision_ids"] = []
            changed["content_sha256"] = content_hash(changed)
            with pytest.raises(PublicationError, match="recovery_hold"):
                client.portal.call(
                    import_publication, app.state.runtime, owner, changed
                )


def test_dated_books_horizons_are_preserved_and_expandable(tmp_path):
    configured = settings(tmp_path)
    p = json.loads(
        (FIXTURE.parent / "accepted-publication.dated.synthetic.json").read_text()
    )
    p["tenant_id"] = configured.instance.tenant_id
    # This formerly collided with the summary metric identifier at projection time.
    p["cash_projection"]["scenarios"][0]["cash_events"][0]["cash_event_id"] = (
        "7-closing_minor"
    )
    p["content_sha256"] = content_hash(p)
    owner = build_internal_session("member-synthetic", "principal", p["tenant_id"])
    app = create_application(configured)
    with TestClient(app) as client:
        assert client.portal
        runtime = app.state.runtime
        client.portal.call(import_publication, runtime, owner, p)

        async def query(report, scenario, horizon):
            return query_publication(
                runtime.projections.connection("money"),
                owner,
                p["book_id"],
                p["publication_id"],
                report,
                scenario_id=scenario,
                horizon_days=horizon,
            )

        for days in (7, 30, 90, 180, 365):
            current = client.portal.call(query, "cash_projection", "current", days)
            expected = client.portal.call(query, "cash_projection", "expected", days)
            assert [m["value_minor"] for m in current["metrics"]] == [40000, 40000]
            assert [m["value_minor"] for m in expected["metrics"]] == [70000, 40000]
            assert expected["metrics"][0]["first_shortage_date"] == "2026-01-03"
            assert expected["report"]["engine_authority"] == "accepted_books"
            assert expected["report"]["opening_liquid_minor"] == 100000
            assert expected["report"]["opening_reconciled"] is True
            assert expected["report"]["mandatory_flow_coverage"] == "complete"
            assert expected["report"]["chosen_floor_minor"] == 50000
            assert (
                expected["report"]["period_end"] == expected["metrics"][0]["period_end"]
            )
            assert expected["report"]["horizon_days"] == days
        events = client.portal.call(query, "cash_events", "expected", 7)["metrics"]
        assert [m["value_minor"] for m in events] == [-60000, 30000]
        assert all(m["cash_event_is_actual"] is False for m in events)
        current_events = client.portal.call(query, "cash_events", "current", 7)[
            "metrics"
        ]
        assert current_events[0]["metric_id"] == "cash-event-current-7-closing_minor"
        incomplete = copy.deepcopy(p)
        incomplete["cash_projection"]["opening_reconciled"] = False
        incomplete["content_sha256"] = content_hash(incomplete)
        with pytest.raises(PublicationError, match="missing_input_verdict"):
            validate_publication(incomplete)
        # One unavailable horizon must not inherit another horizon's readiness.
        p["prior_publication_id"] = p["publication_id"]
        p["publication_id"] = "publication-dated-2"
        horizon = p["cash_projection"]["scenarios"][0]["horizons"][0]
        horizon.update(
            closing_minor=None,
            minimum_minor=None,
            first_shortage_date=None,
            first_surplus_date=None,
            release_state="unavailable",
            required_evidence=["dated_payment_confirmation"],
        )
        p["content_sha256"] = content_hash(p)
        client.portal.call(import_publication, runtime, owner, p)
        unavailable = client.portal.call(query, "cash_projection", "current", 7)
        assert unavailable["state"] == "missing_evidence"
        assert unavailable["report"]["period_end"] == "2026-01-08"
        assert (
            "dated_payment_confirmation" in unavailable["report"]["required_evidence"]
        )
        assert (
            client.portal.call(query, "cash_projection", "current", 30)["state"]
            == "qualified"
        )


@pytest.mark.parametrize(
    "case", ["uncited_event", "future_opening", "metric_collision"]
)
def test_dated_export_evidence_and_identity_gate(case):
    p = json.loads(
        (FIXTURE.parent / "accepted-publication.dated.synthetic.json").read_text()
    )
    if case == "uncited_event":
        p["cash_projection"]["scenarios"][0]["cash_events"][0]["evidence_ids"] = []
    elif case == "future_opening":
        p["cash_projection"]["as_of_date"] = "2027-01-01"
    else:
        p["reports"][0]["metrics"][0]["metric_id"] = (
            "cash-summary-current-7-closing_minor"
        )
    p["content_sha256"] = content_hash(p)
    with pytest.raises(PublicationError):
        validate_publication(p)


@pytest.mark.parametrize(
    "case", ["wrong_basis", "future_source", "source_order", "future_actual"]
)
def test_publication_semantic_clocks_and_bases(case):
    p = publication()
    if case == "wrong_basis":
        p["reports"][0]["basis"] = "cash_movement"
        p["reports"][0]["metrics"][0]["basis"] = "cash_movement"
    elif case == "future_source":
        p["source_coverage"][0]["observed_at"] = "2026-02-01T12:00:00Z"
    elif case == "source_order":
        p["source_coverage"][0]["accepted_at"] = "2026-01-29T12:00:00Z"
    elif case == "future_actual":
        p["reports"][0]["period_end"] = "2026-02-01"
        p["reports"][0]["metrics"][0]["period_end"] = "2026-02-01"
    p["content_sha256"] = content_hash(p)
    with pytest.raises(PublicationError):
        validate_publication(p)


def test_books_mode_excludes_legacy_finance_reverse_but_preserves_operations(tmp_path):
    from adminme.projections.xlsx_workbooks import (
        FINANCE_WORKBOOK_NAME,
        OPS_WORKBOOK_NAME,
    )

    configured = settings(tmp_path)
    app = create_application(configured)
    with TestClient(app) as client:
        assert client.portal
        runtime = app.state.runtime
        daemon = runtime.xlsx_reverse
        assert daemon is not None
        # Stop filesystem notifications so this test exercises each cycle once.
        client.portal.call(daemon.stop)
        p = publication(configured.instance.tenant_id)
        owner = build_internal_session("member-synthetic", "principal", p["tenant_id"])
        client.portal.call(import_publication, runtime, owner, p)

        async def exercise():
            # Books ingress excludes legacy writeback even if a caller bypasses
            # watchdog scheduling. Operations still traverses its real cycle.
            daemon._schedule_cycle(FINANCE_WORKBOOK_NAME)
            assert FINANCE_WORKBOOK_NAME not in daemon._debounce_tasks
            await daemon.run_cycle_now(FINANCE_WORKBOOK_NAME)
            await daemon.run_cycle_now(OPS_WORKBOOK_NAME)
            return [
                event
                async for event in runtime.event_log.read_since()
                if event["source_adapter"] == "xlsx_reverse"
            ]

        reverse_events = client.portal.call(exercise)
        assert reverse_events
        assert all(
            event["payload"].get("workbook_name") != FINANCE_WORKBOOK_NAME
            for event in reverse_events
        )
        assert any(
            event["type"] == "xlsx.reverse_projected"
            and event["payload"]["workbook_name"] == OPS_WORKBOOK_NAME
            for event in reverse_events
        )
