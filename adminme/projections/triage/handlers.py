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
    target_kind: str | None,
    owner_member_id: str,
    title: str,
    candidate: dict[str, Any],
    route: dict[str, Any] | None,
    source_event_id: str,
    confidence: float | None,
    reasons: list[str],
    state: str = "pending_review",
    parent_intent_id: str | None = None,
    candidate_key: str | None = None,
    extraction_event_id: str | None = None,
    eligibility_rule_ids: list[str] | None = None,
    interpretation: dict[str, Any] | None = None,
) -> None:
    if envelope["owner_scope"] != f"private:{owner_member_id}":
        return
    conn.execute(
        """
        INSERT INTO triage_items (
            proposal_id, tenant_id, source_kind, target_kind, owner_member_id,
            title, candidate_json, route_json, source_event_id, confidence,
            reasons_json, state, proposed_at, owner_scope, visibility_scope,
            parent_intent_id, candidate_key, extraction_event_id,
            eligibility_rule_ids_json,
            interpretation_json, sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            state,
            envelope["occurred_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            parent_intent_id,
            candidate_key,
            extraction_event_id,
            _json(eligibility_rule_ids or []),
            _json(interpretation) if interpretation is not None else None,
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
        parent_intent_id=payload.get("parent_intent_id"),
        candidate_key=payload.get("candidate_key"),
        extraction_event_id=payload.get("extraction_event_id"),
        eligibility_rule_ids=list(payload.get("eligibility_rule_ids") or []),
        interpretation=payload.get("interpretation"),
    )
    parent_intent_id = payload.get("parent_intent_id")
    if parent_intent_id:
        conn.execute(
            """UPDATE triage_items
                  SET state='dismissed', resolved_at=?, resolved_by=?,
                      resolution_reason='candidates_extracted', last_event_id=?
                WHERE tenant_id=? AND proposal_id=?
                  AND source_kind='member_intent'
                  AND state='pending_interpretation' AND last_event_id < ?""",
            (
                envelope["occurred_at"],
                "system:deterministic-extractor",
                envelope["event_id"],
                envelope["tenant_id"],
                parent_intent_id,
                envelope["event_id"],
            ),
        )


def _apply_intent_submitted(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    _insert(
        envelope,
        conn,
        proposal_id=payload["intent_id"],
        source_kind="member_intent",
        target_kind=payload.get("kind_hint"),
        owner_member_id=payload["member_id"],
        title=payload["text"],
        candidate={
            "exact_text": payload["text"],
            "linked_source_event_id": payload.get("source_event_id"),
            "source_surface": payload["source_surface"],
        },
        route={"member_id": payload["member_id"]},
        source_event_id=envelope["event_id"],
        confidence=None,
        reasons=["member_submitted_for_review"],
        state="pending_interpretation",
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
        assignments.append("state='pending_review'")
    if payload.get("route") is not None:
        assignments.append("route_json=?")
        values.append(_json(payload["route"]))
    values.extend((envelope["tenant_id"], payload["proposal_id"], envelope["event_id"]))
    conn.execute(
        f"""UPDATE triage_items SET {", ".join(assignments)}
              WHERE tenant_id=? AND proposal_id=?
                AND state IN ('pending_interpretation','pending_review')
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
    resolution_reason: str | None = None,
) -> None:
    conn.execute(
        """UPDATE triage_items
              SET state=?, resolved_at=?, resolved_by=?,
                  materialized_event_ids_json=?, resolution_reason=?, last_event_id=?
            WHERE tenant_id=? AND proposal_id=? AND state='pending_review'
              AND last_event_id < ?""",
        (
            state,
            resolved_at,
            resolved_by,
            _json(materialized_event_ids or []),
            resolution_reason,
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
    elif event_type == "triage.intent.submitted":
        _apply_intent_submitted(envelope, conn)
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
        if int(envelope.get("schema_version") or 1) == 1:
            _resolve(
                envelope,
                conn,
                state="confirmed",
                proposal_id=payload["proposal_id"],
                resolved_at=payload["confirmed_at"],
                resolved_by=payload["confirmed_by_member_id"],
                materialized_event_ids=payload["materialized_event_ids"],
            )
        else:
            conn.execute(
                """UPDATE triage_items
                      SET state='approved', resolved_at=?, resolved_by=?,
                          domain_approval_event_id=?,
                          resolution_reason='approved_awaiting_openclaw',
                          last_event_id=?
                    WHERE tenant_id=? AND proposal_id=?
                      AND state='pending_review' AND last_event_id < ?""",
                (
                    payload["confirmed_at"],
                    payload["confirmed_by_member_id"],
                    envelope["event_id"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    payload["proposal_id"],
                    envelope["event_id"],
                ),
            )
    elif event_type == "external_sync.write_requested":
        proposal_id = payload.get("origin_proposal_id")
        if proposal_id:
            conn.execute(
                """UPDATE triage_items
                      SET resolution_reason='approved_awaiting_provider',
                          materialization_event_id=?, last_event_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='approved'
                      AND last_event_id < ?""",
                (
                    envelope["event_id"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    proposal_id,
                    envelope["event_id"],
                ),
            )
    elif event_type == "external_sync.write_completed":
        proposal_id = payload.get("origin_proposal_id")
        if proposal_id:
            suppressed = bool(payload.get("suppressed"))
            conn.execute(
                """UPDATE triage_items
                      SET state='confirmed', resolution_reason=?,
                          outcome_event_id=?, last_event_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='approved'
                      AND last_event_id < ?""",
                (
                    "observation_suppressed"
                    if suppressed
                    else "provider_completed_awaiting_observation",
                    envelope["event_id"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    proposal_id,
                    envelope["event_id"],
                ),
            )
    elif event_type == "external_sync.write_failed":
        proposal_id = payload.get("origin_proposal_id")
        if proposal_id:
            conn.execute(
                """UPDATE triage_items
                      SET state='failed', resolution_reason='provider_failed',
                          outcome_event_id=?, last_event_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='approved'
                      AND last_event_id < ?""",
                (
                    envelope["event_id"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    proposal_id,
                    envelope["event_id"],
                ),
            )
    elif event_type == "triage.item.materialized":
        conn.execute(
            """UPDATE triage_items
                  SET state='confirmed', materialized_event_ids_json=?,
                      materialization_event_id=?,
                      resolution_reason='confirmed_local', last_event_id=?
                WHERE tenant_id=? AND proposal_id=? AND state='approved'
                  AND domain_approval_event_id=? AND last_event_id < ?""",
            (
                _json(payload["materialized_event_ids"]),
                envelope["event_id"],
                envelope["event_id"],
                envelope["tenant_id"],
                payload["proposal_id"],
                payload["confirmation_event_id"],
                envelope["event_id"],
            ),
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
            resolution_reason=payload.get("reason"),
        )
    elif event_type == "triage.item.superseded":
        _resolve(
            envelope,
            conn,
            state="dismissed",
            proposal_id=payload["proposal_id"],
            resolved_at=payload["superseded_at"],
            resolved_by="system:source_changed",
            resolution_reason=payload["reason"],
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
