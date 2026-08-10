"""Scope-filtered reads for the Triage proposal projection."""

from __future__ import annotations

import json
from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def _public(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result["candidate"] = json.loads(result.pop("candidate_json") or "{}")
    route = result.pop("route_json")
    result["route"] = json.loads(route) if route else None
    result["reasons"] = json.loads(result.pop("reasons_json") or "[]")
    result["materialized_event_ids"] = json.loads(
        result.pop("materialized_event_ids_json") or "[]"
    )
    result["eligibility_rule_ids"] = json.loads(
        result.pop("eligibility_rule_ids_json") or "[]"
    )
    interpretation = result.pop("interpretation_json", None)
    result["interpretation"] = json.loads(interpretation) if interpretation else None
    result["id"] = result["proposal_id"]
    result["kind"] = result["target_kind"]
    result["actions"] = (
        ["edit", "approve", "dismiss"]
        if result["source_kind"] == "commitment_proposal"
        and result["state"] == "pending_review"
        else ["enrich", "approve", "dismiss"]
        if result["state"] in {"pending_interpretation", "pending_review"}
        else ["await_openclaw_approval"]
        if result["state"] == "approved"
        else []
    )
    if result.get("resolution_reason") in {
        "source_changed",
        "source_deleted",
        "source_privileged",
        "eligibility_rule_replaced",
    }:
        result["state"] = "superseded"
    return result


def get_item(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    proposal_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM triage_items WHERE tenant_id=? AND proposal_id=?",
        (session.tenant_id, proposal_id),
    ).fetchone()
    visible = filter_one(session, dict(row) if row is not None else None)
    return _public(visible) if visible is not None else None


def pending_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM triage_items
             WHERE tenant_id=? AND owner_member_id=?
               AND state IN ('pending_interpretation','pending_review')
             ORDER BY proposed_at DESC, proposal_id
             LIMIT ?""",
        (session.tenant_id, member_id, limit),
    ).fetchall()
    return [_public(row) for row in filter_rows(session, [dict(row) for row in rows])]


def recent_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return recently resolved proposals for reversible review history."""

    rows = conn.execute(
        """SELECT * FROM triage_items
             WHERE tenant_id=? AND owner_member_id=?
               AND state IN ('approved','confirmed','dismissed','failed')
               AND COALESCE(resolution_reason, '') != 'candidates_extracted'
             ORDER BY resolved_at DESC, proposal_id
             LIMIT ?""",
        (session.tenant_id, member_id, limit),
    ).fetchall()
    return [_public(row) for row in filter_rows(session, [dict(row) for row in rows])]


__all__ = ["get_item", "pending_for_member", "recent_for_member"]
