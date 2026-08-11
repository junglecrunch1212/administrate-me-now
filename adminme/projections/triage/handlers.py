"""Deterministic Triage projection handlers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import sqlcipher3

from adminme.context_fabric import (
    evidence_state,
    exact_text_fingerprint,
    lifecycle_state,
    ownership_state,
    queue_lane,
    stable_digest,
    triage_context_id,
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _parsed_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _proposal_context(
    envelope: dict[str, Any],
    conn: sqlcipher3.Connection,
    payload: dict[str, Any],
) -> tuple[str, str]:
    """Bound a provider conversation into deterministic temporal matters."""

    interpretation = payload.get("interpretation")
    thread_key = (
        str(interpretation.get("thread_key") or "")
        if isinstance(interpretation, dict)
        else ""
    )
    if not thread_key:
        return triage_context_id(envelope, payload)
    tenant_id = str(envelope["tenant_id"])
    owner = str(payload["owner_member_id"])
    source_id = str(payload["source_event_id"])
    proposed_at = str(envelope["occurred_at"])
    latest = conn.execute(
        """SELECT * FROM triage_context_sessions
            WHERE tenant_id=? AND owner_member_id=? AND thread_key=?
            ORDER BY sequence DESC LIMIT 1""",
        (tenant_id, owner, thread_key),
    ).fetchone()
    if latest is None:
        sequence = 0
        context_id = "ctx_matter_" + stable_digest(
            tenant_id, owner, thread_key, sequence
        )
        conn.execute(
            """INSERT INTO triage_context_sessions (
                   tenant_id, owner_member_id, thread_key, sequence, context_id,
                   first_proposed_at, last_proposed_at, last_source_event_id,
                   source_count, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                tenant_id,
                owner,
                thread_key,
                sequence,
                context_id,
                proposed_at,
                proposed_at,
                source_id,
                envelope["event_id"],
            ),
        )
        conn.execute(
            """INSERT INTO triage_context_session_sources (
                   tenant_id, owner_member_id, thread_key, sequence, source_event_id
               ) VALUES (?, ?, ?, ?, ?)""",
            (tenant_id, owner, thread_key, sequence, source_id),
        )
        return context_id, "deterministic_matter"
    source_already_counted = (
        conn.execute(
            """SELECT 1 FROM triage_context_session_sources
                WHERE tenant_id=? AND owner_member_id=? AND thread_key=?
                  AND sequence=? AND source_event_id=?""",
            (tenant_id, owner, thread_key, int(latest["sequence"]), source_id),
        ).fetchone()
        is not None
    )
    if source_already_counted:
        return str(latest["context_id"]), "deterministic_matter"
    age_days = abs(
        (
            _parsed_time(proposed_at) - _parsed_time(str(latest["first_proposed_at"]))
        ).days
    )
    if int(latest["source_count"]) >= 100 or age_days > 30:
        sequence = int(latest["sequence"]) + 1
        context_id = "ctx_matter_" + stable_digest(
            tenant_id, owner, thread_key, sequence
        )
        conn.execute(
            """INSERT INTO triage_context_sessions (
                   tenant_id, owner_member_id, thread_key, sequence, context_id,
                   first_proposed_at, last_proposed_at, last_source_event_id,
                   source_count, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                tenant_id,
                owner,
                thread_key,
                sequence,
                context_id,
                proposed_at,
                proposed_at,
                source_id,
                envelope["event_id"],
            ),
        )
        conn.execute(
            """INSERT INTO triage_context_session_sources (
                   tenant_id, owner_member_id, thread_key, sequence, source_event_id
               ) VALUES (?, ?, ?, ?, ?)""",
            (tenant_id, owner, thread_key, sequence, source_id),
        )
        return context_id, "deterministic_matter"
    conn.execute(
        """UPDATE triage_context_sessions
              SET last_proposed_at=?, last_source_event_id=?,
                  source_count=source_count+1, last_event_id=?
            WHERE tenant_id=? AND owner_member_id=? AND thread_key=?
              AND sequence=? AND last_event_id < ?""",
        (
            proposed_at,
            source_id,
            envelope["event_id"],
            tenant_id,
            owner,
            thread_key,
            int(latest["sequence"]),
            envelope["event_id"],
        ),
    )
    conn.execute(
        """INSERT OR IGNORE INTO triage_context_session_sources (
               tenant_id, owner_member_id, thread_key, sequence, source_event_id
           ) VALUES (?, ?, ?, ?, ?)""",
        (tenant_id, owner, thread_key, int(latest["sequence"]), source_id),
    )
    return str(latest["context_id"]), "deterministic_matter"


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
    expected_scope = f"private:{owner_member_id}"
    if (
        envelope["owner_scope"] != expected_scope
        or envelope["visibility_scope"] != expected_scope
    ):
        return
    if (
        conn.execute(
            "SELECT 1 FROM triage_items WHERE tenant_id=? AND proposal_id=?",
            (envelope["tenant_id"], proposal_id),
        ).fetchone()
        is not None
    ):
        return
    proposal_payload = {
        "owner_member_id": owner_member_id,
        "target_kind": target_kind,
        "title": title,
        "candidate": candidate,
        "source_event_id": source_event_id,
        "reasons": reasons,
        "interpretation": interpretation,
    }
    context_id, context_kind = _proposal_context(envelope, conn, proposal_payload)
    title_fingerprint = exact_text_fingerprint(target_kind, title)
    object_match_fingerprint = exact_text_fingerprint(
        target_kind,
        (
            f"{candidate.get('kind', '')}|{candidate.get('rrule', '')}"
            if target_kind == "recurrence" and candidate.get("kind")
            else title
        ),
    )
    existing = conn.execute(
        """SELECT target_kind, object_id
             FROM canonical_object_fingerprints
            WHERE tenant_id=? AND owner_member_id=? AND target_kind=?
              AND title_fingerprint=? AND state='active'
            ORDER BY object_id LIMIT 1""",
        (
            envelope["tenant_id"],
            owner_member_id,
            target_kind,
            object_match_fingerprint,
        ),
    ).fetchone()
    duplicate = conn.execute(
        """SELECT proposal_id FROM triage_items
            WHERE tenant_id=? AND owner_member_id=? AND target_kind IS ?
              AND title_fingerprint=? AND context_id=?
              AND proposal_id IS NOT ?
              AND state IN ('pending_interpretation','pending_review','approved')
            ORDER BY proposed_at, proposal_id LIMIT 1""",
        (
            envelope["tenant_id"],
            owner_member_id,
            target_kind,
            title_fingerprint,
            context_id,
            parent_intent_id,
        ),
    ).fetchone()
    decision_disposition = (
        "attach_existing"
        if existing is not None
        else "evidence_only"
        if duplicate
        else "decision"
    )
    group_label = (
        str(interpretation.get("compact_summary") or title)
        if isinstance(interpretation, dict)
        else title
    )
    conn.execute(
        """
        INSERT INTO triage_items (
            proposal_id, tenant_id, source_kind, target_kind, owner_member_id,
            title, candidate_json, route_json, source_event_id, confidence,
            reasons_json, state, proposed_at, owner_scope, visibility_scope,
            parent_intent_id, candidate_key, extraction_event_id,
            eligibility_rule_ids_json,
            interpretation_json, context_id, context_kind, group_label,
            title_fingerprint, object_match_fingerprint, queue_lane,
            evidence_state, ownership_state,
            lifecycle_state, decision_disposition, duplicate_of_proposal_id,
            existing_object_kind, existing_object_id, policy_disposition,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            context_id,
            context_kind,
            group_label,
            title_fingerprint,
            object_match_fingerprint,
            queue_lane(proposal_payload),
            evidence_state(proposal_payload),
            ownership_state(proposal_payload),
            lifecycle_state(proposal_payload),
            decision_disposition,
            str(duplicate[0]) if duplicate is not None else None,
            str(existing[0]) if existing is not None else None,
            str(existing[1]) if existing is not None else None,
            "none",
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
        """SELECT *
             FROM triage_items WHERE tenant_id=? AND proposal_id=?""",
        (envelope["tenant_id"], payload["proposal_id"]),
    ).fetchone()
    if row is None:
        return
    current = dict(row)
    candidate = json.loads(current["candidate_json"])
    candidate.update(payload.get("candidate_updates") or {})
    final_title = str(payload.get("title") or current["title"])
    final_target = payload.get("target_kind") or current["target_kind"]
    interpretation = (
        json.loads(current["interpretation_json"])
        if current.get("interpretation_json")
        else None
    )
    proposal_payload = {
        "owner_member_id": current["owner_member_id"],
        "target_kind": final_target,
        "title": final_title,
        "candidate": candidate,
        "source_event_id": current["source_event_id"],
        "reasons": json.loads(current["reasons_json"] or "[]"),
        "interpretation": interpretation,
    }
    assignments = ["candidate_json=?"]
    values: list[Any] = [_json(candidate)]
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
    title_fingerprint = exact_text_fingerprint(final_target, final_title)
    object_match = exact_text_fingerprint(
        final_target,
        (
            f"{candidate.get('kind', '')}|{candidate.get('rrule', '')}"
            if final_target == "recurrence" and candidate.get("kind")
            else final_title
        ),
    )
    assignments.extend(
        (
            "title_fingerprint=?",
            "object_match_fingerprint=?",
            "group_label=?",
            "queue_lane=?",
            "evidence_state=?",
            "ownership_state=?",
            "lifecycle_state=?",
        )
    )
    values.extend(
        (
            title_fingerprint,
            object_match,
            (
                str(interpretation.get("compact_summary") or final_title)
                if isinstance(interpretation, dict)
                else final_title
            ),
            queue_lane(proposal_payload),
            evidence_state(proposal_payload),
            ownership_state(proposal_payload),
            lifecycle_state(proposal_payload),
        )
    )
    existing = conn.execute(
        """SELECT target_kind, object_id
             FROM canonical_object_fingerprints
            WHERE tenant_id=? AND owner_member_id=? AND target_kind IS ?
              AND title_fingerprint=? AND state='active'
            ORDER BY object_id LIMIT 1""",
        (
            envelope["tenant_id"],
            current["owner_member_id"],
            final_target,
            object_match,
        ),
    ).fetchone()
    duplicate = conn.execute(
        """SELECT proposal_id FROM triage_items
            WHERE tenant_id=? AND owner_member_id=? AND target_kind IS ?
              AND title_fingerprint=? AND context_id=?
              AND proposal_id != ?
              AND (? IS NULL OR proposal_id != ?)
              AND state IN ('pending_interpretation','pending_review','approved')
            ORDER BY proposed_at, proposal_id LIMIT 1""",
        (
            envelope["tenant_id"],
            current["owner_member_id"],
            final_target,
            title_fingerprint,
            current["context_id"],
            payload["proposal_id"],
            current.get("parent_intent_id"),
            current.get("parent_intent_id"),
        ),
    ).fetchone()
    disposition = (
        "attach_existing"
        if existing is not None
        else "evidence_only"
        if duplicate is not None
        else "decision"
    )
    assignments.extend(
        (
            "decision_disposition=?",
            "duplicate_of_proposal_id=?",
            "existing_object_kind=?",
            "existing_object_id=?",
            "last_event_id=?",
        )
    )
    values.extend(
        (
            disposition,
            str(duplicate[0]) if duplicate is not None else None,
            str(existing[0]) if existing is not None else None,
            str(existing[1]) if existing is not None else None,
            envelope["event_id"],
        )
    )
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
    allow_pending_interpretation: bool = False,
) -> None:
    conn.execute(
        """UPDATE triage_items
              SET state=?, resolved_at=?, resolved_by=?,
                  materialized_event_ids_json=?, resolution_reason=?, last_event_id=?
            WHERE tenant_id=? AND proposal_id=?
              AND (state='pending_review' OR (?=1 AND state='pending_interpretation'))
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
            int(allow_pending_interpretation),
            envelope["event_id"],
        ),
    )


_CANONICAL_EVENT_FIELDS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "task.created": (("task",), "task_id", "title"),
    "recurrence.added": (("recurrence",), "recurrence_id", "kind"),
    "calendar.event_added": (("calendar_event",), "external_event_id", "summary"),
    "list_item.added": (("reminder", "list_item"), "item_external_id", "title"),
    "list_item.updated": (("reminder", "list_item"), "item_external_id", "title"),
}


def _index_canonical(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    target_kinds, id_field, title_field = _CANONICAL_EVENT_FIELDS[envelope["type"]]
    payload = envelope["payload"]
    object_id = payload.get(id_field)
    title = payload.get(title_field)
    owner_member_id = payload.get("owner_member_id") or payload.get("member_id")
    if not owner_member_id and str(envelope.get("owner_scope") or "").startswith(
        "private:"
    ):
        owner_member_id = str(envelope["owner_scope"]).removeprefix("private:")
    if not object_id or not title or not owner_member_id:
        return
    for target_kind in target_kinds:
        fingerprint = exact_text_fingerprint(
            target_kind,
            (
                f"{payload.get('kind', '')}|{payload.get('rrule', '')}"
                if target_kind == "recurrence"
                else title
            ),
        )
        conn.execute(
            """INSERT INTO canonical_object_fingerprints (
                   tenant_id, owner_member_id, target_kind, object_id,
                   title_fingerprint, state, owner_scope, visibility_scope,
                   sensitivity, last_event_id
               ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
               ON CONFLICT(tenant_id, target_kind, object_id) DO UPDATE SET
                   title_fingerprint=excluded.title_fingerprint,
                   state='active', last_event_id=excluded.last_event_id
               WHERE canonical_object_fingerprints.last_event_id < excluded.last_event_id""",
            (
                envelope["tenant_id"],
                owner_member_id,
                target_kind,
                object_id,
                fingerprint,
                envelope["owner_scope"],
                envelope["visibility_scope"],
                envelope["sensitivity"],
                envelope["event_id"],
            ),
        )
        conn.execute(
            """UPDATE triage_items
                  SET decision_disposition='attach_existing',
                      existing_object_kind=?, existing_object_id=?, last_event_id=?
                WHERE tenant_id=? AND owner_member_id=? AND target_kind=?
                  AND object_match_fingerprint=?
                  AND state IN ('pending_interpretation','pending_review')
                  AND decision_disposition='decision' AND last_event_id < ?""",
            (
                target_kind,
                object_id,
                envelope["event_id"],
                envelope["tenant_id"],
                owner_member_id,
                target_kind,
                fingerprint,
                envelope["event_id"],
            ),
        )


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    event_type = envelope["type"]
    payload = envelope["payload"]
    if event_type in _CANONICAL_EVENT_FIELDS:
        _index_canonical(envelope, conn)
    elif event_type == "triage.item.proposed":
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
            allow_pending_interpretation=True,
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
            allow_pending_interpretation=True,
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
