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
    policy_scope_digest,
)
from adminme.triage_review import proposal_source_id, triage_review_state
from adminme.triage_management import human_triage_lane


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


_LEARNED_INSTRUCTION_ORDER = {"always": 0, "usually": 1, "ask": 2, "never": 3}


def _learned_scope_digest(row: dict[str, Any], scope_kind: str) -> str | None:
    candidate = json.loads(row.get("candidate_json") or "{}")
    reconciliation_scope = json.loads(row.get("reconciliation_scope_json") or "{}")
    values = {
        "candidate_kind": row.get("target_kind"),
        "context": row.get("context_id"),
        "account": reconciliation_scope.get("provider_account_id"),
        "workspace": reconciliation_scope.get("workspace"),
        "sender": candidate.get("sender_digest"),
    }
    value = values.get(scope_kind)
    if value in {None, ""}:
        return None
    return policy_scope_digest(scope_kind, str(value))


def _matching_learned_rule(
    conn: sqlcipher3.Connection, row: dict[str, Any]
) -> dict[str, Any] | None:
    priority_basis = set(json.loads(row.get("priority_basis_json") or "[]"))
    if (
        priority_basis.intersection(
            {"explicit_member_instruction", "exact_member_commitment"}
        )
        or row.get("admission_decision") == "alert_only"
    ):
        return None
    rules = conn.execute(
        """SELECT * FROM triage_learned_policy_rules
             WHERE tenant_id=? AND member_id=? AND status IN ('active','trial')
             ORDER BY rule_id""",
        (row["tenant_id"], row["owner_member_id"]),
    ).fetchall()
    matching: list[dict[str, Any]] = []
    for rule_value in rules:
        rule = dict(rule_value)
        if rule["status"] == "trial" and row["proposal_id"] not in set(
            json.loads(rule["trial_proposal_ids_json"] or "[]")
        ):
            continue
        digest = _learned_scope_digest(row, str(rule["scope_kind"]))
        if digest == rule["scope_value_digest"]:
            matching.append(rule)
    if not matching:
        return None
    return max(
        matching,
        key=lambda value: (
            _LEARNED_INSTRUCTION_ORDER[str(value["instruction"])],
            str(value["rule_id"]),
        ),
    )


def _apply_learned_policy_to_row(
    conn: sqlcipher3.Connection, row: dict[str, Any]
) -> None:
    rule = _matching_learned_rule(conn, row)
    instruction = str(rule["instruction"]) if rule is not None else "always"
    policy_state = {
        "always": "none",
        "usually": "low_confidence_review",
        "ask": "clarification_required",
        "never": "context_only",
    }[instruction]
    lane = (
        "low_confidence_review"
        if instruction == "usually"
        else "clarification_conflict"
        if instruction == "ask"
        else human_triage_lane(row)
    )
    conn.execute(
        """UPDATE triage_items
              SET learned_policy_state=?, learned_rule_id=?, human_lane=?
            WHERE tenant_id=? AND proposal_id=?""",
        (
            policy_state,
            rule["rule_id"] if rule is not None else None,
            lane,
            row["tenant_id"],
            row["proposal_id"],
        ),
    )


def _recompute_learned_policy_for_proposal(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    proposal_id: str,
) -> None:
    row = conn.execute(
        "SELECT * FROM triage_items WHERE tenant_id=? AND proposal_id=?",
        (tenant_id, proposal_id),
    ).fetchone()
    if row is not None:
        _apply_learned_policy_to_row(conn, dict(row))


def _recompute_learned_policy_for_member(
    conn: sqlcipher3.Connection,
    *,
    tenant_id: str,
    member_id: str,
) -> None:
    rows = conn.execute(
        """SELECT * FROM triage_items
             WHERE tenant_id=? AND owner_member_id=?
               AND owner_scope=? AND visibility_scope=?
             ORDER BY proposal_id""",
        (tenant_id, member_id, f"private:{member_id}", f"private:{member_id}"),
    ).fetchall()
    for row in rows:
        _apply_learned_policy_to_row(conn, dict(row))


def _record_learned_policy(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    expected_scope = f"private:{payload['member_id']}"
    if (
        envelope.get("owner_scope") != expected_scope
        or envelope.get("visibility_scope") != expected_scope
    ):
        return
    if envelope["type"] == "triage.source_policy_rule.decided":
        if payload["decision"] not in {"approve", "trial"}:
            return
        conn.execute(
            """INSERT INTO triage_learned_policy_rules (
                   tenant_id, rule_id, policy_proposal_id, member_id,
                   scope_kind, scope_value_digest, outcome_kind, instruction,
                   activation_mode, trial_proposal_ids_json, status, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id, rule_id) DO NOTHING""",
            (
                envelope["tenant_id"],
                payload["rule_id"],
                payload["policy_proposal_id"],
                payload["member_id"],
                payload["scope_kind"],
                payload["scope_value_digest"],
                payload["outcome_kind"],
                payload["instruction"],
                "approved" if payload["decision"] == "approve" else "trial",
                _json(payload.get("trial_proposal_ids") or []),
                "active" if payload["decision"] == "approve" else "trial",
                envelope["event_id"],
            ),
        )
    else:
        rule = conn.execute(
            """SELECT * FROM triage_learned_policy_rules
                 WHERE tenant_id=? AND rule_id=? AND member_id=?""",
            (envelope["tenant_id"], payload["rule_id"], payload["member_id"]),
        ).fetchone()
        if rule is None or rule["instruction"] != payload["previous_instruction"]:
            return
        status = {
            "retain": rule["status"],
            "narrow": rule["status"],
            "suspend": "suspended",
            "revoke": "revoked",
        }[payload["decision"]]
        conn.execute(
            """UPDATE triage_learned_policy_rules
                  SET instruction=?, status=?, last_event_id=?
                WHERE tenant_id=? AND rule_id=? AND member_id=?
                  AND last_event_id < ?""",
            (
                payload.get("replacement_instruction") or rule["instruction"],
                status,
                envelope["event_id"],
                envelope["tenant_id"],
                payload["rule_id"],
                payload["member_id"],
                envelope["event_id"],
            ),
        )
    _recompute_learned_policy_for_member(
        conn,
        tenant_id=str(envelope["tenant_id"]),
        member_id=str(payload["member_id"]),
    )


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
    eligibility = eligibility_rule_ids or []
    source_coordinate = proposal_source_id(
        source_kind=source_kind,
        candidate=candidate,
        eligibility_rule_ids=eligibility,
    )
    review = triage_review_state(
        {"title": title, "target_kind": target_kind, "state": state},
        candidate=candidate,
        route=route,
    )
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
            title, candidate_json, route_json, source_event_id, source_id, confidence,
            reasons_json, state, proposed_at, owner_scope, visibility_scope,
            parent_intent_id, candidate_key, extraction_event_id,
            eligibility_rule_ids_json,
            interpretation_json, context_id, context_kind, group_label,
            title_fingerprint, object_match_fingerprint, queue_lane,
            evidence_state, ownership_state,
            lifecycle_state, review_state, decision_disposition, duplicate_of_proposal_id,
            existing_object_kind, existing_object_id, policy_disposition,
            sensitivity, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            source_coordinate,
            confidence,
            _json(reasons),
            state,
            envelope["occurred_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            parent_intent_id,
            candidate_key,
            extraction_event_id,
            _json(eligibility),
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
            review["state"],
            decision_disposition,
            str(duplicate[0]) if duplicate is not None else None,
            str(existing[0]) if existing is not None else None,
            str(existing[1]) if existing is not None else None,
            "none",
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    conn.execute(
        """UPDATE triage_items SET human_lane=?
             WHERE tenant_id=? AND proposal_id=?""",
        (
            human_triage_lane(proposal_payload),
            envelope["tenant_id"],
            proposal_id,
        ),
    )
    _recompute_learned_policy_for_proposal(
        conn,
        tenant_id=str(envelope["tenant_id"]),
        proposal_id=proposal_id,
    )


def _apply_triage_proposed(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    # Replay first appends a revision-bound supersession and then the current
    # replacement. If member action won the append race, compare-and-apply
    # leaves the predecessor at its member-owned revision. Do not project a
    # second item beside an enrichment or reopen a terminal decision. A
    # replacement is admissible only when this event order proves the old row
    # was actually retired by the matching eligibility-replacement path.
    idempotency_key = str(envelope.get("idempotency_key") or "")
    candidate_key = payload.get("candidate_key")
    if idempotency_key.startswith("triage:message-candidate:v") and candidate_key:
        authoritative_predecessor = conn.execute(
            """SELECT 1 FROM triage_items
                WHERE tenant_id=? AND owner_member_id=?
                  AND source_kind='triage_item' AND source_event_id=?
                  AND target_kind=? AND candidate_key=? AND proposal_id!=?
                  AND NOT (
                      state='dismissed'
                      AND resolved_by='system:source_changed'
                      AND resolution_reason='eligibility_rule_replaced'
                  )
                LIMIT 1""",
            (
                envelope["tenant_id"],
                payload["owner_member_id"],
                payload["source_event_id"],
                payload["target_kind"],
                candidate_key,
                payload["proposal_id"],
            ),
        ).fetchone()
        if authoritative_predecessor is not None:
            return
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
    final_route = (
        payload["route"]
        if payload.get("route") is not None
        else json.loads(current["route_json"])
        if current.get("route_json")
        else None
    )
    review = triage_review_state(
        {
            "title": final_title,
            "target_kind": final_target,
            "state": (
                "pending_review"
                if payload.get("target_kind") is not None
                else current["state"]
            ),
        },
        candidate=candidate,
        route=final_route,
    )
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
            "review_state=?",
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
            review["state"],
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
    expected_last_event_id: str | None = None,
) -> None:
    conn.execute(
        """UPDATE triage_items
              SET state=?, resolved_at=?, resolved_by=?,
                  materialized_event_ids_json=?, resolution_reason=?, last_event_id=?
            WHERE tenant_id=? AND proposal_id=?
              AND (state='pending_review' OR (?=1 AND state='pending_interpretation'))
              AND (? IS NULL OR last_event_id=?)
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
            expected_last_event_id,
            expected_last_event_id,
            envelope["event_id"],
        ),
    )


def _apply_reconciliation(
    envelope: dict[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    expected_scope = f"private:{payload['owner_member_id']}"
    if (
        envelope.get("owner_scope") != expected_scope
        or envelope.get("visibility_scope") != expected_scope
    ):
        return
    # Phase A receipts remain queryable through the immutable log and Context
    # Fabric, but must not mutate the live Triage backlog until activation is
    # separately authorized.
    if not payload["admission_active"]:
        return
    relationships = list(payload.get("relationships") or [])
    conn.execute(
        """UPDATE triage_items
              SET admission_decision=?, reconciliation_active=?,
                  reconciliation_id=?, reconciliation_event_id=?,
                  reconciliation_revision=?, reconciliation_reasons_json=?,
                  reconciliation_evidence_json=?,
                  reconciliation_authored_text_digest=?,
                  reconciliation_policy_json=?,
                  reconciliation_scope_json=?, reconciliation_freshness=?,
                  priority_rank=?, priority_basis_json=?
            WHERE tenant_id=? AND proposal_id=?
              AND owner_member_id=? AND owner_scope=? AND visibility_scope=?
              AND reconciliation_revision < ?""",
        (
            payload["decision"],
            int(bool(payload["admission_active"])),
            payload["reconciliation_id"],
            envelope["event_id"],
            payload["decision_revision"],
            _json(payload["reason_codes"]),
            _json(relationships),
            payload.get("member_authored_text_digest"),
            _json(payload["policy"]),
            _json(payload["source_coordinate"]),
            payload["freshness"],
            payload["priority_rank"],
            _json(payload["priority_basis"]),
            envelope["tenant_id"],
            payload["proposal_id"],
            payload["owner_member_id"],
            expected_scope,
            expected_scope,
            payload["decision_revision"],
        ),
    )
    decision = str(payload["decision"])
    review_decisions = {"admit", "conflict_review", "unresolved"}
    terminal_decisions = {
        "update_existing_matter",
        "attach_evidence",
        "alert_only",
        "context_only",
        "already_satisfied",
        "superseded",
        "duplicate",
        "source_policy_suppression",
    }
    disposition = "decision" if decision in review_decisions else "evidence_only"
    queue_lane = (
        "member_priority"
        if "explicit_member_instruction" in payload["priority_basis"]
        else "member_promise"
        if "exact_member_commitment" in payload["priority_basis"]
        else "time_bound"
        if "verified_deadline" in payload["priority_basis"]
        else "known_human_request"
        if "known_human_actionable_request" in payload["priority_basis"]
        else "standard"
    )
    if decision in terminal_decisions:
        conn.execute(
            """UPDATE triage_items
                  SET state='dismissed', resolved_at=?,
                      resolved_by='system:cross-source-reconciliation',
                      resolution_reason=?, decision_disposition=?,
                      duplicate_of_proposal_id=?, policy_disposition=?,
                      context_id=?, context_kind='deterministic_matter',
                      queue_lane=?, last_event_id=?
                WHERE tenant_id=? AND proposal_id=?
                  AND state IN ('pending_interpretation','pending_review')
                  AND reconciliation_revision=? AND last_event_id < ?""",
            (
                payload["decided_at"],
                f"reconciliation:{decision}",
                disposition,
                payload.get("primary_proposal_id"),
                decision,
                payload["matter_id"],
                queue_lane,
                envelope["event_id"],
                envelope["tenant_id"],
                payload["proposal_id"],
                payload["decision_revision"],
                envelope["event_id"],
            ),
        )
    else:
        conn.execute(
            """UPDATE triage_items
                  SET decision_disposition='decision',
                      duplicate_of_proposal_id=?, policy_disposition=?,
                      context_id=?, context_kind='deterministic_matter',
                      queue_lane=?, last_event_id=?
                WHERE tenant_id=? AND proposal_id=?
                  AND state IN ('pending_interpretation','pending_review')
                  AND reconciliation_revision=? AND last_event_id < ?""",
            (
                payload.get("primary_proposal_id"),
                decision,
                payload["matter_id"],
                queue_lane,
                envelope["event_id"],
                envelope["tenant_id"],
                payload["proposal_id"],
                payload["decision_revision"],
                envelope["event_id"],
            ),
        )

    current = conn.execute(
        "SELECT * FROM triage_items WHERE tenant_id=? AND proposal_id=?",
        (envelope["tenant_id"], payload["proposal_id"]),
    ).fetchone()
    if current is not None:
        conn.execute(
            """UPDATE triage_items SET human_lane=?
                 WHERE tenant_id=? AND proposal_id=?""",
            (
                human_triage_lane(
                    dict(current),
                    {
                        "decision": payload["decision"],
                        "priority_basis": payload["priority_basis"],
                    },
                ),
                envelope["tenant_id"],
                payload["proposal_id"],
            ),
        )
        _recompute_learned_policy_for_proposal(
            conn,
            tenant_id=str(envelope["tenant_id"]),
            proposal_id=str(payload["proposal_id"]),
        )


_MANAGEMENT_STATES = {
    "link_existing": "linked_existing",
    "mark_handled": "handled",
    "mark_duplicate": "duplicate",
    "keep_context": "context",
    "route_alert": "alert",
    "dismiss_not_useful": "dismissed_not_useful",
    "propose_suppression": "suppression_proposed",
    "ask_clarification": "clarification",
    "snooze": "snoozed",
    "reopen": "open",
    "correct_extraction": "open",
    "correct_ownership": "ownership_corrected",
}


def _apply_managed(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    """Project one exact, member-private, provider-effect-free management action."""

    payload = envelope["payload"]
    expected_scope = f"private:{payload['member_id']}"
    if (
        envelope.get("owner_scope") != expected_scope
        or envelope.get("visibility_scope") != expected_scope
    ):
        return
    current = conn.execute(
        """SELECT * FROM triage_items
             WHERE tenant_id=? AND proposal_id=? AND owner_member_id=?
               AND owner_scope=? AND visibility_scope=?""",
        (
            envelope["tenant_id"],
            payload["proposal_id"],
            payload["member_id"],
            expected_scope,
            expected_scope,
        ),
    ).fetchone()
    if current is None:
        return
    row = dict(current)
    if row["last_event_id"] != payload["expected_last_event_id"]:
        return
    if int(row.get("management_revision") or 0) + 1 != payload["action_revision"]:
        return
    if payload["action"] == "reopen" and row["state"] in {
        "approved",
        "confirmed",
        "failed",
    }:
        return
    if payload.get("supersedes_action_id") != row.get("management_action_id"):
        if row.get("management_action_id") is not None:
            return
    conn.execute(
        """INSERT INTO triage_management_actions (
               tenant_id, action_id, proposal_id, member_id, action,
               action_revision, supersedes_action_id, reason_codes_json,
               evidence_event_ids_json, linked_object_kind, linked_object_id,
               duplicate_of_proposal_id, snoozed_until, policy_proposal_id,
               reversible, provider_effects, managed_at, owner_scope,
               visibility_scope, sensitivity, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?)""",
        (
            envelope["tenant_id"],
            payload["action_id"],
            payload["proposal_id"],
            payload["member_id"],
            payload["action"],
            payload["action_revision"],
            payload.get("supersedes_action_id"),
            _json(payload.get("reason_codes") or []),
            _json(payload.get("evidence_event_ids") or []),
            payload.get("linked_object_kind"),
            payload.get("linked_object_id"),
            payload.get("duplicate_of_proposal_id"),
            payload.get("snoozed_until"),
            payload.get("policy_proposal_id"),
            payload["managed_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    title = payload.get("corrected_title") or row["title"]
    target_kind = payload.get("corrected_target_kind") or row["target_kind"]
    state = _MANAGEMENT_STATES[payload["action"]]
    if payload["action"] == "reopen":
        existing_object_kind = None
        existing_object_id = None
        duplicate_of_proposal_id = None
    else:
        existing_object_kind = payload.get("linked_object_kind") or row.get(
            "existing_object_kind"
        )
        existing_object_id = payload.get("linked_object_id") or row.get(
            "existing_object_id"
        )
        duplicate_of_proposal_id = payload.get("duplicate_of_proposal_id") or row.get(
            "duplicate_of_proposal_id"
        )
    conn.execute(
        """UPDATE triage_items
              SET title=?, target_kind=?, management_state=?,
                  management_action=?, management_action_id=?,
                  management_revision=?, management_event_id=?,
                  management_reason_codes_json=?, snoozed_until=?,
                  existing_object_kind=?, existing_object_id=?,
                  duplicate_of_proposal_id=?,
                  last_event_id=?
            WHERE tenant_id=? AND proposal_id=? AND last_event_id=?""",
        (
            title,
            target_kind,
            state,
            payload["action"],
            payload["action_id"],
            payload["action_revision"],
            envelope["event_id"],
            _json(payload.get("reason_codes") or []),
            payload.get("snoozed_until"),
            existing_object_kind,
            existing_object_id,
            duplicate_of_proposal_id,
            envelope["event_id"],
            envelope["tenant_id"],
            payload["proposal_id"],
            payload["expected_last_event_id"],
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
    elif event_type == "triage.item.managed":
        _apply_managed(envelope, conn)
    elif event_type in {
        "triage.source_policy_rule.decided",
        "triage.source_policy_rule.reviewed",
    }:
        _record_learned_policy(envelope, conn)
    elif event_type == "triage.reconciliation.decided":
        _apply_reconciliation(envelope, conn)
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
                      AND state='pending_review'
                      AND decision_disposition='decision'
                      AND last_event_id < ?""",
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
        confirmation_prefix = (
            f"triage:triage.item.confirmed:{payload['confirmed_by_member_id']}:"
        )
        idempotency_key = str(envelope.get("idempotency_key") or "")
        if idempotency_key.startswith(confirmation_prefix):
            conn.execute(
                """UPDATE triage_items
                      SET confirmation_event_id=?, confirmation_request_id=?
                    WHERE tenant_id=? AND proposal_id=?
                      AND domain_approval_event_id=?""",
                (
                    envelope["event_id"],
                    idempotency_key.removeprefix(confirmation_prefix),
                    envelope["tenant_id"],
                    payload["proposal_id"],
                    envelope["event_id"],
                ),
            )
    elif event_type == "member.provider_action.confirmed":
        proposal_id = payload.get("origin_proposal_id")
        if proposal_id:
            conn.execute(
                """UPDATE triage_items
                      SET resolution_reason='approved_awaiting_openclaw',
                          materialization_event_id=?, last_event_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='approved'
                      AND domain_approval_event_id=? AND last_event_id < ?""",
                (
                    envelope["event_id"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    proposal_id,
                    envelope.get("causation_id"),
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
        confirmation_prefix = (
            f"command:commitment.confirmed:{payload['commitment_id']}:"
        )
        idempotency_key = str(envelope.get("idempotency_key") or "")
        if idempotency_key.startswith(confirmation_prefix):
            conn.execute(
                """UPDATE triage_items
                      SET confirmation_event_id=?, confirmation_request_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='confirmed'
                      AND last_event_id=?""",
                (
                    envelope["event_id"],
                    idempotency_key.removeprefix(confirmation_prefix),
                    envelope["tenant_id"],
                    payload["commitment_id"],
                    envelope["event_id"],
                ),
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
        request_prefix = (
            f"triage:triage.item.dismissed:{payload['dismissed_by_member_id']}:"
        )
        idempotency_key = str(envelope.get("idempotency_key") or "")
        if idempotency_key.startswith(request_prefix):
            conn.execute(
                """UPDATE triage_items
                      SET dismissal_event_id=?, dismissal_request_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='dismissed'
                      AND last_event_id=?""",
                (
                    envelope["event_id"],
                    idempotency_key.removeprefix(request_prefix),
                    envelope["tenant_id"],
                    payload["proposal_id"],
                    envelope["event_id"],
                ),
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
            expected_last_event_id=(
                str(payload["expected_last_event_id"])
                if int(envelope.get("schema_version") or 1) >= 3
                else None
            ),
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
        dismissal_prefix = f"command:commitment.dismissed:{payload['commitment_id']}:"
        idempotency_key = str(envelope.get("idempotency_key") or "")
        if idempotency_key.startswith(dismissal_prefix):
            conn.execute(
                """UPDATE triage_items
                      SET dismissal_event_id=?, dismissal_request_id=?
                    WHERE tenant_id=? AND proposal_id=? AND state='dismissed'
                      AND last_event_id=?""",
                (
                    envelope["event_id"],
                    idempotency_key.removeprefix(dismissal_prefix),
                    envelope["tenant_id"],
                    payload["commitment_id"],
                    envelope["event_id"],
                ),
            )
