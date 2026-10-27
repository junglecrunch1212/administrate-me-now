"""Synthetic metadata proof tests; report names never certify financial inputs."""

from __future__ import annotations

import json
import sqlite3

import pytest

from adminme.finance.capabilities import readiness
from adminme.finance.publication import PublicationError
from adminme.finance.readiness import (
    publication_dependencies,
    publication_readiness_context,
)
from adminme.lib.session import build_internal_session


def _report(
    report_id="actuals", basis="accounting_actual", release_state="ready", **changes
):
    return {
        "report_id": report_id,
        "basis": basis,
        "release_state": release_state,
        "period_start": "2026-06-01",
        "period_end": "2026-06-30",
        "model_id": "model-synthetic",
        "model_version": "1",
        "quality": {
            key: "complete"
            for key in (
                "coverage",
                "reconciliation",
                "freshness",
                "classification",
                "model_validity",
            )
        },
        "required_evidence": [],
        **changes,
    }


def _metadata(**changes):
    return {
        "owner_id": "adult-a",
        "tenant_id": "tenant-a",
        "book_id": "book-a",
        "contract": "household-books.accepted-publication.v1",
        "publication_id": "publication-a",
        "content_sha256": "a" * 64,
        "accepted_at": "2026-07-02T00:00:00+00:00",
        "source_clock_summary": {
            "oldest_observed_at": "2026-07-01T00:00:00+00:00",
            "oldest_accepted_at": "2026-07-01T01:00:00+00:00",
            "current_freshness_verified": False,
        },
        "reports": [_report()],
        "source_reference": "private-source-must-not-return",
        **changes,
    }


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE finance_publications (
            tenant_id TEXT, owner_id TEXT, book_id TEXT, publication_id TEXT,
            metadata_json TEXT
        );
        CREATE TABLE finance_publication_heads (
            tenant_id TEXT, owner_id TEXT, book_id TEXT, publication_id TEXT
        );
    """)
    yield conn
    conn.close()


def _store(conn, metadata):
    coordinates = (
        "tenant-a",
        "adult-a",
        metadata["book_id"],
        metadata["publication_id"],
    )
    conn.execute(
        "INSERT INTO finance_publications VALUES (?,?,?,?,?)",
        (*coordinates, json.dumps(metadata)),
    )
    conn.execute("INSERT INTO finance_publication_heads VALUES (?,?,?,?)", coordinates)


def _session(actor="adult-a", tenant="tenant-a", role="principal"):
    return build_internal_session(actor, role, tenant)


def test_accepted_boundary_and_independent_clocks_are_only_partial_proofs(db):
    _store(db, _metadata())
    context = publication_readiness_context(db, _session(), "F12", book_id="book-a")
    assert set(context["dependencies"]) == {"accepted_revisions", "source_clocks"}
    for dependency in context["dependencies"].values():
        assert dependency["accepted"] is True
        assert dependency["authorized"] is True
        assert dependency["complete"] is False
        assert dependency["current"] is False
        assert dependency["proof_origin"]["publication_id"] == "publication-a"
        assert "current_source_freshness" in dependency["missing_qualifiers"]
    resolved = readiness(
        "F12", dependencies=context["dependencies"], enabled=True, permitted=True
    )
    assert resolved["readiness"] == "missing_evidence"
    assert set(resolved["missing_evidence"]) == {
        "accepted_revisions",
        "source_clocks",
        "statement_totals",
        "openings",
        "matching_evidence",
    }
    assert "private-source-must-not-return" not in json.dumps(context)


def test_actuals_or_budget_report_name_does_not_prove_categories_preferences_or_targets(
    db,
):
    _store(db, _metadata(reports=[_report(), _report("budget", "chosen_budget")]))
    for capability_id in ("F01", "F05", "F08", "F13", "F19", "F21", "F28"):
        assert publication_dependencies(db, _session(), capability_id) == {}
    result = publication_readiness_context(db, _session(), "F01")
    assert result["report_availability"][0]["readiness"] == "qualified"
    assert result["report_availability"][0]["numeric_conclusion_supported"] is False
    assert result["report_availability"][0]["next_query"] == {
        "book_id": "book-a",
        "publication_id": "publication-a",
        "report_id": "actuals",
    }


def test_pinned_baseline_never_implies_complete_long_term_inputs(db):
    _store(db, _metadata(reports=[_report("scenarios", "forecast")]))
    result = publication_readiness_context(
        db, _session(), "F30", book_id="book-a", publication_id="publication-a"
    )
    assert set(result["dependencies"]) == {"accepted_baseline"}
    assert result["dependencies"]["accepted_baseline"]["complete"] is False
    assert result["report_availability"][0]["readiness"] == "qualified"
    assert result["report_availability"][0]["model_version"] == "1"
    assert "income" not in result["dependencies"]


@pytest.mark.parametrize(
    "report,qualifier",
    [
        (_report(release_state="unavailable"), "released_report"),
        (_report(basis="cash_movement"), "report_basis_mismatch"),
        (_report(quality={"coverage": "unknown"}), "report_coverage"),
        (_report(model_version=""), "report_model_identity"),
    ],
)
def test_report_missing_unknown_invalid_basis_or_model_is_not_available(
    db, report, qualifier
):
    _store(db, _metadata(reports=[report]))
    result = publication_readiness_context(db, _session(), "F01")[
        "report_availability"
    ][0]
    assert result["readiness"] == "missing_evidence"
    assert result["next_query"] is None
    assert qualifier in result["missing_qualifiers"]


def test_partial_report_exposes_only_qualified_read_and_preserves_dimensions(db):
    report = _report(release_state="qualified", required_evidence=["statement-period"])
    report["quality"]["reconciliation"] = "partial"
    _store(db, _metadata(reports=[report]))
    result = publication_readiness_context(db, _session(), "F01")[
        "report_availability"
    ][0]
    assert result["readiness"] == "qualified"
    assert set(result["missing_qualifiers"]) >= {
        "report_reconciliation",
        "report_required_evidence",
        "current_source_freshness",
    }
    assert not result["numeric_conclusion_supported"]


def test_missing_report_is_not_replaced_with_another_report(db):
    _store(db, _metadata(reports=[_report("scenarios", "forecast")]))
    result = publication_readiness_context(db, _session(), "F01")[
        "report_availability"
    ][0]
    assert result["readiness"] == "missing_evidence"
    assert result["missing_qualifiers"] == ["released_report"]


@pytest.mark.parametrize(
    "clock_changes",
    [
        {"oldest_observed_at": None},
        {"oldest_accepted_at": None},
        {"oldest_observed_at": "2026-07-01T00:00:00"},
        {"oldest_observed_at": "2026-07-05T00:00:00+00:00"},
        {"oldest_accepted_at": "2026-07-05T00:00:00+00:00"},
    ],
)
def test_clock_summary_does_not_hide_missing_naive_or_future_clock(db, clock_changes):
    metadata = _metadata()
    metadata["source_clock_summary"].update(clock_changes)
    _store(db, metadata)
    result = publication_dependencies(db, _session(), "F12")
    assert "source_clocks" not in result
    assert "accepted_revisions" in result


def test_snapshot_clock_claim_cannot_establish_current_source_freshness(db):
    metadata = _metadata()
    metadata["source_clock_summary"]["current_freshness_verified"] = True
    _store(db, metadata)
    result = publication_dependencies(db, _session(), "F12")
    assert result["source_clocks"]["current"] is False


def test_owner_and_tenant_isolation_return_the_same_missing_state(db):
    _store(db, _metadata())
    absent = {
        "dependencies": {},
        "report_availability": [],
        "missing_qualifiers": ["accepted_publication"],
    }
    assert publication_readiness_context(db, _session("adult-b"), "F12") == absent
    assert (
        publication_readiness_context(db, _session(tenant="tenant-b"), "F12") == absent
    )
    assert (
        publication_readiness_context(db, _session(), "F12", book_id="unknown")
        == absent
    )
    assert (
        publication_readiness_context(
            db, _session(), "F12", book_id="book-a", publication_id="unknown"
        )
        == absent
    )
    with pytest.raises(PublicationError, match="permission_required"):
        publication_readiness_context(db, _session(role="child"), "F12")


def test_inconsistent_stored_owner_fails_closed(db):
    _store(db, _metadata(owner_id="adult-b"))
    with pytest.raises(PublicationError, match="permission_required"):
        publication_readiness_context(db, _session(), "F12")


def test_ambiguous_book_requires_selection_and_exact_pin_is_preserved(db):
    _store(db, _metadata())
    _store(db, _metadata(book_id="book-b", publication_id="publication-b"))
    with pytest.raises(PublicationError, match="book_selection_required"):
        publication_readiness_context(db, _session(), "F12")
    result = publication_readiness_context(
        db, _session(), "F12", book_id="book-b", publication_id="publication-b"
    )
    assert result["publication_id"] == "publication-b"
    assert (
        result["dependencies"]["accepted_revisions"]["proof_origin"]["publication_id"]
        == "publication-b"
    )
