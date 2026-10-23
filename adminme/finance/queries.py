"""Private indexed aggregate reads; no raw transaction, SQL or source URL API."""

from __future__ import annotations
import json
from datetime import date, timedelta
from typing import Any
from adminme.finance.publication import PublicationError
from adminme.lib.session import Session


def _owner(session: Session) -> str:
    if session.auth_role != "principal" or session.is_view_as:
        raise PublicationError("finance_permission_required")
    if f"private:{session.auth_member_id}" not in session.allowed_scopes:
        raise PublicationError("finance_permission_required")
    return session.auth_member_id


def _metadata(
    conn: Any, session: Session, book_id: str | None, publication_id: str | None = None
) -> dict[str, Any] | None:
    owner = _owner(session)
    if publication_id is not None:
        row = conn.execute(
            "SELECT metadata_json FROM finance_publications WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=?",
            (session.tenant_id, owner, book_id, publication_id),
        ).fetchone()
    else:
        # Missing book selection returns a bounded index, never chooses another adult.
        rows = conn.execute(
            "SELECT p.metadata_json FROM finance_publication_heads h JOIN finance_publications p USING(tenant_id,owner_id,book_id,publication_id) WHERE h.tenant_id=? AND h.owner_id=? AND (? IS NULL OR h.book_id=?) ORDER BY h.book_id LIMIT 2",
            (session.tenant_id, owner, book_id, book_id),
        ).fetchall()
        if len(rows) > 1:
            raise PublicationError("finance_book_selection_required")
        row = rows[0] if rows else None
    if row is None:
        return None
    result = json.loads(row[0])
    # Accepted snapshots do not prove current provider freshness.
    for report in result["reports"]:
        report["source_release_state"] = report["release_state"]
        if report["release_state"] == "ready":
            report["release_state"] = "qualified"
        report["freshness_state"] = "historical_snapshot"
        report["required_evidence"] = sorted(
            set(report["required_evidence"]) | {"current_source_freshness"}
        )
    return result


def publication_status(
    conn: Any, session: Session, book_id: str | None = None
) -> dict[str, Any]:
    p = _metadata(conn, session, book_id)
    if p is None:
        return {
            "state": "missing_evidence",
            "required_evidence": ["accepted_publication"],
            "reports": [],
            "provider_effect_count": 0,
        }
    return {
        "state": "qualified",
        "publication_id": p["publication_id"],
        "schema_version": p["contract"],
        "content_hash": p["content_sha256"],
        "book_id": p["book_id"],
        "current_head_publication_id": p["publication_id"],
        "as_of": p["accepted_at"],
        "reports": p["reports"],
        "source_clock_summary": p["source_clock_summary"],
        "provider_effect_count": 0,
    }


def query_publication(
    conn: Any,
    session: Session,
    book_id: str,
    publication_id: str,
    report_id: str,
    offset: int = 0,
    limit: int = 50,
    scenario_id: str | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any]:
    if (
        isinstance(limit, bool)
        or not 1 <= limit <= 100
        or isinstance(offset, bool)
        or not 0 <= offset <= 1000
    ):
        raise PublicationError("finance_pagination_limit")
    if report_id not in {
        "actuals",
        "cash",
        "budget",
        "observed_net",
        "balance_sheet",
        "scenarios",
        "quality",
        "cash_projection",
        "cash_events",
    }:
        raise PublicationError("finance_report_unknown")
    if scenario_id is not None and scenario_id not in {"current", "expected", "chosen"}:
        raise PublicationError("finance_scenario_unknown")
    if horizon_days is not None and (
        type(horizon_days) is not int or horizon_days not in {7, 30, 90, 180, 365}
    ):
        raise PublicationError("finance_horizon_unknown")
    if (scenario_id is not None or horizon_days is not None) and report_id not in {
        "cash_projection",
        "cash_events",
    }:
        raise PublicationError("finance_filter_invalid")
    if report_id == "cash_events":
        scenario_id = scenario_id or "current"
        horizon_days = horizon_days or 90
    p = _metadata(conn, session, book_id, publication_id)
    if p is None:
        return {
            "state": "missing_evidence",
            "required_evidence": ["accepted_publication"],
            "metrics": [],
            "provider_effect_count": 0,
        }
    head = conn.execute(
        "SELECT publication_id FROM finance_publication_heads WHERE tenant_id=? AND owner_id=? AND book_id=?",
        (session.tenant_id, _owner(session), book_id),
    ).fetchone()
    report = next((r for r in p["reports"] if r["report_id"] == report_id), None)
    if report is not None and horizon_days is not None:
        report = {
            **report,
            "source_report_period": {
                "start": report["period_start"],
                "end": report["period_end"],
            },
            "period_end": (
                date.fromisoformat(report["period_start"])
                + timedelta(days=horizon_days)
            ).isoformat(),
            "horizon_days": horizon_days,
        }
    if report is None or report["release_state"] == "unavailable":
        return {
            "state": "missing_evidence",
            "report": report,
            "metrics": [],
            "provider_effect_count": 0,
        }
    filters = ""
    parameters: list[Any] = []
    if scenario_id is not None:
        filters += " AND json_extract(metric_json,'$.scenario_id')=?"
        parameters.append(scenario_id)
    if horizon_days is not None:
        if report_id == "cash_projection":
            filters += " AND json_extract(metric_json,'$.horizon_days')=?"
            parameters.append(horizon_days)
        else:
            end = (
                date.fromisoformat(report["period_start"])
                + timedelta(days=horizon_days)
            ).isoformat()
            filters += " AND json_extract(metric_json,'$.earliest_date')<=?"
            parameters.append(end)
    rows = conn.execute(
        "SELECT metric_json FROM finance_publication_metrics WHERE tenant_id=? AND owner_id=? AND book_id=? AND publication_id=? AND report_id=?"
        + filters
        + " ORDER BY ordinal LIMIT ? OFFSET ?",
        (
            session.tenant_id,
            _owner(session),
            book_id,
            publication_id,
            report_id,
            *parameters,
            limit + 1,
            offset,
        ),
    ).fetchall()
    # Drilldown references are internal IDs. An external Drive URL is never returned.
    metrics = []
    for row in rows[:limit]:
        metric = json.loads(row[0])
        metric.pop("revision_ids", None)
        metric.pop("owner_id", None)
        metrics.append(metric)
    if report_id == "cash_projection" and metrics:
        report["release_state"] = (
            "unavailable"
            if all(m["quality"] == "unavailable" for m in metrics)
            else "qualified"
        )
        report["required_evidence"] = sorted(
            {"current_source_freshness"}
            | {item for m in metrics for item in m.get("required_evidence", [])}
        )
    return {
        "state": "missing_evidence"
        if report["release_state"] == "unavailable"
        else report["release_state"],
        "publication_id": publication_id,
        "book_id": book_id,
        "current_head_publication_id": head[0] if head else None,
        "content_hash": p["content_sha256"],
        "source_clock_summary": p["source_clock_summary"],
        "report": report,
        "metrics": metrics,
        "next_offset": offset + limit if len(rows) > limit else None,
        "provider_effect_count": 0,
    }
