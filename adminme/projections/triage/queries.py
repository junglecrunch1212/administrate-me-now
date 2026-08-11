"""Scope-filtered reads for the Triage proposal projection."""

from __future__ import annotations

import base64
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
        and result["decision_disposition"] == "decision"
        else ["enrich", "dismiss"]
        if result["state"] in {"pending_interpretation", "pending_review"}
        and result["decision_disposition"] != "decision"
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
    limit: int | None = 100,
) -> list[dict[str, Any]]:
    sql = """SELECT * FROM triage_items
             WHERE tenant_id=? AND owner_member_id=?
               AND owner_scope=? AND visibility_scope=?
               AND state IN ('pending_interpretation','pending_review')
             ORDER BY proposed_at DESC, proposal_id"""
    private_scope = f"private:{member_id}"
    params: list[Any] = [
        session.tenant_id,
        member_id,
        private_scope,
        private_scope,
    ]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(max(1, min(limit, 5000)))
    rows = conn.execute(sql, params).fetchall()
    return [_public(row) for row in filter_rows(session, [dict(row) for row in rows])]


def pending_for_sources(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    source_event_ids: list[str],
) -> list[dict[str, Any]]:
    """Return pending candidates only for source rows already on a surface."""

    source_ids = sorted({value for value in source_event_ids if value})
    if not source_ids:
        return []
    rows_by_proposal: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(source_ids), 400):
        chunk = source_ids[offset : offset + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""SELECT * FROM triage_items
                 WHERE tenant_id=? AND owner_member_id=?
                   AND owner_scope=? AND visibility_scope=?
                   AND state IN ('pending_interpretation','pending_review')
                   AND source_event_id IN ({placeholders})
                 ORDER BY proposed_at DESC, proposal_id""",  # noqa: S608 - placeholders only
            [
                session.tenant_id,
                member_id,
                f"private:{member_id}",
                f"private:{member_id}",
                *chunk,
            ],
        ).fetchall()
        for row in filter_rows(session, [dict(value) for value in rows]):
            rows_by_proposal[str(row["proposal_id"])] = row
    ordered = sorted(
        rows_by_proposal.values(),
        key=lambda row: (str(row["proposed_at"]), str(row["proposal_id"])),
        reverse=True,
    )
    return [_public(row) for row in ordered]


_LANE_ORDER = {
    "security": 0,
    "time_bound": 1,
    "financial": 2,
    "member_promise": 3,
    "member_priority": 4,
    "recurring_exception": 5,
    "standard": 6,
}


def matter_for_proposal(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    proposal_id: str,
) -> dict[str, Any] | None:
    """Return one exact pending matter independent of cursor position.

    The requested candidate still passes tenant, member, private-scope, and
    row-level authorization checks. This read helper does not change queue
    ordering or candidate state; it only makes a typed deep link resolvable.
    """

    focused = get_item(conn, session, proposal_id=proposal_id)
    private_scope = f"private:{member_id}"
    if (
        focused is None
        or focused.get("owner_member_id") != member_id
        or focused.get("owner_scope") != private_scope
        or focused.get("visibility_scope") != private_scope
        or focused.get("state") not in {"pending_interpretation", "pending_review"}
    ):
        return None
    context_id = str(focused.get("context_id") or proposal_id)
    rows = conn.execute(
        """SELECT * FROM triage_items
             WHERE tenant_id=? AND owner_member_id=?
               AND owner_scope=? AND visibility_scope=?
               AND state IN ('pending_interpretation','pending_review')
               AND context_id=?
             ORDER BY proposed_at, proposal_id""",
        (
            session.tenant_id,
            member_id,
            private_scope,
            private_scope,
            context_id,
        ),
    ).fetchall()
    children = [
        _public(row) for row in filter_rows(session, [dict(value) for value in rows])
    ]
    if not children:
        return None
    root = min(
        children,
        key=lambda item: (
            _LANE_ORDER.get(str(item.get("queue_lane") or "standard"), 99),
            str(item.get("proposed_at") or ""),
            str(item.get("proposal_id") or ""),
        ),
    )
    return {
        "context_id": context_id,
        "context_kind": root.get("context_kind"),
        "label": root.get("group_label") or root.get("title"),
        "queue_lane": root.get("queue_lane"),
        "root_proposal_id": root.get("proposal_id"),
        "candidate_count": len(children),
        "source_count": len({str(child["source_event_id"]) for child in children}),
        "oldest_proposed_at": min(
            str(child.get("proposed_at") or "") for child in children
        ),
        "candidates": children,
    }


def _cursor_encode(rank: int, proposed_at: str, proposal_id: str) -> str:
    raw = json.dumps([rank, proposed_at, proposal_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(value: str | None) -> tuple[int, str, str] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
        if (
            isinstance(decoded, list)
            and len(decoded) == 3
            and isinstance(decoded[0], int)
            and all(isinstance(item, str) for item in decoded[1:])
        ):
            return decoded[0], decoded[1], decoded[2]
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    raise ValueError("invalid triage cursor")


def _lens_where(lens: str) -> tuple[str, list[Any]]:
    if lens == "needs_interpretation":
        return "AND target_kind IS NULL", []
    if lens == "needs_details":
        return "AND (ownership_state='unknown' OR lifecycle_state='unknown')", []
    if lens == "low_signal":
        return (
            "AND queue_lane='standard' AND ownership_state='unknown' "
            "AND lifecycle_state='unknown'",
            [],
        )
    if lens == "recurring_exceptions":
        return "AND queue_lane='recurring_exception'", []
    if lens == "learned_rules":
        # Shadow rules live in the separate Context Fabric projection.  The
        # member surface composes their digest-only matches at read time; this
        # projection never treats a learned rule as queue authority.
        return "", []
    if lens == "all_candidates":
        return "", []
    if lens == "ready":
        return "AND target_kind IS NOT NULL", []
    return "", []


def triage_page_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    lens: str = "all_candidates",
    cursor: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Return complete counts plus one stable page of matter-first decisions.

    Inventory counts every unresolved candidate.  The decision queue chooses
    one root per deterministic context and nests every candidate from that
    context on the returned page.  Nothing is deleted or merged.
    """

    allowed_lenses = {
        "ready",
        "needs_details",
        "needs_interpretation",
        "low_signal",
        "recurring_exceptions",
        "learned_rules",
        "all_candidates",
    }
    if lens not in allowed_lenses:
        raise ValueError("unsupported triage lens")
    limit = max(1, min(limit, 100))
    decoded = _cursor_decode(cursor)
    private_scope = f"private:{member_id}"
    lens_sql, lens_params = _lens_where(lens)
    lane_case = (
        "CASE queue_lane "
        + " ".join(f"WHEN '{lane}' THEN {rank}" for lane, rank in _LANE_ORDER.items())
        + " ELSE 99 END"
    )
    disposition_sql = (
        "" if lens == "all_candidates" else "AND decision_disposition='decision'"
    )
    cursor_sql = ""
    cursor_params: list[Any] = []
    if decoded is not None:
        rank, proposed_at, proposal_id = decoded
        cursor_sql = (
            "AND (lane_rank > ? OR (lane_rank=? AND (proposed_at > ? OR "
            "(proposed_at=? AND proposal_id>?))))"
        )
        cursor_params = [rank, rank, proposed_at, proposed_at, proposal_id]
    roots = conn.execute(
        f"""
        WITH ranked AS (
          SELECT *, {lane_case} AS lane_rank,
                 ROW_NUMBER() OVER (
                   PARTITION BY context_id
                   ORDER BY {lane_case}, proposed_at, proposal_id
                 ) AS context_row
            FROM triage_items
           WHERE tenant_id=? AND owner_member_id=?
             AND owner_scope=? AND visibility_scope=?
             AND state IN ('pending_interpretation','pending_review')
             {disposition_sql} {lens_sql}
        )
        SELECT * FROM ranked WHERE context_row=1 {cursor_sql}
        ORDER BY lane_rank, proposed_at, proposal_id LIMIT ?
        """,  # noqa: S608 - fragments are fixed allowlists above
        [
            session.tenant_id,
            member_id,
            private_scope,
            private_scope,
            *lens_params,
            *cursor_params,
            limit + 1,
        ],
    ).fetchall()
    has_more = len(roots) > limit
    roots = roots[:limit]
    context_ids = [str(row["context_id"]) for row in roots]
    children_by_context: dict[str, list[dict[str, Any]]] = {
        context_id: [] for context_id in context_ids
    }
    if context_ids:
        placeholders = ",".join("?" for _ in context_ids)
        children = conn.execute(
            f"""SELECT * FROM triage_items
                 WHERE tenant_id=? AND owner_member_id=?
                   AND owner_scope=? AND visibility_scope=?
                   AND state IN ('pending_interpretation','pending_review')
                   AND context_id IN ({placeholders})
                 ORDER BY proposed_at, proposal_id""",  # noqa: S608 - placeholders only
            [
                session.tenant_id,
                member_id,
                private_scope,
                private_scope,
                *context_ids,
            ],
        ).fetchall()
        visible = filter_rows(session, [dict(row) for row in children])
        for row in visible:
            children_by_context[str(row["context_id"])].append(_public(row))
    matters: list[dict[str, Any]] = []
    root_public_by_id = {
        str(value["proposal_id"]): value
        for value in (
            _public(row) for row in filter_rows(session, [dict(root) for root in roots])
        )
    }
    for root in roots:
        proposal_id = str(root["proposal_id"])
        root_public = root_public_by_id.get(proposal_id)
        if root_public is None:
            continue
        context_id = str(root["context_id"])
        children = children_by_context.get(context_id, [])
        matters.append(
            {
                "context_id": context_id,
                "context_kind": root["context_kind"],
                "label": root["group_label"] or root["title"],
                "queue_lane": root["queue_lane"],
                "root_proposal_id": proposal_id,
                "candidate_count": len(children),
                "source_count": len(
                    {str(child["source_event_id"]) for child in children}
                ),
                "oldest_proposed_at": min(
                    (str(child["proposed_at"]) for child in children),
                    default=str(root["proposed_at"]),
                ),
                "candidates": children,
            }
        )
    inventory_row = conn.execute(
        """SELECT COUNT(*) AS total,
                  COUNT(DISTINCT context_id) AS matters,
                  SUM(CASE WHEN decision_disposition='decision' THEN 1 ELSE 0 END) AS decisions,
                  SUM(CASE WHEN decision_disposition='evidence_only' THEN 1 ELSE 0 END) AS duplicate_evidence,
                  SUM(CASE WHEN decision_disposition='attach_existing' THEN 1 ELSE 0 END) AS existing_evidence
             FROM triage_items
            WHERE tenant_id=? AND owner_member_id=?
              AND owner_scope=? AND visibility_scope=?
              AND state IN ('pending_interpretation','pending_review')""",
        (session.tenant_id, member_id, private_scope, private_scope),
    ).fetchone()
    lane_rows = conn.execute(
        """SELECT queue_lane, COUNT(*) AS candidates,
                  COUNT(DISTINCT context_id) AS matters
             FROM triage_items
            WHERE tenant_id=? AND owner_member_id=?
              AND owner_scope=? AND visibility_scope=?
              AND state IN ('pending_interpretation','pending_review')
            GROUP BY queue_lane ORDER BY queue_lane""",
        (session.tenant_id, member_id, private_scope, private_scope),
    ).fetchall()
    next_cursor = None
    if has_more and roots:
        last = roots[-1]
        next_cursor = _cursor_encode(
            int(last["lane_rank"]), str(last["proposed_at"]), str(last["proposal_id"])
        )
    return {
        "lens": lens,
        "inventory": {
            "total_candidates": int(inventory_row["total"] or 0),
            "total_matters": int(inventory_row["matters"] or 0),
            "decision_candidates": int(inventory_row["decisions"] or 0),
            "duplicate_evidence": int(inventory_row["duplicate_evidence"] or 0),
            "existing_object_evidence": int(inventory_row["existing_evidence"] or 0),
            "lanes": [dict(row) for row in lane_rows],
        },
        "matters": matters,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "page_size": limit,
    }


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
               AND owner_scope=? AND visibility_scope=?
               AND state IN ('approved','confirmed','dismissed','failed')
               AND COALESCE(resolution_reason, '') != 'candidates_extracted'
             ORDER BY resolved_at DESC, proposal_id
             LIMIT ?""",
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            limit,
        ),
    ).fetchall()
    return [_public(row) for row in filter_rows(session, [dict(row) for row in rows])]


__all__ = [
    "get_item",
    "matter_for_proposal",
    "pending_for_member",
    "pending_for_sources",
    "recent_for_member",
    "triage_page_for_member",
]
