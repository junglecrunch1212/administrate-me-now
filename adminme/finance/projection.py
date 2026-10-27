"""Atomic, replayable publication projection within the money transaction."""

from typing import Any
from datetime import datetime, timezone
from adminme.finance.cash_publication import cash_reports
from adminme.finance.publication import (
    PublicationError,
    canonical_bytes,
    validate_publication,
    validate_transition,
)


def apply_publication(envelope: dict[str, Any], conn: Any) -> None:
    p = validate_publication(envelope["payload"]["publication"])
    scope = f"private:{p['owner_id']}"
    if (
        envelope["tenant_id"] != p["tenant_id"]
        or envelope["owner_scope"] != scope
        or envelope["visibility_scope"] != scope
        or envelope["sensitivity"] != "privileged"
    ):
        raise PublicationError("publication_scope_invalid")
    key = (p["tenant_id"], p["owner_id"], p["book_id"])
    existing = conn.execute(
        "SELECT content_sha256 FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        (*key, p["publication_id"]),
    ).fetchone()
    if existing:
        if existing[0] != p["content_sha256"]:
            raise PublicationError("publication_identity_conflict")
        from adminme.finance.provenance_store import verify_archived

        verify_archived(conn, p)
        return
    head = conn.execute(
        "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        key,
    ).fetchone()
    if (head[0] if head else None) != p["prior_publication_id"]:
        raise PublicationError("publication_prior_conflict")
    previous = conn.execute(
        "SELECT publication_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
        (*key, p["prior_publication_id"]),
    ).fetchone()
    import json

    from adminme.finance.provenance_store import verify_archived

    prior_publication = json.loads(previous[0]) if previous else None
    validate_transition(prior_publication, p)
    prior_proof = (
        verify_archived(conn, prior_publication) if prior_publication else None
    )
    verify_archived(conn, p, prior_proof)
    metadata = {
        k: v
        for k, v in p.items()
        if k
        not in {
            "reports",
            "cash_projection",
            "accepted_journal_revision_ids",
            "excluded_pending_revision_ids",
            "source_coverage",
            "source_references",
        }
    }
    metadata["source_clock_summary"] = {
        "oldest_observed_at": min(
            (
                datetime.fromisoformat(s["observed_at"])
                .astimezone(timezone.utc)
                .isoformat()
                for s in p["source_coverage"]
            ),
            default=None,
        ),
        "oldest_accepted_at": min(
            (
                datetime.fromisoformat(s["accepted_at"])
                .astimezone(timezone.utc)
                .isoformat()
                for s in p["source_coverage"]
            ),
            default=None,
        ),
        "current_freshness_verified": False,
    }
    reports = p["reports"] + (
        cash_reports(p["cash_projection"]) if "cash_projection" in p else []
    )
    metadata["reports"] = [
        {k: v for k, v in r.items() if k != "metrics"} for r in reports
    ]
    conn.execute(
        "INSERT INTO finance_publications VALUES (?,?,?,?,?,?,?,?)",
        (
            *key,
            p["publication_id"],
            p["content_sha256"],
            canonical_bytes(p).decode(),
            canonical_bytes(metadata).decode(),
            envelope["event_id"],
        ),
    )
    for report in reports:
        conn.executemany(
            "INSERT INTO finance_publication_metrics VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    *key,
                    p["publication_id"],
                    report["report_id"],
                    m["metric_id"],
                    i,
                    canonical_bytes(m).decode(),
                )
                for i, m in enumerate(report["metrics"])
            ],
        )
    conn.execute(
        "INSERT INTO finance_publication_heads VALUES (?,?,?,?) ON CONFLICT(tenant_id,owner_id,book_id) DO UPDATE SET publication_id=excluded.publication_id",
        (*key, p["publication_id"]),
    )
