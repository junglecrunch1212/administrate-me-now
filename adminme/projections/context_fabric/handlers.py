"""Pure deterministic handlers for the rebuildable Context Fabric."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import sqlcipher3

from adminme.context_fabric import (
    CONTEXT_ALGORITHM,
    owner_member_id,
    provider_conversation_context_id,
    provider_coordinates,
    stable_digest,
    triage_context_id,
)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _event_node_id(event_id: str) -> str:
    return f"event:{event_id}"


def _is_private_member_envelope(envelope: Mapping[str, Any], member_id: object) -> bool:
    member = str(member_id or "")
    expected = f"private:{member}"
    return (
        bool(member)
        and envelope.get("owner_scope") == expected
        and envelope.get("visibility_scope") == expected
    )


def _parsed_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _matter_context_id(
    envelope: Mapping[str, Any],
    conn: sqlcipher3.Connection,
    payload: Mapping[str, Any],
) -> tuple[str, str]:
    interpretation = payload.get("interpretation")
    thread_key = (
        str(interpretation.get("thread_key") or "")
        if isinstance(interpretation, Mapping)
        else ""
    )
    if not thread_key:
        return triage_context_id(envelope, payload)
    tenant = str(envelope["tenant_id"])
    owner = str(payload["owner_member_id"])
    source_id = str(payload["source_event_id"])
    proposed_at = str(payload.get("source_occurred_at") or envelope["occurred_at"])
    latest = conn.execute(
        """SELECT * FROM context_matter_sessions
            WHERE tenant_id=? AND owner_member_id=? AND thread_key=?
            ORDER BY sequence DESC LIMIT 1""",
        (tenant, owner, thread_key),
    ).fetchone()
    if latest is not None:
        source_already_counted = (
            conn.execute(
                """SELECT 1 FROM context_matter_session_sources
                    WHERE tenant_id=? AND owner_member_id=? AND thread_key=?
                      AND sequence=? AND source_event_id=?""",
                (tenant, owner, thread_key, int(latest["sequence"]), source_id),
            ).fetchone()
            is not None
        )
        if source_already_counted:
            return str(latest["context_id"]), "deterministic_matter"
    new_session = latest is None
    if latest is not None:
        age_days = abs(
            (
                _parsed_time(proposed_at)
                - _parsed_time(str(latest["first_proposed_at"]))
            ).days
        )
        new_session = int(latest["source_count"]) >= 100 or age_days > 30
    if new_session:
        sequence = 0 if latest is None else int(latest["sequence"]) + 1
        context_id = "ctx_matter_" + stable_digest(tenant, owner, thread_key, sequence)
        conn.execute(
            """INSERT INTO context_matter_sessions (
                   tenant_id, owner_member_id, thread_key, sequence, context_id,
                   first_proposed_at, last_proposed_at, last_source_event_id,
                   source_count, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                tenant,
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
            """INSERT INTO context_matter_session_sources (
                   tenant_id, owner_member_id, thread_key, sequence, source_event_id
               ) VALUES (?, ?, ?, ?, ?)""",
            (tenant, owner, thread_key, sequence, source_id),
        )
        return context_id, "deterministic_matter"
    conn.execute(
        """UPDATE context_matter_sessions
              SET last_proposed_at=?, last_source_event_id=?,
                  source_count=source_count+1, last_event_id=?
            WHERE tenant_id=? AND owner_member_id=? AND thread_key=?
              AND sequence=? AND last_event_id < ?""",
        (
            proposed_at,
            source_id,
            envelope["event_id"],
            tenant,
            owner,
            thread_key,
            int(latest["sequence"]),
            envelope["event_id"],
        ),
    )
    conn.execute(
        """INSERT OR IGNORE INTO context_matter_session_sources (
               tenant_id, owner_member_id, thread_key, sequence, source_event_id
           ) VALUES (?, ?, ?, ?, ?)""",
        (tenant, owner, thread_key, int(latest["sequence"]), source_id),
    )
    return str(latest["context_id"]), "deterministic_matter"


def _put_node(
    envelope: Mapping[str, Any],
    conn: sqlcipher3.Connection,
    *,
    node_id: str,
    node_kind: str,
    authority: str,
    provider: str | None = None,
    provider_account_id: str | None = None,
    workspace: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO context_nodes (
            tenant_id, node_id, node_kind, owner_member_id, owner_scope,
            visibility_scope, sensitivity, provider, provider_account_id,
            workspace, authority, created_at, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, node_id) DO UPDATE SET
            node_kind=excluded.node_kind,
            authority=CASE
              WHEN context_nodes.authority='member_confirmed' THEN context_nodes.authority
              ELSE excluded.authority END,
            provider=COALESCE(excluded.provider, context_nodes.provider),
            provider_account_id=COALESCE(
                excluded.provider_account_id, context_nodes.provider_account_id
            ),
            workspace=COALESCE(excluded.workspace, context_nodes.workspace),
            last_event_id=excluded.last_event_id
        WHERE context_nodes.last_event_id <= excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            node_id,
            node_kind,
            owner_member_id(envelope),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            provider,
            provider_account_id,
            workspace,
            authority,
            envelope["occurred_at"],
            envelope["event_id"],
        ),
    )


def _put_context(
    envelope: Mapping[str, Any],
    conn: sqlcipher3.Connection,
    *,
    context_id: str,
    level: str,
    label: str | None = None,
    member_confirmed: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO contexts (
            tenant_id, context_id, context_level, label, member_confirmed,
            owner_member_id, owner_scope, visibility_scope, sensitivity,
            created_at, last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, context_id) DO UPDATE SET
            context_level=CASE WHEN excluded.member_confirmed=1
                THEN 'member_confirmed' ELSE contexts.context_level END,
            label=COALESCE(excluded.label, contexts.label),
            member_confirmed=MAX(contexts.member_confirmed, excluded.member_confirmed),
            last_event_id=excluded.last_event_id
        WHERE contexts.last_event_id < excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            context_id,
            level,
            label,
            int(member_confirmed),
            owner_member_id(envelope),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["occurred_at"],
            envelope["event_id"],
        ),
    )
    _put_node(
        envelope,
        conn,
        node_id=context_id,
        node_kind="context",
        authority="member_confirmed" if member_confirmed else "deterministic_derived",
    )


def _put_edge(
    envelope: Mapping[str, Any],
    conn: sqlcipher3.Connection,
    *,
    from_node_id: str,
    to_node_id: str,
    edge_type: str,
    authority: str,
    reason_codes: list[str],
    source_event_ids: list[str] | None = None,
    valid_to: str | None = None,
    status: str = "active",
    revoked_by_event_id: str | None = None,
) -> str:
    edge_id = "edge_" + stable_digest(
        envelope["tenant_id"], from_node_id, to_node_id, edge_type
    )
    conn.execute(
        """
        INSERT INTO context_edges (
            tenant_id, edge_id, from_node_id, to_node_id, edge_type,
            authority, reason_codes_json, source_event_ids_json, algorithm_id,
            schema_version, valid_from, valid_to, status, revoked_by_event_id,
            owner_member_id, owner_scope, visibility_scope, sensitivity,
            last_event_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tenant_id, edge_id) DO UPDATE SET
            authority=excluded.authority,
            reason_codes_json=excluded.reason_codes_json,
            source_event_ids_json=excluded.source_event_ids_json,
            valid_to=excluded.valid_to,
            status=excluded.status,
            revoked_by_event_id=excluded.revoked_by_event_id,
            last_event_id=excluded.last_event_id
        WHERE context_edges.last_event_id < excluded.last_event_id
        """,
        (
            envelope["tenant_id"],
            edge_id,
            from_node_id,
            to_node_id,
            edge_type,
            authority,
            _json(reason_codes),
            _json(source_event_ids or [str(envelope["event_id"])]),
            CONTEXT_ALGORITHM,
            envelope["occurred_at"],
            valid_to,
            status,
            revoked_by_event_id,
            owner_member_id(envelope),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    return edge_id


def _record_lineage(envelope: Mapping[str, Any], conn: sqlcipher3.Connection) -> None:
    current = _event_node_id(str(envelope["event_id"]))
    for field, edge_type in (
        ("causation_id", "caused_by"),
        ("correlation_id", "correlated_with"),
    ):
        related = envelope.get(field)
        if not related:
            continue
        target = _event_node_id(str(related))
        _put_node(
            envelope,
            conn,
            node_id=target,
            node_kind="source_event_reference",
            authority="deterministic_derived",
        )
        _put_edge(
            envelope,
            conn,
            from_node_id=current,
            to_node_id=target,
            edge_type=edge_type,
            authority="deterministic_derived",
            reason_codes=[f"envelope_{field}"],
        )


def _record_message(envelope: Mapping[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    member, provider, account, workspace = provider_coordinates(envelope, payload)
    if not _is_private_member_envelope(envelope, member):
        return
    context_id = provider_conversation_context_id(envelope, payload)
    if context_id is None:
        return
    _put_context(
        envelope,
        conn,
        context_id=context_id,
        level="provider_conversation",
    )
    _put_edge(
        envelope,
        conn,
        from_node_id=_event_node_id(str(envelope["event_id"])),
        to_node_id=context_id,
        edge_type="member_of_context",
        authority="provider_exact",
        reason_codes=["provider_thread_id", "scope_exact"],
    )
    # Coordinates are retained on the source node, not exposed as graph labels.
    _put_node(
        envelope,
        conn,
        node_id=_event_node_id(str(envelope["event_id"])),
        node_kind="source_event",
        authority="source_fact",
        provider=provider,
        provider_account_id=account,
        workspace=workspace,
    )


def _record_telephony(envelope: Mapping[str, Any], conn: sqlcipher3.Connection) -> None:
    """Index one exact SMS address pair without storing message text or numbers."""

    payload = envelope["payload"]
    member = owner_member_id(envelope)
    if not _is_private_member_envelope(envelope, member):
        return
    from_number = str(payload.get("from_number") or "").strip()
    to_number = str(payload.get("to_number") or "").strip()
    account = str(envelope.get("source_account_id") or "")
    if not from_number or not to_number or not account:
        return
    participant_pair_digest = stable_digest(
        *sorted((from_number, to_number)), length=64
    )
    context_id = "ctx_provider_" + stable_digest(
        envelope["tenant_id"],
        member,
        "twilio_sms",
        account,
        "personal",
        participant_pair_digest,
    )
    _put_context(
        envelope,
        conn,
        context_id=context_id,
        level="provider_conversation",
    )
    _put_edge(
        envelope,
        conn,
        from_node_id=_event_node_id(str(envelope["event_id"])),
        to_node_id=context_id,
        edge_type="member_of_context",
        authority="provider_exact",
        reason_codes=["telephony_party_pair", "scope_exact"],
    )
    _put_node(
        envelope,
        conn,
        node_id=_event_node_id(str(envelope["event_id"])),
        node_kind="source_event",
        authority="source_fact",
        provider="twilio_sms",
        provider_account_id=account,
        workspace="personal",
    )


def _record_triage_proposal(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("owner_member_id")):
        return
    proposal_node = f"triage:{payload['proposal_id']}"
    if (
        conn.execute(
            "SELECT 1 FROM context_nodes WHERE tenant_id=? AND node_id=?",
            (envelope["tenant_id"], proposal_node),
        ).fetchone()
        is not None
    ):
        return
    context_id, level = _matter_context_id(envelope, conn, payload)
    source_node = _event_node_id(str(payload["source_event_id"]))
    interpretation = payload.get("interpretation")
    # A model-authored compact summary remains on the Triage proposal where
    # its provenance is explicit.  It is never promoted to a graph label.
    _put_context(envelope, conn, context_id=context_id, level=level)
    _put_node(
        envelope,
        conn,
        node_id=proposal_node,
        node_kind="triage_candidate",
        authority="deterministic_derived",
    )
    _put_node(
        envelope,
        conn,
        node_id=source_node,
        node_kind="source_event_reference",
        authority="deterministic_derived",
    )
    _put_edge(
        envelope,
        conn,
        from_node_id=proposal_node,
        to_node_id=source_node,
        edge_type="extracted_from",
        authority="deterministic_derived",
        reason_codes=["explicit_source_event_id"],
        source_event_ids=[str(payload["source_event_id"]), str(envelope["event_id"])],
    )
    _put_edge(
        envelope,
        conn,
        from_node_id=proposal_node,
        to_node_id=context_id,
        edge_type="member_of_context",
        authority=(
            "provider_exact"
            if level == "provider_conversation"
            else "deterministic_derived"
        ),
        reason_codes=[
            "code_owned_thread_key"
            if level == "provider_conversation"
            else "isolated_source"
        ],
        source_event_ids=[str(payload["source_event_id"]), str(envelope["event_id"])],
    )
    if isinstance(interpretation, Mapping):
        for related_id in interpretation.get("related_source_event_ids") or []:
            related_node = _event_node_id(str(related_id))
            _put_node(
                envelope,
                conn,
                node_id=related_node,
                node_kind="source_event_reference",
                authority="llm_proposed",
            )
            _put_edge(
                envelope,
                conn,
                from_node_id=proposal_node,
                to_node_id=related_node,
                edge_type="semantic_support_suggestion",
                authority="llm_proposed",
                reason_codes=["bounded_interpretation_reference"],
                source_event_ids=[str(envelope["event_id"]), str(related_id)],
            )


def _record_materialization(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    proposal_node = f"triage:{payload['proposal_id']}"
    for event_id in payload.get("materialized_event_ids") or []:
        canonical_node = _event_node_id(str(event_id))
        _put_node(
            envelope,
            conn,
            node_id=canonical_node,
            node_kind="canonical_event_reference",
            authority="deterministic_derived",
        )
        _put_edge(
            envelope,
            conn,
            from_node_id=proposal_node,
            to_node_id=canonical_node,
            edge_type="materialized_as",
            authority="member_confirmed",
            reason_codes=["confirmed_materialization_receipt"],
        )


def _record_membership_decision(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    actor_field = (
        "revoked_by_member_id"
        if envelope["type"] == "context.membership.revoked"
        else "confirmed_by_member_id"
    )
    if not _is_private_member_envelope(envelope, payload.get(actor_field)):
        return
    object_node = f"{payload['object_kind']}:{payload['object_id']}"
    context_id = str(payload["context_id"])
    _put_context(
        envelope,
        conn,
        context_id=context_id,
        level="member_confirmed",
        member_confirmed=True,
    )
    _put_node(
        envelope,
        conn,
        node_id=object_node,
        node_kind=str(payload["object_kind"]),
        authority="member_confirmed",
    )
    edge_id = "edge_" + stable_digest(
        envelope["tenant_id"], object_node, context_id, "member_of_context"
    )
    if envelope["type"] == "context.membership.revoked":
        conn.execute(
            """UPDATE context_edges
                  SET status='revoked', valid_to=?, revoked_by_event_id=?,
                      last_event_id=?
                WHERE tenant_id=? AND edge_id=? AND status='active'
                  AND last_event_id < ?""",
            (
                envelope["occurred_at"],
                envelope["event_id"],
                envelope["event_id"],
                envelope["tenant_id"],
                edge_id,
                envelope["event_id"],
            ),
        )
    else:
        _put_edge(
            envelope,
            conn,
            from_node_id=object_node,
            to_node_id=context_id,
            edge_type="member_of_context",
            authority="member_confirmed",
            reason_codes=["explicit_member_confirmation"],
        )


def _record_merge_or_split(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    actor_field = (
        "merged_by_member_id"
        if envelope["type"] == "context.merged"
        else "split_by_member_id"
    )
    if not _is_private_member_envelope(envelope, payload.get(actor_field)):
        return
    if envelope["type"] == "context.merged":
        target = str(payload["target_context_id"])
        _put_context(
            envelope,
            conn,
            context_id=target,
            level="member_confirmed",
            member_confirmed=True,
        )
        for source in payload["source_context_ids"]:
            _put_context(
                envelope, conn, context_id=str(source), level="member_confirmed"
            )
            conn.execute(
                """UPDATE contexts SET merged_into_context_id=?, last_event_id=?
                    WHERE tenant_id=? AND context_id=? AND last_event_id < ?""",
                (
                    target,
                    envelope["event_id"],
                    envelope["tenant_id"],
                    source,
                    envelope["event_id"],
                ),
            )
            _put_edge(
                envelope,
                conn,
                from_node_id=str(source),
                to_node_id=target,
                edge_type="merged_into",
                authority="member_confirmed",
                reason_codes=["explicit_member_merge"],
            )
    else:
        source = str(payload["source_context_id"])
        target = str(payload["target_context_id"])
        _put_context(
            envelope,
            conn,
            context_id=source,
            level="member_confirmed",
            member_confirmed=True,
        )
        _put_context(
            envelope,
            conn,
            context_id=target,
            level="member_confirmed",
            member_confirmed=True,
        )
        for object_id in payload["object_ids"]:
            object_node = f"{payload['object_kind']}:{object_id}"
            _put_node(
                envelope,
                conn,
                node_id=object_node,
                node_kind=str(payload["object_kind"]),
                authority="member_confirmed",
            )
            source_edge_id = "edge_" + stable_digest(
                envelope["tenant_id"], object_node, source, "member_of_context"
            )
            conn.execute(
                """UPDATE context_edges
                      SET status='revoked', valid_to=?, revoked_by_event_id=?,
                          last_event_id=?
                    WHERE tenant_id=? AND edge_id=? AND status='active'
                      AND last_event_id < ?""",
                (
                    envelope["occurred_at"],
                    envelope["event_id"],
                    envelope["event_id"],
                    envelope["tenant_id"],
                    source_edge_id,
                    envelope["event_id"],
                ),
            )
            _put_edge(
                envelope,
                conn,
                from_node_id=object_node,
                to_node_id=target,
                edge_type="member_of_context",
                authority="member_confirmed",
                reason_codes=["explicit_member_split"],
            )


def _record_feedback(envelope: Mapping[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("member_id")):
        return
    conn.execute(
        """INSERT INTO triage_feedback (
               tenant_id, feedback_id, proposal_id, member_id, reason, context_id,
               target_kind, source_adapter, provider_account_id, workspace,
               sender_digest, recorded_at, owner_scope, visibility_scope,
               sensitivity, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, feedback_id) DO NOTHING""",
        (
            envelope["tenant_id"],
            payload["feedback_id"],
            payload["proposal_id"],
            payload["member_id"],
            payload["reason"],
            payload.get("context_id"),
            payload.get("target_kind"),
            payload.get("source_adapter"),
            payload.get("provider_account_id"),
            payload.get("workspace"),
            payload.get("sender_digest"),
            payload["recorded_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def _record_policy(envelope: Mapping[str, Any], conn: sqlcipher3.Connection) -> None:
    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("member_id")):
        return
    if envelope["type"] == "triage.policy_rule.approved":
        conn.execute(
            """INSERT INTO triage_policy_rules (
                   tenant_id, rule_id, member_id, scope_kind, scope_value_digest,
                   disposition, enforcement_mode, preview_affected_count,
                   status, approved_at,
                   approved_by_member_id, expires_at, owner_scope,
                   visibility_scope, sensitivity, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id, rule_id) DO UPDATE SET
                   disposition=excluded.disposition,
                   enforcement_mode='shadow',
                   preview_affected_count=excluded.preview_affected_count,
                   status='active', approved_at=excluded.approved_at,
                   approved_by_member_id=excluded.approved_by_member_id,
                   expires_at=excluded.expires_at, revoked_at=NULL,
                   revoked_by_member_id=NULL, revoke_reason=NULL,
                   last_event_id=excluded.last_event_id
               WHERE triage_policy_rules.last_event_id < excluded.last_event_id""",
            (
                envelope["tenant_id"],
                payload["rule_id"],
                payload["member_id"],
                payload["scope_kind"],
                payload["scope_value_digest"],
                payload["disposition"],
                payload["enforcement_mode"],
                payload["preview_affected_count"],
                payload["approved_at"],
                payload["approved_by_member_id"],
                payload.get("expires_at"),
                envelope["owner_scope"],
                envelope["visibility_scope"],
                envelope["sensitivity"],
                envelope["event_id"],
            ),
        )
    else:
        conn.execute(
            """UPDATE triage_policy_rules
                  SET status='revoked', revoked_at=?, revoked_by_member_id=?,
                      revoke_reason=?, last_event_id=?
                WHERE tenant_id=? AND rule_id=? AND member_id=?
                  AND status='active' AND last_event_id < ?""",
            (
                payload["revoked_at"],
                payload["revoked_by_member_id"],
                payload["reason"],
                envelope["event_id"],
                envelope["tenant_id"],
                payload["rule_id"],
                payload["member_id"],
                envelope["event_id"],
            ),
        )


def _record_source_policy(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    """Project the explicit propose/decide/review lifecycle for a narrow rule."""

    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("member_id")):
        return
    event_type = envelope["type"]
    if event_type == "triage.source_policy_rule.proposed":
        conn.execute(
            """INSERT INTO triage_source_policy_proposals (
                   tenant_id, policy_proposal_id, member_id, scope_kind,
                   scope_value_digest, instruction, outcome_kind,
                   verified_feedback_event_ids_json,
                   affected_proposal_ids_json, preview_digest,
                   policy_diff_json, explanation, status, self_activates,
                   provider_effects, proposed_at, owner_scope, visibility_scope,
                   sensitivity, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', 0, 0,
                         ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id, policy_proposal_id) DO NOTHING""",
            (
                envelope["tenant_id"],
                payload["policy_proposal_id"],
                payload["member_id"],
                payload["scope_kind"],
                payload["scope_value_digest"],
                payload["instruction"],
                payload["outcome_kind"],
                _json(payload["verified_feedback_event_ids"]),
                _json(payload.get("affected_proposal_ids") or []),
                payload["preview_digest"],
                _json(payload["policy_diff"]),
                payload["explanation"],
                payload["proposed_at"],
                envelope["owner_scope"],
                envelope["visibility_scope"],
                envelope["sensitivity"],
                envelope["event_id"],
            ),
        )
        return
    if event_type == "triage.source_policy_rule.decided":
        proposal = conn.execute(
            """SELECT * FROM triage_source_policy_proposals
                 WHERE tenant_id=? AND policy_proposal_id=? AND member_id=?
                   AND owner_scope=? AND visibility_scope=?""",
            (
                envelope["tenant_id"],
                payload["policy_proposal_id"],
                payload["member_id"],
                envelope["owner_scope"],
                envelope["visibility_scope"],
            ),
        ).fetchone()
        if (
            proposal is None
            or proposal["status"] != "proposed"
            or proposal["preview_digest"] != payload["preview_digest"]
            or proposal["scope_kind"] != payload["scope_kind"]
            or proposal["scope_value_digest"] != payload["scope_value_digest"]
            or proposal["outcome_kind"] != payload["outcome_kind"]
        ):
            return
        status = {
            "approve": "approved",
            "trial": "trial",
            "reject": "rejected",
            "suppress": "suppressed",
            "edit": "proposed",
        }[payload["decision"]]
        conn.execute(
            """UPDATE triage_source_policy_proposals
                  SET status=?, last_event_id=?
                WHERE tenant_id=? AND policy_proposal_id=?
                  AND member_id=? AND last_event_id < ?""",
            (
                status,
                envelope["event_id"],
                envelope["tenant_id"],
                payload["policy_proposal_id"],
                payload["member_id"],
                envelope["event_id"],
            ),
        )
        if payload["decision"] not in {"approve", "trial"}:
            return
        conn.execute(
            """INSERT INTO triage_source_policy_rules (
                   tenant_id, rule_id, policy_proposal_id, member_id,
                   scope_kind, scope_value_digest, instruction,
                   activation_mode, trial_proposal_ids_json, status,
                   activated_at, owner_scope, visibility_scope, sensitivity,
                   provider_effects, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
               ON CONFLICT(tenant_id, rule_id) DO NOTHING""",
            (
                envelope["tenant_id"],
                payload["rule_id"],
                payload["policy_proposal_id"],
                payload["member_id"],
                proposal["scope_kind"],
                proposal["scope_value_digest"],
                payload["instruction"],
                "approved" if payload["decision"] == "approve" else "trial",
                _json(payload.get("trial_proposal_ids") or []),
                "active" if payload["decision"] == "approve" else "trial",
                payload["decided_at"],
                envelope["owner_scope"],
                envelope["visibility_scope"],
                envelope["sensitivity"],
                envelope["event_id"],
            ),
        )
        return
    rule = conn.execute(
        """SELECT * FROM triage_source_policy_rules
             WHERE tenant_id=? AND rule_id=? AND member_id=?
               AND owner_scope=? AND visibility_scope=?""",
        (
            envelope["tenant_id"],
            payload["rule_id"],
            payload["member_id"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
        ),
    ).fetchone()
    if rule is None or rule["instruction"] != payload["previous_instruction"]:
        return
    status = {
        "retain": rule["status"],
        "narrow": rule["status"],
        "suspend": "suspended",
        "revoke": "revoked",
    }[payload["decision"]]
    instruction = payload.get("replacement_instruction") or rule["instruction"]
    conn.execute(
        """UPDATE triage_source_policy_rules
              SET instruction=?, status=?, last_review_decision=?,
                  last_review_reason=?, reviewed_at=?, last_event_id=?
            WHERE tenant_id=? AND rule_id=? AND member_id=?
              AND last_event_id < ?""",
        (
            instruction,
            status,
            payload["decision"],
            payload["reason"],
            payload["reviewed_at"],
            envelope["event_id"],
            envelope["tenant_id"],
            payload["rule_id"],
            payload["member_id"],
            envelope["event_id"],
        ),
    )


def _record_relationship_decision(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("member_id")):
        return
    confirmed = envelope["type"] == "context.relationship.confirmed"
    decided_at = payload["confirmed_at"] if confirmed else payload["rejected_at"]
    decided_by = (
        payload["confirmed_by_member_id"]
        if confirmed
        else payload["rejected_by_member_id"]
    )
    conn.execute(
        """INSERT INTO context_relationship_decisions (
               tenant_id, relationship_id, source_event_id,
               related_source_event_id, member_id, provider,
               provider_account_id, workspace, algorithm, state, reason, decided_at,
               decided_by_member_id, owner_scope, visibility_scope,
               sensitivity, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, relationship_id) DO UPDATE SET
               algorithm=excluded.algorithm, state=excluded.state,
               reason=excluded.reason,
               decided_at=excluded.decided_at,
               decided_by_member_id=excluded.decided_by_member_id,
               last_event_id=excluded.last_event_id
           WHERE context_relationship_decisions.last_event_id < excluded.last_event_id""",
        (
            envelope["tenant_id"],
            payload["relationship_id"],
            payload["source_event_id"],
            payload["related_source_event_id"],
            payload["member_id"],
            payload["provider"],
            payload["provider_account_id"],
            payload["workspace"],
            payload["algorithm"],
            "confirmed" if confirmed else "rejected",
            payload["reason"],
            decided_at,
            decided_by,
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def _record_source_text_decision(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("member_id")):
        return
    reinstated = envelope["type"] == "context.source_text.reinstated"
    decided_at = payload["reinstated_at"] if reinstated else payload["corrected_at"]
    decided_by = (
        payload["reinstated_by_member_id"]
        if reinstated
        else payload["corrected_by_member_id"]
    )
    conn.execute(
        """INSERT INTO context_source_text_corrections (
               tenant_id, correction_id, source_event_id, proposal_id,
               candidate_key, member_id, algorithm, authored_text_digest,
               evidence_start, evidence_end, state, reason,
               evidence_quote_digest, decided_at, decided_by_member_id,
               owner_scope, visibility_scope, sensitivity, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, correction_id) DO UPDATE SET
               state=excluded.state, reason=excluded.reason,
               proposal_id=COALESCE(excluded.proposal_id,
                   context_source_text_corrections.proposal_id),
               candidate_key=COALESCE(excluded.candidate_key,
                   context_source_text_corrections.candidate_key),
               algorithm=COALESCE(excluded.algorithm,
                   context_source_text_corrections.algorithm),
               authored_text_digest=COALESCE(excluded.authored_text_digest,
                   context_source_text_corrections.authored_text_digest),
               evidence_start=COALESCE(excluded.evidence_start,
                   context_source_text_corrections.evidence_start),
               evidence_end=COALESCE(excluded.evidence_end,
                   context_source_text_corrections.evidence_end),
               evidence_quote_digest=COALESCE(
                   excluded.evidence_quote_digest,
                   context_source_text_corrections.evidence_quote_digest
               ),
               decided_at=excluded.decided_at,
               decided_by_member_id=excluded.decided_by_member_id,
               last_event_id=excluded.last_event_id
           WHERE context_source_text_corrections.last_event_id < excluded.last_event_id""",
        (
            envelope["tenant_id"],
            payload["correction_id"],
            payload["source_event_id"],
            payload.get("proposal_id"),
            payload.get("candidate_key"),
            payload["member_id"],
            payload.get("algorithm"),
            payload.get("authored_text_digest"),
            payload.get("evidence_start"),
            payload.get("evidence_end"),
            "reinstated" if reinstated else "corrected",
            payload["reason"],
            payload.get("evidence_quote_digest"),
            decided_at,
            decided_by,
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def _record_shared_reconciliation_fact(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if (
        envelope.get("owner_scope") != "shared:household"
        or envelope.get("visibility_scope") != "shared:household"
        or envelope.get("actor_identity") != payload.get("source_member_id")
    ):
        return
    conn.execute(
        """INSERT INTO shared_reconciliation_facts (
               tenant_id, fact_id, source_member_id,
               shared_with_member_ids_json, share_grant_id,
               source_reference_digest, fact_kind, references_json,
               current_state, occurred_at, expires_at, owner_scope,
               visibility_scope, sensitivity, last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, fact_id) DO NOTHING""",
        (
            envelope["tenant_id"],
            payload["fact_id"],
            payload["source_member_id"],
            _json(payload["shared_with_member_ids"]),
            payload["share_grant_id"],
            payload["source_reference_digest"],
            payload["fact_kind"],
            _json(payload["references"]),
            payload["current_state"],
            payload["occurred_at"],
            payload.get("expires_at"),
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )


def _record_reconciliation(
    envelope: Mapping[str, Any], conn: sqlcipher3.Connection
) -> None:
    payload = envelope["payload"]
    if not _is_private_member_envelope(envelope, payload.get("owner_member_id")):
        return
    conn.execute(
        """INSERT INTO reconciliation_decisions (
               tenant_id, reconciliation_id, proposal_id, primary_proposal_id,
               matter_id, owner_member_id, source_event_id,
               evidence_source_event_ids_json, member_authored_text_digest,
               decision, decision_revision,
               supersedes_reconciliation_event_id, policy_json,
               source_coordinate_json, freshness, priority_rank,
               priority_basis_json, reason_codes_json, admission_active,
               decided_at, owner_scope, visibility_scope, sensitivity,
               last_event_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(tenant_id, reconciliation_id) DO NOTHING""",
        (
            envelope["tenant_id"],
            payload["reconciliation_id"],
            payload["proposal_id"],
            payload.get("primary_proposal_id"),
            payload["matter_id"],
            payload["owner_member_id"],
            payload["source_event_id"],
            _json(payload["evidence_source_event_ids"]),
            payload.get("member_authored_text_digest"),
            payload["decision"],
            payload["decision_revision"],
            payload.get("supersedes_reconciliation_event_id"),
            _json(payload["policy"]),
            _json(payload["source_coordinate"]),
            payload["freshness"],
            payload["priority_rank"],
            _json(payload["priority_basis"]),
            _json(payload["reason_codes"]),
            int(bool(payload["admission_active"])),
            payload["decided_at"],
            envelope["owner_scope"],
            envelope["visibility_scope"],
            envelope["sensitivity"],
            envelope["event_id"],
        ),
    )
    matter_id = str(payload["matter_id"])
    proposal_node = f"triage:{payload['proposal_id']}"
    _put_context(
        envelope,
        conn,
        context_id=matter_id,
        level="deterministic_matter",
    )
    _put_node(
        envelope,
        conn,
        node_id=proposal_node,
        node_kind="triage_candidate",
        authority="deterministic_derived",
    )
    _put_edge(
        envelope,
        conn,
        from_node_id=proposal_node,
        to_node_id=matter_id,
        edge_type="member_of_reconciled_matter",
        authority="deterministic_derived",
        reason_codes=["triage_reconciliation_decision"],
        source_event_ids=list(payload["evidence_source_event_ids"]),
    )
    for relationship in payload.get("relationships") or []:
        conn.execute(
            """INSERT INTO reconciliation_relationships (
                   tenant_id, relationship_id, reconciliation_id, proposal_id,
                   source_event_id, related_source_event_id,
                   relationship_type, authority, automatic_effect,
                   reason_codes_json, owner_member_id, owner_scope,
                   visibility_scope, sensitivity, last_event_id
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id, relationship_id, reconciliation_id)
               DO NOTHING""",
            (
                envelope["tenant_id"],
                relationship["relationship_id"],
                payload["reconciliation_id"],
                payload["proposal_id"],
                payload["source_event_id"],
                relationship["related_source_event_id"],
                relationship["relationship_type"],
                relationship["authority"],
                int(bool(relationship["automatic_effect"])),
                _json(relationship["reason_codes"]),
                payload["owner_member_id"],
                envelope["owner_scope"],
                envelope["visibility_scope"],
                envelope["sensitivity"],
                envelope["event_id"],
            ),
        )
        if not relationship["automatic_effect"]:
            continue
        related_node = _event_node_id(str(relationship["related_source_event_id"]))
        _put_node(
            envelope,
            conn,
            node_id=related_node,
            node_kind="source_event_reference",
            authority="deterministic_derived",
        )
        _put_edge(
            envelope,
            conn,
            from_node_id=proposal_node,
            to_node_id=related_node,
            edge_type=str(relationship["relationship_type"]),
            authority=(
                "member_confirmed"
                if relationship["authority"]
                in {"member_confirmed", "explicitly_shared_typed_fact"}
                else "provider_exact"
                if relationship["authority"] == "provider_exact"
                else "deterministic_derived"
            ),
            reason_codes=list(relationship["reason_codes"]),
            source_event_ids=list(payload["evidence_source_event_ids"]),
        )


def apply_event(envelope: dict[str, Any], conn: sqlcipher3.Connection) -> None:
    """Index one envelope without reading another projection or the network."""

    payload = envelope.get("payload") or {}
    if not isinstance(payload, Mapping):
        payload = {}
    event_type = str(envelope["type"])
    message_payload = (
        payload if event_type in {"messaging.received", "messaging.sent"} else {}
    )
    _, message_provider, message_account, message_workspace = (
        provider_coordinates(envelope, message_payload)
        if message_payload
        else ("", "", "", "")
    )
    _put_node(
        envelope,
        conn,
        node_id=_event_node_id(str(envelope["event_id"])),
        node_kind="source_event"
        if event_type
        in {
            "messaging.received",
            "messaging.sent",
            "telephony.sms_received",
            "calendar.event_added",
            "artifact.received",
        }
        else "event",
        authority="source_fact",
        provider=message_provider or None,
        provider_account_id=message_account or None,
        workspace=message_workspace or None,
    )
    _record_lineage(envelope, conn)
    if event_type in {"messaging.received", "messaging.sent"}:
        _record_message(envelope, conn)
    elif event_type == "telephony.sms_received":
        _record_telephony(envelope, conn)
    elif event_type == "triage.item.proposed":
        _record_triage_proposal(envelope, conn)
    elif event_type == "triage.item.materialized":
        _record_materialization(envelope, conn)
    elif event_type in {"context.membership.confirmed", "context.membership.revoked"}:
        _record_membership_decision(envelope, conn)
    elif event_type in {"context.merged", "context.split"}:
        _record_merge_or_split(envelope, conn)
    elif event_type in {
        "context.relationship.rejected",
        "context.relationship.confirmed",
    }:
        _record_relationship_decision(envelope, conn)
    elif event_type in {
        "context.source_text.corrected",
        "context.source_text.reinstated",
    }:
        _record_source_text_decision(envelope, conn)
    elif event_type == "context.reconciliation.typed_fact_shared":
        _record_shared_reconciliation_fact(envelope, conn)
    elif event_type == "triage.reconciliation.decided":
        _record_reconciliation(envelope, conn)
    elif event_type == "triage.feedback.recorded":
        _record_feedback(envelope, conn)
    elif event_type in {"triage.policy_rule.approved", "triage.policy_rule.revoked"}:
        _record_policy(envelope, conn)
    elif event_type in {
        "triage.source_policy_rule.proposed",
        "triage.source_policy_rule.decided",
        "triage.source_policy_rule.reviewed",
    }:
        _record_source_policy(envelope, conn)
