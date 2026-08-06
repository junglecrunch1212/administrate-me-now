"""Deterministic Triage projection handlers."""

from __future__ import annotations

import json
from typing import Any

import sqlcipher3


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _insert(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    proposal_id: str,
    source_kind: str,
    target_kind: str,
    owner_member_id: str,
    title: str,
    candidate: dict[str, Any],
    route: dict[str, Any] | None,
    source_event_id: str,
    confidence: float | None,
    reasons: list[str],
) -> None:
    if envelope["owner_scope"] != f"private:{owner_member_id}":
        return
    conn.execute(
        """
        INSERT INTO triage_items (
            proposal_id, tenant_id, source_kind, target_kind, owner_member_id,
            title, candidate_json, route_json, source_event_id, confidence,
            reasons_json, state, proposed_at, owner_scope, visibility_scope,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, proposal_id) DO NOTHING
        """,
        (
            proposal_id,
            envelope["tenant_id"],
            source_kind,
            target_kind,
            owner_member_id,
            title,
            _json(candidate),
            _json(route) if route is not None else None,
            source_event_id,
            confidence,
            _json(reasons),
            envelope["occurred_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def _apply_triage_proposed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    _insert(
        envelope,
        conn,
        proposal_id=payload["proposal_id"],
        source_kind="triage_item",
        target_kind=payload["target_kind"],
        owner_member_id=payload["owner_member_id"],
        title=payload["title"],
        candidate=dict(payload.get("candidate") or {}),
        route=payload.get("route"),
        source_event_id=payload["source_event_id"],
        confidence=payload.get("confidence"),
        reasons=list(payload.get("reasons") or []),
    )


def _apply_household_action(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    _insert(
        envelope,
        conn,
        proposal_id=payload["proposal_id"],
        source_kind="household_action",
        target_kind=payload["kind"],
        owner_member_id=payload["owner_member_id"],
        title=payload["title"],
        candidate={
            "title": payload["title"],
            "due": payload.get("due"),
            **(
                {"owed_to_party_id": payload["owed_to_party_id"]}
                if payload.get("owed_to_party_id")
                else {}
            ),
        },
        route={"member_id": payload["owner_member_id"]},
        source_event_id=envelope.get("causation_id") or envelope["event_id"],
        confidence=1.0,
        reasons=[str(payload.get("reason") or "explicit_proposal")],
    )


def _apply_commitment_proposed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    _insert(
        envelope,
        conn,
        proposal_id=payload["commitment_id"],
        source_kind="commitment_proposal",
        target_kind="commitment",
        owner_member_id=payload["owed_by_member_id"],
        title=payload["text_summary"],
        candidate=dict(payload),
        route={"member_id": payload["owed_by_member_id"]},
        source_event_id=envelope.get("causation_id") or envelope["event_id"],
        confidence=payload.get("confidence"),
        reasons=list(payload.get("classify_reasons") or []),
    )


def _apply_identity_suggestion(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    owner_scope = str(envelope["owner_scope"])
    if not owner_scope.startswith("private:"):
        return
    owner_member_id = owner_scope.removeprefix("private:")
    payload = envelope["payload"]
    _insert(
        envelope,
        conn,
        proposal_id=f"identity_{envelope['event_id']}",
        source_kind="identity_suggestion",
        target_kind="party_identifier",
        owner_member_id=owner_member_id,
        title=f"Review possible {payload['candidate_kind']} identity match",
        candidate=dict(payload),
        route={"member_id": owner_member_id},
        source_event_id=payload["source_event_id"],
        confidence=payload.get("confidence"),
        reasons=[str(payload.get("heuristic") or "identity similarity")],
    )


def _apply_enriched(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    row = conn.execute(
        "SELECT candidate_json FROM triage_items WHERE tenant_id=? AND proposal_id=?",
        (envelope["tenant_id"], payload["proposal_id"]),
    ).fetchone()
    if row is None:
        return
    candidate = json.loads(row[0])
    candidate.update(payload.get("candidate_updates") or {})
    assignments = ["candidate_json=?", "last_event_id=?"]
    values: list[Any] = [_json(candidate), envelope["event_id"]]
    if payload.get("title") is not None:
        assignments.append("title=?")
        values.append(payload["title"])
    if payload.get("target_kind") is not None:
        assignments.append("target_kind=?")
        values.append(payload["target_kind"])
    if payload.get("route") is not None:
        assignments.append("route_json=?")
        values.append(_json(payload["route"]))
    values.extend((envelope["tenant_id"], payload["proposal_id"], envelope["event_id"]))
    conn.execute(
        f"""UPDATE triage_items SET {", ".join(assignments)}
              WHERE tenant_id=? AND proposal_id=? AND state='pending_review'
                AND last_event_id < ?""",  # noqa: S608 - fixed column allowlist
        values,
    )


def _resolve(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    *,
    state: str,
    proposal_id: str,
    resolved_at: str,
    resolved_by: str,
    materialized_event_ids: list[str] | None = None,
) -> None:
    conn.execute(
        """UPDATE triage_items
              SET state=?, resolved_at=?, resolved_by=?,
                  materialized_event_ids_json=?, last_event_id=?
            WHERE tenant_id=? AND proposal_id=? AND state='pending_review'
              AND last_event_id < ?""",
        (
            state,
            resolved_at,
            resolved_by,
            _json(materialized_event_ids or []),
            envelope["event_id"],
            envelope["tenant_id"],
            proposal_id,
            envelope["event_id"],
        ),
    )


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    payload = envelope["payload"]
    if event_type == "triage.item.proposed":
        _apply_triage_proposed(envelope, conn)
    elif event_type == "household.action.proposed":
        _apply_household_action(envelope, conn)
    elif event_type == "identity.merge_suggested":
        _apply_identity_suggestion(envelope, conn)
    elif event_type == "commitment.proposed":
        _apply_commitment_proposed(envelope, conn)
    elif event_type == "triage.item.enriched":
        _apply_enriched(envelope, conn)
    elif event_type == "commitment.edited":
        updates = payload.get("field_updates") or {}
        _apply_enriched(
            {
                **envelope,
                "payload": {
                    "proposal_id": payload["commitment_id"],
                    "enriched_at": payload["edited_at"],
                    "enriched_by_member_id": payload["edited_by_party_id"],
                    "title": updates.get("description"),
                    "candidate_updates": updates,
                    "route": None,
                },
            },
            conn,
        )
    elif event_type == "triage.item.confirmed":
        _resolve(
            envelope,
            conn,
            state="confirmed",
            proposal_id=payload["proposal_id"],
            resolved_at=payload["confirmed_at"],
            resolved_by=payload["confirmed_by_member_id"],
            materialized_event_ids=payload["materialized_event_ids"],
        )
    elif event_type == "commitment.confirmed":
        _resolve(
            envelope,
            conn,
            state="confirmed",
            proposal_id=payload["commitment_id"],
            resolved_at=payload["confirmed_at"],
            resolved_by=payload["confirmed_by_member_id"],
            materialized_event_ids=[envelope["event_id"]],
        )
    elif event_type == "triage.item.dismissed":
        _resolve(
            envelope,
            conn,
            state="dismissed",
            proposal_id=payload["proposal_id"],
            resolved_at=payload["dismissed_at"],
            resolved_by=payload["dismissed_by_member_id"],
        )
    elif event_type == "commitment.dismissed":
        _resolve(
            envelope,
            conn,
            state="dismissed",
            proposal_id=payload["commitment_id"],
            resolved_at=payload["dismissed_at"],
            resolved_by=payload["dismissed_by_party_id"],
        )
