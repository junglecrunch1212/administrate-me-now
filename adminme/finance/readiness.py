"""Conservative question evidence from private accepted-publication metadata.

Report identity is not proof of metric meaning. The initial publication
contract has opaque metric IDs and no typed category/holding/preference proof.
This module therefore proves only an accepted revision boundary, a pinned
baseline and the available clock summary. It never upgrades these partial
structural facts into complete/current financial inputs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from adminme.finance.capabilities import capability
from adminme.finance.publication import PublicationError
from adminme.finance.queries import _metadata
from adminme.lib.session import Session

_DIMENSIONS = (
    "coverage",
    "reconciliation",
    "freshness",
    "classification",
    "model_validity",
)
_REPORT_BASES = {"actuals": "accounting_actual", "scenarios": "forecast"}
_STRUCTURAL_PROOFS = {
    "accepted_revisions": (
        "accepted_journal_revision_boundary",
        ("source_coverage", "current_source_freshness"),
    ),
    "accepted_baseline": (
        "pinned_accepted_publication",
        ("baseline_semantic_coverage", "current_source_freshness"),
    ),
    "source_clocks": (
        "independent_oldest_source_clocks",
        ("per_source_clocks", "current_source_freshness"),
    ),
}


def _clock_summary_present(metadata: dict[str, Any]) -> bool:
    clocks = metadata.get("source_clock_summary")
    if not isinstance(clocks, dict):
        return False
    try:
        observed = datetime.fromisoformat(clocks["oldest_observed_at"])
        accepted = datetime.fromisoformat(clocks["oldest_accepted_at"])
        publication = datetime.fromisoformat(metadata["accepted_at"])
        return all(
            value.tzinfo is not None for value in (observed, accepted, publication)
        ) and (observed <= accepted <= publication)
    except (KeyError, TypeError, ValueError):
        return False


def _report_availability(
    metadata: dict[str, Any],
    report_id: str,
    origin: dict[str, str],
) -> dict[str, Any]:
    """Suggest only an exact supported report read, never its numeric meaning."""
    result: dict[str, Any] = {
        "report_id": report_id,
        "readiness": "missing_evidence",
        "numeric_conclusion_supported": False,
        "next_query": None,
    }
    expected_basis = _REPORT_BASES.get(report_id)
    if expected_basis is None:
        return {**result, "missing_qualifiers": ["report_semantics_not_mapped"]}
    report = next(
        (
            item
            for item in metadata.get("reports", [])
            if item.get("report_id") == report_id
        ),
        None,
    )
    if report is None or report.get("release_state") == "unavailable":
        return {**result, "missing_qualifiers": ["released_report"]}
    if report.get("basis") != expected_basis:
        return {**result, "missing_qualifiers": ["report_basis_mismatch"]}
    quality = report.get("quality", {})
    missing = {
        f"report_{dimension}"
        for dimension in _DIMENSIONS
        if quality.get(dimension) != "complete"
    }
    if not report.get("model_id") or not report.get("model_version"):
        missing.add("report_model_identity")
    # A historical accepted snapshot does not establish current provider health.
    missing.add("current_source_freshness")
    required = report.get("required_evidence", [])
    if required:
        missing.add("report_required_evidence")
    invalid = any(
        quality.get(dimension) not in {"complete", "partial"}
        for dimension in _DIMENSIONS
    )
    if report.get("release_state") not in {"ready", "qualified"} or invalid:
        return {**result, "missing_qualifiers": sorted(missing), "proof_origin": origin}
    if "report_model_identity" in missing:
        return {**result, "missing_qualifiers": sorted(missing), "proof_origin": origin}
    return {
        **result,
        "readiness": "qualified",
        "basis": expected_basis,
        "period_start": report["period_start"],
        "period_end": report["period_end"],
        "model_id": report["model_id"],
        "model_version": report["model_version"],
        "proof_origin": {**origin, "kind": "accepted_publication_report_metadata"},
        "missing_qualifiers": sorted(missing),
        "available_output": "bounded_accepted_report_read",
        "next_query": {
            "book_id": metadata["book_id"],
            "publication_id": metadata["publication_id"],
            "report_id": report_id,
        },
    }


def publication_readiness_context(
    conn: Any,
    session: Session,
    capability_id: str,
    *,
    book_id: str | None = None,
    publication_id: str | None = None,
) -> dict[str, Any]:
    """Return verified partial dependencies and scoped, pinned report pointers.

    The caller must independently verify the current tool/processing grant and
    reauthorize delivery. This helper enforces private owner selection through
    the same projection query used for report reads; no hidden-account counts,
    source IDs, evidence links, metric values or raw records are returned.
    """
    item = capability(capability_id)
    metadata = _metadata(conn, session, book_id, publication_id)
    if metadata is None:
        return {
            "dependencies": {},
            "report_availability": [],
            "missing_qualifiers": ["accepted_publication"],
        }
    # Projection insertion already checks these coordinates. Fail closed if
    # stored metadata is inconsistent rather than exposing a corrupt row.
    if (
        metadata.get("owner_id") != session.auth_member_id
        or metadata.get("tenant_id") != session.tenant_id
    ):
        raise PublicationError("finance_permission_required")
    origin = {
        "kind": "accepted_publication_metadata",
        "publication_id": metadata["publication_id"],
        "content_hash": metadata["content_sha256"],
    }
    dependencies: dict[str, dict[str, Any]] = {}
    for evidence_key in item.evidence:
        proof = _STRUCTURAL_PROOFS.get(evidence_key)
        if proof is None:
            continue
        if evidence_key == "source_clocks" and not _clock_summary_present(metadata):
            continue
        proof_kind, qualifiers = proof
        dependencies[evidence_key] = {
            "accepted": True,
            "authorized": True,
            "complete": False,
            "current": False,
            "proof_origin": {**origin, "kind": proof_kind},
            "missing_qualifiers": list(qualifiers),
        }
    return {
        "publication_id": metadata["publication_id"],
        "book_id": metadata["book_id"],
        "content_hash": metadata["content_sha256"],
        "dependencies": dependencies,
        "report_availability": [
            _report_availability(metadata, report_id, origin)
            for report_id in item.report_ids
        ],
        "missing_qualifiers": [
            "question_semantic_dependencies",
            "current_source_freshness",
        ],
    }


def publication_dependencies(
    conn: Any,
    session: Session,
    capability_id: str,
    *,
    book_id: str | None = None,
    publication_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Compatibility helper for capability readiness's dependency argument."""
    context = publication_readiness_context(
        conn,
        session,
        capability_id,
        book_id=book_id,
        publication_id=publication_id,
    )
    return context["dependencies"]
