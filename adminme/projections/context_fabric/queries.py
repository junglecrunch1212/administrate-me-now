"""Strictly scoped, bounded reads for the Context Fabric."""

from __future__ import annotations

import json
from collections import deque
from typing import Any

import sqlcipher3

from adminme.lib.scope import filter_one, filter_rows
from adminme.lib.session import Session


def context_summary(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    context_id: str,
    member_id: str,
    edge_limit: int = 200,
) -> dict[str, Any] | None:
    edge_limit = max(1, min(edge_limit, 500))
    row = conn.execute(
        """SELECT * FROM contexts
            WHERE tenant_id=? AND context_id=? AND owner_member_id=?
              AND owner_scope=? AND visibility_scope=?""",
        (
            session.tenant_id,
            context_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
        ),
    ).fetchone()
    visible_context = filter_one(session, dict(row) if row is not None else None)
    if visible_context is None:
        return None
    edges = conn.execute(
        """SELECT * FROM context_edges
            WHERE tenant_id=? AND owner_member_id=? AND status='active'
              AND owner_scope=? AND visibility_scope=?
              AND (from_node_id=? OR to_node_id=?)
            ORDER BY valid_from DESC, edge_id LIMIT ?""",
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            context_id,
            context_id,
            edge_limit,
        ),
    ).fetchall()
    result = visible_context
    result["edges"] = []
    visible_edges = filter_rows(session, [dict(edge) for edge in edges])
    for value in visible_edges:
        value["reason_codes"] = json.loads(value.pop("reason_codes_json") or "[]")
        value["source_event_ids"] = json.loads(
            value.pop("source_event_ids_json") or "[]"
        )
        result["edges"].append(value)
    return result


def reconciliation_for_proposal(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    proposal_id: str,
    member_id: str,
) -> dict[str, Any] | None:
    """Return the latest exact private reconciliation receipt and relationships."""

    private_scope = f"private:{member_id}"
    row = conn.execute(
        """SELECT * FROM reconciliation_decisions
            WHERE tenant_id=? AND proposal_id=? AND owner_member_id=?
              AND owner_scope=? AND visibility_scope=?
            ORDER BY decision_revision DESC LIMIT 1""",
        (session.tenant_id, proposal_id, member_id, private_scope, private_scope),
    ).fetchone()
    visible = filter_one(session, dict(row) if row is not None else None)
    if visible is None:
        return None
    result = dict(visible)
    for field in (
        "evidence_source_event_ids_json",
        "policy_json",
        "source_coordinate_json",
        "priority_basis_json",
        "reason_codes_json",
    ):
        result[field.removesuffix("_json")] = json.loads(result.pop(field) or "[]")
    rows = conn.execute(
        """SELECT * FROM reconciliation_relationships
            WHERE tenant_id=? AND reconciliation_id=? AND owner_member_id=?
              AND owner_scope=? AND visibility_scope=?
            ORDER BY relationship_type, relationship_id""",
        (
            session.tenant_id,
            result["reconciliation_id"],
            member_id,
            private_scope,
            private_scope,
        ),
    ).fetchall()
    result["relationships"] = []
    for relationship in filter_rows(session, [dict(value) for value in rows]):
        relationship["reason_codes"] = json.loads(
            relationship.pop("reason_codes_json") or "[]"
        )
        result["relationships"].append(relationship)
    return result


def bounded_traversal(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    start_node_id: str,
    member_id: str,
    max_depth: int = 2,
    max_nodes: int = 100,
) -> dict[str, Any]:
    """Traverse only one exact private member scope with hard resource bounds."""

    max_depth = max(0, min(max_depth, 4))
    max_nodes = max(1, min(max_nodes, 500))
    allowed_authority = {"provider_exact", "member_confirmed", "deterministic_derived"}
    queue: deque[tuple[str, int]] = deque([(start_node_id, 0)])
    visited: set[str] = set()
    max_edges = min(max_nodes * 4, 1000)
    edges_by_id: dict[str, dict[str, Any]] = {}
    edge_limit_reached = False
    while queue and len(visited) < max_nodes:
        node_id, depth = queue.popleft()
        if node_id in visited:
            continue
        visible = conn.execute(
            """SELECT * FROM context_nodes
                WHERE tenant_id=? AND node_id=? AND owner_member_id=?
                  AND owner_scope=? AND visibility_scope=?""",
            (
                session.tenant_id,
                node_id,
                member_id,
                f"private:{member_id}",
                f"private:{member_id}",
            ),
        ).fetchone()
        if filter_one(session, dict(visible) if visible is not None else None) is None:
            continue
        visited.add(node_id)
        if depth >= max_depth:
            continue
        rows = conn.execute(
            """SELECT * FROM context_edges
                WHERE tenant_id=? AND owner_member_id=? AND status='active'
                  AND owner_scope=? AND visibility_scope=?
                  AND (from_node_id=? OR to_node_id=?)
                ORDER BY edge_type, edge_id LIMIT ?""",
            (
                session.tenant_id,
                member_id,
                f"private:{member_id}",
                f"private:{member_id}",
                node_id,
                node_id,
                max_edges,
            ),
        ).fetchall()
        for edge in filter_rows(session, [dict(row) for row in rows]):
            if edge["authority"] not in allowed_authority:
                continue
            edge_id = str(edge["edge_id"])
            if edge_id in edges_by_id:
                continue
            if len(edges_by_id) >= max_edges:
                edge_limit_reached = True
                queue.clear()
                break
            edges_by_id[edge_id] = edge
            other = (
                edge["to_node_id"]
                if edge["from_node_id"] == node_id
                else edge["from_node_id"]
            )
            if other not in visited:
                queue.append((other, depth + 1))
    return {
        "start_node_id": start_node_id,
        "node_ids": sorted(visited),
        "edges": sorted(
            edges_by_id.values(),
            key=lambda value: (value["edge_type"], value["edge_id"]),
        ),
        "truncated": bool(queue) or edge_limit_reached,
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "max_edges": max_edges,
    }


def feedback_pattern_counts(
    conn: sqlcipher3.Connection, session: Session, *, member_id: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT scope_kind, scope_value, reason, occurrences
             FROM (
                 SELECT 'context' AS scope_kind, context_id AS scope_value,
                        reason, COUNT(*) AS occurrences
                   FROM triage_feedback
                  WHERE tenant_id=? AND member_id=? AND owner_scope=?
                    AND visibility_scope=?
                    AND reason='wrong_context'
                    AND context_id IS NOT NULL AND context_id!=''
                  GROUP BY context_id, reason
                 UNION ALL
                 SELECT 'candidate_kind', target_kind, reason, COUNT(*)
                   FROM triage_feedback
                  WHERE tenant_id=? AND member_id=? AND owner_scope=?
                    AND visibility_scope=?
                    AND reason IN (
                        'not_actionable','duplicate','wrong_kind',
                        'recurring_normal','low_signal'
                    )
                    AND target_kind IS NOT NULL AND target_kind!=''
                  GROUP BY target_kind, reason
                 UNION ALL
                 SELECT 'account', provider_account_id, reason, COUNT(*)
                   FROM triage_feedback
                  WHERE tenant_id=? AND member_id=? AND owner_scope=?
                    AND visibility_scope=?
                    AND reason IN ('spam','provider_promotion')
                    AND provider_account_id IS NOT NULL AND provider_account_id!=''
                  GROUP BY provider_account_id, reason
                 UNION ALL
                 SELECT 'workspace', workspace, reason, COUNT(*)
                   FROM triage_feedback
                  WHERE tenant_id=? AND member_id=? AND owner_scope=?
                    AND visibility_scope=?
                    AND reason IN ('spam','provider_promotion')
                    AND workspace IS NOT NULL AND workspace!=''
                  GROUP BY workspace, reason
                 UNION ALL
                 SELECT 'sender', sender_digest, reason, COUNT(*)
                   FROM triage_feedback
                  WHERE tenant_id=? AND member_id=? AND owner_scope=?
                    AND visibility_scope=?
                    AND reason IN ('spam','provider_promotion')
                    AND sender_digest IS NOT NULL AND sender_digest!=''
                  GROUP BY sender_digest, reason
             )
            ORDER BY occurrences DESC, reason, scope_kind, scope_value""",
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def feedback_scope_count(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    scope_kind: str,
    scope_value: str,
) -> int:
    columns = {
        "context": "context_id",
        "candidate_kind": "target_kind",
        "account": "provider_account_id",
        "workspace": "workspace",
        "sender": "sender_digest",
    }
    column = columns.get(scope_kind)
    if column is None:
        raise ValueError("unsupported feedback policy scope")
    row = conn.execute(
        f"""SELECT COUNT(*) FROM triage_feedback
             WHERE tenant_id=? AND member_id=? AND owner_scope=?
               AND visibility_scope=?
               AND {column}=?""",  # noqa: S608 - column is a fixed allowlist
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            scope_value,
        ),
    ).fetchone()
    return int(row[0])


def verified_feedback_for_scope(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    scope_kind: str,
    scope_value: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return exact projected member outcomes usable as learning evidence."""

    columns = {
        "context": "context_id",
        "candidate_kind": "target_kind",
        "account": "provider_account_id",
        "workspace": "workspace",
        "sender": "sender_digest",
    }
    column = columns.get(scope_kind)
    if column is None:
        raise ValueError("unsupported feedback policy scope")
    rows = conn.execute(
        f"""SELECT feedback_id, proposal_id, reason, last_event_id, recorded_at
              FROM triage_feedback
             WHERE tenant_id=? AND member_id=? AND owner_scope=?
               AND visibility_scope=? AND {column}=?
             ORDER BY recorded_at DESC, feedback_id LIMIT ?""",  # noqa: S608
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
            scope_value,
            max(1, min(limit, 100)),
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def policy_rules_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM triage_policy_rules
            WHERE tenant_id=? AND member_id=? AND owner_scope=?
              AND visibility_scope=?
            ORDER BY approved_at DESC, rule_id""",
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def get_policy_rule(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    rule_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT * FROM triage_policy_rules
            WHERE tenant_id=? AND member_id=? AND rule_id=? AND owner_scope=?
              AND visibility_scope=?""",
        (
            session.tenant_id,
            member_id,
            rule_id,
            f"private:{member_id}",
            f"private:{member_id}",
        ),
    ).fetchone()
    return dict(row) if row is not None else None


def source_policy_proposals_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM triage_source_policy_proposals
             WHERE tenant_id=? AND member_id=? AND owner_scope=?
               AND visibility_scope=?
             ORDER BY proposed_at DESC, policy_proposal_id""",
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
        ),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        for key in (
            "verified_feedback_event_ids_json",
            "affected_proposal_ids_json",
            "policy_diff_json",
        ):
            value[key.removesuffix("_json")] = json.loads(value.pop(key))
        result.append(value)
    return result


def get_source_policy_proposal(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    policy_proposal_id: str,
) -> dict[str, Any] | None:
    rows = source_policy_proposals_for_member(conn, session, member_id=member_id)
    return next(
        (value for value in rows if value["policy_proposal_id"] == policy_proposal_id),
        None,
    )


def source_policy_rules_for_member(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT * FROM triage_source_policy_rules
             WHERE tenant_id=? AND member_id=? AND owner_scope=?
               AND visibility_scope=?
             ORDER BY activated_at DESC, rule_id""",
        (
            session.tenant_id,
            member_id,
            f"private:{member_id}",
            f"private:{member_id}",
        ),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        value = dict(row)
        value["trial_proposal_ids"] = json.loads(
            value.pop("trial_proposal_ids_json") or "[]"
        )
        result.append(value)
    return result


def get_source_policy_rule(
    conn: sqlcipher3.Connection,
    session: Session,
    *,
    member_id: str,
    rule_id: str,
) -> dict[str, Any] | None:
    rows = source_policy_rules_for_member(conn, session, member_id=member_id)
    return next((value for value in rows if value["rule_id"] == rule_id), None)


__all__ = [
    "bounded_traversal",
    "context_summary",
    "feedback_pattern_counts",
    "feedback_scope_count",
    "get_source_policy_proposal",
    "get_source_policy_rule",
    "get_policy_rule",
    "policy_rules_for_member",
    "reconciliation_for_proposal",
    "source_policy_proposals_for_member",
    "source_policy_rules_for_member",
    "verified_feedback_for_scope",
]
